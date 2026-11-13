import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.base_links.models import Operadora
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import (
    CMDBLinkMirror,
    OTRSStateMapping,
    OTRSTicket,
    OTRSTicketConfigItem,
)

from .models import (
    QualityAction,
    QualityAuditEvent,
    QualityContact,
    QualityDeletedOrder,
    QualityEvidence,
    QualityMonitoringCycle,
    QualityOccurrence,
    QualityOrder,
    QualityTicket,
    QualityTreatment,
    QualityTreatmentIdentity,
)
from .services.dashboard import get_dashboard_data, get_operational_pulse
from .services.history import get_global_quality_history
from .services.monitoring import (
    finish_monitoring_cycle,
    get_monitoring_queue,
    start_monitoring_cycle,
)
from .services.occurrences import link_occurrence
from .services.orders import (
    create_quality_order,
    delete_quality_order,
    get_quality_order_delete_impact,
)
from .services.orders_queue import get_quality_orders_queue
from .services.priority_link_detail import get_priority_link_detail
from .services.priority_links import get_priority_links
from .services.tq_correlation import link_quality_ticket_from_article
from .services.tq_delivery import (
    claim_quality_treatment_send,
    record_tq_send_accepted,
    record_tq_send_failure,
)
from .services.tq_otrs_correlation import (
    reconcile_quality_articles,
    refresh_linked_quality_ticket_states,
)
from .services.treatments import (
    create_quality_treatment,
    link_quality_ticket,
    toggle_quality_action,
)
from .tests import QualityModuleTestCase


class _FakeOTRSArticleClient:
    def __init__(self, articles=None, ticket_payloads=None):
        self.articles = list(articles or [])
        self.ticket_payloads = dict(ticket_payloads or {})

    def fetch_quality_articles(self, queue_name, *, after_article_id=0, limit=100):
        return [
            item
            for item in self.articles
            if item.get("queue_name") == queue_name and int(item.get("article_id") or 0) > after_article_id
        ][:limit]

    def fetch_quality_ticket_states(self, ticket_ids, *, queue_names, limit=100):
        wanted = set(ticket_ids)
        return [payload for tid, payload in self.ticket_payloads.items() if tid in wanted][:limit]


