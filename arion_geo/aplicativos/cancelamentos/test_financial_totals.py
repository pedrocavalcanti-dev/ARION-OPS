import csv
import io
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import tests as operational
from .models import CancellationProcess, FinancialObligation, LegacyCancellationReference
from .services.financial_totals import money_columns, format_brl
from .services.presentation import prepare_rows
from .services.queries import query_module
from .services.workflow import financial_summary


def obligation(kind, value, *, charged=None):
    return SimpleNamespace(kind=kind, estimated_amount=Decimal(value) if value is not None else None,
                           charged_amount=Decimal(charged) if charged is not None else None,
                           reconciliation_state="UNRECONCILED")


class InformativeTotalTests(SimpleTestCase):
    def test_main_values_are_summed_with_decimal_even_without_reconciliation(self):
        for values, expected in ((("594.25", "600", "0"), "1194.25"),
                                 (("239.76", "450", "0"), "689.76"),
                                 (("200", "1000", "126.45"), "1326.45"),
                                 (("0", "0", "169.90"), "169.90"),
                                 (("0", "0", "0"), "0")):
            with self.subTest(values=values):
                result = money_columns([obligation(k, v) for k, v in zip(("CONTRACT", "EQUIPMENT", "OTHER"), values)])
                self.assertEqual(result["total"], Decimal(expected))
                self.assertIsInstance(result["total"], Decimal)
                self.assertFalse(result["partial"])
                self.assertTrue(result["review"])

    def test_null_values_keep_known_subtotal_and_are_distinct_from_zero(self):
        for values, expected in ((("12.30", None, "0"), Decimal("12.30")),
                                 (("0", None, "0"), Decimal("0")),
                                 ((None, None, None), None)):
            with self.subTest(values=values):
                result = money_columns([obligation(k, v) for k, v in zip(("CONTRACT", "EQUIPMENT", "OTHER"), values)])
                self.assertEqual(result["total"], expected)
                self.assertTrue(result["partial"])
                self.assertIsNone(result["equipment"])
        self.assertEqual(format_brl(Decimal("0")), "R$ 0,00")
        self.assertEqual(format_brl(Decimal("1326.45")), "R$ 1.326,45")
        self.assertEqual(format_brl(None), "—")

    def test_pending_values_never_enter_or_fill_the_main_sum(self):
        ledger = [obligation("CONTRACT", "200"), obligation("EQUIPMENT", "1000"), obligation("OTHER", "126.45")]
        pending = [{"kind": "CONTRACT_FINE", "amount": Decimal("200")}, {"kind": "OTHER", "amount": Decimal("5000")}]
        self.assertEqual(money_columns(ledger, pending)["total"], Decimal("1326.45"))
        result = money_columns([], pending)
        self.assertIsNone(result["total"])
        self.assertIsNone(result["other"])
        self.assertTrue(result["partial"])

    def test_partially_known_category_and_existing_charged_precedence(self):
        result = money_columns([obligation("CONTRACT", "10"), obligation("CONTRACT", None),
                                obligation("EQUIPMENT", "0"), obligation("OTHER", "0")])
        self.assertEqual(result["fine"], Decimal("10"))
        self.assertEqual(result["total"], Decimal("10"))
        self.assertTrue(result["partial"])
        self.assertEqual(money_columns([obligation("CONTRACT", "10", charged="0")])["total"], Decimal("0"))


