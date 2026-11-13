import csv
import io
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from aplicativos.auditoria.models import AuditEvent

from . import tests as operational
from .models import CancellationPending, CancellationProcess, FinancialObligation, LegacyCancellationReference
from .services.dashboard import build_dashboard
from .services.financial_totals import format_brl, money_columns
from .services.presentation import prepare_rows
from .services.queries import query_module
from .services.workflow import act_on_process, financial_summary


def obligation(kind, value, *, charged=None, source_pending_id=None, legacy_field=None,
               financial_status="UNKNOWN", paid_amount=None, reconciliation_state="UNRECONCILED"):
    return SimpleNamespace(
        kind=kind,
        estimated_amount=Decimal(value) if value is not None else None,
        charged_amount=Decimal(charged) if charged is not None else None,
        source_pending_id=source_pending_id,
        legacy_field=legacy_field,
        financial_status=financial_status,
        paid_amount=Decimal(paid_amount) if paid_amount is not None else None,
        reconciliation_state=reconciliation_state,
    )


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

    def test_unlinked_monetary_pendings_enter_total_and_categories_without_fabricating_zeros(self):
        # Caso 1 & 3: pendências sem cobrança vinculada compõem sua respectiva categoria e mantêm ausentes como None.
        pending = [{"pk": 1, "kind": "OVERDUE", "status": "OPEN", "amount": Decimal("22.00")}]
        result = money_columns([], pending)
        self.assertEqual(result["total"], Decimal("22.00"))
        self.assertEqual(result["pending_unlinked"], Decimal("22.00"))
        self.assertEqual(result["pending_open"], Decimal("22.00"))
        self.assertEqual(result["open"], Decimal("22.00"))
        self.assertIsNone(result["fine"])
        self.assertIsNone(result["equipment"])
        self.assertEqual(result["other"], Decimal("22.00"))
        self.assertIsNone(result["paid"])
        self.assertTrue(result["partial"])
        self.assertFalse(result["possible_duplicate"])

        multiple = pending + [
            {"pk": 2, "kind": "CONTRACT_FINE", "status": "OPEN", "amount": Decimal("30.00")},
            {"pk": 3, "kind": "DOCUMENT", "status": "OPEN", "amount": None},
        ]
        res_multi = money_columns([], multiple)
        self.assertEqual(res_multi["fine"], Decimal("30.00"))
        self.assertIsNone(res_multi["equipment"])
        self.assertEqual(res_multi["other"], Decimal("22.00"))
        self.assertEqual(res_multi["total"], Decimal("52.00"))
        self.assertEqual(res_multi["pending_open"], Decimal("52.00"))
        self.assertEqual(res_multi["pending_open_count"], 3)
        self.assertEqual(res_multi["pending_open_with_value_count"], 2)
        self.assertEqual(res_multi["pending_open_without_value_count"], 1)
        self.assertTrue(res_multi["partial"])

    def test_deduplication_requires_explicit_link_or_legacy_provenance_never_equal_amount(self):
        # Caso 5: igualdade de valor sem vínculo não é deduplicada automaticamente; apenas sinalizada para revisão.
        ob = obligation("CONTRACT", "22.00", reconciliation_state="NOT_REQUIRED")
        unlinked_pending = {"pk": 10, "kind": "CONTRACT_FINE", "status": "OPEN", "amount": Decimal("22.00")}
        dup_res = money_columns([ob], [unlinked_pending])
        self.assertEqual(dup_res["total"], Decimal("44.00"))
        self.assertTrue(dup_res["possible_duplicate"])
        self.assertTrue(dup_res["review"])

        # Caso 4a: vínculo explícito source_pending evita contagem dupla sem alertar duplicidade.
        linked_ob = obligation("CONTRACT", "22.00", source_pending_id=10, reconciliation_state="NOT_REQUIRED")
        linked_res = money_columns([linked_ob], [unlinked_pending])
        self.assertEqual(linked_res["total"], Decimal("22.00"))
        self.assertIsNone(linked_res["pending_unlinked"])
        self.assertEqual(linked_res["covered_pending_count"], 1)
        self.assertFalse(linked_res["possible_duplicate"])

        # Caso 4b: proveniência legada ArionBroker (incl. valor_outros cobrindo OVERDUE/mensalidades_atrasadas).
        legacy_ob = obligation("OTHER", "439.90", legacy_field="valor_outros", reconciliation_state="UNRECONCILED")
        legacy_pendings = [
            {"pk": 11, "kind": "OVERDUE", "status": "OPEN", "amount": Decimal("159.90"), "is_legacy": True},
            {"pk": 12, "kind": "OVERDUE", "status": "OPEN", "amount": Decimal("280.00"), "is_legacy": True},
        ]
        legacy_res = money_columns([legacy_ob], legacy_pendings)
        self.assertEqual(legacy_res["total"], Decimal("439.90"))
        self.assertIsNone(legacy_res["pending_unlinked"])
        self.assertEqual(legacy_res["covered_pending_count"], 2)
        self.assertFalse(legacy_res["possible_duplicate"])

        # Uma nova pendência nativa (is_legacy=False) em processo importado continua entrando no total.
        native_pending = {"pk": 13, "kind": "OVERDUE", "status": "OPEN", "amount": Decimal("22.00"), "is_legacy": False}
        mixed_res = money_columns([legacy_ob], legacy_pendings + [native_pending])
        self.assertEqual(mixed_res["total"], Decimal("461.90"))
        self.assertEqual(mixed_res["pending_unlinked"], Decimal("22.00"))
        self.assertTrue(mixed_res["possible_duplicate"])

    def test_distinguishes_estimated_charged_paid_open_and_resolved(self):
        # Caso 8: diferenciação rigorosa dos conceitos financeiros.
        obs = [
            obligation("CONTRACT", "100.00", charged="100.00", financial_status="PAID", paid_amount="100.00", reconciliation_state="NOT_REQUIRED"),
            obligation("EQUIPMENT", "50.00", charged="60.00", financial_status="UNKNOWN", reconciliation_state="NOT_REQUIRED"),
        ]
        pendings = [
            {"pk": 20, "kind": "OVERDUE", "status": "OPEN", "amount": Decimal("22.00")},
            {"pk": 21, "kind": "OTHER", "status": "RESOLVED", "amount": Decimal("15.00")},
            {"pk": 22, "kind": "OTHER", "status": "DISCARDED", "amount": Decimal("99.00")},
        ]
        res = money_columns(obs, pendings)
        self.assertEqual(res["total"], Decimal("197.00"))
        self.assertEqual(res["estimated"], Decimal("150.00"))
        self.assertEqual(res["charged"], Decimal("160.00"))
        self.assertEqual(res["paid"], Decimal("100.00"))
        self.assertEqual(res["open"], Decimal("82.00"))
        self.assertEqual(res["pending_open"], Decimal("22.00"))
        self.assertEqual(res["pending_resolved"], Decimal("15.00"))
        self.assertEqual(res["pending_discarded"], Decimal("99.00"))
        self.assertEqual(res["resolved_cancelled"], Decimal("114.00"))

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
        pending = self.process.pendings.create(kind="CONTRACT_FINE", description="Evidência repetida", amount=Decimal("200"))
        LegacyCancellationReference.objects.create(process=self.process, pending=pending, source="arionbroker",
                                                   entity="pendenciacancelamento", legacy_pk="1", reconciliation={})

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

    def test_single_22_pending_flows_through_list_detail_summary_export_and_dashboard(self):
        # Casos 1 & 10: reprodução fiel do caso real (pendência OVERDUE R$ 22,00 sem FinancialObligation).
        proc = self.approve(self.entry(9001))
        CancellationProcess.objects.filter(pk=proc.pk).update(stage="PENDING")
        proc.pendings.create(kind="OVERDUE", description="Mensalidade atrasada caso real", amount=Decimal("22.00"))

        list_resp = self.client.get(reverse("cancelamentos:index"), {"tab": "pending", "q": str(proc.external_ticket_id)})
        self.assertContains(list_resp, "1 pendência")
        self.assertContains(list_resp, "R$ 22,00 em aberto")
        self.assertContains(list_resp, "R$ 22,00")
        self.assertContains(list_resp, "Parcial")

        detail_resp = self.client.get(reverse("cancelamentos:detail", args=[proc.pk]))
        self.assertContains(detail_resp, "Pendências s/ cobrança")
        self.assertContains(detail_resp, "R$ 22,00")
        self.assertContains(detail_resp, "(não confirmado)")

        summary = financial_summary(proc)
        self.assertIn("Total informado: R$ 22,00", summary)
        self.assertIn("Pendências monetárias sem cobrança vinculada: R$ 22,00", summary)
        self.assertIn("Em aberto: R$ 22,00", summary)
        self.assertIn("Pago: Não confirmado", summary)

        export_resp = self.client.get(reverse("cancelamentos:export"), {"tab": "pending", "q": str(proc.external_ticket_id)})
        csv_row = list(csv.DictReader(io.StringIO(export_resp.content.decode("utf-8-sig")), delimiter=";"))[0]
        self.assertEqual(csv_row["Total informado (R$)"], "R$ 22,00")
        self.assertEqual(csv_row["Pendências s/ vínculo (R$)"], "R$ 22,00")
        self.assertEqual(csv_row["Em aberto (R$)"], "R$ 22,00")
        self.assertEqual(csv_row["Pago (R$)"], "—")

        dash = build_dashboard(self.admin, {})
        self.assertEqual(dash["metrics"]["total"], Decimal("1348.45"))

    def test_pending_without_value_and_multiple_independent_pendings(self):
        # Casos 2 & 3
        proc = self.approve(self.entry(9002))
        CancellationProcess.objects.filter(pk=proc.pk).update(stage="PENDING")
        proc.pendings.create(kind="DOCUMENT", description="Aguardando distrato")

        resp_no_val = self.client.get(reverse("cancelamentos:index"), {"tab": "pending", "q": str(proc.external_ticket_id)})
        self.assertContains(resp_no_val, "1 pendência")
        self.assertContains(resp_no_val, "Sem valor monetário")
        self.assertContains(resp_no_val, "Não informado")

        proc.pendings.create(kind="OVERDUE", description="Fatura 1", amount=Decimal("22.00"))
        proc.pendings.create(kind="CONTRACT_FINE", description="Multa 2", amount=Decimal("30.00"))
        resp_multi = self.client.get(reverse("cancelamentos:index"), {"tab": "pending", "q": str(proc.external_ticket_id)})
        self.assertContains(resp_multi, "3 pendências")
        self.assertContains(resp_multi, "R$ 52,00 em aberto · 1 s/ valor")
        self.assertContains(resp_multi, "R$ 52,00")

    def test_edit_resolve_and_link_obligation_workflows_with_rbac_scope_and_audit(self):
        # Casos 4, 6, 7, 9
        proc = self.approve(self.entry(9003))
        other_proc = self.approve(self.entry(9004))
        CancellationProcess.objects.filter(pk=proc.pk).update(stage="PENDING")
        CancellationProcess.objects.filter(pk=other_proc.pk).update(stage="PENDING")
        other_pending = other_proc.pendings.create(kind="OVERDUE", description="Outro", amount=Decimal("10.00"))

        req = self.request(self.admin)
        act_on_process(request=req, pk=proc.pk, action="pending_add", data={
            "revision": proc.revision, "kind": "OVERDUE", "description": "Mensalidade inicial", "amount": "22,00",
        })
        proc.refresh_from_db()
        pending = proc.pendings.get()
        self.assertEqual(pending.amount, Decimal("22.00"))

        # Edição de pendência via string BRL atualiza listagem e resumo
        act_on_process(request=req, pk=proc.pk, action="pending_update", data={
            "revision": proc.revision, "child_id": pending.pk, "kind": "OVERDUE",
            "description": "Mensalidade corrigida", "amount": "35,50",
        })
        proc.refresh_from_db()
        row = prepare_rows([proc], self.admin)[0]
        self.assertEqual(row.finance["total"], Decimal("35.50"))
        self.assertEqual(row.finance["pending_open"], Decimal("35.50"))

        # Vínculo cruzado de pendência de outro processo deve ser rejeitado
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            act_on_process(request=req, pk=proc.pk, action="obligation_add", data={
                "revision": proc.revision, "kind": "OVERDUE", "source_pending": other_pending.pk,
                "description": "Tentativa cruzada", "estimated_amount": "35,50",
                "document_status": "WAITING",
            })

        # Resolver pendência (sem obrigação vinculada) zera aberto, mantém histórico e não marca como paga
        act_on_process(request=req, pk=proc.pk, action="pending_resolve", data={
            "revision": proc.revision, "child_id": pending.pk, "status": "RESOLVED",
            "resolution": "Tratativa encerrada sem cobrança formal", "evidence_reference": "OTRS#9003",
        })
        proc.refresh_from_db()
        row = prepare_rows([proc], self.admin)[0]
        self.assertEqual(row.open_pending_count, 0)
        self.assertIsNone(row.finance["pending_open"])
        self.assertEqual(row.finance["open"], Decimal("0.00"))
        self.assertEqual(row.finance["pending_resolved"], Decimal("35.50"))
        self.assertEqual(row.finance["total"], Decimal("35.50"))
        self.assertIsNone(row.finance["paid"])

        # Vincular formalmente uma obrigação a uma nova pendência no mesmo processo contabiliza uma única vez
        p2 = proc.pendings.create(kind="CONTRACT_FINE", description="Multa contratual", amount=Decimal("100.00"))
        act_on_process(request=req, pk=proc.pk, action="obligation_add", data={
            "revision": proc.revision, "kind": "CONTRACT", "source_pending": p2.pk,
            "description": "Multa vinculada", "estimated_amount": "100,00", "charged_amount": "120,00",
            "document_status": "WAITING",
        })
        proc.refresh_from_db()
        row = prepare_rows([proc], self.admin)[0]
        # 120,00 da cobrança vinculada + 35,50 da pendência resolvida sem cobrança = 155,50
        self.assertEqual(row.finance["total"], Decimal("155.50"))
        self.assertEqual(row.finance["covered_pending_count"], 1)
        self.assertFalse(row.finance["possible_duplicate"])

        # Auditoria não vaza valores nem secrets na metadata central
        latest_audit = AuditEvent.objects.filter(module="cancelamentos", action="obligation_add").latest("pk")
        self.assertNotIn("120", str(latest_audit.metadata))
        self.assertEqual(latest_audit.metadata.get("source_pending_id"), p2.pk)

        # RBAC: usuário sem view_cancellation_finance não recebe valores nem vê pendência financeira
        no_fin = self.restricted(("view_cancellations", "export_cancellations"))
        self.client.force_login(no_fin)
        list_no_fin = self.client.get(reverse("cancelamentos:index"), {"tab": "pending", "q": str(proc.external_ticket_id)})
        self.assertNotContains(list_no_fin, "155,50")
        self.assertNotContains(list_no_fin, "35,50")
        detail_no_fin = self.client.get(reverse("cancelamentos:detail", args=[proc.pk]))
        self.assertNotContains(detail_no_fin, 'id="financeiro"')
        self.assertNotContains(detail_no_fin, "120,00")

        # Data Scope: usuário sem escopo recebe 404 no detalhe
        outsider = self.restricted(("view_cancellations", "view_cancellation_finance"),
                                   client="Cliente B", project="Projeto B")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("cancelamentos:detail", args=[proc.pk])).status_code, 404)

    def test_unlinked_duplicate_signal_discarded_exclusion_and_payment_breakdown_in_ui(self):
        # Casos 5, 7 (descartada) e 8 (previsto/pago/em aberto/resolvido na interface)
        proc = self.approve(self.entry(9005))
        CancellationProcess.objects.filter(pk=proc.pk).update(stage="PENDING")
        proc.obligations.create(
            kind="CONTRACT", description="Multa paga", estimated_amount=Decimal("100.00"),
            charged_amount=Decimal("100.00"), financial_status="PAID", paid_amount=Decimal("100.00"),
            reconciliation_state="NOT_REQUIRED",
        )
        proc.obligations.create(
            kind="CONTRACT", description="Nova cobrança sem vínculo com pendência",
            estimated_amount=Decimal("22.00"), financial_status="UNKNOWN", reconciliation_state="NOT_REQUIRED",
        )
        proc.pendings.create(kind="CONTRACT_FINE", description="Pendência sem vínculo formal", amount=Decimal("22.00"))
        proc.pendings.create(kind="OTHER", description="Pendência improcedente", amount=Decimal("99.00"), status="DISCARDED")

        index_resp = self.client.get(reverse("cancelamentos:index"), {"tab": "pending", "q": str(proc.external_ticket_id)})
        self.assertContains(index_resp, "R$ 144,00")
        self.assertContains(index_resp, "Possível duplicidade")

        detail_resp = self.client.get(reverse("cancelamentos:detail", args=[proc.pk]))
        self.assertContains(detail_resp, "Previsto em cobranças")
        self.assertContains(detail_resp, "R$ 122,00")
        self.assertContains(detail_resp, "Em aberto")
        self.assertContains(detail_resp, "R$ 44,00")
        self.assertContains(detail_resp, "Pago")
        self.assertContains(detail_resp, "R$ 100,00")
        self.assertContains(detail_resp, "Resolvido / descartado")
        self.assertContains(detail_resp, "R$ 99,00")
        self.assertContains(detail_resp, "possível duplicidade sem vínculo formal")

    def test_pendings_distribute_across_fine_equipment_other_in_list_detail_export_and_all_stages(self):
        proc = self.approve(self.entry(9006))
        CancellationProcess.objects.filter(pk=proc.pk).update(stage="PENDING", ticket_number="2026071036000568")
        proc.pendings.create(kind="OVERDUE", description="Mensalidade em aberto", amount=Decimal("22.00"))
        p_fine = proc.pendings.create(kind="CONTRACT_FINE", description="Multa contratual", amount=Decimal("200.00"))
        p_equip = proc.pendings.create(kind="EQUIPMENT_FINE", description="Multa equipamento", amount=Decimal("200.00"))

        for stage, tab in (("PENDING", "pending"), ("COMPLETED", "completed")):
            CancellationProcess.objects.filter(pk=proc.pk).update(stage=stage)
            resp = self.client.get(reverse("cancelamentos:index"), {"tab": tab, "q": "2026071036000568"})
            html = resp.content.decode()
            self.assertIn("R$ 200,00", html)
            self.assertIn("R$ 22,00", html)
            self.assertIn("R$ 422,00", html)
            self.assertNotContains(resp, ">Parcial<")

        for stage, tab in (("ORDER", "order"), ("REQUEST", "request"), ("PENDING", "pending"), ("COMPLETED", "completed")):
            CancellationProcess.objects.filter(pk=proc.pk).update(stage=stage)
            proc.refresh_from_db()
            row = prepare_rows(query_module(self.admin, {"tab": tab, "q": "2026071036000568"})["queryset"], self.admin)[0]
            self.assertEqual(row.finance["fine"], Decimal("200.00"))
            self.assertEqual(row.finance["equipment"], Decimal("200.00"))
            self.assertEqual(row.finance["other"], Decimal("22.00"))
            self.assertEqual(row.finance["total"], Decimal("422.00"))
            self.assertFalse(row.finance["partial"])
            export_resp = self.client.get(reverse("cancelamentos:export"), {"tab": tab, "q": "2026071036000568"})
            csv_row = list(csv.DictReader(io.StringIO(export_resp.content.decode("utf-8-sig")), delimiter=";"))[0]
            self.assertEqual(csv_row["Multa (R$)"], "R$ 200,00")
            self.assertEqual(csv_row["Equipamento (R$)"], "R$ 200,00")
            self.assertEqual(csv_row["Outros (R$)"], "R$ 22,00")
            self.assertEqual(csv_row["Total informado (R$)"], "R$ 422,00")
            self.assertEqual(csv_row["Total parcial"], "Não")

        CancellationProcess.objects.filter(pk=proc.pk).update(stage="PENDING")
        detail_resp = self.client.get(reverse("cancelamentos:detail", args=[proc.pk]))
        self.assertContains(detail_resp, "<dt>Multa contratual</dt><dd>R$ 200,00</dd>")
        self.assertContains(detail_resp, "<dt>Equipamentos</dt><dd>R$ 200,00</dd>")
        self.assertContains(detail_resp, "<dt>Outros valores</dt><dd>R$ 22,00</dd>")
        self.assertContains(detail_resp, "R$ 422,00")
        summary = financial_summary(proc)
        self.assertIn("Multa contratual: R$ 200,00", summary)
        self.assertIn("Equipamento: R$ 200,00", summary)
        self.assertIn("Outros valores: R$ 22,00", summary)
        self.assertIn("Total informado: R$ 422,00", summary)

        # Dashboard soma uma única vez por categoria (base do setUp + processo 9006)
        dash = build_dashboard(self.admin, {})
        self.assertEqual(dash["metrics"]["contract"], Decimal("400.00"))
        self.assertEqual(dash["metrics"]["equipment"], Decimal("1200.00"))
        self.assertEqual(dash["metrics"]["other"], Decimal("148.45"))
        self.assertEqual(dash["metrics"]["total"], Decimal("1748.45"))

        # Atualizar uma pendência reflete imediatamente nas colunas sem alterar etapa
        proc.refresh_from_db()
        act_on_process(request=self.request(self.admin), pk=proc.pk, action="pending_update", data={
            "revision": proc.revision, "child_id": p_fine.pk, "kind": "BILL",
            "description": "Reclassificada como boleto", "amount": "150,00",
        })
        proc.refresh_from_db()
        updated = prepare_rows([proc], self.admin)[0]
        self.assertIsNone(updated.finance["fine"])
        self.assertEqual(updated.finance["equipment"], Decimal("200.00"))
        self.assertEqual(updated.finance["other"], Decimal("172.00"))
        self.assertEqual(updated.finance["total"], Decimal("372.00"))
        self.assertTrue(updated.finance["partial"])
        self.assertEqual(updated.stage, "PENDING")
        resp_partial = self.client.get(reverse("cancelamentos:index"), {"tab": "pending", "q": "2026071036000568"})
        self.assertContains(resp_partial, ">—</td>")
        self.assertContains(resp_partial, "R$ 200,00")
        self.assertContains(resp_partial, "R$ 172,00")
        self.assertContains(resp_partial, "R$ 372,00")
        self.assertContains(resp_partial, "Parcial")

        # Vincular obrigação à pendência de equipamento mantém total consolidado sem dupla contagem
        act_on_process(request=self.request(self.admin), pk=proc.pk, action="obligation_add", data={
            "revision": proc.revision, "kind": "EQUIPMENT", "source_pending": p_equip.pk,
            "description": "Equipamento cobrado", "estimated_amount": "200,00",
            "charged_amount": "200,00", "document_status": "AVAILABLE", "document_reference": "DOC-EQ-9006",
        })
        proc.refresh_from_db()
        linked_row = prepare_rows([proc], self.admin)[0]
        self.assertIsNone(linked_row.finance["fine"])
        self.assertEqual(linked_row.finance["equipment"], Decimal("200.00"))
        self.assertEqual(linked_row.finance["other"], Decimal("172.00"))
        self.assertEqual(linked_row.finance["total"], Decimal("372.00"))
        self.assertEqual(linked_row.finance["covered_pending_count"], 1)
        self.assertFalse(linked_row.finance["possible_duplicate"])
