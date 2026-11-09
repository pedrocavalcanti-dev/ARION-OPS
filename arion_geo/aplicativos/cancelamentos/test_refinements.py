import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from .tests import CancellationTests
from .models import CancellationProcess, CancellationPending, CancellationEvent, FinancialObligation, LegacyCancellationReference, LegacyPerson
from .services.legacy_import import SOURCE
from .services.legacy_people import import_people
from .services.legacy_history import present_events
from .services.presentation import prepare_rows
from .services.queries import query_module


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class RefinementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        CancellationTests.setUpTestData.__func__(cls)

    request = CancellationTests.request
    ticket = CancellationTests.ticket
    entry = CancellationTests.entry
    approve = CancellationTests.approve
    act = CancellationTests.act
    confirm = CancellationTests.confirm
    restricted = CancellationTests.restricted

    def setUp(self):
        self.client.force_login(self.admin)

    def reference(self, process, **data):
        return LegacyCancellationReference.objects.create(process=process, source=SOURCE, entity="cancelamento", legacy_pk=str(process.pk),
            source_data={"responsavel_id": 7, "atendente_id": 8, **data})

    def test_total_counts_only_official_processes_and_fragment_keeps_scope(self):
        self.approve()
        self.entry(124)
        self.assertEqual(query_module(self.admin, {})["metrics"]["total"], 1)
        response = self.client.get(reverse("cancelamentos:index"), {"tab": "order", "owner": "me"}, HTTP_X_CANCELLATION_FRAGMENT="1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Cancellation-Fragment", response["Vary"])
        self.assertIn("Total Geral", response.json()["html"])
        self.assertNotIn("<!DOCTYPE", response.json()["html"])
        outsider = self.restricted(project="Projeto B", client="Cliente B")
        self.assertEqual(query_module(outsider, {})["metrics"]["total"], 0)

    def test_dialogs_use_existing_forms_and_deny_unauthorized_scope_and_stage(self):
        process = self.approve()
        dialog = reverse("cancelamentos:process_dialog", args=(process.pk, "pending_add"))
        response = self.client.get(dialog)
        self.assertContains(response, "Adicionar outra pendência")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, 'name="amount"')
        self.assertNotContains(response, "<html")
        self.assertEqual(self.client.get(reverse("cancelamentos:process_dialog", args=(process.pk, "complete"))).status_code, 403)
        user = self.restricted()
        self.client.force_login(user)
        self.assertEqual(self.client.get(dialog).status_code, 403)
        user = self.restricted(("view_cancellations", "manage_cancellation_pending"), project="Projeto B", client="Cliente B")
        self.client.force_login(user)
        self.assertEqual(self.client.get(dialog).status_code, 404)

    def test_repeated_pending_add_returns_revision_preserves_list_and_handles_brl(self):
        process = self.approve()
        url = reverse("cancelamentos:process_action", args=(process.pk, "pending_add"))
        revision = process.revision
        for description in ("Primeira cobrança", "Segunda cobrança"):
            response = self.client.post(url, {"revision": revision, "kind": "CONTRACT_FINE", "description": description,
                "amount": "1.234,50", "return_query": "tab=order&owner=me&page=1"}, HTTP_ACCEPT="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            revision = response.json()["revision"]
            self.assertIn("tab=order", response.json()["url"])
        self.assertEqual(process.pendings.count(), 2)
        self.assertTrue(all(p.amount == Decimal("1234.50") for p in process.pendings.all()))
        self.assertEqual(process.events.filter(action="pending_add", audit_event__isnull=False).count(), 2)
        process.refresh_from_db()
        self.assertEqual(process.stage, "ORDER")

    def test_pending_edit_preserves_legacy_snapshot_and_protects_financial_history(self):
        process = self.approve()
        pending = CancellationPending.objects.create(process=process, kind="CONTRACT_FINE", description="Descrição anterior", amount=Decimal("20"))
        ref = LegacyCancellationReference.objects.create(process=process, source=SOURCE, entity="pendenciacancelamento", legacy_pk="1",
            pending=pending, source_data={"id": 1, "descricao": "Descrição anterior", "valor": "20.00", "criada_por_id": 7})
        before = ref.source_data.copy()
        self.act(process, "pending_update", child_id=pending.pk, kind="CONTRACT_FINE", description="Descrição revisada", amount="25,00")
        pending.refresh_from_db()
        self.assertEqual(pending.amount, Decimal("25"))
        ref.refresh_from_db()
        self.assertEqual(ref.source_data, before)
        event = process.events.get(action="pending_update")
        self.assertTrue(event.financial)
        self.assertEqual(event.details["before"]["description"], "Descrição anterior")
        self.assertIsNotNone(event.audit_event_id)
        user = self.restricted(("view_cancellations", "manage_cancellation_pending"))
        self.client.force_login(user)
        process.refresh_from_db()
        response = self.client.post(reverse("cancelamentos:process_action", args=(process.pk, "pending_update")),
            {"revision": process.revision, "child_id": pending.pk, "kind": "DOCUMENT", "description": "Não autorizado"})
        self.assertEqual(response.status_code, 403)

    def test_pending_picker_excludes_other_process_and_resolution_remains_scoped(self):
        process = self.approve()
        other = self.approve(self.entry(124))
        self.act(other, "pending_add", kind="DOCUMENT", description="De outro processo")
        self.act(process, "pending_add", kind="DOCUMENT", description="Deste processo")
        url = reverse("cancelamentos:process_dialog", args=(process.pk, "pending_resolve"))
        response = self.client.get(url)
        self.assertContains(response, "Deste processo")
        self.assertNotContains(response, "De outro processo")
        self.assertEqual(self.client.get(url, {"child_id": other.pendings.get().pk}).status_code, 403)

    def test_completion_in_pending_keeps_existing_blockers_and_followup_does_not_reopen(self):
        process = self.approve()
        self.confirm(process)
        self.act(process, "move", stage="PENDING", reason="Conferência final")
        self.act(process, "complete")
        process.refresh_from_db()
        completed = process.completed_at
        self.act(process, "note", note="Acompanhamento autorizado após conclusão")
        process.refresh_from_db()
        self.assertEqual(process.stage, "COMPLETED")
        self.assertEqual(process.completed_at, completed)
        self.assertTrue(process.events.filter(action="note", audit_event__isnull=False).exists())

    def test_reliable_financial_total_and_historical_date_are_presentation_only(self):
        process = self.approve()
        self.reference(process, data_cancelamento_confirmado="2026-10-02", data_criacao_chamado="0026-10-02")
        FinancialObligation.objects.create(process=process, kind="CONTRACT", charged_amount=Decimal("0"), reconciliation_state="NOT_REQUIRED")
        process = prepare_rows(query_module(self.admin, {"tab": "order"})["queryset"], self.admin)[0]
        self.assertEqual(process.table_effective, date(2026, 10, 2))
        self.assertEqual(process.finance["total"], Decimal("0"))
        self.assertTrue(process.finance["partial"])
        self.assertTrue(process.finance["review"])
        process.treatment.refresh_from_db()
        self.assertIsNone(process.treatment.effective_date)
        self.assertFalse(process.treatment.service_confirmed)

    def test_legacy_names_csv_preview_apply_and_replay_are_independent_of_accounts(self):
        process = self.approve()
        reference = self.reference(process)
        original = reference.source_data.copy()
        event = CancellationEvent.objects.create(process=process, action="legacy_historicofila", description="PEDIDO → SOLICITACAO",
            details={"legacy_actor_id": 7}, actor=None)
        accounts = get_user_model().objects.count()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "people.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as output:
                writer = csv.DictWriter(output, fieldnames=["legacy_user_id", "username", "first_name", "last_name"])
                writer.writeheader()
                writer.writerows([{"legacy_user_id": 7, "username": "pessoa.sintetica", "first_name": "Pessoa", "last_name": "Sintética"}])
            self.assertEqual(import_people(path, self.admin)["new"], 1)
            self.assertFalse(LegacyPerson.objects.exists())
            self.assertEqual(import_people(path, self.admin, apply=True)["new"], 1)
            self.assertEqual(import_people(path, self.admin, apply=True)["unchanged"], 1)
        self.assertEqual(get_user_model().objects.count(), accounts)
        process.refresh_from_db()
        self.assertEqual(process.owner_id, self.admin.pk)
        reference.refresh_from_db()
        self.assertEqual(reference.source_data, original)
        presented = present_events([event])[0]
        self.assertEqual(presented.display_actor, "Pessoa Sintética")
        self.assertIsNone(event.actor_id)
        response = self.client.get(reverse("cancelamentos:detail", args=(process.pk,)))
        self.assertContains(response, "Responsável anterior")
        self.assertContains(response, "Pessoa Sintética")

    def test_people_file_rejects_secrets_duplicates_and_unreferenced_ids_atomically(self):
        self.reference(self.approve())
        with TemporaryDirectory() as folder:
            path = Path(folder) / "people.json"
            valid = {"id": 7, "username": "sintetico", "first_name": "Pessoa", "last_name": "Teste"}
            for rows in ([{**valid, "password": "proibido"}], [valid, valid], [{**valid, "id": 999}],
                         [{**valid, "id": 7.5}], [{**valid, "id": True}]):
                path.write_text(json.dumps(rows), encoding="utf-8")
                with self.assertRaises(ValidationError):
                    import_people(path, self.admin, apply=True)
                self.assertFalse(LegacyPerson.objects.exists())
            path.write_text(json.dumps([valid]), encoding="utf-8")
            user = self.restricted()
            with self.assertRaises(PermissionDenied):
                import_people(path, user, apply=True)

    def test_windows_csv_and_people_replay_preserve_timestamps_and_production_is_denied(self):
        from aplicativos.auditoria.models import AuditEvent
        self.reference(self.approve())
        with TemporaryDirectory() as folder:
            path = Path(folder) / "people.csv"
            path.write_text("legacy_user_id;username;first_name;last_name\n7;pessoa;Nome;Sintético\n", encoding="utf-16")
            self.assertEqual(import_people(path, self.admin, apply=True)["new"], 1)
            original = LegacyPerson.objects.get().updated_at
            self.assertEqual(import_people(path, self.admin, apply=True)["unchanged"], 1)
            self.assertEqual(LegacyPerson.objects.get().updated_at, original)
            self.assertEqual(AuditEvent.objects.filter(action="legacy_people_import").count(), 1)
            with override_settings(ARION_PRODUCTION=True, ARION_ENV="production"):
                with self.assertRaises(ValidationError):
                    import_people(path, self.admin, apply=True)

    def test_regular_history_hides_technical_ids_and_unknown_names_use_plain_language(self):
        process = self.approve()
        self.reference(process)
        CancellationEvent.objects.create(process=process, action="legacy_historicofila", description="Movimento anterior",
            details={"legacy_id": 99999, "legacy_actor_id": 7, "legacy_type": "PENDENCIA_CRIADA"}, actor=None)
        user = self.restricted()
        self.client.force_login(user)
        response = self.client.get(reverse("cancelamentos:detail", args=(process.pk,)))
        self.assertContains(response, "Nome não disponível")
        self.assertNotContains(response, "Usuário legado #")
        self.assertNotContains(response, "99999")
        self.assertNotContains(response, "Informações técnicas")
