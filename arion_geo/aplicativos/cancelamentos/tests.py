from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, RequestFactory, override_settings, Client
from django.urls import reverse
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.access_control import managed_permissions, bootstrap_access
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import (Integration, IntegrationRun, IntegrationSyncState,
                                           CMDBLinkMirror, CMDBOperatorMirror, OTRSTicket, OTRSTicketConfigItem)
from aplicativos.integracoes.otrs_queues import CANCELLATION_REQUEST_QUEUE_NAME, COCKPIT_QUEUE_NAMES, queue_signature

from .models import (CancellationIntake, CancellationProcess, CancellationPending, CancellationEquipment,
                     FinancialObligation, FinancialHandoff, CancellationEvent, LegacyCancellationReference)
from .services.data_scope import CancellationScope
from .services.otrs_intake import project_intakes
from .services.queries import query_module
from .services.workflow import decide_intake, act_on_process, Conflict, completion_blockers


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class CancellationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("cancel-admin", "admin@example.test", "test-password-1")
        cls.provider = CMDBOperatorMirror.objects.create(source_config_item_id="provider-1", name="ISP A")
        cls.link = CMDBLinkMirror.objects.create(source_config_item_id="ci-a", name="Circuito A", designation="DES-A",
            contracting_client="Cliente A", contracting_client_raw="Cliente A", business_project="Projeto A",
            city="Recife", state="PE", operator=cls.provider, deployment_state="Ativo")
        cls.other_link = CMDBLinkMirror.objects.create(source_config_item_id="ci-b", name="Circuito B", designation="DES-B",
            contracting_client="Cliente B", business_project="Projeto B", operator=cls.provider)
        cls.integration = Integration.objects.create(key=Integration.Key.OTRS, name="OTRS", enabled=True)
        now = timezone.now()
        IntegrationRun.objects.create(integration=cls.integration, operation=IntegrationRun.Operation.OTRS_FULL_SYNC,
            status=IntegrationRun.Status.SUCCESS, finished_at=now, message=f"Apply; queues={queue_signature(COCKPIT_QUEUE_NAMES)}; tickets=2")
        IntegrationSyncState.objects.create(key="otrs", last_success_at=now, last_full_success_at=now)

    def setUp(self):
        self.client.force_login(self.admin)

    def request(self, user=None):
        request = RequestFactory().post("/cancelamentos/")
        request.user = user or self.admin
        return request

    def ticket(self, external_id=123, link=True):
        now = timezone.now()
        ticket = OTRSTicket.objects.create(external_id=external_id, ticket_number=f"2026-{external_id}",
            title="Pedido de cancelamento", queue_external_id=222, queue_name=CANCELLATION_REQUEST_QUEUE_NAME,
            external_state_id=7, external_state_name="em atendimento", business_status="OPEN", is_business_open=True,
            source_created_at=now-timedelta(days=1), source_changed_at=now, source_synced_at=now)
        if link:
            OTRSTicketConfigItem.objects.create(ticket=ticket, config_item_id=self.link.source_config_item_id,
                                               cmdb_link=self.link, relation_type="DependsOn", relation_state="Valid")
        return ticket

    def entry(self, external_id=123, link=True):
        self.ticket(external_id, link)
        project_intakes(force=True)
        return CancellationIntake.objects.get(ticket_external_id=external_id)

    def approve(self, entry=None):
        entry = entry or self.entry()
        entry = decide_intake(request=self.request(), pk=entry.pk, action="approve",
                              data={"revision": entry.revision, "link": self.link.pk})
        return entry.process

    def act(self, process, action, **data):
        process.refresh_from_db()
        return act_on_process(request=self.request(), pk=process.pk, action=action, data={"revision": process.revision, **data})

    def confirm(self, process):
        self.act(process, "start")
        now = timezone.now()
        return self.act(process, "treatment", protocol="ISP-123", contact="Atendimento ISP", requested_at=now.isoformat(),
            service_confirmed="on", confirmed_at=now.isoformat(), effective_date=now.date().isoformat(), billing_closed="on",
            billing_closed_date=now.date().isoformat(), evidence_reference="Protocolo / distrato privado", response="Cancelamento confirmado")

    def restricted(self, permissions=("view_cancellations",), project="Projeto A", client="Cliente A"):
        group = Group.objects.create(name=f"Escopo {Group.objects.count()}")
        group.permissions.add(*Permission.objects.filter(content_type__app_label="cancelamentos", codename__in=permissions))
        scope = GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.CUSTOM)
        operator, _ = Operadora.objects.get_or_create(name=client, defaults={"normalized_name": client.casefold()})
        proj, _ = Projeto.objects.get_or_create(name=project, defaults={"normalized_name": project.casefold()})
        scope.allowed_operators.add(operator)
        scope.allowed_projects.add(proj)
        user = get_user_model().objects.create_user(f"u-{group.pk}", password="test-password-1")
        user.groups.add(group)
        return user

    def test_projection_never_creates_orders_and_replays_without_events(self):
        self.ticket()
        self.ticket(124)
        first = project_intakes(force=True)
        self.assertEqual(first["created"], 2)
        self.assertEqual(CancellationProcess.objects.count(), 0)
        second = project_intakes(force=True)
        self.assertEqual(second["created"], 0)
        self.assertEqual(CancellationIntake.objects.count(), 2)
        self.assertFalse(CancellationEvent.objects.exists())

    def test_projection_requires_applied_coverage_and_fresh_source(self):
        self.ticket()
        IntegrationRun.objects.all().update(message="Dry-run; queues=qualquer")
        self.assertIn("skipped", project_intakes(force=True))
        self.assertFalse(CancellationIntake.objects.exists())

    def test_human_approval_creates_one_order_and_preserves_ticket_identity(self):
        process = self.approve()
        self.assertEqual(process.stage, "ORDER")
        self.assertEqual(process.config_item_id, "ci-a")
        self.assertEqual(process.intake.ticket_external_id, 123)
        self.assertEqual(process.intake.ticket_number, "2026-123")
        self.assertEqual(process.owner, self.admin)
        self.assertEqual(AuditEvent.objects.filter(action="triage_approve").count(), 1)

    def test_duplicate_approval_and_database_constraint(self):
        process = self.approve()
        with self.assertRaises(Conflict):
            decide_intake(request=self.request(), pk=process.intake.pk, action="approve", data={"revision": 1, "link": self.link.pk})
        with self.assertRaises(IntegrityError), transaction.atomic():
            CancellationProcess.objects.create(intake=process.intake)
        self.assertEqual(CancellationProcess.objects.count(), 1)

    def test_rejection_requires_reason_and_survives_queue_change_and_replay(self):
        entry = self.entry()
        with self.assertRaises(ValidationError):
            decide_intake(request=self.request(), pk=entry.pk, action="reject", data={"revision": entry.revision})
        decide_intake(request=self.request(), pk=entry.pk, action="reject", data={"revision": entry.revision, "reason": "Fora do objeto"})
        OTRSTicket.objects.filter(pk=entry.ticket_id).update(queue_name="Outra fila", is_present_in_queue=False)
        project_intakes(force=True)
        entry.refresh_from_db()
        self.assertEqual(entry.status, "REJECTED")
        self.assertEqual(entry.decision_reason, "Fora do objeto")
        self.assertFalse(entry.eligible)
        self.assertEqual(entry.events.count(), 1)
        self.assertFalse(CancellationProcess.objects.exists())

    def test_wait_and_assume_do_not_decide_or_create_orders(self):
        entry = self.entry()
        entry = decide_intake(request=self.request(), pk=entry.pk, action="assume", data={"revision": entry.revision})
        entry = decide_intake(request=self.request(), pk=entry.pk, action="wait", data={"revision": entry.revision, "reason": "Validar contrato"})
        self.assertEqual(entry.status, "WAITING")
        self.assertEqual(entry.assigned_to, self.admin)
        self.assertFalse(CancellationProcess.objects.exists())

    def test_stale_origin_or_form_revision_blocks_approval(self):
        entry = self.entry()
        OTRSTicket.objects.filter(pk=entry.ticket_id).update(source_changed_at=timezone.now())
        with self.assertRaises(Conflict):
            decide_intake(request=self.request(), pk=entry.pk, action="approve", data={"revision": entry.revision, "link": self.link.pk})
        self.assertFalse(CancellationProcess.objects.exists())

    def test_only_current_related_cmdb_candidates_may_be_selected(self):
        entry = self.entry()
        with self.assertRaises(ValidationError):
            decide_intake(request=self.request(), pk=entry.pk, action="approve", data={"revision": entry.revision, "link": self.other_link.pk})

    def test_isp_confirmation_fields_and_stage_are_validated(self):
        process = self.approve()
        with self.assertRaises(ValidationError):
            self.act(process, "treatment", service_confirmed="on")
        self.act(process, "start")
        with self.assertRaises(ValidationError):
            self.act(process, "treatment", service_confirmed="on")
        self.confirm_after_start(process)
        process.refresh_from_db()
        self.assertEqual(process.stage, "REQUEST")
        self.assertTrue(process.treatment.service_confirmed)
        self.assertFalse(FinancialHandoff.objects.exists())

    def confirm_after_start(self, process):
        now = timezone.now()
        self.act(process, "treatment", service_confirmed="on", confirmed_at=now.isoformat(),
                 effective_date=now.date().isoformat(), billing_closed="on", billing_closed_date=now.date().isoformat(), evidence_reference="Distrato ISP")

    def test_simultaneous_pendings_preserve_treatment_and_resolve_with_evidence(self):
        process = self.approve()
        self.confirm(process)
        self.act(process, "pending_add", kind="BILL", description="Aguardando boleto", amount="12.34")
        self.act(process, "pending_add", kind="COLLECTION", description="Aguardando coleta")
        self.assertEqual(process.pendings.filter(status="OPEN").count(), 2)
        process.refresh_from_db()
        self.assertEqual(process.stage, "REQUEST")
        first = process.pendings.get(kind="BILL")
        self.assertEqual(first.amount, Decimal("12.34"))
        with self.assertRaises(ValidationError):
            self.act(process, "pending_resolve", child_id=first.pk, status="RESOLVED", resolution="Recebido")
        self.act(process, "pending_resolve", child_id=first.pk, status="REFERRED", resolution="Encaminhado", evidence_reference="Processo interno 123")
        first.refresh_from_db()
        self.assertIsNotNone(first.resolved_at)

    def test_equipment_can_be_multiple_and_does_not_imply_payment(self):
        process = self.approve()
        self.act(process, "equipment_add", description="ONU", status="TO_COLLECT")
        self.act(process, "equipment_add", description="Roteador", status="SCHEDULED", scheduled_at=(timezone.now()+timedelta(days=1)).isoformat())
        item = process.equipment.get(description="ONU")
        self.act(process, "equipment_update", child_id=item.pk, description="ONU", status="WAIVED", evidence_reference="Dispensa ISP 123")
        self.assertEqual(process.equipment.count(), 2)
        self.assertFalse(process.obligations.exists())

    def test_obligation_does_not_accept_payment_status_and_rejects_negative_values(self):
        process = self.approve()
        with self.assertRaises(ValidationError):
            self.act(process, "obligation_add", kind="CONTRACT", description="Multa", charged_amount="-1.00", document_status="WAITING")
        self.act(process, "obligation_add", kind="CONTRACT", description="Multa", charged_amount="98.76", estimated_amount="100.00",
                 document_status="WAITING", financial_status="PAID", paid_amount="98.76")
        item = process.obligations.get()
        self.assertEqual(item.financial_status, "UNKNOWN")
        self.assertIsNone(item.paid_amount)
        self.assertEqual(item.charged_amount, Decimal("98.76"))

    def test_handoff_snapshots_documents_and_completion_is_not_payment(self):
        process = self.approve()
        self.confirm(process)
        self.act(process, "obligation_add", kind="CONTRACT", description="Multa", charged_amount="98.76",
                 document_status="AVAILABLE", document_reference="Arquivo privado 123")
        with self.assertRaises(ValidationError):
            self.act(process, "complete")
        self.act(process, "handoff", sent_at=timezone.now().isoformat(), recipient="Financeiro", reference="Email 123", evidence_reference="Registro de envio")
        handoff = process.handoffs.get()
        self.assertEqual(handoff.actor, self.admin)
        self.assertIn("98.76", handoff.summary)
        self.assertEqual(handoff.obligations.count(), 1)
        self.assertFalse(completion_blockers(CancellationProcess.objects.get(pk=process.pk)))
        self.act(process, "complete")
        process.refresh_from_db()
        self.assertEqual(process.stage, "COMPLETED")
        self.assertEqual(process.obligations.get().financial_status, "UNKNOWN")
        self.link.refresh_from_db()
        self.assertEqual(self.link.deployment_state, "Ativo")
        process.intake.ticket.refresh_from_db()
        self.assertEqual(process.intake.ticket.queue_name, CANCELLATION_REQUEST_QUEUE_NAME)

    def test_modified_obligation_requires_new_handoff(self):
        process = self.approve()
        self.confirm(process)
        self.act(process, "obligation_add", kind="CONTRACT", description="Multa", charged_amount="10.00", document_status="AVAILABLE", document_reference="Doc 1")
        self.act(process, "handoff", sent_at=timezone.now().isoformat(), recipient="Financeiro", reference="Email 1", evidence_reference="Envio 1")
        self.act(process, "obligation_update", child_id=process.obligations.get().pk, kind="CONTRACT", description="Correção", charged_amount="20.00", document_status="AVAILABLE", document_reference="Doc 2")
        with self.assertRaises(ValidationError):
            self.act(process, "complete")
        self.assertIn("10.00", process.handoffs.get().summary)

    def test_conclusion_without_equipment_or_debt_is_allowed_after_confirmation(self):
        process = self.approve()
        self.confirm(process)
        self.act(process, "complete")
        process.refresh_from_db()
        self.assertEqual(process.stage, "COMPLETED")
        self.assertIsNotNone(process.completed_at)

    def test_pending_blocks_silent_completion_and_exception_requires_evidence(self):
        process = self.approve()
        self.confirm(process)
        self.act(process, "equipment_add", description="ONU", status="NOT_RETURNED")
        with self.assertRaises(ValidationError):
            self.act(process, "complete", exception_reason="Encaminhado à área responsável")
        self.act(process, "complete", exception_reason="Área patrimonial assumiu", exception_evidence="Processo patrimonial 1")
        process.refresh_from_db()
        self.assertTrue(process.completion_exception)
        self.assertEqual(process.equipment.get().status, "NOT_RETURNED")
        self.assertEqual(process.events.filter(action="complete").count(), 1)

    def test_permissions_are_registered_without_new_automatic_grants(self):
        count = managed_permissions().filter(content_type__app_label="cancelamentos").count()
        self.assertGreaterEqual(count, 12)
        bootstrap_access()
        self.assertFalse(Group.objects.filter(permissions__content_type__app_label="cancelamentos").exists())

    def test_rbac_manual_requests_and_financial_fields_are_protected(self):
        process = self.approve()
        self.act(process, "obligation_add", kind="CONTRACT", description="FINANCIAL-PRIVATE", charged_amount="4321.09", document_status="WAITING")
        user = self.restricted()
        self.client.force_login(user)
        response = self.client.get(reverse("cancelamentos:detail", args=(process.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "FINANCIAL-PRIVATE")
        self.assertNotContains(response, "4321")
        response = self.client.post(reverse("cancelamentos:process_action", args=(process.pk, "obligation_add")), {"revision": 0})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse("cancelamentos:export")).status_code, 403)

    def test_data_scope_intersection_unresolved_and_mixed_links_fail_closed(self):
        first = self.entry()
        no_link = self.entry(124, link=False)
        user = self.restricted()
        self.assertEqual(list(CancellationScope.intakes(user).values_list("pk", flat=True)), [first.pk])
        self.assertFalse(CancellationScope.intakes(self.restricted(project="Projeto B")).exists())
        self.assertFalse(CancellationScope.intakes(get_user_model().objects.create_user("no-scope")).exists())
        OTRSTicketConfigItem.objects.create(ticket_id=first.ticket_id, config_item_id="ci-b", cmdb_link=self.other_link, relation_type="DependsOn", relation_state="Valid")
        self.assertFalse(CancellationScope.intakes(user).exists())
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("cancelamentos:intake_detail", args=(first.pk,))).status_code, 404)
        self.assertEqual(self.client.get(reverse("cancelamentos:intake_detail", args=(no_link.pk,))).status_code, 404)

    def test_current_scope_and_snapshot_must_both_be_authorized(self):
        process = self.approve()
        user = self.restricted()
        self.assertTrue(CancellationScope.processes(user).filter(pk=process.pk).exists())
        CMDBLinkMirror.objects.filter(pk=self.link.pk).update(contracting_client="Cliente B", contracting_client_raw="Cliente B")
        self.assertFalse(CancellationScope.processes(user).exists())

    def test_assignee_without_process_scope_is_rejected(self):
        process = self.approve()
        user = self.restricted(project="Projeto B", client="Cliente B")
        with self.assertRaises(ValidationError):
            self.act(process, "assign", owner=user.pk)

    def test_atomic_audit_failure_rolls_back_approval(self):
        entry = self.entry()
        with patch("aplicativos.cancelamentos.services.workflow.AuditService.record", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                decide_intake(request=self.request(), pk=entry.pk, action="approve", data={"revision": entry.revision, "link": self.link.pk})
        self.assertFalse(CancellationProcess.objects.exists())
        entry.refresh_from_db()
        self.assertEqual(entry.status, "WAITING")

    def test_financial_audit_never_contains_documents_or_amounts(self):
        process = self.approve()
        self.confirm(process)
        self.act(process, "obligation_add", kind="CONTRACT", description="DOCUMENT-PRIVATE", charged_amount="987.65", document_status="AVAILABLE", document_reference="BANKING-PRIVATE")
        self.act(process, "handoff", sent_at=timezone.now().isoformat(), recipient="finance@example.test", reference="EMAIL-PRIVATE", evidence_reference="FILE-PRIVATE")
        audits = " ".join(str(a.metadata) + a.description for a in AuditEvent.objects.filter(module="cancelamentos"))
        for secret in ("BANKING-PRIVATE", "987.65", "EMAIL-PRIVATE", "FILE-PRIVATE", "DOCUMENT-PRIVATE"):
            self.assertNotIn(secret, audits)

    def test_metrics_separate_official_stages_and_overlapping_pending(self):
        order = self.approve(self.entry(123))
        request = self.approve(self.entry(124))
        self.act(request, "start")
        self.act(request, "pending_add", kind="BILL", description="Boleto")
        self.act(request, "pending_add", kind="DOCUMENT", description="Termo")
        self.entry(125)
        metrics = query_module(self.admin, {})["metrics"]
        self.assertEqual(metrics["intake"], 1)
        self.assertEqual(metrics["order"], 1)
        self.assertEqual(metrics["request"], 1)
        self.assertEqual(metrics["pending"], 1)
        self.assertEqual(metrics["documents"], 1)
        self.assertEqual(query_module(self.admin, {"tab": "pending"})["queryset"].count(), 1)

    def test_export_uses_scope_filters_and_csv_formula_protection(self):
        process = self.approve()
        CancellationProcess.objects.filter(pk=process.pk).update(designation="=HYPERLINK(unsafe)")
        response = self.client.get(reverse("cancelamentos:export"), {"tab": "order", "q": "HYPERLINK"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("'=HYPERLINK", response.content.decode("utf-8-sig"))
        self.assertIn("no-store", response["Cache-Control"])
        user = self.restricted(("view_cancellations", "export_cancellations"), client="Cliente B", project="Projeto B")
        self.client.force_login(user)
        response = self.client.get(reverse("cancelamentos:export"), {"tab": "order"})
        self.assertNotIn("HYPERLINK", response.content.decode())

    def test_local_pages_all_tabs_dialogs_and_configuration_render(self):
        process = self.approve()
        self.act(process, "pending_add", kind="DOCUMENT", description="Documento")
        self.act(process, "equipment_add", description="ONU", status="TO_COLLECT")
        self.act(process, "obligation_add", kind="CONTRACT", description="Multa", document_status="WAITING")
        for tab in ("intake", "order", "request", "pending", "completed"):
            response = self.client.get(reverse("cancelamentos:index"), {"tab": tab})
            self.assertEqual(response.status_code, 200)
        for url in (reverse("cancelamentos:detail", args=(process.pk,)), reverse("cancelamentos:intake_detail", args=(process.intake_id,)), reverse("cancelamentos:configuration")):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

    def test_post_conflict_and_csrf_and_bad_filters(self):
        process = self.approve()
        response = self.client.post(reverse("cancelamentos:process_action", args=(process.pk, "start")), {"revision": 99}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 409)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(reverse("cancelamentos:process_action", args=(process.pk, "start")), {"revision": 0}).status_code, 403)
        self.assertEqual(self.client.get(reverse("cancelamentos:export"), {"start": "invalid"}).status_code, 400)

    def test_legacy_mapping_is_idempotent_and_no_importer_runs(self):
        process = self.approve()
        data = dict(source="future-source", entity="cancellation", legacy_pk="1", process=process, batch="test", mapping_version="v1", checksum="a"*64)
        LegacyCancellationReference.objects.create(**data)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LegacyCancellationReference.objects.create(**data)

    def test_global_navigation_smoke_with_module_visible(self):
        for url in ("/painel/", "/dashboard/", "/cockpit/", "/mapa/", "/rankings/", "/base/", "/configuracoes/", "/chamados/", "/qualidade/"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertContains(response, reverse("cancelamentos:index"))

    def test_module_only_access_has_entry_in_personal_overview(self):
        user = self.restricted()
        self.client.force_login(user)
        response = self.client.get("/painel/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Abrir Cancelamentos")
        self.assertEqual(self.client.get("/dashboard/").status_code, 403)

    def test_partial_projection_failure_recovers_committed_entries_without_orders(self):
        self.ticket(123)
        self.ticket(124)
        original_save = CancellationIntake.save

        def fail_second(instance, *args, **kwargs):
            if instance.ticket_external_id == 124:
                raise RuntimeError("simulated local failure")
            return original_save(instance, *args, **kwargs)

        with patch.object(CancellationIntake, "save", fail_second):
            with self.assertRaises(RuntimeError):
                project_intakes(force=True)
        self.assertEqual(CancellationIntake.objects.count(), 1)
        state = IntegrationSyncState.objects.get(key="cancellation_intake")
        self.assertIsNone(state.last_success_at)
        self.assertEqual(state.failure_count, 1)
        result = project_intakes(force=True)
        self.assertEqual(result["created"], 1)
        self.assertEqual(CancellationIntake.objects.count(), 2)
        self.assertFalse(CancellationProcess.objects.exists())

    def test_stale_raw_client_alias_does_not_widen_current_scope(self):
        process = self.approve()
        user = self.restricted()
        CMDBLinkMirror.objects.filter(pk=self.link.pk).update(contracting_client="Cliente B")
        self.assertFalse(CancellationScope.intakes(user).exists())
        self.assertFalse(CancellationScope.processes(user).exists())

    def test_completion_exception_is_a_separate_permission(self):
        process = self.approve()
        self.confirm(process)
        self.act(process, "equipment_add", description="ONU", status="NOT_RETURNED")
        user = self.restricted(("view_cancellations", "complete_cancellations"))
        process.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            act_on_process(request=self.request(user), pk=process.pk, action="complete", data={
                "revision": process.revision, "exception_reason": "Outra área assumiu", "exception_evidence": "Processo 1"})
        process.refresh_from_db()
        self.assertEqual(process.stage, "REQUEST")

    def test_child_identifiers_are_validated_and_bound_to_the_process(self):
        process = self.approve()
        response = self.client.post(reverse("cancelamentos:process_action", args=(process.pk, "equipment_update")),
                                    {"revision": process.revision, "child_id": "invalid"}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 400)
        other = self.approve(self.entry(124))
        self.act(other, "equipment_add", description="Equipamento de outro processo", status="TO_COLLECT")
        process.refresh_from_db()
        response = self.client.post(reverse("cancelamentos:process_action", args=(process.pk, "equipment_update")),
            {"revision": process.revision, "child_id": other.equipment.get().pk, "description": "Changed", "status": "TO_COLLECT"}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(other.equipment.get().description, "Equipamento de outro processo")

    def test_history_keeps_prior_isp_responses_and_financial_values_private(self):
        process = self.approve()
        self.confirm(process)
        self.act(process, "treatment", response="Novo retorno do ISP", protocol="ISP-2")
        self.assertTrue(process.events.filter(description__contains="Cancelamento confirmado").exists())
        self.assertTrue(process.events.filter(description__contains="Novo retorno do ISP").exists())
        self.act(process, "obligation_add", kind="CONTRACT", description="Multa", charged_amount="10.00", document_status="WAITING")
        self.act(process, "obligation_update", child_id=process.obligations.get().pk, kind="CONTRACT", description="Multa", charged_amount="20.00", document_status="WAITING")
        details = process.events.filter(action="obligation_update").get().details
        self.assertEqual(details["Valor cobrado anterior"], "10.00")
        self.assertEqual(details["Valor cobrado registrado"], "20.00")
        self.assertEqual(details["Valor previsto registrado"], "Não informado")
        self.assertNotIn("20.00", str(AuditEvent.objects.filter(action="obligation_update").get().metadata))
