from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from . import tests as operational
from .models import CancellationEvent, LegacyCancellationReference, LegacyImportRun, LegacyPerson
from .services.legacy_history import present_events
from .services.legacy_import import SOURCE
from .services.legacy_workbook import import_workbook


@override_settings(ARION_ENV="development", ARION_PRODUCTION=False, ALLOWED_HOSTS=["testserver"])
class WorkbookNamesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        operational.CancellationTests.setUpTestData.__func__(cls)

    request = operational.CancellationTests.request
    ticket = operational.CancellationTests.ticket
    entry = operational.CancellationTests.entry
    approve = operational.CancellationTests.approve

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.xlsx"
        self.process = self.approve(self.entry(123))
        self.run = LegacyImportRun.objects.create(batch="synthetic-names", counts={"source_timezone": "UTC"})
        self.main = LegacyCancellationReference.objects.create(source=SOURCE, entity="cancelamento", legacy_pk="1",
            process=self.process, import_run=self.run, source_data={"ticket_arion": self.process.ticket_number, "responsavel_id": 7})
        self.pending = self.process.pendings.create(kind="CONTRACT_FINE", description="Documento sintético", amount="12.50")
        self.ref = LegacyCancellationReference.objects.create(source=SOURCE, entity="pendenciacancelamento", legacy_pk="10",
            process=self.process, pending=self.pending, source_data={"tipo": "multa_contrato", "descricao": "Documento sintético",
            "valor": "12.50", "status": "ABERTA", "data_prevista": "2026-11-10", "criado_em": "2026-11-09 19:20:30.987654", "criada_por_id": 8})
        self.event = CancellationEvent.objects.create(process=self.process, action="legacy_historicocancelamento",
            description="Registro sintético", details={"legacy_actor_id": 8})
        self.write()

    def write(self, *, responsible="Pessoa Responsável", creator="Pessoa Autora", duplicate=False, created=None, amount=12.5):
        book = Workbook()
        main = book.active
        main.title = "Cancelamentos"
        main.append(["Relatório sintético"])
        main.append([])
        main.append(["Ticket Arion", "Responsável"])
        main.append([self.process.ticket_number, responsible])
        pending = book.create_sheet("Pendências")
        pending.append(["Relatório sintético"])
        pending.append([])
        pending.append(["Ticket Arion", "Tipo", "Descrição", "Valor", "Status", "Data Prevista", "Criada em", "Criada por", "Responsável"])
        row = [self.process.ticket_number, "Multa Contrato", "Documento sintético", amount, "Aberta",
               datetime(2026, 11, 10), created or datetime(2026, 11, 9, 16, 20, 30), creator, responsible]
        pending.append(row)
        if duplicate:
            pending.append(row)
        book.save(self.path)
        book.close()

    def test_preview_apply_replay_preserve_originals_and_accounts(self):
        from django.contrib.auth import get_user_model
        before = (list(type(self.process).objects.values()), list(type(self.pending).objects.values()),
                  list(LegacyCancellationReference.objects.values()), list(CancellationEvent.objects.values()),
                  list(get_user_model().objects.values()))
        preview = import_workbook(self.path, self.admin)
        self.assertEqual(preview["people"], {"created": 2})
        self.assertFalse(LegacyPerson.objects.exists())
        applied = import_workbook(self.path, self.admin, apply=True)
        self.assertEqual(applied["pendings"]["matched"], 1)
        self.assertEqual(applied["coverage"]["pending_creator"]["newly_identified"], 1)
        timestamps = list(LegacyPerson.objects.values_list("updated_at", flat=True))
        replay = import_workbook(self.path, self.admin, apply=True)
        self.assertEqual(replay["people"], {"unchanged": 2})
        self.assertEqual(timestamps, list(LegacyPerson.objects.values_list("updated_at", flat=True)))
        after = (list(type(self.process).objects.values()), list(type(self.pending).objects.values()),
                 list(LegacyCancellationReference.objects.values()), list(CancellationEvent.objects.values()),
                 list(get_user_model().objects.values()))
        self.assertEqual(before, after)
        self.assertEqual(present_events([self.event])[0].display_actor, "Pessoa Autora")
        self.client.force_login(self.admin)
        body = self.client.get(reverse("cancelamentos:detail", args=[self.process.pk])).content.decode()
        self.assertIn("Pessoa Responsável", body)
        self.assertIn("Registrada por Pessoa Autora", body)
        self.assertIn("Responsável atual", body)

    def test_duplicate_pending_rows_do_not_assign_an_author(self):
        self.write(duplicate=True)
        result = import_workbook(self.path, self.admin, apply=True)
        self.assertEqual(result["pendings"]["ambiguous"], 2)
        self.assertFalse(LegacyPerson.objects.filter(external_id=8).exists())

    def test_ambiguous_source_pending_is_not_matched(self):
        LegacyCancellationReference.objects.create(source=SOURCE, entity="pendenciacancelamento", legacy_pk="11",
            process=self.process, pending=self.process.pendings.create(kind="CONTRACT_FINE"), source_data=self.ref.source_data)
        result = import_workbook(self.path, self.admin, apply=True)
        self.assertEqual(result["pendings"]["ambiguous"], 1)
        self.assertFalse(LegacyPerson.objects.filter(external_id=8).exists())

    def test_confirmed_names_and_empty_values_are_preserved(self):
        LegacyPerson.objects.create(source=SOURCE, external_id=7, first_name="Nome confirmado")
        result = import_workbook(self.path, self.admin, apply=True)
        self.assertEqual(result["people"]["conflicts"], 1)
        self.assertEqual(LegacyPerson.objects.get(external_id=7).display_name, "Nome confirmado")
        self.write(responsible="", creator="")
        result = import_workbook(self.path, self.admin, apply=True)
        self.assertEqual(result["people"], {})
        self.assertEqual(LegacyPerson.objects.get(external_id=7).display_name, "Nome confirmado")

    def test_inconsistent_evidence_is_not_propagated(self):
        self.ref.source_data["criada_por_id"] = 7
        self.ref.save(update_fields=["source_data"])
        result = import_workbook(self.path, self.admin, apply=True)
        self.assertEqual(result["people"], {"conflicts": 1})
        self.assertFalse(LegacyPerson.objects.exists())

    def test_matching_requires_value_and_timestamp_and_known_source_timezone(self):
        for kwargs in ({"created": datetime(2026, 11, 9, 19, 20, 30)}, {"amount": 15}):
            self.write(**kwargs)
            result = import_workbook(self.path, self.admin, apply=True)
            self.assertEqual(result["pendings"]["unidentified"], 1)
            self.assertFalse(LegacyPerson.objects.filter(external_id=8).exists())
        self.write()
        self.main.import_run = None
        self.main.save(update_fields=["import_run"])
        self.assertEqual(import_workbook(self.path, self.admin)["pendings"]["unidentified"], 1)

    def test_unknown_ticket_formula_and_environment_are_safe(self):
        book = Workbook()
        book.active.title = "Cancelamentos"
        book.active.append(["Ticket Arion", "Responsável"])
        book.active.append(["=1+1", "Nome sintético"])
        book.create_sheet("Pendências")
        book.save(self.path)
        with self.assertRaises(ValidationError):
            import_workbook(self.path, self.admin, apply=True)
        with override_settings(ARION_PRODUCTION=True):
            with self.assertRaises(ValidationError):
                import_workbook(self.path, self.admin, apply=True)
        with self.assertRaises(PermissionDenied):
            from django.contrib.auth import get_user_model
            import_workbook(self.path, get_user_model().objects.create_user("no-import-permission"), apply=True)
        self.assertFalse(LegacyPerson.objects.exists())