class QualityOrderDeleteTests(QualityModuleTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.manage_permission = Permission.objects.get(
            content_type__app_label="qualidade",
            codename="manage_quality_orders",
        )
        cls.delete_permission = Permission.objects.get(
            content_type__app_label="qualidade",
            codename="delete_qualityorder",
        )

    def setUp(self):
        self.admin_user = self.create_authorized_user("quality-delete-admin")
        self.admin_user.user_permissions.add(self.manage_permission, self.delete_permission)
        self.link = CMDBLinkMirror.objects.create(
            source_config_item_id="CI-DEL-001",
            designation="CLARO.SP.CIR000101",
            contracting_client="Operadora Alpha",
            business_project="Projeto Alpha",
            state="SP",
        )
        self.client.force_login(self.admin_user)

    def _create_full_order(self, *, code_suffix="001", link=None, owner=None, media_content=b"evidencia"):
        target_link = link or self.link
        order = create_quality_order(
            link=target_link,
            owner=owner or self.admin_user,
            actor=self.admin_user,
            status=QualityOrder.Status.MONITORAMENTO,
            priority=QualityOrder.Priority.ALTA,
            recurrence_level=QualityOrder.RecurrenceLevel.R5_PLUS,
            current_reason="Quedas recorrentes confirmadas",
            current_root_cause="Falha intermitente de transmissão",
            internal_notes="Observação operacional interna da OQ",
            treatment_deadline=timezone.now() - timedelta(days=1),
        )
        support_ticket = self.create_ticket(
            link=target_link,
            external_id=71000 + int(code_suffix),
            created_at=timezone.now() - timedelta(days=5),
        )
        linked_otrs_ticket = self.create_ticket(
            link=target_link,
            external_id=72000 + int(code_suffix),
            created_at=timezone.now() - timedelta(days=3),
        )
        initial_occ = link_occurrence(
            quality_order=order,
            actor=self.admin_user,
            otrs_ticket=support_ticket,
            ticket_external_id_snapshot=support_ticket.external_id,
            ticket_number=support_ticket.ticket_number,
            occurred_at=support_ticket.source_created_at,
            is_initial_occurrence=True,
            recurrence_number=0,
            reason="Incidente inicial",
            root_cause="Falha intermitente de transmissão",
            counts_as_recurrence=False,
            same_root_cause=False,
        )
        recurrence_occ = link_occurrence(
            quality_order=order,
            actor=self.admin_user,
            otrs_ticket=linked_otrs_ticket,
            ticket_external_id_snapshot=linked_otrs_ticket.external_id,
            ticket_number=linked_otrs_ticket.ticket_number,
            occurred_at=linked_otrs_ticket.source_created_at,
            is_initial_occurrence=False,
            recurrence_number=1,
            reason="Reincidência da mesma causa",
            root_cause="Falha intermitente de transmissão",
            counts_as_recurrence=True,
            same_root_cause=True,
        )
        pending_action = QualityAction.objects.create(
            quality_order=order,
            title="Validar reparo definitivo em campo",
            completed=False,
            order=99,
        )
        first_action = order.actions.order_by("order", "pk").first()
        if first_action:
            toggle_quality_action(
                quality_order=order,
                action_id=first_action.pk,
                completed=True,
                actor=self.admin_user,
            )
        treatment, q_otrs_ticket = self.prepare_treatment_for_rreip(
            self.admin_user,
            target_link,
            order,
            ticket_id=73000 + int(code_suffix),
        )
        quality_ticket_record, _ = link_quality_ticket(
            quality_order=order,
            ticket_identifier=q_otrs_ticket.ticket_number,
            ticket_type=QualityTicket.TicketType.TREATMENT,
            actor=self.admin_user,
        )
        active_cycle = start_monitoring_cycle(
            quality_order=order,
            quality_ticket=quality_ticket_record,
            started_at=timezone.now() - timedelta(days=10),
            actor=self.admin_user,
            actions_analyzed=True,
            otrs_actions_summary="Troca de transceiver validada.",
            financial_regularization_confirmed=True,
            financial_evidence="NF 102030 compensada",
        )
        evidence = QualityEvidence.objects.create(
            quality_order=order,
            quality_treatment=treatment,
            title="Relatório de vistoria",
            file=SimpleUploadedFile(
                f"evidencia-{code_suffix}.pdf",
                media_content,
                content_type="application/pdf",
            ),
            original_filename=f"evidencia-{code_suffix}.pdf",
            notes="Arquivo anexo exclusivo da OQ",
            uploaded_by=self.admin_user,
        )
        return {
            "order": order,
            "support_ticket": support_ticket,
            "linked_otrs_ticket": linked_otrs_ticket,
            "quality_otrs_ticket": q_otrs_ticket,
            "initial_occ": initial_occ,
            "recurrence_occ": recurrence_occ,
            "pending_action": pending_action,
            "treatment": treatment,
            "quality_ticket": quality_ticket_record,
            "cycle": active_cycle,
            "evidence": evidence,
        }

    # 1. Excluir OQ sem registros dependentes + confirmação + não reutilização de código
    def test_delete_empty_order_and_never_reuse_code(self):
        order = QualityOrder.objects.create(link=self.link, current_reason="Abertura simples")
        deleted_code = order.code

        bad_response = self.client.post(
            reverse("qualidade:ordem_excluir", kwargs={"codigo": deleted_code}),
            {"confirm_code": "WRONG-CODE", "reason": "Teste"},
        )
        self.assertEqual(bad_response.status_code, 302)
        self.assertTrue(QualityOrder.objects.filter(pk=order.pk).exists())

        with self.assertRaises(ValidationError):
            delete_quality_order(
                order_id=order.pk,
                confirm_code="",
                actor=self.admin_user,
            )

        response = self.client.post(
            reverse("qualidade:ordem_excluir", kwargs={"codigo": deleted_code}),
            {"confirm_code": deleted_code, "reason": "Encerramento administrativo"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("qualidade:ordens"))
        self.assertFalse(QualityOrder.objects.filter(pk=order.pk).exists())
        self.assertTrue(QualityDeletedOrder.objects.filter(order_code=deleted_code).exists())
        self.assertEqual(
            self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": deleted_code})).status_code,
            404,
        )

        replacement = create_quality_order(link=self.link, actor=self.admin_user, current_reason="Nova OQ")
        self.assertNotEqual(replacement.code, deleted_code)

    # 2. Excluir OQ com TQ aberta em qualquer status (inclusive envio incerto / em progresso)
    def test_delete_order_with_open_tqs_in_any_send_or_workflow_status(self):
        cases = [
            (QualityTreatment.Status.PENDING_SEND, QualityTreatment.SendStatus.NOT_SENT, 74001),
            (QualityTreatment.Status.PENDING_SEND, QualityTreatment.SendStatus.IN_PROGRESS, 74002),
            (QualityTreatment.Status.PENDING_SEND, QualityTreatment.SendStatus.UNCERTAIN, 74003),
            (QualityTreatment.Status.WAITING_OTRS, QualityTreatment.SendStatus.SENT, 74004),
            (QualityTreatment.Status.IN_PROGRESS, QualityTreatment.SendStatus.SENT, 74005),
        ]
        for idx, (status_value, send_status_value, ticket_id) in enumerate(cases, start=1):
            link = CMDBLinkMirror.objects.create(
                source_config_item_id=f"CI-TQ-{idx}",
                designation=f"CLARO.SP.TQ{idx:05d}",
                contracting_client="Operadora Alpha",
                business_project="Projeto Alpha",
                state="SP",
            )
            order = create_quality_order(link=link, actor=self.admin_user, current_reason="Teste TQ")
            support = self.create_ticket(link=link, external_id=ticket_id)
            tq = create_quality_treatment(
                quality_order=order,
                reason="Tratativa em andamento",
                support_ticket=support,
                actor=self.admin_user,
            )
            QualityTreatment.objects.filter(pk=tq.pk).update(
                status=status_value,
                send_status=send_status_value,
            )
            result = delete_quality_order(
                order_id=order.pk,
                confirm_code=order.code,
                actor=self.admin_user,
            )
            self.assertEqual(result["order_code"], order.code)
            self.assertFalse(QualityOrder.objects.filter(pk=order.pk).exists())
            self.assertFalse(QualityTreatment.objects.filter(pk=tq.pk).exists())
            identity = QualityTreatmentIdentity.objects.get(code=tq.code)
            self.assertIsNotNone(identity.deleted_at)
            # OTRS support ticket stays intact
            self.assertTrue(OTRSTicket.objects.filter(pk=support.pk).exists())

    # 3. Excluir OQ com monitoramento ativo e ciclo encerrado sem erro de PROTECT
    def test_delete_order_with_active_and_completed_rreip_monitoring_cycles(self):
        order = create_quality_order(
            link=self.link,
            actor=self.admin_user,
            status=QualityOrder.Status.MONITORAMENTO,
        )
        tq, q_ticket = self.prepare_treatment_for_rreip(self.admin_user, self.link, order, ticket_id=75001)
        qt_record, _ = link_quality_ticket(
            quality_order=order,
            ticket_identifier=q_ticket.ticket_number,
            ticket_type=QualityTicket.TicketType.TREATMENT,
            actor=self.admin_user,
        )
        reopen_ticket = self.create_ticket(
            link=self.link,
            external_id=75002,
            created_at=timezone.now() - timedelta(days=3),
        )
        first_cycle = start_monitoring_cycle(
            quality_order=order,
            quality_ticket=qt_record,
            started_at=timezone.now() - timedelta(days=20),
            actor=self.admin_user,
            actions_analyzed=True,
            otrs_actions_summary="Análise inicial concluída no OTRS.",
        )
        finish_monitoring_cycle(
            cycle=first_cycle,
            result=QualityMonitoringCycle.Result.RECURRENCE,
            actor=self.admin_user,
            trigger_ticket_identifier=reopen_ticket.ticket_number,
            final_analysis="Reaberta por novo chamado",
            recurrence_description="Nova queda durante RREIP",
            same_root_cause_confirmed=True,
        )
        _tq2, q_ticket2 = self.prepare_treatment_for_rreip(self.admin_user, self.link, order, ticket_id=75003)
        qt_record2, _ = link_quality_ticket(
            quality_order=order,
            ticket_identifier=q_ticket2.ticket_number,
            ticket_type=QualityTicket.TicketType.TREATMENT,
            actor=self.admin_user,
        )
        active_cycle = start_monitoring_cycle(
            quality_order=order,
            quality_ticket=qt_record2,
            started_at=timezone.now() - timedelta(days=95),
            actor=self.admin_user,
            actions_analyzed=True,
            otrs_actions_summary="Novo acompanhamento RREIP iniciado.",
        )
        self.assertEqual(active_cycle.result, QualityMonitoringCycle.Result.IN_PROGRESS)

        summary = delete_quality_order(
            order_id=order.pk,
            confirm_code=order.code,
            actor=self.admin_user,
        )
        self.assertEqual(summary["counts"]["monitoring_cycles"], 2)
        self.assertFalse(QualityMonitoringCycle.objects.filter(pk__in=[first_cycle.pk, active_cycle.pk]).exists())
        self.assertFalse(QualityTreatment.objects.filter(pk=tq.pk).exists())
        self.assertFalse(QualityOrder.objects.filter(pk=order.pk).exists())

    # 4. Excluir OQ com tratativas, ações pendentes, reincidências, observações e histórico
    def test_delete_order_removes_treatments_actions_occurrences_notes_and_replaces_history_with_minimal_audit(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=media_dir):
                bundle = self._create_full_order(code_suffix="004")
                order = bundle["order"]
                order_code = order.code
                order_pk = order.pk
                treatment_pk = bundle["treatment"].pk

                self.assertGreater(AuditEvent.objects.filter(category="QUALITY", metadata__code=order_code).count(), 0)
                response = self.client.post(
                    reverse("qualidade:ordem_excluir", kwargs={"codigo": order_code}),
                    {"confirm_code": order_code, "reason": "Duplicidade operacional"},
                )
                self.assertEqual(response.status_code, 302)

        self.assertFalse(QualityOrder.objects.filter(pk=order_pk).exists())
        self.assertFalse(QualityTreatment.objects.filter(pk=treatment_pk).exists())
        self.assertFalse(QualityAction.objects.filter(quality_order_id=order_pk).exists())
        self.assertFalse(QualityOccurrence.objects.filter(quality_order_id=order_pk).exists())
        self.assertFalse(QualityTicket.objects.filter(quality_order_id=order_pk).exists())
        self.assertFalse(QualityAuditEvent.objects.filter(quality_order_id=order_pk).exists())
        self.assertEqual(AuditEvent.objects.filter(category="QUALITY", metadata__code=order_code).count(), 0)

        audit_event = AuditEvent.objects.get(category="ADMINISTRATION", action="OQ_DELETED", object_label=order_code)
        self.assertEqual(audit_event.module, "Qualidade")
        self.assertEqual(audit_event.metadata["order_code"], order_code)
        self.assertEqual(audit_event.metadata["reason"], "Duplicidade operacional")
        self.assertEqual(audit_event.metadata["deleted_dependents"]["treatments"], 1)
        self.assertEqual(audit_event.metadata["deleted_dependents"]["monitoring_cycles"], 1)
        self.assertEqual(audit_event.metadata["deleted_dependents"]["occurrences"], 2)

        self.admin_user.is_superuser = True
        self.admin_user.save(update_fields=("is_superuser",))
        self.assertEqual(self.client.get(reverse("configuracoes:audit")).status_code, 200)

    # 5. Excluir OQ com evidências anexadas (pós-commit) e resiliência em falha de storage
    def test_delete_order_cleans_exclusive_evidence_files_on_commit_and_retries_storage_errors(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=media_dir):
                bundle = self._create_full_order(code_suffix="005", media_content=b"PDF-A")
                order = bundle["order"]
                evidence = bundle["evidence"]
                evidence_path = Path(media_dir) / evidence.file.name
                orphan_dir = Path(media_dir) / "qualidade" / "treatments" / bundle["treatment"].code
                orphan_dir.mkdir(parents=True, exist_ok=True)
                orphan_file = orphan_dir / "anexo-exclusivo.txt"
                orphan_file.write_text("conteudo exclusivo", encoding="utf-8")
                self.assertTrue(evidence_path.exists())
                self.assertTrue(orphan_file.exists())

                with self.captureOnCommitCallbacks(execute=True):
                    summary = delete_quality_order(
                        order_id=order.pk,
                        confirm_code=order.code,
                        actor=self.admin_user,
                    )

                self.assertGreaterEqual(summary["deleted_files"], 2)
                self.assertFalse(QualityEvidence.objects.filter(pk=evidence.pk).exists())
                self.assertFalse(evidence_path.exists())
                self.assertFalse(orphan_file.exists())
                self.assertFalse((Path(media_dir) / "qualidade" / "orders" / order.code).exists())

            # Storage delete fails once then succeeds on retry (2 attempts total)
            with override_settings(MEDIA_ROOT=media_dir):
                bundle_retry = self._create_full_order(code_suffix="006", media_content=b"PDF-B")
                ev_retry = bundle_retry["evidence"]
                orig_delete = FileSystemStorage.delete
                calls = []

                def flaky_delete(storage_self, name):
                    calls.append(name)
                    if len(calls) == 1:
                        raise OSError("transient storage failure")
                    return orig_delete(storage_self, name)

                with patch.object(FileSystemStorage, "delete", autospec=True, side_effect=flaky_delete):
                    with self.captureOnCommitCallbacks(execute=True):
                        delete_quality_order(
                            order_id=bundle_retry["order"].pk,
                            confirm_code=bundle_retry["order"].code,
                            actor=self.admin_user,
                        )
                self.assertEqual(len(calls), 2)
                self.assertFalse((Path(media_dir) / ev_retry.file.name).exists())

            # Persistent storage failure is logged and never breaks DB commit
            with override_settings(MEDIA_ROOT=media_dir):
                bundle_fail = self._create_full_order(code_suffix="007", media_content=b"PDF-C")
                with patch.object(FileSystemStorage, "delete", side_effect=OSError("disk locked")):
                    with self.assertLogs("aplicativos.qualidade.services.orders", level="ERROR") as logs:
                        with self.captureOnCommitCallbacks(execute=True):
                            delete_quality_order(
                                order_id=bundle_fail["order"].pk,
                                confirm_code=bundle_fail["order"].code,
                                actor=self.admin_user,
                            )
                self.assertTrue(any("Falha ao remover arquivo exclusivo" in entry for entry in logs.output))
                self.assertFalse(QualityOrder.objects.filter(pk=bundle_fail["order"].pk).exists())

    # 6. Preservar dados compartilhados com outra OQ (CMDB, OTRS, contatos, evidência compartilhada, FK cruzada)
    def test_delete_order_preserves_shared_cmdb_otrs_contacts_files_and_other_orders(self):
        contact = QualityContact.objects.create(
            company="Claro",
            region="SP",
            client="Operadora Alpha",
            project="Projeto Alpha",
            contact_type="ISP",
            name="Ponto Focal Compartilhado",
            email="focal@operadora.example",
        )
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=media_dir):
                bundle_a = self._create_full_order(code_suffix="008")
                order_a = bundle_a["order"]

                # Mark order_a concluded so order_b can exist on the same link (real-world sequential or shared-file case)
                order_a.status = QualityOrder.Status.CONCLUIDA
                order_a.completed_at = timezone.now()
                order_a.save(update_fields=("status", "completed_at", "updated_at"))

                link_b = self.link
                order_b = create_quality_order(
                    link=link_b,
                    owner=self.admin_user,
                    actor=self.admin_user,
                    status=QualityOrder.Status.MONITORAMENTO,
                    current_reason="Segunda OQ ativa no mesmo enlace",
                )
                shared_evidence = QualityEvidence.objects.create(
                    quality_order=order_b,
                    title="Arquivo compartilhado",
                    file=bundle_a["evidence"].file.name,
                    original_filename="shared.pdf",
                    uploaded_by=self.admin_user,
                )
                cross_cycle = QualityMonitoringCycle.objects.create(
                    quality_order=order_b,
                    quality_treatment=bundle_a["treatment"],
                    quality_ticket=bundle_a["quality_ticket"],
                    trigger_occurrence=bundle_a["recurrence_occ"],
                    cycle_number=1,
                    started_at=timezone.now() - timedelta(days=5),
                    expected_end_at=timezone.now() + timedelta(days=85),
                    result=QualityMonitoringCycle.Result.IN_PROGRESS,
                )
                shared_file_path = Path(media_dir) / bundle_a["evidence"].file.name
                self.assertTrue(shared_file_path.exists())

                with self.captureOnCommitCallbacks(execute=True):
                    delete_quality_order(
                        order_id=order_a.pk,
                        confirm_code=order_a.code,
                        actor=self.admin_user,
                    )

                self.assertFalse(QualityOrder.objects.filter(pk=order_a.pk).exists())
                self.assertTrue(QualityOrder.objects.filter(pk=order_b.pk).exists())
                self.assertTrue(CMDBLinkMirror.objects.filter(pk=self.link.pk).exists())
                self.assertTrue(OTRSTicket.objects.filter(pk=bundle_a["support_ticket"].pk).exists())
                self.assertTrue(OTRSTicket.objects.filter(pk=bundle_a["linked_otrs_ticket"].pk).exists())
                self.assertTrue(OTRSTicket.objects.filter(pk=bundle_a["quality_otrs_ticket"].pk).exists())
                self.assertTrue(OTRSTicketConfigItem.objects.filter(ticket=bundle_a["support_ticket"], cmdb_link=self.link).exists())
                self.assertTrue(QualityContact.objects.filter(pk=contact.pk).exists())
                self.assertTrue(QualityEvidence.objects.filter(pk=shared_evidence.pk).exists())
                self.assertTrue(shared_file_path.exists())
                cross_cycle.refresh_from_db()
                self.assertIsNone(cross_cycle.quality_treatment)
                self.assertIsNone(cross_cycle.quality_ticket)
                self.assertIsNone(cross_cycle.trigger_occurrence)

    # 7. Impedir exclusão sem permissão ou fora do escopo + confirmar UI e modal
    def test_delete_permission_data_scope_and_modal_contract(self):
        order = QualityOrder.objects.create(link=self.link, current_reason="Validação de acesso")
        detail_url = reverse("qualidade:ordem_detail", kwargs={"codigo": order.code})
        delete_url = reverse("qualidade:ordem_excluir", kwargs={"codigo": order.code})

        # Authorized user sees action button, required warning text, impact counts, and confirm input
        detail_response = self.client.get(detail_url)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Excluir OQ</button>")
        self.assertContains(
            detail_response,
            "Esta ação excluirá permanentemente a Ordem de Qualidade e seus registros dependentes, "
            "incluindo TQs, monitoramentos, tratativas, históricos e anexos exclusivos. Esta ação não poderá ser desfeita.",
        )
        self.assertContains(detail_response, 'data-quality-delete-confirm')
        self.assertContains(detail_response, 'data-quality-delete-submit')
        self.assertContains(detail_response, delete_url)

        # Viewer without delete permission
        viewer = self.create_authorized_user("quality-viewer-only")
        self.client.force_login(viewer)
        viewer_detail = self.client.get(detail_url)
        self.assertEqual(viewer_detail.status_code, 200)
        self.assertNotContains(viewer_detail, 'id="quality-order-delete-dialog"')
        self.assertNotContains(viewer_detail, "Excluir OQ</button>")
        self.assertEqual(self.client.post(delete_url, {"confirm_code": order.code}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            delete_quality_order(order_id=order.pk, confirm_code=order.code, actor=viewer)

        # Manager without delete permission is also blocked
        manager = self.create_authorized_user("quality-manager-no-del")
        manager.user_permissions.add(self.manage_permission)
        self.client.force_login(manager)
        manager_detail = self.client.get(detail_url)
        self.assertEqual(manager_detail.status_code, 200)
        self.assertNotContains(manager_detail, 'id="quality-order-delete-dialog"')
        self.assertEqual(self.client.post(delete_url, {"confirm_code": order.code}).status_code, 403)

        # User with delete permission restricted to another Operadora scope gets 404
        scoped_user = self.admin_user.__class__.objects.create_user(
            username="quality-other-scope-del",
            password="unused-test-password",
        )
        scoped_user.user_permissions.add(self.view_permission, self.manage_permission, self.delete_permission)
        other_operadora = Operadora.objects.create(
            name="Outra Operadora",
            normalized_name="outra operadora",
        )
        other_group = Group.objects.create(name="Scope Outra Operadora Delete")
        scope = GroupDataScope.objects.create(
            group=other_group,
            mode=GroupDataScope.Mode.CUSTOM,
        )
        scope.allowed_operators.add(other_operadora)
        scoped_user.groups.add(other_group)
        self.client.force_login(scoped_user)
        self.assertEqual(self.client.get(detail_url).status_code, 404)
        self.assertEqual(self.client.post(delete_url, {"confirm_code": order.code}).status_code, 404)
        with self.assertRaises(QualityOrder.DoesNotExist):
            delete_quality_order(order_id=order.pk, confirm_code=order.code, actor=scoped_user)
        self.assertTrue(QualityOrder.objects.filter(pk=order.pk).exists())

    # 8. Não deixar registros órfãos em nenhuma tabela dependente
    def test_delete_leaves_zero_orphan_dependents(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=media_dir):
                bundle = self._create_full_order(code_suffix="009")
                order = bundle["order"]
                impact = get_quality_order_delete_impact(order)
                self.assertEqual(impact["treatments"], 1)
                self.assertEqual(impact["monitoring_cycles"], 1)
                self.assertEqual(impact["quality_tickets"], 1)
                self.assertEqual(impact["occurrences"], 2)
                self.assertGreaterEqual(impact["actions"], 1)
                self.assertEqual(impact["evidences"], 1)
                self.assertGreaterEqual(impact["audit_events"], 1)

                delete_quality_order(
                    order_id=order.pk,
                    confirm_code=order.code,
                    actor=self.admin_user,
                )

        self.assertEqual(QualityOrder.objects.count(), 0)
        self.assertEqual(QualityTreatment.objects.count(), 0)
        self.assertEqual(QualityMonitoringCycle.objects.count(), 0)
        self.assertEqual(QualityTicket.objects.count(), 0)
        self.assertEqual(QualityOccurrence.objects.count(), 0)
        self.assertEqual(QualityAction.objects.count(), 0)
        self.assertEqual(QualityEvidence.objects.count(), 0)
        self.assertEqual(QualityAuditEvent.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.filter(category="QUALITY").count(), 0)

    # 9. Reverter tudo atomicamente (BD + arquivos) em caso de falha transacional
    def test_delete_rolls_back_db_and_preserves_evidence_files_on_transaction_failure(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=media_dir):
                bundle = self._create_full_order(code_suffix="010", media_content=b"SAFE-PDF")
                order = bundle["order"]
                evidence_path = Path(media_dir) / bundle["evidence"].file.name
                treatment_code = bundle["treatment"].code

                with patch(
                    "aplicativos.qualidade.services.orders.AuditService.record",
                    side_effect=DatabaseError("simulated atomic failure"),
                ):
                    with self.captureOnCommitCallbacks(execute=True) as callbacks:
                        with self.assertRaises(DatabaseError):
                            delete_quality_order(
                                order_id=order.pk,
                                confirm_code=order.code,
                                actor=self.admin_user,
                            )

                self.assertEqual(callbacks, [])
                self.assertTrue(QualityOrder.objects.filter(pk=order.pk).exists())
                self.assertTrue(QualityMonitoringCycle.objects.filter(quality_order=order).exists())
                self.assertTrue(QualityTreatment.objects.filter(quality_order=order).exists())
                self.assertTrue(QualityOccurrence.objects.filter(quality_order=order).exists())
                self.assertTrue(QualityTicket.objects.filter(quality_order=order).exists())
                self.assertTrue(QualityAction.objects.filter(quality_order=order).exists())
                self.assertTrue(QualityEvidence.objects.filter(quality_order=order).exists())
                self.assertTrue(QualityAuditEvent.objects.filter(quality_order=order).exists())
                self.assertFalse(QualityDeletedOrder.objects.filter(order_code=order.code).exists())
                self.assertIsNone(QualityTreatmentIdentity.objects.get(code=treatment_code).deleted_at)
                self.assertTrue(evidence_path.exists())

    # 10. Atualizar corretamente contadores, buscas, filtros e indicadores após exclusão
    def test_delete_updates_queues_search_kpis_monitoring_history_and_priority_links(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=media_dir):
                bundle = self._create_full_order(code_suffix="011")
                order = bundle["order"]
                order_code = order.code

                before_orders = get_quality_orders_queue(self.admin_user, {"tab": "all"})
                before_pulse = get_operational_pulse(self.admin_user)
                before_dashboard = get_dashboard_data(self.admin_user, {"period": "all"})
                before_monitoring = get_monitoring_queue(self.admin_user, {"tab": "all", "period": "all"})
                before_history = get_global_quality_history(self.admin_user, {"period": "all"})
                before_priority = get_priority_links(self.admin_user, {"period": "all"})
                before_detail = get_priority_link_detail(self.admin_user, self.link.source_config_item_id, {"period": "all"})

                self.assertEqual(before_orders["counts"]["all"], 1)
                self.assertEqual(before_pulse["open_orders"], 1)
                self.assertEqual(before_pulse["monitoring"], 1)
                self.assertGreaterEqual(before_monitoring["active_count"], 1)
                self.assertGreater(before_history["total_count"], 0)
                self.assertEqual(before_priority["rows"][0].active_order_code, order_code)
                self.assertEqual(before_detail["same_cause_recurrences"], 1)

                delete_quality_order(
                    order_id=order.pk,
                    confirm_code=order_code,
                    actor=self.admin_user,
                )

                after_orders = get_quality_orders_queue(self.admin_user, {"tab": "all", "q": order_code})
                after_all_orders = get_quality_orders_queue(self.admin_user, {"tab": "all"})
                after_pulse = get_operational_pulse(self.admin_user)
                after_dashboard = get_dashboard_data(self.admin_user, {"period": "all"})
                after_monitoring = get_monitoring_queue(self.admin_user, {"tab": "all", "period": "all"})
                after_history = get_global_quality_history(self.admin_user, {"period": "all"})
                after_priority = get_priority_links(self.admin_user, {"period": "all"})
                after_detail = get_priority_link_detail(self.admin_user, self.link.source_config_item_id, {"period": "all"})

                self.assertEqual(after_orders["rows"], [])
                self.assertEqual(after_all_orders["counts"]["all"], 0)
                self.assertEqual(after_pulse["open_orders"], 0)
                self.assertEqual(after_pulse["monitoring"], 0)
                self.assertEqual(after_dashboard["kpis"]["monitoring"], 0)
                self.assertEqual(after_dashboard["attention_count"], 0)
                self.assertEqual(after_monitoring["active_count"], 0)
                self.assertEqual(after_monitoring["cycles"], [])
                self.assertEqual(after_history["total_count"], 0)
                self.assertIsNone(after_priority["rows"][0].active_order_code)
                self.assertEqual(after_detail["same_cause_recurrences"], 0)
                self.assertLess(
                    after_dashboard["kpis"]["open_orders"],
                    before_dashboard["kpis"]["open_orders"] or 999,
                )

                for route_name, kwargs in (
                    ("qualidade:dashboard", {}),
                    ("qualidade:links_prioritarios", {}),
                    ("qualidade:priority_link_detail", {"identificador": self.link.source_config_item_id}),
                    ("qualidade:ordens", {}),
                    ("qualidade:monitoramento", {}),
                    ("qualidade:historico", {}),
                ):
                    resp = self.client.get(reverse(route_name, kwargs=kwargs))
                    self.assertEqual(resp.status_code, 200, route_name)

    # 11. Impedir recriação, correlação tardia ou retorno de worker atrasado após exclusão
    def test_delayed_worker_and_correlation_jobs_ignore_deleted_order_and_treatment(self):
        order = create_quality_order(link=self.link, actor=self.admin_user, current_reason="Job atrasado")
        support = self.create_ticket(link=self.link, external_id=76001)
        tq = create_quality_treatment(
            quality_order=order,
            reason="Envio pendente antes da exclusão",
            support_ticket=support,
            actor=self.admin_user,
        )
        tq_id = tq.pk
        tq_code = tq.code
        tq_corr_id = tq.correlation_id
        order_code = order.code

        # Snapshot in-memory copy to simulate an async worker that started before delete
        stale_tq = QualityTreatment.objects.get(pk=tq_id)
        stale_tq.quality_ticket_external_id = 76099
        stale_tq.quality_ticket_state_snapshot = "open"

        delete_quality_order(
            order_id=order.pk,
            confirm_code=order_code,
            actor=self.admin_user,
        )

        # Delivery claim/accept/failure guards
        claim_result = claim_quality_treatment_send(tq_id)
        self.assertEqual(claim_result["status"], "NOT_CLAIMED")
        self.assertIsNone(claim_result["treatment"])
        self.assertFalse(record_tq_send_accepted(tq_id, "brevo-delayed-1"))
        self.assertFalse(record_tq_send_failure(tq_id, "smtp timeout", uncertain=True))

        # Delayed OTRS article correlation must not recreate or relink
        delayed_otrs_ticket = OTRSTicket.objects.create(
            external_id=76099,
            ticket_number="20260000076099",
            title="TQ Atrasada",
            queue_external_id=9,
            queue_name="N1::QUALIDADE",
            external_state_id=1,
            external_state_name="new",
            business_status=OTRSStateMapping.BusinessStatus.OPEN,
            is_present_in_queue=True,
            source_created_at=timezone.now(),
        )
        result = link_quality_ticket_from_article(
            ticket=delayed_otrs_ticket,
            article_body=f"Correlação automática {tq_corr_id}",
            allowed_queue_names=["N1::QUALIDADE"],
            actor=self.admin_user,
            trusted_source=True,
        )
        self.assertEqual(result["status"], "TREATMENT_DELETED")
        self.assertNotIn("treatment", result)
        self.assertFalse(QualityOrder.objects.filter(code=order_code).exists())
        self.assertFalse(QualityTreatment.objects.filter(code=tq_code).exists())

        fake_client = _FakeOTRSArticleClient(
            articles=[
                {
                    "article_id": 9001,
                    "ticket_id": delayed_otrs_ticket.external_id,
                    "ticket_number": delayed_otrs_ticket.ticket_number,
                    "queue_id": 9,
                    "queue_name": "N1::QUALIDADE",
                    "state_id": 1,
                    "state_name": "new",
                    "article_body": f"Token tardio {tq_corr_id}",
                }
            ],
            ticket_payloads={
                delayed_otrs_ticket.external_id: {
                    "ticket_id": delayed_otrs_ticket.external_id,
                    "ticket_number": delayed_otrs_ticket.ticket_number,
                    "queue_name": "N1::QUALIDADE",
                    "state_name": "closed successful",
                }
            },
        )
        with override_settings(QUALITY_OTRS_QUEUE_NAMES=("N1::QUALIDADE",)):
            reconcile_summary = reconcile_quality_articles(fake_client)
        self.assertEqual(reconcile_summary["linked"], 0)
        self.assertEqual(reconcile_summary["without_qid"], 1)
        self.assertFalse(QualityOrder.objects.filter(code=order_code).exists())
        self.assertFalse(QualityTreatment.objects.filter(code=tq_code).exists())

        # Concurrent state refresh with stale object must exit quietly without orphan AuditEvent
        with patch.object(QualityTreatment.objects, "filter") as mock_filter:
            mock_filter.return_value.only.return_value.order_by.return_value.__getitem__.return_value = [stale_tq]
            refreshed_count = refresh_linked_quality_ticket_states(
                fake_client,
                queue_names=["N1::QUALIDADE"],
            )
        self.assertEqual(refreshed_count, 0)
        self.assertEqual(AuditEvent.objects.filter(category="QUALITY").count(), 0)
