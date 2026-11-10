"""Synthetic domain fixtures; no MySQL migration is simulated with SQLite."""
import json
from datetime import datetime, timezone as utc
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from .models import (CancellationEvent, CancellationPending, CancellationProcess, FinancialObligation,
                     LegacyCancellationReference, LegacyImportRun)
from .services.financial_totals import money_columns
from .services.legacy_import import digest
from .services.legacy_reconciliation import reconcile_workbook


@override_settings(ARION_ENV="development", ARION_PRODUCTION=False, ALLOWED_HOSTS=["testserver"])
class HistoricalReconciliationTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.xlsx"
        self.values = Path(self.temp.name) / "principal.json"
        self.actor = get_user_model().objects.create_superuser("synthetic", password="test-only")
        self.process = CancellationProcess.objects.create(origin="ARIONBROKER", stage="ORDER", ticket_number="900001", owner=self.actor)
        self.run = LegacyImportRun.objects.create(batch="synthetic", counts={"source_timezone": "UTC"})
        raw = {"ticket_arion": "900001", "fila": "PEDIDO", "status": "NOVO", "responsavel_id": None}
        self.ref = LegacyCancellationReference.objects.create(source="arionbroker", entity="cancelamento", legacy_pk="1",
            process=self.process, import_run=self.run, source_data=raw, checksum=digest(raw))
        self.write()

    def write(self, *, duplicate=False, pending_duplicate=False, total=25, conclusion=datetime(2026, 11, 9, 17, 2)):
        book = Workbook()
        main = book.active
        main.title = "Cancelamentos"
        main.append(["Ticket Arion", "Responsável", "Fila Atual", "Status Atual", "Valor Final", "Conclusão", "Data Cancelamento"])
        row = ["#900001", "Nome histórico sintético", "Concluídos", "Concluído Cancelamento", total, conclusion, datetime(2026, 11, 9)]
        main.append(row)
        if duplicate:
            main.append(row)
        pending = book.create_sheet("Pendências")
        pending.append(["Ticket Arion", "Tipo", "Descrição", "Valor", "Status", "Data Prevista", "Criada em", "Criada por", "Responsável"])
        p = ["900001", "Multa Contrato", "Cobrança sintética", 25, "Aberta", datetime(2026, 11, 15),
             datetime(2026, 11, 9, 17, 1, 3, 123000), "Autora sintética", "Nome histórico sintético"]
        pending.append(p)
        if pending_duplicate:
            pending.append(p)
        book.save(self.path)
        book.close()
        self.values.write_text(json.dumps([{"ticket": "900001", "evidence": "Evidência sintética revisada",
            "valor_multa_rescisao": str(total), "valor_equipamento": None, "valor_outros": None}]), encoding="utf-8")

    def preview(self):
        return reconcile_workbook(self.path, self.actor, principal_path=self.values)

    def apply(self, approved):
        return reconcile_workbook(self.path, self.actor, apply=True, approved=approved, principal_path=self.values)

    def test_preview_apply_replay_preserves_identity_originals_owner_and_financial_uncertainty(self):
        original = LegacyCancellationReference.objects.get(pk=self.ref.pk).__dict__.copy()
        preview = self.preview()
        self.assertEqual((preview["stage_moves"], preview["pending_new"], preview["principal_new"], preview["issues"]), (1, 1, 1, []))
        self.assertFalse(FinancialObligation.objects.exists())
        self.apply(preview)
        self.process.refresh_from_db()
        self.assertEqual(self.process.stage, "COMPLETED")
        self.assertEqual(self.process.completed_at, datetime(2026, 11, 9, 20, 2, tzinfo=utc.utc))
        self.assertIsNone(self.process.completed_by)
        self.assertEqual(self.process.owner_id, self.actor.pk)
        self.assertEqual(CancellationProcess.objects.count(), 1)
        self.assertEqual(CancellationPending.objects.count(), 1)
        self.assertEqual(CancellationPending.objects.get().created_at.microsecond, 123000)
        self.assertIsNone(CancellationPending.objects.get().owner)
        obligation = FinancialObligation.objects.get()
        self.assertEqual(obligation.financial_status, "UNKNOWN")
        self.assertIsNone(obligation.paid_amount)
        total = money_columns([obligation], [Decimal("25")])
        self.assertEqual(total["total"], Decimal("25"))
        self.assertTrue(total["partial"])
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.source_data, original["source_data"])
        self.assertEqual(self.ref.checksum, original["checksum"])
        self.assertTrue(all(digest(ref.source_data) == ref.checksum for ref in LegacyCancellationReference.objects.all()))
        before = (CancellationEvent.objects.count(), LegacyCancellationReference.objects.count())
        replay = self.preview()
        self.assertEqual((replay["stage_moves"], replay["pending_new"], replay["principal_new"], replay["issues"]), (0, 0, 0, []))
        self.apply(replay)
        self.assertEqual(before, (CancellationEvent.objects.count(), LegacyCancellationReference.objects.count()))
        self.client.force_login(self.actor)
        body = self.client.get(reverse("cancelamentos:detail", args=[self.process.pk])).content.decode()
        self.assertIn("Autora sintética", body)
        self.assertIn("Estado histórico reconciliado", body)

    def test_stale_preview_and_local_edits_block_apply_atomically(self):
        preview = self.preview()
        CancellationProcess.objects.filter(pk=self.process.pk).update(revision=1)
        with self.assertRaises(ValidationError):
            self.apply(preview)
        self.assertFalse(CancellationPending.objects.exists())
        self.assertFalse(CancellationEvent.objects.exists())

    def test_ambiguous_pending_and_ticket_never_write(self):
        for options in ({"duplicate": True}, {"pending_duplicate": True}):
            self.write(**options)
            preview = self.preview()
            self.assertTrue(preview["issues"])
            with self.assertRaises(ValidationError):
                self.apply(preview)
        self.assertEqual(LegacyCancellationReference.objects.count(), 1)

    def test_missing_principal_evidence_does_not_derive_amounts_from_pending(self):
        preview = reconcile_workbook(self.path, self.actor)
        self.assertIn("principal_evidence_required", [i["reason"] for i in preview["issues"]])
        self.assertFalse(FinancialObligation.objects.exists())

    def test_explicit_zero_and_absent_conclusion_are_preserved(self):
        self.write(total=0, conclusion=None)
        preview = self.preview()
        self.apply(preview)
        self.process.refresh_from_db()
        self.assertIsNone(self.process.completed_at)
        self.assertEqual(FinancialObligation.objects.get().estimated_amount, Decimal("0.00"))

    def test_production_native_identity_and_permissions_are_protected(self):
        with override_settings(ARION_PRODUCTION=True, ARION_ENV="production"):
            with self.assertRaises(ValidationError):
                self.preview()
        self.actor.is_superuser = False
        self.actor.save()
        with self.assertRaises(PermissionDenied):
            self.preview()
