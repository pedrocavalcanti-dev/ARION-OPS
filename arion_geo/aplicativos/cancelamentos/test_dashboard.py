import json
from datetime import datetime, timezone as utc
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission, Group
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.db.models.functions import TruncMonth
from django.urls import reverse

from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import CMDBLinkMirror
from .models import CancellationProcess, CancellationPending, FinancialObligation, LegacyCancellationReference
from .services.dashboard import build_dashboard, _month_key
from .services.financial_totals import money_columns


@override_settings(ALLOWED_HOSTS=["testserver"], TIME_ZONE="America/Sao_Paulo", USE_TZ=True)
class CancellationDashboardTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("dashboard-synthetic", password="synthetic-only")
        self.a = CancellationProcess.objects.create(origin="ARIONBROKER", ticket_number="900001", stage="COMPLETED",
            created_at=datetime(2026, 2, 1, 2, 59, tzinfo=utc.utc), contracting_client="Claro", project="BDN", isp=" ISP A ")
        self.b = CancellationProcess.objects.create(origin="ARIONBROKER", ticket_number="900002", stage="PENDING",
            created_at=datetime(2026, 2, 15, 12, tzinfo=utc.utc), contracting_client="Claro", project="Bradesco", isp="isp a")
        self.c = CancellationProcess.objects.create(origin="ARIONBROKER", ticket_number="900003", stage="ORDER",
            created_at=datetime(2026, 2, 16, 12, tzinfo=utc.utc), contracting_client="Vivo", project="PROJETO BB", isp="ISP B")
        for process, amounts in ((self.a, ("10.25", "20.00", "0.00")), (self.b, ("0.00", "0.00", "0.00"))):
            for kind, value in zip(("CONTRACT", "EQUIPMENT", "OTHER"), amounts):
                FinancialObligation.objects.create(process=process, kind=kind, estimated_amount=Decimal(value))
        p_dup = CancellationPending.objects.create(process=self.a, kind="CONTRACT_FINE", amount="10.25", description="Mesmo valor legado")
        LegacyCancellationReference.objects.create(process=self.a, pending=p_dup, source="arionbroker",
                                                   entity="pendenciacancelamento", legacy_pk="999", reconciliation={})
        FinancialObligation.objects.filter(process=self.a, kind="CONTRACT").update(legacy_field="valor_multa_rescisao")
        self.params = {"period": "custom", "start": "2026-01-01", "end": "2026-02-28"}

    def test_official_counts_finance_null_zero_months_and_legacy_dates(self):
        result = build_dashboard(self.admin, self.params)
        self.assertEqual((result["metrics"]["count"], result["metrics"]["completed"], result["metrics"]["ongoing"]), (3, 1, 2))
        self.assertEqual(result["metrics"]["total"], Decimal("30.25"))
        self.assertTrue(result["metrics"]["partial"])
        self.assertEqual([r["count"] for r in result["charts"]["months"]], [1, 2])
        self.assertEqual(result["charts"]["finance"][1]["total_label"], "R$ 0,00")
        self.assertTrue(result["charts"]["finance"][1]["partial"])
        self.assertEqual(sum(row["value"] for row in result["charts"]["stages"]), 3)
        self.assertEqual(result["charts"]["providers"][0]["count"], 2)
        self.assertEqual(result["client_rows"][0]["count"], 2)
        exact = sum((money_columns(p.obligations.all(), p.pendings.select_related("legacy_reference").all())["total"] or Decimal(0)
                     for p in (self.a, self.b, self.c)), Decimal(0))
        self.assertEqual(result["metrics"]["total"], exact)

    def test_unlinked_monetary_pending_enters_dashboard_and_matches_process_totals(self):
        proc = CancellationProcess.objects.create(
            origin="ARIONBROKER", ticket_number="900099", stage="PENDING",
            created_at=datetime(2026, 2, 20, 12, tzinfo=utc.utc),
            contracting_client="Claro", project="BDN", isp="ISP A",
        )
        CancellationPending.objects.create(process=proc, kind="OVERDUE", amount=Decimal("22.00"), description="Mensalidade R$ 22,00")
        result = build_dashboard(self.admin, self.params)
        self.assertEqual(result["metrics"]["total"], Decimal("52.25"))
        self.assertEqual(result["metrics"]["other"], Decimal("22.00"))
        self.assertEqual(result["metrics"]["total_label"], "R$ 52,25")
        self.assertEqual(result["charts"]["finance"][1]["other"], Decimal("22.00"))
        self.assertEqual(result["charts"]["finance"][1]["total_label"], "R$ 22,00")

    def test_historical_dashboard_without_database_timezone_definitions(self):
        message = "Database returned an invalid datetime value. Are time zone definitions for your database installed?"

        def missing_timezones(execute, sql, params, many, context):
            if "convert_tz(" in sql.lower() or "django_datetime_trunc(" in sql.lower():
                raise ValueError(message)
            return execute(sql, params, many, context)

        original = list(CancellationProcess.objects.values())
        original_values = list(FinancialObligation.objects.values())
        self.client.force_login(self.admin)
        with connection.execute_wrapper(missing_timezones):
            # Reproduce the old query's production failure, then exercise both fixed series.
            with self.assertRaisesRegex(ValueError, "time zone definitions"):
                list(CancellationProcess.objects.annotate(month=TruncMonth("created_at")).values("month"))
            result = build_dashboard(self.admin, self.params)
            response = self.client.get(reverse("cancelamentos:dashboard"), self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["metrics"]["count"], 3)
        self.assertEqual(result["metrics"]["completed"], 1)
        self.assertEqual(result["metrics"]["total"], Decimal("30.25"))
        self.assertEqual([row["count"] for row in result["charts"]["months"]], [1, 2])
        self.assertEqual([row["total_label"] for row in result["charts"]["finance"]], ["R$ 30,25", "R$ 0,00"])
        self.assertEqual(list(CancellationProcess.objects.values()), original)
        self.assertEqual(list(FinancialObligation.objects.values()), original_values)

    def test_local_year_boundary_and_repeated_category_preserve_monthly_totals(self):
        CancellationProcess.objects.filter(pk=self.a.pk).update(created_at=datetime(2026, 1, 1, 2, 59, tzinfo=utc.utc))
        FinancialObligation.objects.create(process=self.a, kind="CONTRACT", estimated_amount=Decimal("1.25"))
        result = build_dashboard(self.admin, {"period":"custom", "start":"2025-12-31", "end":"2026-02-28"})
        self.assertEqual([row["count"] for row in result["charts"]["months"]], [1, 0, 2])
        self.assertEqual(result["charts"]["finance"][0]["contract"], Decimal("11.50"))
        self.assertEqual(result["metrics"]["total"], Decimal("31.50"))
        # Repeated charges of one category still represent just one known process.
        filtered = build_dashboard(self.admin, {"period":"custom", "start":"2025-12-31", "end":"2025-12-31"})
        self.assertFalse(filtered["metrics"]["partial"])

    def test_all_widgets_follow_canonical_context_and_provider_filters(self):
        all_data = build_dashboard(self.admin, self.params)
        context = next(key for key, label in all_data["dashboard_form"].fields["context"].choices if "Bradesco" in label)
        result = build_dashboard(self.admin, {**self.params, "context": context, "isp": "isp a"})
        self.assertEqual(result["metrics"]["count"], 2)
        self.assertEqual(result["metrics"]["total"], Decimal("30.25"))
        self.assertFalse(result["metrics"]["partial"])
        self.assertEqual(sum(r["count"] for r in result["charts"]["months"]), 2)
        self.assertEqual(len(result["charts"]["providers"]), 1)
        self.assertEqual(len(result["client_rows"]), 1)

    def test_invalid_filters_and_empty_period_fail_closed(self):
        for params in ({**self.params, "isp": "outside-scope"}, {**self.params, "start": "9999-12-31", "end": "9999-12-31"}):
            result = build_dashboard(self.admin, params)
            self.assertFalse(result["dashboard_form"].is_valid())
            self.assertEqual(result["metrics"]["count"], 0)
            self.assertIsNone(result["metrics"]["total"])
        result = build_dashboard(self.admin, {**self.params, "start": "2020-01-01", "end": "2020-01-31"})
        self.assertTrue(result["is_empty"])
        self.assertIsNone(result["charts"]["finance"][0]["contract"])

    def test_dates_use_registration_not_opening_or_completion_and_end_is_inclusive(self):
        self.a.ticket_opened_on = datetime(2025, 1, 1).date()
        self.a.completed_at = datetime(2026, 3, 1, tzinfo=utc.utc)
        self.a.save(update_fields=["ticket_opened_on", "completed_at"])
        result = build_dashboard(self.admin, {"period": "custom", "start": "2026-01-31", "end": "2026-01-31"})
        self.assertEqual(result["metrics"]["count"], 1)

    def test_financial_permission_denies_html_and_json_disclosure(self):
        user = get_user_model().objects.create_user("dashboard-no-finance", password="synthetic-only")
        user.user_permissions.add(Permission.objects.get(content_type__app_label="cancelamentos", codename="view_cancellations"))
        group = Group.objects.create(name="Synthetic global")
        GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.ALL)
        user.groups.add(group)
        result = build_dashboard(user, self.params)
        self.assertNotIn("total_label", result["metrics"])
        self.assertNotIn("finance", result["charts"])
        self.assertNotIn("total_label", json.dumps(result["charts"]))
        self.client.force_login(user)
        response = self.client.get(reverse("cancelamentos:dashboard"), self.params)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "R$ 30,25")
        self.assertContains(response, "Requer acesso financeiro")

    def test_scope_applies_before_counts_money_rankings_and_filter_options(self):
        user = get_user_model().objects.create_user("dashboard-restricted", password="synthetic-only")
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="cancelamentos", codename__in=["view_cancellations", "view_cancellation_finance"]))
        group = Group.objects.create(name="Synthetic custom")
        scope = GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.add(Operadora.objects.create(name="Claro", normalized_name="claro"))
        scope.allowed_projects.add(Projeto.objects.create(name="BDN", normalized_name="bdn"))
        user.groups.add(group)
        link = CMDBLinkMirror.objects.create(source_config_item_id="synthetic-ci", contracting_client="Claro", business_project="BDN", is_present_in_source=True)
        self.a.link = link
        self.a.save(update_fields=["link"])
        result = build_dashboard(user, self.params)
        self.assertEqual(result["metrics"]["count"], 1)
        self.assertEqual(result["metrics"]["total"], Decimal("30.25"))
        self.assertNotIn("ISP B", dict(result["dashboard_form"].fields["isp"].choices).values())
        self.assertEqual(sum(r["count"] for r in result["charts"]["months"]), 1)
        self.assertEqual(build_dashboard(self.admin, self.params)["metrics"]["count"], 3)

    def test_route_permission_and_constant_query_count(self):
        self.assertEqual(self.client.get(reverse("cancelamentos:dashboard")).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user("dashboard-denied", password="synthetic-only"))
        self.assertEqual(self.client.get(reverse("cancelamentos:dashboard")).status_code, 403)
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("cancelamentos:dashboard"), self.params)
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(queries), 20)
        self.assertContains(response, "Dashboard de Cancelamentos")
        self.assertContains(response, "vendor/echarts/echarts.min.js")
        self.assertNotContains(response, 'href="/dashboard/" class="header-link is-active"')
        self.assertContains(response, 'href="/cancelamentos/" class="header-link is-active"')


@override_settings(TIME_ZONE="America/Sao_Paulo", USE_TZ=True)
class DashboardHistoricalDateTests(SimpleTestCase):
    def test_missing_naive_invalid_and_out_of_range_dates_have_no_invented_month(self):
        for value in (None, "invalid", datetime(2026, 1, 1), datetime(1, 1, 1, tzinfo=utc.utc)):
            with self.subTest(value=value):
                self.assertIsNone(_month_key(value))
        self.assertEqual(_month_key(datetime(2026, 2, 1, 2, 59, tzinfo=utc.utc)), "2026-01")
