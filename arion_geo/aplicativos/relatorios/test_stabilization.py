from pathlib import Path
from datetime import datetime, timedelta
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.query import QuerySet
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.configuracoes.access_control import GROUP_ANALYST, GROUP_MANAGER, GROUP_SYSTEM_ADMIN
from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror, OTRSTicket, OTRSTicketConfigItem

from .models import PenaltyCompetence, PenaltySourceRow, PenaltyTreatmentRow, PenaltyTreatmentTicket
from .services.competencies import CompetenceAlreadyExists, create_draft_competence
from .templatetags.reports_format import br_datetime, br_datetime_full, br_precision


class ReportDisplayFormatTests(TestCase):
    def test_compact_and_full_dates_and_decimal_precision(self):
        value = "2026-09-28T15:28:00"
        self.assertEqual(br_datetime(value, True), "28/09 15:28")
        self.assertEqual(br_datetime(value), "28/09/2026 15:28")
        self.assertEqual(br_datetime_full(value), "28/09/2026 15:28:00")
        self.assertEqual(br_precision("48.53333333333333"), "48,53")
        self.assertEqual(br_precision("76.0000"), "76")


class CompetenceCreationStabilizationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(username="stability-manager", password="test-password")
        self.manager.groups.set([Group.objects.get(name=GROUP_MANAGER)])
        self.client.force_login(self.manager)

    def test_first_period_creation_succeeds_and_duplicate_is_friendly_with_existing_link(self):
        create_url = reverse("relatorios:competence-create")
        payload = {"competence_month": 10, "competence_year": 2026}
        first = self.client.post(create_url, payload)
        existing = PenaltyCompetence.objects.get(competence_month=10, competence_year=2026)
        self.assertRedirects(first, reverse("relatorios:workspace", args=(existing.pk,)))

        duplicate = self.client.post(create_url, payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertContains(duplicate, "Já existe uma competência Claro Multas para Outubro 2026.")
        self.assertContains(duplicate, reverse("relatorios:workspace", args=(existing.pk,)))
        self.assertContains(duplicate, "Abrir competência existente")
        self.assertEqual(PenaltyCompetence.objects.filter(competence_month=10, competence_year=2026).count(), 1)

    def test_race_after_preflight_integrity_error_returns_safe_form_and_link(self):
        existing = PenaltyCompetence.objects.create(competence_month=10, competence_year=2026, created_by=self.manager)
        original_first = QuerySet.first
        first_call = True

        def hide_first_read(queryset):
            nonlocal first_call
            if first_call:
                first_call = False
                return None
            return original_first(queryset)

        with patch.object(QuerySet, "first", new=hide_first_read), patch.object(QuerySet, "exists", return_value=False):
            response = self.client.post(reverse("relatorios:competence-create"), {
                "competence_month": 10, "competence_year": 2026,
            })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Já existe uma competência Claro Multas para Outubro 2026.")
        self.assertContains(response, reverse("relatorios:workspace", args=(existing.pk,)))
        self.assertEqual(PenaltyCompetence.objects.filter(competence_month=10, competence_year=2026).count(), 1)

    def test_period_endpoint_returns_safe_existing_link_or_empty_result(self):
        existing = PenaltyCompetence.objects.create(competence_month=11, competence_year=2026)
        url = reverse("relatorios:competence-period-exists")
        available = self.client.get(url, {"month": 12, "year": 2026})
        duplicate = self.client.get(url, {"month": 11, "year": 2026})
        self.assertEqual(available.json(), {"exists": False})
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json()["exists"])
        self.assertEqual(duplicate.json()["period"], "Novembro 2026")
        self.assertEqual(duplicate.json()["url"], reverse("relatorios:workspace", args=(existing.pk,)))

    def test_ajax_duplicate_is_json_not_a_server_error(self):
        existing = PenaltyCompetence.objects.create(competence_month=8, competence_year=2026)
        response = self.client.post(
            reverse("relatorios:competence-create"),
            {"competence_month": 8, "competence_year": 2026},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Já existe uma competência", " ".join(response.json()["messages"]))
        self.assertEqual(response.json()["duplicate_url"], reverse("relatorios:workspace", args=(existing.pk,)))

    def test_upload_wizard_has_progress_feedback_filename_stage_and_retry(self):
        response = self.client.get(reverse("relatorios:competence-create"))
        self.assertContains(response, 'data-progress-upload')
        self.assertContains(response, 'role="progressbar"')
        self.assertContains(response, 'data-progress-filename')
        self.assertContains(response, 'data-progress-stage')
        self.assertContains(response, 'data-upload-retry')
        self.assertContains(response, "não representam percentual exato de leitura")
        script = Path("frontend_src/js/relatorios/competence-wizard.js").read_text(encoding="utf-8")
        self.assertIn('if (submitting) return;', script)
        self.assertIn('xhr.upload.addEventListener("progress"', script)

    def test_invalid_xhr_upload_returns_safe_parser_error(self):
        response = self.client.post(
            reverse("relatorios:competence-create"),
            {
                "competence_month": 7,
                "competence_year": 2026,
                "original_file": SimpleUploadedFile("corrompido.xlsx", b"not a workbook"),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content[:1200])
        self.assertEqual(response["Content-Type"], "application/json", response.content[:1200])
        payload = response.json()
        self.assertFalse(payload["ok"])
        text = " ".join(payload["messages"])
        self.assertNotIn("Traceback", text)
        self.assertNotIn("IntegrityError", text)
        self.assertEqual(PenaltyCompetence.objects.filter(competence_month=7, competence_year=2026).count(), 0)


class CompetenceDeletionStabilizationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(username="delete-manager", password="test-password")
        self.manager.groups.set([Group.objects.get(name=GROUP_MANAGER)])
        self.admin = User.objects.create_user(username="delete-admin", password="test-password")
        self.admin.groups.set([Group.objects.get(name=GROUP_SYSTEM_ADMIN)])
        self.viewer = User.objects.create_user(username="delete-viewer", password="test-password")
        view_permission = Permission.objects.get(content_type__app_label="relatorios", codename="view_reports")
        self.viewer.user_permissions.add(view_permission)
        self.competence = PenaltyCompetence.objects.create(competence_year=2026, competence_month=8)

    def delete_url(self, competence=None):
        return reverse("relatorios:competence-delete", args=((competence or self.competence).pk,))

    def test_manager_physically_deletes_nonfinalized_competence_rows_file_and_audit(self):
        source = PenaltySourceRow.objects.create(
            competence=self.competence, source_row_number=2, source_data=[], row_hash="a" * 64,
            canonical_data={"rec": "REC-DELETE"},
        )
        PenaltyTreatmentRow.objects.create(source_row=source)
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.competence.original_file.save("original.xlsx", ContentFile(b"private"), save=True)
            private_path = Path(self.competence.original_file.path)
            client = Client()
            client.force_login(self.manager)
            with self.captureOnCommitCallbacks(execute=True):
                response = client.post(self.delete_url(), {"confirmation": "EXCLUIR", "reason": "Período criado incorretamente."})
            self.assertRedirects(response, reverse("relatorios:claro-penalidades"))
            self.assertFalse(PenaltyCompetence.objects.filter(pk=self.competence.pk).exists())
            self.assertFalse(PenaltySourceRow.objects.filter(pk=source.pk).exists())
            self.assertFalse(private_path.exists())

        event = AuditEvent.objects.get(module="relatorios", action="COMPETENCE_DELETED")
        self.assertEqual(event.object_id, str(self.competence.pk))
        self.assertEqual(event.metadata["period"], "Agosto 2026")
        self.assertEqual(event.metadata["source_rows"], 1)
        self.assertEqual(event.metadata["reason"], "Período criado incorretamente.")

    def test_manager_cannot_delete_finalized_competence_but_admin_archives_it(self):
        self.competence.status = PenaltyCompetence.Status.FINALIZED
        self.competence.imported_row_count = 184
        self.competence.save(update_fields=("status", "imported_row_count"))
        source = PenaltySourceRow.objects.create(
            competence=self.competence, source_row_number=2, source_data=[], row_hash="b" * 64,
            canonical_data={"rec": "REC-ARCHIVE"},
        )
        treatment = PenaltyTreatmentRow.objects.create(source_row=source)

        manager_client = Client()
        manager_client.force_login(self.manager)
        manager_workspace = manager_client.get(reverse("relatorios:workspace", args=(self.competence.pk,)))
        self.assertNotContains(manager_workspace, "data-open-delete-dialog")
        self.assertEqual(manager_client.post(self.delete_url(), {
            "confirmation": "EXCLUIR AGOSTO 2026", "reason": "Pedido de arquivo.",
        }).status_code, 403)
        self.competence.refresh_from_db()
        self.assertIsNone(self.competence.deleted_at)

        admin_client = Client()
        admin_client.force_login(self.admin)
        workspace = admin_client.get(reverse("relatorios:workspace", args=(self.competence.pk,)))
        self.assertContains(workspace, "data-open-delete-dialog")
        self.assertContains(workspace, "EXCLUIR AGOSTO 2026")
        response = admin_client.post(self.delete_url(), {
            "confirmation": "EXCLUIR AGOSTO 2026", "reason": "Consolidado por solicitação administrativa.",
        })
        self.assertRedirects(response, reverse("relatorios:claro-penalidades"))
        self.competence.refresh_from_db()
        self.assertIsNotNone(self.competence.deleted_at)
        self.assertEqual(self.competence.deleted_by, self.admin)
        self.assertEqual(self.competence.delete_reason, "Consolidado por solicitação administrativa.")
        self.assertTrue(PenaltyTreatmentRow.objects.filter(pk=treatment.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(module="relatorios", action="COMPETENCE_ARCHIVED", object_id=str(self.competence.pk)).exists())
        self.assertNotContains(admin_client.get(reverse("relatorios:claro-penalidades")), "Agosto 2026")
        self.assertContains(admin_client.get(reverse("relatorios:workspace", args=(self.competence.pk,))), "Competência arquivada")
        self.assertEqual(manager_client.get(reverse("relatorios:workspace", args=(self.competence.pk,))).status_code, 404)

    def test_user_without_delete_permission_gets_403_and_csrf_is_enforced(self):
        client = Client()
        client.force_login(self.viewer)
        self.assertEqual(client.post(self.delete_url(), {"confirmation": "EXCLUIR", "reason": "Não autorizado."}).status_code, 403)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.manager)
        self.assertEqual(csrf_client.post(self.delete_url(), {
            "confirmation": "EXCLUIR", "reason": "Sem token CSRF.",
        }).status_code, 403)
        self.assertTrue(PenaltyCompetence.objects.filter(pk=self.competence.pk).exists())

    def test_fallback_fullscreen_controls_are_in_both_workspace_grids(self):
        self.competence.original_filename = "original.xlsx"
        self.competence.original_file.name = "relatorios/originais/2026-08/original.xlsx"
        self.competence.original_file_size = 123
        self.competence.original_file_sha256 = "f" * 64
        self.competence.imported_at = timezone.now()
        self.competence.save(update_fields=("original_filename", "original_file", "original_file_size", "original_file_sha256", "imported_at"))
        self.manager.user_permissions.add(Permission.objects.get(content_type__app_label="relatorios", codename="view_reports"))
        client = Client()
        client.force_login(self.manager)
        original = client.get(reverse("relatorios:workspace", args=(self.competence.pk,)), {"aba": "original"})
        treatment = client.get(reverse("relatorios:workspace", args=(self.competence.pk,)), {"aba": "tratativa"})
        self.assertContains(original, 'aria-label="Abrir Original em tela cheia"')
        self.assertContains(treatment, 'aria-label="Abrir Tratativa em tela cheia"')
        self.assertContains(original, "data-fullscreen-toggle")
        self.assertContains(treatment, "data-fullscreen-toggle")


class EnrichmentRedirectRegressionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(username="enrichment-manager", password="test-password")
        self.manager.groups.set([Group.objects.get(name=GROUP_MANAGER)])
        self.client.force_login(self.manager)
        self.competence = PenaltyCompetence.objects.create(
            competence_year=2026, competence_month=6, status=PenaltyCompetence.Status.IN_TREATMENT,
            imported_at=timezone.now(), imported_row_count=1,
        )
        source = PenaltySourceRow.objects.create(
            competence=self.competence, source_row_number=2, source_data=[], row_hash="c" * 64,
            canonical_data={"rec": "REC-ENRICH", "interconnection_fine": "25", "penalty_fine": "0", "unavailability_fine": "25"},
        )
        PenaltyTreatmentRow.objects.create(source_row=source)

    def test_enrichment_post_redirects_and_both_workspace_tabs_render(self):
        response = self.client.post(reverse("relatorios:competence-enrich", args=(self.competence.pk,)))
        self.assertRedirects(response, reverse("relatorios:workspace", args=(self.competence.pk,)))
        original = self.client.get(reverse("relatorios:workspace", args=(self.competence.pk,)), {"aba": "original"})
        treatment = self.client.get(reverse("relatorios:workspace", args=(self.competence.pk,)), {"aba": "tratativa"})
        self.assertEqual(original.status_code, 200)
        self.assertEqual(treatment.status_code, 200)
        self.assertContains(treatment, "REC-ENRICH")

    def test_enrichment_failure_rolls_back_and_workspace_remains_available(self):
        treatment = PenaltyTreatmentRow.objects.get(source_row__competence=self.competence)

        def write_then_fail(competence_id, *, user, remote_lookup=False):
            treatment.isp = "VALOR TEMPORÁRIO"
            treatment.save(update_fields=("isp",))
            raise RuntimeError("failure injected by test")

        with patch("aplicativos.relatorios.views.enrich_competence", side_effect=write_then_fail):
            response = self.client.post(reverse("relatorios:competence-enrich", args=(self.competence.pk,)))

        self.assertRedirects(response, reverse("relatorios:workspace", args=(self.competence.pk,)))
        treatment.refresh_from_db()
        self.assertEqual(treatment.isp, "")
        self.assertEqual(self.client.get(reverse("relatorios:workspace", args=(self.competence.pk,)), {"aba": "original"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("relatorios:workspace", args=(self.competence.pk,)), {"aba": "tratativa"}).status_code, 200)

    def test_service_worker_endpoint_is_independent_and_remains_available(self):
        response = self.client.get(reverse("pwa-service-worker"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/javascript"))
        self.assertEqual(response["Cache-Control"], "no-cache, no-store, must-revalidate")


class TreatmentDrawerAndTicketRelationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(username="drawer-manager", password="test-password")
        self.manager.groups.set([Group.objects.get(name=GROUP_MANAGER)])
        self.client.force_login(self.manager)
        self.competence = PenaltyCompetence.objects.create(
            competence_year=2026, competence_month=5, status=PenaltyCompetence.Status.IN_TREATMENT,
            imported_at=timezone.now(), imported_row_count=1,
        )
        self.source = PenaltySourceRow.objects.create(
            competence=self.competence, source_row_number=2, source_data=[], row_hash="d" * 64,
            canonical_data={
                "rec": "REC-MULTI-1", "designation": "ASPA/IP/00003", "client": "Cliente de teste",
                "provider_unit_price": "", "interconnection_fine": "100", "penalty_fine": "0",
                "unavailability_fine": "1", "monthly_value": "1000",
            },
        )
        self.treatment = PenaltyTreatmentRow.objects.create(source_row=self.source, validation_code_ciphertext="protected-secret-marker")

    @staticmethod
    def make_ticket(number, external_id):
        return OTRSTicket.objects.create(
            external_id=external_id, ticket_number=number, title=f"Caso {number}",
            queue_external_id=1, queue_name="NOC Atacado", external_state_id=2,
            external_state_name="Resolved", business_status="RESOLVED",
        )

    def test_detail_endpoint_is_scoped_safe_and_does_not_return_validation_secrets(self):
        url = reverse("relatorios:treatment-detail-data", args=(self.treatment.pk,))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response.json()["rec"], "REC-MULTI-1")
        self.assertEqual(response.json()["designation"], "ASPA/IP/00003")
        self.assertIsNone(response.json()["navigation"]["next_pending_id"])
        self.assertEqual(response.json()["validation_codes"], {"first_configured": True, "second_configured": False})
        self.assertNotIn("protected-secret-marker", response.content.decode())

        analyst = get_user_model().objects.create_user(username="drawer-analyst", password="test-password")
        analyst.groups.set([Group.objects.get(name=GROUP_ANALYST)])
        client = Client()
        client.force_login(analyst)
        self.assertEqual(client.get(url).status_code, 403)
        self.assertEqual(self.client.get(reverse("relatorios:treatment-detail-data", args=(999999,))).status_code, 404)

    def test_drawer_exposes_operator_config_item_id_and_number_separately(self):
        from .services.treatment import cmdb_isp_identity

        operator = CMDBOperatorMirror.objects.create(
            source_config_item_id="10013", source_number="36132000705",
            name="Infonet Provedor (Infotreini Provedores e Informática Ltda)",
        )
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="CI-DRAWER-ISP", isp_raw="10013", operator=operator,
        )
        ticket = self.make_ticket("2026100136000301", 99011)
        OTRSTicketConfigItem.objects.create(
            ticket=ticket, config_item_id=link.source_config_item_id, cmdb_link=link,
            relation_type="DependsOn", relation_state="Valid",
        )
        identity = cmdb_isp_identity(link)
        self.treatment.ticket = ticket
        self.treatment.isp = identity["name"]
        self.treatment.field_origins = {"isp": PenaltyTreatmentRow.Origin.CMDB}
        self.treatment.arion_values = {"cmdb_isp_sources": [identity]}
        self.treatment.save(update_fields=("ticket", "isp", "field_origins", "arion_values"))
        PenaltyTreatmentTicket.objects.create(
            treatment=self.treatment, otrs_ticket=ticket,
            source=PenaltyTreatmentTicket.Source.AUTOMATIC, is_primary=True,
        )

        response = self.client.get(reverse("relatorios:treatment-detail-data", args=(self.treatment.pk,)))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["arion"]["isp"], "Infonet Provedor")
        self.assertEqual(payload["arion"]["isp_config_item_id"], "10013")
        self.assertEqual(payload["arion"]["isp_provider_number"], "36132000705")
        self.assertEqual(payload["arion"]["isp_full_name"], "Infonet Provedor (Infotreini Provedores e Informática Ltda)")
        config_item = payload["evidence"]["tickets"][0]["config_items"][0]
        self.assertEqual(config_item["isp_internal_id"], "10013")
        self.assertEqual(config_item["isp_provider_number"], "36132000705")

    def test_exact_pu_finds_closed_historical_ticket_and_promotes_it_to_primary(self):
        from .services.treatment import enrich_competence

        number = "2026100136000301"
        old_date = timezone.make_aware(datetime(2018, 3, 2, 9, 15))
        ticket = self.make_ticket(number, 99001)
        ticket.business_status = "CLOSED"
        ticket.is_business_open = False
        ticket.is_present_in_queue = False
        ticket.source_created_at = old_date
        ticket.resolved_at = old_date + timedelta(hours=3)
        ticket.save(update_fields=(
            "business_status", "is_business_open", "is_present_in_queue",
            "source_created_at", "resolved_at",
        ))
        self.source.canonical_data["provider_unit_price"] = number
        self.source.canonical_data["pu_fornecedor_ticket"] = number
        self.source.save(update_fields=("canonical_data",))

        result = enrich_competence(
            self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk],
            eligible_only=False,
        )
        self.assertEqual(result["found"], 1)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.ticket_id, ticket.pk)
        self.assertEqual(self.treatment.ticket_identifier, number)
        self.assertEqual(self.treatment.arion_opening_at, old_date)
        self.assertEqual(self.treatment.arion_opening_origin, PenaltyTreatmentRow.Origin.OTRS)
        self.assertEqual(self.treatment.arion_fetec_at, ticket.resolved_at)
        self.assertEqual(self.treatment.arion_fetec_origin, PenaltyTreatmentRow.Origin.OTRS)
        relations = list(self.treatment.ticket_relations.select_related("otrs_ticket").order_by("-is_primary"))
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].otrs_ticket_id, ticket.pk)
        self.assertEqual(relations[0].source, PenaltyTreatmentTicket.Source.AUTOMATIC)
        self.assertTrue(relations[0].is_primary)
        self.assertEqual(self.treatment.arion_values["ticket_match"]["method"], "EXACT_PU")
        self.assertEqual(self.treatment.arion_values["ticket_match"]["confidence"], "HIGH")

        payload = self.client.get(reverse("relatorios:treatment-detail-data", args=(self.treatment.pk,))).json()
        self.assertTrue(payload["evidence"]["found"])
        self.assertEqual(payload["evidence"]["ticket_identifier"], number)
        self.assertEqual(payload["evidence"]["tickets"][0]["number"], number)
        self.assertEqual(payload["evidence"]["tickets"][0]["status"], "Resolvido")
        self.assertEqual(payload["evidence"]["tickets"][0]["source_state"], "Resolved")

    def test_rounded_excel_pu_resolves_only_with_unique_exact_rec_ci_and_lp_evidence(self):
        from .services.treatment import enrich_competence

        examples = (
            ("2026100136000300", "2026100136000301", "603204", "BL30000096592", 99101),
            ("2026092836000150", "2026092836000156", "601945", "BL30000074155", 99102),
        )
        for index, (pu, number, rec, lp, external_id) in enumerate(examples):
            with self.subTest(ticket=number):
                source = self.source if index == 0 else PenaltySourceRow.objects.create(
                    competence=self.competence, source_row_number=3, source_data=[], row_hash="a" * 64,
                    canonical_data={"rec": rec, "designation": "ASPA/IP/00003", "lp": lp,
                                    "provider_unit_price": pu, "pu_fornecedor_ticket": pu,
                                    "unavailability_fine": "1", "monthly_value": "1000"},
                )
                if index == 0:
                    source.canonical_data.update({"rec": rec, "lp": lp, "provider_unit_price": pu, "pu_fornecedor_ticket": pu})
                    source.save(update_fields=("canonical_data",))
                    treatment = self.treatment
                else:
                    treatment = PenaltyTreatmentRow.objects.create(source_row=source)
                ticket = self.make_ticket(number, external_id)
                ticket.title = f"Circuito {lp} · CI: {rec}"
                ticket.business_status = "CLOSED"
                ticket.is_business_open = False
                ticket.is_present_in_queue = False
                ticket.save(update_fields=("title", "business_status", "is_business_open", "is_present_in_queue"))

                result = enrich_competence(self.competence.pk, user=self.manager, treatment_ids=[treatment.pk], eligible_only=False)
                treatment.refresh_from_db()
                self.assertEqual(result["found"], 1)
                self.assertEqual(treatment.ticket_id, ticket.pk)
                self.assertEqual(treatment.arion_values["ticket_match"]["method"], "PU_PREFIX_REC_LP")
                self.assertEqual(treatment.arion_values["ticket_match"]["confidence"], "HIGH")
                relation = treatment.ticket_relations.get(otrs_ticket=ticket)
                self.assertTrue(relation.is_primary)
                self.assertEqual(relation.source, PenaltyTreatmentTicket.Source.AUTOMATIC)

    def test_ambiguous_rounded_pu_is_shown_without_automatic_link_and_can_be_selected(self):
        from .services.treatment import enrich_competence

        self.source.canonical_data.update({
            "rec": "603204", "lp": "BL30000096592", "provider_unit_price": "2026100136000300",
            "pu_fornecedor_ticket": "2026100136000300",
        })
        self.source.save(update_fields=("canonical_data",))
        first = self.make_ticket("2026100136000301", 99111)
        second = self.make_ticket("2026100136000308", 99112)
        for ticket in (first, second):
            ticket.title = "Circuito BL30000096592 · CI: 603204"
            ticket.business_status = "CLOSED"
            ticket.is_business_open = False
            ticket.is_present_in_queue = False
            ticket.save(update_fields=("title", "business_status", "is_business_open", "is_present_in_queue"))

        result = enrich_competence(self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk], eligible_only=False)
        self.treatment.refresh_from_db()
        self.assertEqual(result["ticket_ambiguous"], 1)
        self.assertIsNone(self.treatment.ticket_id)
        self.assertEqual(self.treatment.evidence_status, PenaltyTreatmentRow.EvidenceStatus.AMBIGUOUS)
        payload = self.client.get(reverse("relatorios:treatment-detail-data", args=(self.treatment.pk,))).json()
        self.assertEqual({item["number"] for item in payload["evidence"]["ticket_candidates"]}, {first.ticket_number, second.ticket_number})
        self.assertEqual(payload["grid"]["ticket_state"], "ambiguous")

        response = self.client.post(reverse("relatorios:treatment-tickets", args=(self.treatment.pk,)), {
            "action": "candidate", "ticket_number": second.ticket_number,
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.ticket_id, second.pk)
        self.assertEqual(self.treatment.arion_values["ticket_match"]["selection_source"], "MANUAL")
        self.assertEqual(self.treatment.arion_values["ticket_match"]["confidence"], "USER_CONFIRMED")

    def test_prefix_candidate_without_both_rec_ci_and_lp_is_not_promoted(self):
        from .services.treatment import enrich_competence

        self.source.canonical_data.update({
            "rec": "603204", "lp": "BL30000096592", "provider_unit_price": "2026100136000300",
            "pu_fornecedor_ticket": "2026100136000300",
        })
        self.source.save(update_fields=("canonical_data",))
        candidate = self.make_ticket("2026100136000301", 99121)
        candidate.title = "Circuito BL30000096592 · CI: 603205"
        candidate.save(update_fields=("title",))

        enrich_competence(self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk], eligible_only=False)
        self.treatment.refresh_from_db()
        self.assertIsNone(self.treatment.ticket_id)
        self.assertEqual(self.treatment.evidence_status, PenaltyTreatmentRow.EvidenceStatus.AMBIGUOUS)
        self.assertEqual(self.treatment.arion_values["ticket_candidates"][0]["number"], candidate.ticket_number)

    def test_invalid_pu_uses_unique_exact_rec_ci_and_lp_match(self):
        from .services.treatment import enrich_competence

        self.source.canonical_data.update({
            "rec": "603204", "lp": "BL30000096592", "provider_unit_price": "X",
            "pu_fornecedor_ticket": "",
        })
        self.source.save(update_fields=("canonical_data",))
        ticket = self.make_ticket("2026083136000713", 99131)
        ticket.title = "Circuito BL30000096592 - CI: 603204"
        ticket.business_status = "CLOSED"
        ticket.is_business_open = False
        ticket.is_present_in_queue = False
        ticket.save(update_fields=("title", "business_status", "is_business_open", "is_present_in_queue"))

        result = enrich_competence(self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk], eligible_only=False)
        self.treatment.refresh_from_db()
        self.assertEqual(result["found"], 1)
        self.assertEqual(self.treatment.ticket_id, ticket.pk)
        self.assertEqual(self.treatment.ticket_identifier, "")
        self.assertEqual(self.treatment.arion_values["ticket_match"]["method"], "REC_CI_LP")
        self.assertEqual(self.treatment.arion_values["ticket_match"]["confidence"], "HIGH")

    def test_exact_august_ticket_number_uses_exact_pu_method(self):
        from .services.treatment import enrich_competence

        number = "2026083136000713"
        self.source.canonical_data.update({
            "rec": "535043", "lp": "BD100000098997", "provider_unit_price": number,
            "pu_fornecedor_ticket": number,
        })
        self.source.save(update_fields=("canonical_data",))
        ticket = self.make_ticket(number, 99132)
        ticket.title = "Ticket sem CI e sem LP no t\u00edtulo"
        ticket.save(update_fields=("title",))

        result = enrich_competence(self.competence.pk, treatment_ids=[self.treatment.pk], eligible_only=False)
        self.treatment.refresh_from_db()
        self.assertEqual(result["found"], 1)
        self.assertEqual(self.treatment.ticket_id, ticket.pk)
        self.assertEqual(self.treatment.arion_values["ticket_match"]["method"], "EXACT_PU")
        self.assertEqual(self.treatment.arion_values["ticket_match"]["confidence"], "HIGH")

    def test_existing_manual_primary_is_not_replaced_by_exact_pu_enrichment(self):
        from .services.treatment import enrich_competence

        exact = self.make_ticket("2026100136000301", 99141)
        manual = self.make_ticket("2026100136000999", 99142)
        self.source.canonical_data.update({
            "provider_unit_price": exact.ticket_number, "pu_fornecedor_ticket": exact.ticket_number,
        })
        self.source.save(update_fields=("canonical_data",))
        PenaltyTreatmentTicket.objects.create(
            treatment=self.treatment, otrs_ticket=manual, is_primary=True,
            source=PenaltyTreatmentTicket.Source.MANUAL,
        )

        enrich_competence(self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk], eligible_only=False)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.ticket_id, manual.pk)
        self.assertEqual(self.treatment.arion_values["ticket_match"]["method"], "MANUAL")
        self.assertEqual(self.treatment.arion_values["ticket_match"]["selected_ticket_number"], manual.ticket_number)

    def test_legacy_ticket_foreign_key_without_relation_is_preserved_as_legacy_primary(self):
        from .services.treatment import enrich_competence

        exact = self.make_ticket("2026100136000301", 99143)
        legacy = self.make_ticket("2026100136000998", 99144)
        self.source.canonical_data.update({
            "provider_unit_price": exact.ticket_number, "pu_fornecedor_ticket": exact.ticket_number,
        })
        self.source.save(update_fields=("canonical_data",))
        self.treatment.ticket = legacy
        self.treatment.save(update_fields=("ticket",))

        enrich_competence(self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk], eligible_only=False)
        self.treatment.refresh_from_db()
        relation = self.treatment.ticket_relations.get()
        self.assertEqual(self.treatment.ticket_id, legacy.pk)
        self.assertEqual(relation.source, PenaltyTreatmentTicket.Source.LEGACY)
        self.assertTrue(relation.is_primary)

    def test_explicit_remote_lookup_caches_metadata_and_drawer_reads_cache_only(self):
        self.source.canonical_data.update({
            "rec": "603204", "lp": "BL30000096592", "provider_unit_price": "2026100136000300",
            "pu_fornecedor_ticket": "2026100136000300",
        })
        self.source.save(update_fields=("canonical_data",))
        remote_ticket = {
            "number": "2026100136000301", "external_id": 99151,
            "title": "Circuito BL30000096592 - CI: 603204", "queue": "Arquivo Geral",
            "state": "closed successful", "responsible": 153,
            "created_at": "2026-10-01T13:20:14", "source": "OTRS_GENERAL",
        }
        with patch("aplicativos.relatorios.services.treatment.lookup_general_otrs", return_value=[remote_ticket]) as lookup:
            response = self.client.post(reverse("relatorios:treatment-enrich", args=(self.treatment.pk,)))
        self.assertEqual(response.status_code, 200, response.content.decode())
        lookup.assert_called_once()
        self.assertEqual(lookup.call_args.args[0][0]["prefix"], "202610013600030")
        self.treatment.refresh_from_db()
        self.assertIsNone(self.treatment.ticket_id)
        self.assertEqual(self.treatment.evidence_status, PenaltyTreatmentRow.EvidenceStatus.FOUND)
        self.assertEqual(self.treatment.arion_values["ticket_match"]["method"], "PU_PREFIX_REC_LP")
        self.assertEqual(self.treatment.arion_values["ticket_match"]["confidence"], "HIGH")
        snapshot = self.treatment.arion_values["ticket_snapshot"]
        self.assertEqual(snapshot["number"], "2026100136000301")
        self.assertEqual(snapshot["source"], "OTRS_GENERAL")
        self.assertFalse(OTRSTicket.objects.filter(ticket_number=snapshot["number"]).exists())

        from .services.treatment import enrich_competence
        enrich_competence(self.competence.pk, treatment_ids=[self.treatment.pk], eligible_only=False)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.evidence_status, PenaltyTreatmentRow.EvidenceStatus.FOUND)
        self.assertEqual(self.treatment.arion_values["ticket_snapshot"]["number"], "2026100136000301")

        with patch("aplicativos.relatorios.services.treatment.lookup_general_otrs") as lookup:
            detail = self.client.get(reverse("relatorios:treatment-detail-data", args=(self.treatment.pk,)))
        lookup.assert_not_called()
        payload = detail.json()
        self.assertEqual(payload["grid"]["ticket"], "2026100136000301")
        self.assertEqual(payload["evidence"]["tickets"][0]["source"], "OTRS geral")

    def test_remote_tie_stays_ambiguous_and_safe_error_is_cached_without_details(self):
        from .services.treatment import enrich_competence

        self.source.canonical_data.update({
            "rec": "603204", "lp": "BL30000096592", "provider_unit_price": "X", "pu_fornecedor_ticket": "",
        })
        self.source.save(update_fields=("canonical_data",))
        candidates = [
            {"number": f"20261001360004{suffix}", "external_id": 99200 + index,
             "title": "Circuito BL30000096592 - CI: 603204", "queue": "Arquivo Geral",
             "state": "closed successful", "source": "OTRS_GENERAL"}
            for index, suffix in enumerate(("01", "02"))
        ]
        with patch("aplicativos.relatorios.services.treatment.lookup_general_otrs", return_value=candidates):
            result = enrich_competence(self.competence.pk, treatment_ids=[self.treatment.pk], eligible_only=False, remote_lookup=True)
        self.treatment.refresh_from_db()
        self.assertEqual(result["ticket_ambiguous"], 1)
        self.assertIsNone(self.treatment.ticket_id)
        self.assertEqual(self.treatment.evidence_status, PenaltyTreatmentRow.EvidenceStatus.AMBIGUOUS)
        self.assertEqual(len(self.treatment.arion_values["ticket_candidates"]), 2)

        with patch("aplicativos.relatorios.services.treatment.lookup_general_otrs", side_effect=RuntimeError("private-password-marker")):
            result = enrich_competence(self.competence.pk, treatment_ids=[self.treatment.pk], eligible_only=False, remote_lookup=True)
        self.treatment.refresh_from_db()
        self.assertTrue(result["remote_unavailable"])
        self.assertNotIn("private-password-marker", self.treatment.arion_values["ticket_lookup_error"])
        self.assertNotIn("private-password-marker", str(self.treatment.arion_values))
        self.assertEqual(self.treatment.evidence_status, PenaltyTreatmentRow.EvidenceStatus.AMBIGUOUS)
        self.assertEqual(len(self.treatment.arion_values["ticket_candidates"]), 2)

    def test_missing_or_invalid_pu_is_not_reported_as_searched_and_not_found(self):
        from .services.treatment import enrich_competence

        self.source.canonical_data["provider_unit_price"] = "R$ 1.234,56"
        self.source.canonical_data["pu_fornecedor_ticket"] = ""
        self.source.save(update_fields=("canonical_data",))
        result = enrich_competence(
            self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk],
            eligible_only=False,
        )
        self.assertEqual(result["not_found"], 0)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.evidence_status, PenaltyTreatmentRow.EvidenceStatus.NOT_CHECKED)
        payload = self.client.get(reverse("relatorios:treatment-detail-data", args=(self.treatment.pk,))).json()
        self.assertEqual(payload["grid"]["ticket"], "Não verificado")
        self.assertEqual(payload["grid"]["ticket_state"], "not_checked")

    def test_full_editor_uses_portuguese_labels_and_does_not_prefill_actor_as_decision_reason(self):
        response = self.client.get(reverse("relatorios:treatment-edit", args=(self.treatment.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["decision_reason"].value(), "")
        for label in (
            "Abertura ARION", "FETEC ARION", "Causa raiz", "Observação",
            "Justificativa da tratativa", "Decisão", "Motivo da decisão", "Procedência",
            "Tipo", "Minutos", "Início", "Fim", "Campo", "Novo valor", "Justificativa",
        ):
            self.assertContains(response, label)
        for label in ("Opening at", "Fetec at", "Root cause", "Observation", "Decision reason", "Procedence", "Started at", "Ended at"):
            self.assertNotContains(response, label)
        self.assertNotContains(response, "INCOMPLETE")

    @override_settings(OTRS_WEB_BASE_URL="https://otrs.example.test/otrs/index.pl")
    def test_one_claro_row_supports_three_exact_otrs_links_primary_change_and_unlink(self):
        tickets = [self.make_ticket(f"20261028360001{index}", 9000 + index) for index in range(3)]
        url = reverse("relatorios:treatment-tickets", args=(self.treatment.pk,))
        for ticket in tickets:
            response = self.client.post(url, {"action": "link", "ticket_number": ticket.ticket_number})
            self.assertEqual(response.status_code, 200, response.content.decode())

        relations = list(PenaltyTreatmentTicket.objects.filter(treatment=self.treatment).order_by("created_at", "pk"))
        self.assertEqual(len(relations), 3)
        self.assertEqual(sum(item.is_primary for item in relations), 1)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.ticket_id, tickets[0].pk)
        self.assertEqual(self.client.post(url, {"action": "link", "ticket_number": tickets[0].ticket_number}).status_code, 409)
        self.assertEqual(PenaltyTreatmentTicket.objects.filter(treatment=self.treatment).count(), 3)

        response = self.client.post(url, {"action": "primary", "relation_id": relations[1].pk})
        self.assertEqual(response.status_code, 200)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.ticket_id, tickets[1].pk)
        self.assertEqual(PenaltyTreatmentTicket.objects.filter(treatment=self.treatment, is_primary=True).count(), 1)

        response = self.client.get(reverse("relatorios:treatment-detail-data", args=(self.treatment.pk,)))
        payload = response.json()
        self.assertEqual(len(payload["evidence"]["tickets"]), 3)
        self.assertTrue(payload["evidence"]["tickets"][1]["url"].startswith("https://otrs.example.test/otrs/index.pl?Action=AgentTicketZoom;TicketID="))
        self.assertEqual(sum(item["is_primary"] for item in payload["evidence"]["tickets"]), 1)
        self.assertTrue(all(item["relation_id"] for item in payload["evidence"]["tickets"]))

        response = self.client.post(url, {"action": "unlink", "relation_id": relations[2].pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PenaltyTreatmentTicket.objects.filter(treatment=self.treatment).count(), 2)
        self.assertTrue(AuditEvent.objects.filter(module="relatorios", action="TREATMENT_TICKET_LINKED").count() == 3)
        self.assertTrue(AuditEvent.objects.filter(module="relatorios", action="TREATMENT_TICKET_PRIMARY_CHANGED").exists())
        self.assertTrue(AuditEvent.objects.filter(module="relatorios", action="TREATMENT_TICKET_UNLINKED").exists())

    def test_enrichment_uses_unique_isp_and_official_responsibility_without_guessing(self):
        from .services.treatment import enrich_competence

        tickets = [self.make_ticket(f"20261029360002{index}", 9100 + index) for index in range(3)]
        tickets[0].responsibility_normalized = "PROCEDENTE"
        tickets[0].save(update_fields=("responsibility_normalized",))
        tickets[1].responsibility_normalized = "PROCEDENTE"
        tickets[1].save(update_fields=("responsibility_normalized",))
        for index, ticket in enumerate(tickets):
            PenaltyTreatmentTicket.objects.create(
                treatment=self.treatment, otrs_ticket=ticket, is_primary=index == 0,
                source=PenaltyTreatmentTicket.Source.MANUAL,
            )
        link_a = CMDBLinkMirror.objects.create(source_config_item_id="CI-9001", source_number="9001", name="Link A", isp_raw="ATIVANET")
        link_b = CMDBLinkMirror.objects.create(source_config_item_id="CI-9002", source_number="9002", name="Link B", isp_raw="ATIVANET")
        OTRSTicketConfigItem.objects.create(ticket=tickets[0], config_item_id="CI-9001", cmdb_link=link_a, relation_type="DependsOn", relation_state="Valid")
        OTRSTicketConfigItem.objects.create(ticket=tickets[1], config_item_id="CI-9002", cmdb_link=link_b, relation_type="DependsOn", relation_state="Valid")

        result = enrich_competence(self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk], include_direct=False, eligible_only=False)
        self.assertEqual(result["found"], 1)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.isp, "ATIVANET")
        self.assertEqual(self.treatment.field_origins["isp"], PenaltyTreatmentRow.Origin.CMDB)
        self.assertEqual(self.treatment.procedence, PenaltyTreatmentRow.Procedence.PROCEDENT)
        self.assertEqual(self.treatment.procedence_origin, PenaltyTreatmentRow.Origin.OTRS)

        link_c = CMDBLinkMirror.objects.create(source_config_item_id="CI-9003", source_number="9003", name="Link C", isp_raw="OUTRA ISP")
        OTRSTicketConfigItem.objects.create(ticket=tickets[2], config_item_id="CI-9003", cmdb_link=link_c, relation_type="DependsOn", relation_state="Valid")
        tickets[2].responsibility_normalized = "IMPROCEDENTE"
        tickets[2].save(update_fields=("responsibility_normalized",))
        result = enrich_competence(self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk], include_direct=False, eligible_only=False)
        self.assertEqual(result["ambiguous"], 1)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.isp, "")
        self.assertEqual(set(self.treatment.arion_values["cmdb_isp_candidates"]), {"ATIVANET", "OUTRA ISP"})
        self.assertEqual(self.treatment.procedence, PenaltyTreatmentRow.Procedence.UNSPECIFIED)
        self.assertEqual(set(self.treatment.arion_values["otrs_procedence_candidates"]), {"PROCEDENTE", "IMPROCEDENTE"})

        inline_url = reverse("relatorios:treatment-inline-edit", args=(self.treatment.pk,))
        response = self.client.post(inline_url, {
            "field": "procedence", "value": PenaltyTreatmentRow.Procedence.IMPROCEDENT,
            "version": self.treatment.version, "reason": "Revisado manualmente.",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.procedence_origin, PenaltyTreatmentRow.Origin.MANUAL, response.content.decode())

    def test_workspaces_render_single_controlled_scroll_and_simplified_treatment_grid(self):
        from .services.treatment import enrich_competence

        self.competence.original_filename = "original.xlsx"
        self.competence.original_file.name = "relatorios/originais/2026-05/original.xlsx"
        self.competence.original_file_size = 123
        self.competence.original_file_sha256 = "e" * 64
        self.competence.column_mapping = {"0": "rec", "1": "designation"}
        self.competence.source_headers = ["REC", "Designação"]
        self.competence.save(update_fields=("original_filename", "original_file", "original_file_size", "original_file_sha256", "column_mapping", "source_headers"))

        original = self.client.get(reverse("relatorios:workspace", args=(self.competence.pk,)), {"aba": "original"})
        treatment = self.client.get(reverse("relatorios:workspace", args=(self.competence.pk,)), {"aba": "tratativa"})
        self.assertContains(original, "data-horizontal-scroll-proxy")
        self.assertNotContains(original, 'data-sticky-role="rec"')
        self.assertNotContains(original, 'data-sticky-role="designation"')
        self.assertContains(original, 'data-sticky-role="source-row"')
        self.assertContains(treatment, "data-horizontal-scroll-proxy")
        self.assertNotContains(treatment, 'data-sticky-role="rec"')
        self.assertNotContains(treatment, 'data-sticky-role="designation"')
        self.assertNotContains(treatment, "data-formula-bar")
        self.assertContains(treatment, "data-formula-key=\"ev\"")
        self.assertContains(treatment, "data-detail-query=")
        self.assertContains(treatment, 'name="page_size"')
        self.assertContains(treatment, "data-sidebar-toggle")
        self.assertContains(treatment, "data-count-key=\"pending\"")
        self.assertNotContains(treatment, 'data-column-key="actions"')
        self.assertNotContains(treatment, "data-start-edit")
        self.assertNotContains(treatment, "Abrir edição completa")
        self.assertContains(treatment, "tabindex=\"0\" aria-label=\"Abrir tratativa")
        treatment_html = treatment.content.decode("utf-8")
        thead = re.search(r"<thead>(.*?)</thead>", treatment_html, re.DOTALL)
        self.assertIsNotNone(thead)
        group_headers = re.findall(r'<th colspan="(\d+)" data-group="([^"]+)"[^>]*>(.*?)</th>', thead.group(1))
        self.assertEqual([(name, int(span)) for span, name, _label in group_headers], [
            ("identification", 10), ("claro", 9), ("arion", 10), ("result", 6),
        ])
        header_keys = re.findall(r'<th data-column-key="([^"]+)"', thead.group(1))
        self.assertEqual(header_keys, [
            "rec", "designation", "client", "uf", "lp", "ticket", "isp", "provider", "contract", "bandwidth",
            "claro-opening", "claro-fetec", "claro-duration", "claro-ev", "monthly", "claro-inter", "claro-penalty", "claro-indisp", "hours",
            "arion-opening", "arion-fetec", "pauses", "tmp", "ev", "inter", "indisp", "cause", "procedence", "observation",
            "total-claro", "total-arion", "contested", "reduction", "decision", "status",
        ])
        self.assertFalse(any(key.startswith("claro-") for key in header_keys[:10]))
        self.assertLess(treatment_html.index("data-horizontal-scroll-proxy"), treatment_html.index("class=\"treatment-grid-wrap"))
        self.treatment.procedence = PenaltyTreatmentRow.Procedence.IMPROCEDENT
        self.treatment.procedence_origin = PenaltyTreatmentRow.Origin.MANUAL
        self.treatment.save(update_fields=("procedence", "procedence_origin"))
        enrich_competence(self.competence.pk, user=self.manager, treatment_ids=[self.treatment.pk], include_direct=False, eligible_only=False)
        self.treatment.refresh_from_db()
        self.assertEqual(self.treatment.procedence, PenaltyTreatmentRow.Procedence.IMPROCEDENT)
        self.assertEqual(self.treatment.procedence_origin, PenaltyTreatmentRow.Origin.MANUAL)

    def test_spreadsheet_assets_default_columns_row_drawer_and_popover_close_rules(self):
        script = Path("frontend_src/js/relatorios/spreadsheet.js").read_text(encoding="utf-8")
        navigation = Path("frontend_src/js/relatorios/navigation.js").read_text(encoding="utf-8")
        styles = Path("arion_geo/static/css/relatorios/relatorios.css").read_text(encoding="utf-8")
        self.assertIn('"columns:treatment:v3"', script)
        self.assertIn('"total-arion", "decision"', script)
        self.assertIn('table.addEventListener("click"', script)
        self.assertIn('table.addEventListener("keydown"', script)
        self.assertIn("pointerleave", script)
        self.assertIn("2000", script)
        self.assertIn("drawer-ticket-candidate-form", script)
        self.assertIn("data-sidebar-toggle", navigation)
        self.assertIn("arion-relatorios:sidebar-collapsed", navigation)
        self.assertIn("::-webkit-scrollbar:horizontal", styles)

    def test_next_pending_drawer_navigation_respects_filters_and_never_returns_current_row(self):
        next_source = PenaltySourceRow.objects.create(
            competence=self.competence, source_row_number=3, source_data=[], row_hash="f" * 64,
            canonical_data={
                "rec": "REC-MULTI-2", "designation": "ASPA/IP/00004", "client": "Cliente de teste",
                "interconnection_fine": "80", "penalty_fine": "0", "unavailability_fine": "1",
            },
        )
        next_treatment = PenaltyTreatmentRow.objects.create(source_row=next_source)
        excluded_source = PenaltySourceRow.objects.create(
            competence=self.competence, source_row_number=4, source_data=[], row_hash="a" * 64,
            canonical_data={
                "rec": "REC-OUTRO", "designation": "FORA/DO/FILTRO", "client": "Outro cliente",
                "interconnection_fine": "70", "penalty_fine": "0", "unavailability_fine": "0",
            },
        )
        PenaltyTreatmentRow.objects.create(source_row=excluded_source)

        current = self.client.get(
            reverse("relatorios:treatment-detail-data", args=(self.treatment.pk,)),
            {"cliente": "Cliente de teste"},
        ).json()
        self.assertEqual(current["navigation"]["next_pending_id"], next_treatment.pk)
        next_payload = self.client.get(
            reverse("relatorios:treatment-detail-data", args=(next_treatment.pk,)),
            {"cliente": "Cliente de teste"},
        ).json()
        self.assertEqual(next_payload["navigation"]["next_pending_id"], self.treatment.pk)

    def test_save_and_next_keeps_current_filters_for_the_following_case(self):
        self.source.canonical_data.update({
            "monthly_value": "1000", "interconnection_fine": "99",
            "penalty_fine": "0", "unavailability_fine": "1",
        })
        self.source.save(update_fields=("canonical_data",))
        self.treatment.arion_opening_at = timezone.make_aware(datetime(2026, 9, 1, 0, 0))
        self.treatment.arion_fetec_at = timezone.make_aware(datetime(2026, 9, 2, 0, 0))
        self.treatment.save(update_fields=("arion_opening_at", "arion_fetec_at"))

        pending = []
        for row_number in (3, 4, 5):
            client_name = "Outro cliente" if row_number == 5 else "Cliente de teste"
            source = PenaltySourceRow.objects.create(
                competence=self.competence, source_row_number=row_number, source_data=[],
                row_hash=str(row_number) * 64,
                canonical_data={
                    "rec": f"REC-NEXT-{row_number}", "designation": f"ASPA/IP/{row_number:05d}",
                    "client": client_name, "interconnection_fine": "80",
                    "penalty_fine": "0", "unavailability_fine": "1",
                },
            )
            pending.append(PenaltyTreatmentRow.objects.create(source_row=source))
        next_case, following_case, _outside_filter = pending

        url = reverse("relatorios:treatment-edit", args=(self.treatment.pk,))
        query = f"?cliente=Cliente+de+teste&next_id={next_case.pk}"
        response = self.client.post(url + query, {
            "version": self.treatment.version,
            "arion_opening_at": "2026-09-01T00:00", "arion_fetec_at": "2026-09-02T00:00",
            "isp": "", "root_cause": "", "observation": "",
            "amount_not_charged": "0", "amount_not_charged_justification": "",
            "status": PenaltyTreatmentRow.Status.PENDING, "treatment_justification": "Revisão concluída.",
            "decision": PenaltyTreatmentRow.Decision.DUE, "decision_reason": "Cobrança integral validada.",
            "procedence": PenaltyTreatmentRow.Procedence.UNSPECIFIED,
            "validation_code": "", "validation_code_2": "",
            "save_and_next": "1", "next_id": str(next_case.pk),
        })
        self.assertEqual(response.status_code, 302)
        location = urlparse(response["Location"])
        self.assertEqual(location.path, reverse("relatorios:treatment-edit", args=(next_case.pk,)))
        self.assertEqual(parse_qs(location.query), {
            "cliente": ["Cliente de teste"], "next_id": [str(following_case.pk)],
        })
        next_page = self.client.get(response["Location"])
        self.assertEqual(next_page.status_code, 200)
        self.assertEqual(next_page.context["next_treatment"].pk, following_case.pk)

    def test_drawer_ajax_save_and_next_returns_next_pending_without_page_navigation(self):
        next_source = PenaltySourceRow.objects.create(
            competence=self.competence, source_row_number=3, source_data=[], row_hash="b" * 64,
            canonical_data={
                "rec": "REC-AJAX-NEXT", "designation": "ASPA/IP/00005", "client": "Cliente de teste",
                "interconnection_fine": "80", "penalty_fine": "0", "unavailability_fine": "1",
            },
        )
        next_treatment = PenaltyTreatmentRow.objects.create(source_row=next_source)
        url = reverse("relatorios:treatment-edit", args=(self.treatment.pk,)) + "?cliente=Cliente+de+teste"
        response = self.client.post(url, {
            "version": self.treatment.version,
            "arion_opening_at": "", "arion_fetec_at": "", "isp": "", "root_cause": "", "observation": "",
            "amount_not_charged": "0", "amount_not_charged_justification": "",
            "status": PenaltyTreatmentRow.Status.PENDING, "treatment_justification": "",
            "decision": PenaltyTreatmentRow.Decision.PENDING, "decision_reason": "",
            "procedence": PenaltyTreatmentRow.Procedence.UNSPECIFIED,
            "validation_code": "", "validation_code_2": "", "save_and_next": "1",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertEqual(response.json()["next_id"], next_treatment.pk)
        self.assertEqual(response.json()["id"], self.treatment.pk)
        self.assertEqual(response.json()["counters"], {
            "with_penalty": 2, "treated": 0, "reduced": 0, "invalid": 0, "pending": 2,
        })
        self.treatment.refresh_from_db()
        self.assertGreater(self.treatment.version, 1)
        self.assertEqual(self.treatment.decision_reason, "")
        self.assertTrue(AuditEvent.objects.filter(
            module="relatorios", action="TREATMENT_FIELDS_CHANGED", user=self.manager,
        ).exists())