@override_settings(ALLOWED_HOSTS=["testserver"])
class FinancialPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        operational.CancellationTests.setUpTestData.__func__(cls)

    request = operational.CancellationTests.request
    ticket = operational.CancellationTests.ticket
    entry = operational.CancellationTests.entry
    approve = operational.CancellationTests.approve
    restricted = operational.CancellationTests.restricted

    def setUp(self):
        self.client.force_login(self.admin)
        self.process = self.approve(self.entry(123))
        CancellationProcess.objects.filter(pk=self.process.pk).update(origin="ARIONBROKER", stage="PENDING")
        LegacyCancellationReference.objects.create(process=self.process, source="arionbroker", entity="cancelamento", legacy_pk="1",
            source_data={"valor_multa_rescisao": "200.00", "valor_equipamento": "1000.00", "valor_outros": "126.45"})
        for kind, field, value in (("CONTRACT", "valor_multa_rescisao", "200"), ("EQUIPMENT", "valor_equipamento", "1000"),
                                   ("OTHER", "valor_outros", "126.45")):
            FinancialObligation.objects.create(process=self.process, kind=kind, estimated_amount=Decimal(value),
                                               legacy_field=field, reconciliation_state="POSSIBLE_DUPLICATE")
        self.process.pendings.create(kind="CONTRACT_FINE", description="Evidência repetida", amount=Decimal("200"))

    def test_legacy_tables_detail_summary_and_export_agree_without_writes(self):
        before = (list(CancellationProcess.objects.values()), list(FinancialObligation.objects.values()),
                  list(self.process.pendings.values()), list(LegacyCancellationReference.objects.values()))
        for stage, tab in (("PENDING", "pending"), ("COMPLETED", "completed")):
            CancellationProcess.objects.filter(pk=self.process.pk).update(stage=stage)
            response = self.client.get(reverse("cancelamentos:index"), {"tab": tab})
            self.assertContains(response, "R$ 1.326,45")
            self.assertNotContains(response, ">A conferir<")
            self.assertContains(response, "Total (R$)")
        response = self.client.get(reverse("cancelamentos:detail", args=[self.process.pk]))
        self.assertContains(response, "Total informado")
        self.assertContains(response, "R$ 1.326,45")
        self.assertContains(response, "Conciliação financeira: A conferir")
        self.assertIn("Total informado: R$ 1.326,45", financial_summary(self.process))
        response = self.client.get(reverse("cancelamentos:export"), {"tab": "completed"})
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(rows[0]["Total informado (R$)"], "R$ 1.326,45")
        self.assertEqual(rows[0]["Total parcial"], "Não")
        self.assertEqual(rows[0]["Conciliação financeira"], "A conferir")
        CancellationProcess.objects.filter(pk=self.process.pk).update(stage="PENDING")
        after = (list(CancellationProcess.objects.values()), list(FinancialObligation.objects.values()),
                 list(self.process.pendings.values()), list(LegacyCancellationReference.objects.values()))
        self.assertEqual(before, after)

    def test_partial_subtotal_and_no_financial_access_in_export(self):
        FinancialObligation.objects.filter(process=self.process, kind="OTHER").update(estimated_amount=None)
        response = self.client.get(reverse("cancelamentos:index"), {"tab": "pending"})
        self.assertContains(response, "R$ 1.200,00")
        self.assertContains(response, "Parcial")
        row = prepare_rows(query_module(self.admin, {"tab": "pending"})["queryset"], self.admin)[0]
        self.assertTrue(row.finance["partial"])
        export = self.client.get(reverse("cancelamentos:export"), {"tab": "pending"})
        record = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig")), delimiter=";"))[0]
        self.assertEqual(record["Total informado (R$)"], "R$ 1.200,00")
        self.assertEqual(record["Total parcial"], "Sim")
        user = self.restricted(("view_cancellations", "export_cancellations"))
        self.client.force_login(user)
        export = self.client.get(reverse("cancelamentos:export"), {"tab": "pending"})
        self.assertNotIn("Total informado", export.content.decode())
        self.assertNotIn("1.200,00", export.content.decode())

    def test_financial_export_still_restricts_data_scope(self):
        user = self.restricted(("view_cancellations", "export_cancellations", "view_cancellation_finance"),
                               client="Cliente B", project="Projeto B")
        self.client.force_login(user)
        response = self.client.get(reverse("cancelamentos:export"), {"tab": "pending"})
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(rows, [])
