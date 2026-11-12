from io import BytesIO
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import load_workbook

from aplicativos.base_links.models import Operadora
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import (
    CMDBLinkMirror, CMDBOperatorMirror, OTRSTicket, OTRSTicketConfigItem,
)
from aplicativos.painel.services.analytics import AnalyticsQueryService
from .services import OPERATIONAL_SCORE_WEIGHTS, RANKING_WEIGHTS, RankingService, _apply_scores


class RankingsMirrorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="rank-mirror", password="safe-password-1")
        group = Group.objects.get(name="Analista NOC")
        self.user.groups.set([group])
        scope, _ = GroupDataScope.objects.get_or_create(group=group)
        scope.mode = GroupDataScope.Mode.ALL
        scope.save()
        self.operator = CMDBOperatorMirror.objects.create(source_config_item_id="op-1", name="Operadora Real")
        self.link = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-1", source_number="N-1", name="Link Real", state="SP",
            contracting_client="Claro", business_project="Bradesco", operator=self.operator,
            deployment_state="Operacional",
        )
        self.client.force_login(self.user)
        self._next_id = 910000

    def make_ticket(self, *, day=5, status="OPEN", links=None, hours_ago=1, terminal_hours=None,
                    terminal_missing=False, priority="Médio", created_at=None):
        self._next_id += 1
        created = created_at or datetime(2026, 10, day, 12, tzinfo=datetime_timezone.utc)
        terminal = created + timedelta(hours=terminal_hours) if terminal_hours is not None else None
        if terminal_missing:
            terminal = None
        ticket = OTRSTicket.objects.create(
            external_id=self._next_id, ticket_number=f"T-{self._next_id}", title="Ticket de teste",
            queue_external_id=107, queue_name="NOC - ATACADO", external_state_id=self._next_id,
            external_state_name=status, business_status=status,
            is_business_open=status in {"OPEN", "PENDING", "SCHEDULED"},
            external_priority_id=2, priority_name=priority, source_created_at=created,
            terminal_state_at=terminal,
        )
        for link in (links or [self.link]):
            OTRSTicketConfigItem.objects.create(
                ticket=ticket, config_item_id=link.source_config_item_id, cmdb_link=link,
                relation_type="DependsOn", relation_state="Valid",
            )
        return ticket

    def test_valid_ticket_count_matches_cockpit_and_excludes_canceled_unknown(self):
        self.make_ticket(status="OPEN")
        self.make_ticket(status="RESOLVED", terminal_hours=30)
        self.make_ticket(status="CANCELED")
        self.make_ticket(status="UNKNOWN")
        start, end = "2026-10-01", "2026-10-24"
        response = self.client.get(reverse("rankings:index"), {"view": "custom", "date_from": start, "date_to": end})
        cockpit = AnalyticsQueryService().query({
            "metric": "tickets", "start": start, "end": end, "group_by": "provider", "filters": {},
            "include_cancelled": False,
        }, self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["tickets_valid"], 2)
        self.assertEqual(response.context["tickets_valid"], cockpit["value"])
        self.assertEqual(response.context["providers"][0]["tickets"], 2)
        self.assertContains(response, "Score operacional comparativo")

    def test_ranking_page_skips_export_only_ticket_serialization(self):
        self.make_ticket(status="RESOLVED", terminal_hours=2)
        params = {"view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24"}
        with patch("aplicativos.rankings.services.RankingService._provider_ticket_rows", return_value=[]) as serialize:
            response = self.client.get(reverse("rankings:index"), params)
        self.assertEqual(response.status_code, 200)
        serialize.assert_not_called()

        with patch("aplicativos.rankings.services.RankingService._provider_ticket_rows", return_value=[]) as serialize:
            response = self.client.get(reverse("rankings:export", args=["xlsx"]), params)
        self.assertEqual(response.status_code, 200)
        serialize.assert_called()

    def test_canceled_and_unknown_do_not_change_ranking_drawer_history_or_exports(self):
        now = datetime(2026, 10, 25, 12, tzinfo=datetime_timezone.utc)
        date_from = datetime(2026, 10, 1).date()
        date_to = datetime(2026, 10, 24).date()
        valid = self.make_ticket(status="OPEN", created_at=now - timedelta(hours=30))

        def build_snapshot():
            return RankingService.build(
                self.user, date_from=date_from, date_to=date_to, now=now,
                detail_provider_id=self.operator.pk,
            )

        baseline = build_snapshot()
        canceled = self.make_ticket(
            status="CANCELED", created_at=now - timedelta(hours=72), terminal_hours=48,
        )
        unknown = self.make_ticket(
            status="UNKNOWN", created_at=now - timedelta(hours=72), terminal_hours=48,
        )
        snapshot = build_snapshot()

        for key in ("tickets_valid", "links_affected", "links_recurrent", "tickets_over_24h", "backlog_current"):
            self.assertEqual(snapshot[key], baseline[key], key)
        provider = snapshot["providers"][0]
        baseline_provider = baseline["providers"][0]
        for key in (
            "tickets", "links_affected", "recurrent_links", "tickets_over_24h", "backlog_current",
            "risk_score", "impact_noc_score", "trend", "trend_delta", "history",
        ):
            self.assertEqual(provider[key], baseline_provider[key], key)
        self.assertEqual(provider["tickets"], 1)
        self.assertEqual(provider["recurrent_links"], 0)
        self.assertEqual(provider["tickets_over_24h"], 0)
        self.assertEqual(provider["backlog_current"], 1)

        link = snapshot["ranked_links_all"][0]
        self.assertEqual((link["tickets"], link["recurrent"], link["over_24h"]), (1, False, 0))
        self.assertEqual([row["id"] for row in snapshot["ticket_export_rows"]], [valid.pk])
        detail = snapshot["detail"]
        self.assertEqual(detail["ticket_records_total"], 1)
        self.assertEqual([row["id"] for row in detail["ticket_records"]], [valid.pk])
        self.assertEqual(detail["over_24h_tickets"], [])
        self.assertEqual([row["id"] for row in detail["backlog_tickets"]], [valid.pk])
        self.assertEqual(detail["problematic_links"][0]["recurrent"], False)

        params = {"view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24"}
        detail_response = self.client.get(reverse("rankings:provider-detail", args=[self.operator.pk]), params)
        self.assertEqual(detail_response.status_code, 200)
        drawer_ids = {row["id"] for row in detail_response.json()["ticket_records"]}
        self.assertEqual(drawer_ids, {valid.pk})
        self.assertNotIn(canceled.pk, drawer_ids)
        self.assertNotIn(unknown.pk, drawer_ids)

        for file_format, builder in (("pdf", "build_pdf"), ("xlsx", "build_xlsx")):
            with patch(f"aplicativos.rankings.exports.{builder}", return_value=b"export") as build_export:
                response = self.client.get(reverse("rankings:export", args=[file_format]), params)
            self.assertEqual(response.status_code, 200)
            export_data = build_export.call_args.args[0]
            export_ids = {row["id"] for row in export_data["ticket_export_rows"]}
            self.assertEqual(export_ids, {valid.pk})
            self.assertNotIn(canceled.pk, export_ids)
            self.assertNotIn(unknown.pk, export_ids)

    def test_reason_filter_and_top_reasons_canonicalize_confirmed_aliases(self):
        raws = ("FalhaMassiva", "Falha Massiva", "Defeito Massivo")
        for index, raw in enumerate(raws, 1):
            ticket = self.make_ticket(day=10 + index, status="RESOLVED", terminal_hours=2)
            ticket.closing_code_raw = raw
            ticket.save(update_fields=("closing_code_raw",))
        other = self.make_ticket(day=18, status="RESOLVED", terminal_hours=2)
        other.closing_code_raw = "Falha Lógica na Operadora"
        other.save(update_fields=("closing_code_raw",))
        data = RankingService.build(
            self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date(),
            filters={"reason": "falha_massiva"}, detail_provider_id=self.operator.pk,
        )
        self.assertEqual(data["tickets_valid"], 3)
        self.assertEqual(data["detail"]["reasons_top"][0]["name"], "Falha Massiva")
        self.assertEqual(data["detail"]["reasons_top"][0]["tickets"], 3)

    def test_same_provider_multiple_links_deduplicate_ticket_and_count_affected_links(self):
        other = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-2", source_number="N-2", state="SP", operator=self.operator,
        )
        first = self.make_ticket(links=[self.link, other])
        self.make_ticket(day=6, status="RESOLVED", links=[self.link], terminal_hours=30)
        data = RankingService.build(self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date(), now=datetime(2026, 10, 24, tzinfo=datetime_timezone.utc))
        provider = data["providers"][0]
        self.assertIn(first.pk, provider["period_ticket_ids"])
        self.assertEqual(provider["tickets"], 2)
        self.assertEqual(provider["links_affected"], 2)
        self.assertEqual(provider["recurrent_links"], 1)
        # The resolved-period metric excludes the current open ticket; backlog aging is separate.
        self.assertEqual(provider["tickets_over_24h"], 1)
        self.assertEqual(provider["aged_open"], 1)

    def test_cross_provider_ticket_counts_once_per_provider_but_once_nationally(self):
        other_provider = CMDBOperatorMirror.objects.create(source_config_item_id="op-2", name="Provider B")
        other_link = CMDBLinkMirror.objects.create(source_config_item_id="ci-2", operator=other_provider, state="RJ")
        self.make_ticket(links=[self.link, other_link])
        data = RankingService.build(self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date())
        self.assertEqual(data["tickets_valid"], 1)
        self.assertEqual(sum(provider["tickets"] for provider in data["providers"]), 2)

    def test_open_aging_uses_now_and_terminal_aging_uses_resolution_or_terminal_fallback(self):
        now = datetime(2026, 10, 24, 12, tzinfo=datetime_timezone.utc)
        self.make_ticket(status="OPEN", created_at=now - timedelta(hours=25))
        self.make_ticket(status="CLOSED", created_at=now - timedelta(hours=40), terminal_hours=30)
        missing = self.make_ticket(status="RESOLVED", created_at=now - timedelta(hours=60), terminal_missing=True)
        OTRSTicket.objects.filter(pk=missing.pk).update(resolved_at=now - timedelta(hours=58))
        no_terminal_date = self.make_ticket(status="RESOLVED", created_at=now - timedelta(hours=60), terminal_missing=True)
        data = RankingService.build(self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date(), now=now)
        provider = data["providers"][0]
        self.assertEqual(provider["aged_open"], 1)
        self.assertEqual(provider["terminal_over_24h"], 1)
        self.assertEqual(provider["tickets_over_24h"], 1)
        self.assertEqual(provider["eligible_tickets"], 2)
        self.assertEqual(provider["over_24h_rate"], 1 / 2)
        self.assertEqual(OTRSTicket.objects.get(pk=no_terminal_date.pk).resolved_at, None)

    def test_ranking_duration_and_xlsx_use_resolved_at_not_later_closed_date(self):
        created = datetime(2026, 10, 10, 8, tzinfo=datetime_timezone.utc)
        ticket = self.make_ticket(status="CLOSED", created_at=created, terminal_hours=31)
        ticket.resolved_at = created + timedelta(hours=7)
        ticket.save(update_fields=("resolved_at",))

        data = RankingService.build(
            self.user, date_from=created.date(), date_to=created.date(),
            now=created + timedelta(days=2), detail_provider_id=self.operator.pk,
        )
        record = data["detail"]["ticket_records"][0]
        self.assertEqual(record["status"], "RESOLVED")
        self.assertEqual(record["duration_hours"], 7)
        self.assertEqual(record["resolved_at"], "10/10/2026 12:00")
        self.assertEqual(record["terminal_at"], "11/10/2026 12:00")
        self.assertEqual(record["operational_end_at"], "10/10/2026 12:00")
        self.assertEqual(record["operational_end_source"], "resolved_at")

        response = self.client.get(reverse("rankings:export", args=["xlsx"]), {
            "view": "custom", "date_from": "2026-10-10", "date_to": "2026-10-10",
        })
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content), read_only=True, data_only=True)
        sheet = workbook["Tickets"]
        headers = [cell.value for cell in sheet[1]]
        values = [cell.value for cell in sheet[2]]
        row = dict(zip(headers, values))
        self.assertEqual(row["Status"], "RESOLVED")
        self.assertEqual(row["Resolvido em"], "10/10/2026 12:00")
        self.assertEqual(row["Terminal OTRS em"], "11/10/2026 12:00")
        self.assertEqual(row["Término operacional usado"], "10/10/2026 12:00")
        self.assertEqual(row["Duração (horas)"], 7)

    def test_provider_with_zero_tickets_has_zero_index_and_stays_in_the_service_universe(self):
        small = CMDBOperatorMirror.objects.create(source_config_item_id="op-small", name="Provider pequeno")
        CMDBLinkMirror.objects.create(source_config_item_id="ci-small", operator=small)
        data = RankingService.build(self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date())
        provider = next(row for row in data["providers"] if row["name"] == "Provider pequeno")
        self.assertEqual(provider["tickets"], 0)
        self.assertTrue(provider["sample_small"])
        self.assertEqual(provider["risk_score"], 0)
        self.assertEqual(provider["risk_label"], "Sem ocorrências")
        self.assertTrue(provider["no_occurrence"])

    def test_current_inventory_exposure_includes_every_deployment_state(self):
        extra_active = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-active-2", operator=self.operator, deployment_state="operational",
        )
        for index, state in enumerate(("Cancelado", "Em Manutenção", "", "Em implantação"), 1):
            CMDBLinkMirror.objects.create(
                source_config_item_id=f"ci-inactive-{index}", operator=self.operator, deployment_state=state,
            )
        data = RankingService.build(
            self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date(),
        )
        provider = next(row for row in data["providers"] if row["id"] == self.operator.pk)
        self.assertEqual(provider["links_total"], 6)
        self.assertEqual(provider["links_active"], 6)
        self.assertEqual(data["links_active"], 6)
        self.assertEqual(data["links_total"], 6)
        self.assertEqual(data["deployment_active_links_total"], 2)
        self.assertEqual(provider["deployment_active_links"], 2)
        self.assertEqual(data["active_provider_count"], 1)
        self.assertGreater(extra_active.pk, 0)

    def test_exposure_incidence_is_per_100_current_inventory_links(self):
        provider_a = CMDBOperatorMirror.objects.create(source_config_item_id="op-exposure-a", name="Provider A")
        provider_a_link = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-exposure-a", operator=provider_a, deployment_state="Operacional",
        )
        for index in range(99):
            CMDBLinkMirror.objects.create(
                source_config_item_id=f"ci-exposure-a-{index}", operator=provider_a, deployment_state="Operacional",
            )
        provider_b = CMDBOperatorMirror.objects.create(source_config_item_id="op-exposure-b", name="Provider B")
        provider_b_link = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-exposure-b", operator=provider_b, deployment_state="Operacional",
        )
        for index in range(9):
            CMDBLinkMirror.objects.create(
                source_config_item_id=f"ci-exposure-b-{index}", operator=provider_b, deployment_state="Operacional",
            )
        provider_zero = CMDBOperatorMirror.objects.create(source_config_item_id="op-exposure-zero", name="Provider zero")
        zero_link = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-exposure-zero", operator=provider_zero, deployment_state="Cancelado",
        )
        for _ in range(20):
            self.make_ticket(links=[provider_a_link])
        for _ in range(10):
            self.make_ticket(links=[provider_b_link])
        self.make_ticket(links=[zero_link])

        data = RankingService.build(
            self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date(),
        )
        providers = {row["id"]: row for row in data["providers"]}
        self.assertEqual(providers[provider_a.pk]["tickets_per_100_active_links"], 20)
        self.assertEqual(providers[provider_b.pk]["tickets_per_100_active_links"], 100)
        self.assertEqual(providers[provider_zero.pk]["tickets_per_100_active_links"], 100)
        self.assertEqual(providers[provider_zero.pk]["links_active"], 1)
        self.assertEqual(providers[provider_zero.pk]["deployment_active_links"], 0)

    def test_average_resolution_uses_terminal_tickets_resolved_in_period(self):
        old_created = datetime(2026, 9, 29, 12, tzinfo=datetime_timezone.utc)
        self.make_ticket(status="RESOLVED", created_at=old_created, terminal_hours=40)
        data = RankingService.build(
            self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date(),
        )
        provider = next(row for row in data["providers"] if row["id"] == self.operator.pk)
        self.assertEqual(provider["tickets"], 0)
        self.assertEqual(provider["resolution_tickets"], 1)
        self.assertEqual(provider["avg_resolution_hours"], 40)
        self.assertEqual(provider["avg_resolution_display"], "40h 00m")

    def test_active_exposure_view_defaults_to_twenty_and_supports_minimum_link_filter(self):
        operators = []
        for index in range(21):
            operator = CMDBOperatorMirror.objects.create(
                source_config_item_id=f"op-top-{index}", name=f"Provider {index:02d}",
            )
            CMDBLinkMirror.objects.create(
                source_config_item_id=f"ci-top-{index}", operator=operator,
                deployment_state="Operacional" if index != 0 else "Cancelado",
            )
            operators.append(operator)
        response = self.client.get(reverse("rankings:index"), {
            "view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filters"]["ranking_view"], "links")
        self.assertEqual(response.context["filters"]["top"], "20")
        self.assertEqual(response.context["page_obj"].paginator.count, 22)
        self.assertEqual(len(response.context["page_obj"].object_list), 20)
        filtered = self.client.get(reverse("rankings:index"), {
            "view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24",
            "top": "all", "min_links": "10",
        })
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.context["page_obj"].paginator.count, 0)

    def test_period_windows_top_options_and_minimum_defaults(self):
        from django.test import RequestFactory
        from .views import _read_filters

        with patch("aplicativos.rankings.views.timezone.localdate", return_value=datetime(2026, 10, 29).date()):
            expected_starts = {
                "month": "2026-10-01", "3months": "2026-08-01",
                "6months": "2026-05-01", "12months": "2025-11-01",
            }
            for view, expected_start in expected_starts.items():
                with self.subTest(view=view):
                    request = RequestFactory().get("/rankings/", {"view": view, "month": "2026-10"})
                    filters = _read_filters(request)
                    self.assertEqual(filters["date_from"], expected_start)
                    self.assertEqual(filters["date_to"], "2026-10-29")
            for top in ("10", "20", "30", "50", "all"):
                with self.subTest(top=top):
                    request = RequestFactory().get("/rankings/", {"top": top})
                    self.assertEqual(_read_filters(request)["top"], top)
            defaults = _read_filters(RequestFactory().get("/rankings/"))
            self.assertEqual(defaults["top"], "20")
            self.assertEqual(defaults["min_links"], "0")

    def test_one_link_one_incident_remains_visible_with_high_proportional_rate(self):
        small = CMDBOperatorMirror.objects.create(source_config_item_id="op-risk", name="Provider 1 link")
        link = CMDBLinkMirror.objects.create(source_config_item_id="ci-risk", operator=small, state="MG")
        self.make_ticket(links=[link])
        large = CMDBOperatorMirror.objects.create(source_config_item_id="op-large", name="Provider 10 links")
        for index in range(10):
            CMDBLinkMirror.objects.create(source_config_item_id=f"ci-large-{index}", operator=large)
        data = RankingService.build(self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date())
        provider = next(row for row in data["providers"] if row["name"] == "Provider 1 link")
        self.assertEqual(provider["tickets"], 1)
        self.assertEqual(provider["affected_rate"], 1)
        self.assertEqual(provider["tickets_per_100_links"], 100)
        self.assertTrue(provider["sample_small"])
        large_provider = next(row for row in data["providers"] if row["name"] == "Provider 10 links")
        self.assertGreater(provider["risk_score"], large_provider["risk_score"])

    def test_canonical_client_project_filters_and_rolling_history(self):
        for month, day in ((8, 8), (9, 8), (10, 8)):
            self.make_ticket(created_at=datetime(2026, month, day, 12, tzinfo=datetime_timezone.utc))
        data = RankingService.build(
            self.user, date_from=datetime(2026, 8, 1).date(), date_to=datetime(2026, 10, 24).date(),
            filters={"client": "Claro", "project": "Bradesco", "uf": "SP"},
        )
        provider = data["providers"][0]
        self.assertEqual(provider["tickets"], 3)
        self.assertEqual(data["links_active"], 1)
        self.assertEqual(len(provider["history"]), 3)
        self.assertEqual(provider["trend"], "insufficient")
        self.assertEqual(provider["risk_score"], round(provider["risk_score"], 1))
        self.assertEqual(provider["risk_score"], round(sum(provider["risk_components"].values()), 1))
        self.assertTrue(all(point["score"] is None for point in provider["history"]))

    def test_monthly_trend_does_not_invent_scores_for_small_samples(self):
        other = CMDBOperatorMirror.objects.create(source_config_item_id="op-trend", name="Provider tendência")
        other_link = CMDBLinkMirror.objects.create(source_config_item_id="ci-trend", operator=other)
        self.make_ticket(day=5, status="RESOLVED", terminal_hours=1)
        self.make_ticket(day=5, status="RESOLVED", terminal_hours=1,
                         created_at=datetime(2026, 9, 5, 12, tzinfo=datetime_timezone.utc), links=[other_link])
        data = RankingService.build(self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date())
        current_only = next(row for row in data["providers"] if row["name"] == "Operadora Real")
        previous_only = next(row for row in data["providers"] if row["name"] == "Provider tendência")
        self.assertEqual(current_only["operational_score"], None)
        self.assertEqual(current_only["trend"], "insufficient")
        self.assertEqual(previous_only["trend"], "insufficient")

    def test_provider_detail_matches_ranking_row_and_link_identity_is_canonical(self):
        self.make_ticket(status="RESOLVED", terminal_hours=30)
        response = self.client.get(reverse("rankings:index"), {"view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24"})
        row = response.context["providers"][0]
        detail = self.client.get(reverse("rankings:provider-detail", args=[self.operator.pk]), {
            "view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24",
        })
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(payload["risk_score"], row["risk_score"])
        self.assertEqual(payload["tickets"], row["tickets"])
        self.assertEqual(payload["links_total"], row["links_total"])
        link = response.context["ranked_links"][0]
        self.assertEqual((link["client"], link["project"], link["provider"]), ("Claro", "Bradesco", "Operadora Real"))

    def test_best_and_attention_views_require_comparable_resolution_samples(self):
        small = CMDBOperatorMirror.objects.create(source_config_item_id="op-small-best", name="Amostra pequena")
        small_link = CMDBLinkMirror.objects.create(source_config_item_id="ci-small-best", operator=small)
        eligible_by_links = CMDBOperatorMirror.objects.create(source_config_item_id="op-eligible", name="Provider elegivel por links")
        eligible_links = [CMDBLinkMirror.objects.create(source_config_item_id=f"ci-eligible-{index}", operator=eligible_by_links) for index in range(5)]
        attention_provider = CMDBOperatorMirror.objects.create(source_config_item_id="op-attention", name="Provider atencao")
        attention_links = [CMDBLinkMirror.objects.create(source_config_item_id=f"ci-attention-{index}", operator=attention_provider) for index in range(5)]
        stable = CMDBOperatorMirror.objects.create(source_config_item_id="op-stable", name="Provider estavel")
        stable_links = [CMDBLinkMirror.objects.create(
            source_config_item_id=f"ci-stable-{index}", operator=stable, deployment_state="Operacional",
        ) for index in range(8)]
        self.make_ticket(links=[small_link])
        for day in (5, 6, 7):
            self.make_ticket(day=day, status="RESOLVED", terminal_hours=1, links=[eligible_links[0]])
            self.make_ticket(day=day, status="RESOLVED", terminal_hours=30, links=[attention_links[0]])

        response = self.client.get(reverse("rankings:index"), {
            "view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24", "ranking_view": "best",
        })
        self.assertEqual(response.status_code, 200)
        rows = response.context["providers"]
        self.assertTrue(all(row["operational_score"] is not None for row in rows))
        self.assertEqual(rows, sorted(rows, key=lambda row: (-row["operational_score"], -row["links_total"], -row["resolution_tickets"], row["name"].casefold())))
        self.assertEqual(response.context["best_eligible_count"], 2)
        self.assertTrue(all(row["best_eligible"] for row in response.context["top_providers"]))
        self.assertNotIn(small.pk, [row["id"] for row in response.context["top_providers"]])
        self.assertNotIn(stable.pk, [row["id"] for row in rows])

        risk_response = self.client.get(reverse("rankings:index"), {
            "view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24", "ranking_view": "risk",
        })
        risk_rows = risk_response.context["providers"]
        self.assertEqual(risk_rows, sorted(risk_rows, key=lambda row: (row["operational_score"], -row["links_total"], -row["resolution_tickets"], row["name"].casefold())))
        self.assertNotIn(small.pk, [row["id"] for row in risk_rows])
        self.assertEqual(risk_rows[0]["id"], attention_provider.pk)
        self.assertEqual(rows[0]["id"], eligible_by_links.pk)

        stable_response = self.client.get(reverse("rankings:index"), {
            "view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24", "ranking_view": "stable",
        })
        self.assertEqual(stable_response.status_code, 200)
        self.assertEqual(stable_response.context["providers"][0]["id"], stable.pk)
        self.assertEqual(stable_response.context["providers"][0]["links_total"], len(stable_links))
        self.assertContains(stable_response, "Sem ocorr&ecirc;ncias no per&iacute;odo")

    def test_detail_responsibility_and_ticket_rows_deduplicate_same_provider_and_sanitize_title(self):
        second_link = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-1b", source_number="N-1B", name="Segundo link", state="RJ",
            contracting_client="Claro", business_project="Bradesco", operator=self.operator,
        )
        procedente = self.make_ticket(links=[self.link, second_link])
        procedente.title = "Contato pessoa@example.com 11 99999-1234"
        procedente.responsibility_raw = "Procedente"
        procedente.responsibility_normalized = "PROCEDENTE"
        procedente.save(update_fields=("title", "responsibility_raw", "responsibility_normalized"))
        improcedente = self.make_ticket(day=6, status="RESOLVED", terminal_hours=2)
        improcedente.responsibility_raw = "Improcedente"
        improcedente.responsibility_normalized = "IMPROCEDENTE"
        improcedente.save(update_fields=("responsibility_raw", "responsibility_normalized"))
        for day in (7, 8, 9):
            ticket = self.make_ticket(day=day, status="RESOLVED", terminal_hours=2)
            ticket.closing_code_raw = "SemIntervencaoTecnica"
            ticket.responsibility_raw = "Improcedente"
            ticket.responsibility_normalized = "IMPROCEDENTE"
            ticket.save(update_fields=("closing_code_raw", "responsibility_raw", "responsibility_normalized"))
        second_procedente = self.make_ticket(day=10, status="RESOLVED", terminal_hours=2)
        second_procedente.responsibility_raw = "Procedente"
        second_procedente.responsibility_normalized = "PROCEDENTE"
        second_procedente.save(update_fields=("responsibility_raw", "responsibility_normalized"))
        self.make_ticket(day=11)

        response = self.client.get(reverse("rankings:provider-detail", args=[self.operator.pk]), {
            "view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24",
        })
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["tickets"], 3)
        self.assertEqual(detail["responsibility"]["procedentes"], 2)
        self.assertEqual(detail["responsibility"]["improcedentes"], 0)
        self.assertEqual(detail["responsibility"]["indeterminados"], 1)
        self.assertEqual(detail["responsibility"]["procedure_rate"], 1)
        self.assertFalse(detail["responsibility"]["review"])
        self.assertEqual(detail["ticket_records_total"], 3)
        record = next(item for item in detail["ticket_records"] if item["id"] == procedente.pk)
        self.assertEqual(len(record["links"]), 2)
        self.assertIn("Criado no per", record["why"][0])
        self.assertNotIn("pessoa@example.com", record["title"])
        self.assertNotIn("99999-1234", record["title"])

    def test_invalid_incidents_are_excluded_from_arion_v2_score(self):
        ticket = self.make_ticket()
        before = RankingService.build(
            self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date(),
        )["providers"][0]["risk_score"]
        OTRSTicket.objects.filter(pk=ticket.pk).update(
            responsibility_raw="Improcedente", responsibility_normalized="IMPROCEDENTE",
        )
        after = RankingService.build(
            self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date(),
        )["providers"][0]["risk_score"]
        self.assertGreater(before, after)
        self.assertEqual(after, 0)

    def test_pdf_and_xlsx_exports_preserve_data_scope_and_include_full_filtered_rows(self):
        from aplicativos.base_links.models import Operadora, Projeto

        allowed_operator = Operadora.objects.create(name="Claro", normalized_name="claro")
        allowed_project = Projeto.objects.create(name="Bradesco", normalized_name="bradesco")
        group = self.user.groups.get(name="Analista NOC")
        scope = group.data_scope
        scope.mode = GroupDataScope.Mode.CUSTOM
        scope.save()
        scope.allowed_operators.set([allowed_operator])
        scope.allowed_projects.set([allowed_project])
        outside_operator = CMDBOperatorMirror.objects.create(source_config_item_id="op-export-out", name="Provider fora do escopo")
        outside_link = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-export-out", name="Link fora do escopo", operator=outside_operator,
            business_project="Bradesco", state="RJ",
        )
        wrong_project_link = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-export-project-out", name="Projeto fora do escopo", operator=self.operator,
            business_project="Projeto externo", state="RJ",
        )
        self.make_ticket(links=[self.link])
        self.make_ticket(links=[outside_link])
        self.make_ticket(links=[wrong_project_link])
        params = {"view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24", "ranking_view": "best"}

        pdf = self.client.get(reverse("rankings:export", args=["pdf"]), params)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        self.assertGreater(len(pdf.content), 1000)

        xlsx = self.client.get(reverse("rankings:export", args=["xlsx"]), params)
        self.assertEqual(xlsx.status_code, 200)
        self.assertIn("spreadsheetml", xlsx["Content-Type"])
        workbook = load_workbook(BytesIO(xlsx.content), read_only=True, data_only=True)
        self.assertEqual(workbook.sheetnames, ["Resumo", "Ranking", "Providers", "Links", "Tickets"])
        self.assertEqual(workbook["Links"].max_row, 2)
        self.assertEqual(workbook["Tickets"].max_row, 2)
        ranking_headers = next(workbook["Ranking"].iter_rows(values_only=True))
        self.assertIn("Score operacional /100", ranking_headers)
        self.assertNotIn("V2", " ".join(str(value) for value in ranking_headers))
        self.assertIn("Resolvidos >24h /30", ranking_headers)
        self.assertIn("Chamados / link CMDB", ranking_headers)
        self.assertIn("Chamados / 100 links", ranking_headers)
        self.assertIn("Em opera\u00e7\u00e3o", ranking_headers)
        self.assertNotIn("Tickets / 100 links", ranking_headers)
        values = [cell for row in workbook["Links"].iter_rows(values_only=True) for cell in row]
        self.assertNotIn("ci-export-out", values)
        self.assertNotIn("ci-export-project-out", values)

    def test_custom_data_scope_excludes_links_outside_authorized_intersection(self):
        CMDBLinkMirror.objects.create(source_config_item_id="ci-out", operator=self.operator, business_project="Outside")
        group = self.user.groups.get(name="Analista NOC")
        scope = group.data_scope
        scope.mode = GroupDataScope.Mode.CUSTOM
        scope.save()
        scope.allowed_operators.set([Operadora.objects.create(name=self.operator.name, normalized_name=self.operator.name.casefold())])
        data = RankingService.build(self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date())
        self.assertEqual(data["links_total"], 0)
        self.assertEqual(data["links_active"], 0)
        self.assertEqual(data["providers"], [])

    def test_inventory_exposure_groups_provider_name_case_variants(self):
        duplicate = CMDBOperatorMirror.objects.create(source_config_item_id="op-name-duplicate", name="operadora real")
        duplicate_link = CMDBLinkMirror.objects.create(
            source_config_item_id="ci-name-duplicate", operator=duplicate, deployment_state="Cancelado",
        )
        other_provider = CMDBOperatorMirror.objects.create(source_config_item_id="op-name-other", name="Outra operadora")
        CMDBLinkMirror.objects.create(source_config_item_id="ci-name-other", operator=other_provider)
        CMDBLinkMirror.objects.create(source_config_item_id="ci-name-unresolved", isp_raw="", operator=None)
        self.make_ticket(links=[duplicate_link])

        data = RankingService.build(
            self.user, date_from=datetime(2026, 10, 1).date(), date_to=datetime(2026, 10, 24).date(),
        )

        provider = next(row for row in data["providers"] if row["name"].casefold() == "operadora real")
        self.assertEqual(provider["links_active"], 2)
        self.assertEqual(provider["links_total"], 2)
        self.assertEqual(provider["tickets"], 1)
        self.assertEqual(data["active_provider_count"], 2)
        self.assertEqual(data["links_total"], 4)
        self.assertEqual(data["top10_links"], 3)
        self.assertEqual(data["top10_link_concentration"], 75)
        self.assertEqual(data["active_links_unidentified"], 1)


class RankingScoreTests(SimpleTestCase):
    def test_operational_score_is_relative_volume_normalized_and_requires_a_peer_sample(self):
        self.assertEqual(sum(OPERATIONAL_SCORE_WEIGHTS.values()), 1)
        low_pressure = {
            "id": 1, "links_total": 10, "tickets": 1, "links_affected": 1,
            "recurrent_links": 0, "tickets_over_24h": 0, "eligible_tickets": 3,
            "resolution_tickets": 3, "avg_resolution_hours": 2,
            "over_24h_rate": 0,
            "tickets_per_100_links": 10, "recurrent_rate": 0,
            "backlog_per_100_links": 0, "backlog_current": 0,
        }
        high_pressure = {
            "id": 2, "links_total": 100, "tickets": 100, "links_affected": 20,
            "recurrent_links": 20, "tickets_over_24h": 3, "eligible_tickets": 3,
            "resolution_tickets": 3, "avg_resolution_hours": 48,
            "over_24h_rate": 1,
            "tickets_per_100_links": 100, "recurrent_rate": 1,
            "backlog_per_100_links": 10, "backlog_current": 10,
        }
        undersized = {
            "id": 3, "links_total": 2, "tickets": 3, "links_affected": 1,
            "recurrent_links": 0, "tickets_over_24h": 1, "eligible_tickets": 3,
            "resolution_tickets": 3, "avg_resolution_hours": 48,
            "over_24h_rate": 1 / 3,
            "tickets_per_100_links": 150, "recurrent_rate": 0,
            "backlog_per_100_links": 0, "backlog_current": 0,
        }
        undated = {
            "id": 4, "links_total": 12, "tickets": 5, "links_affected": 3,
            "recurrent_links": 1, "tickets_over_24h": 1, "eligible_tickets": 3,
            "resolution_tickets": 3, "avg_resolution_hours": 10,
            "over_24h_rate": 1 / 3,
            "tickets_per_100_links": 41.7, "recurrent_rate": 1 / 3,
            "backlog_per_100_links": 8.3, "backlog_current": 1,
            "created_date_missing_tickets": 1,
        }
        rows = [low_pressure, high_pressure, undersized, undated]
        _apply_scores(rows)
        self.assertEqual(low_pressure["operational_score"], 100)
        self.assertEqual(high_pressure["operational_score"], 0)
        self.assertIsNone(undersized["operational_score"])
        self.assertFalse(undersized["best_eligible"])
        self.assertIsNone(undated["operational_score"])
        self.assertEqual(undated["operational_score_label"], "Dados insuficientes")
        self.assertIn("sem data de criação", undated["operational_score_reason"])
        self.assertEqual(sum(low_pressure["performance_components"].values()), low_pressure["operational_score"])

        lone_candidate = {**low_pressure, "id": 4}
        _apply_scores([lone_candidate])
        self.assertIsNone(lone_candidate["operational_score"])
        self.assertIn("dois provedores", lone_candidate["operational_score_reason"])

    def test_weight_sets_are_complete_and_scores_are_bounded(self):
        self.assertEqual(sum(RANKING_WEIGHTS.values()), 1)
        rows = [
            {"links_total": 50, "tickets": 0, "links_affected": 0, "recurrent_links": 0,
             "eligible_tickets": 0, "tickets_over_24h": 0, "backlog_current": 0},
            {"links_total": 1, "tickets": 4, "links_affected": 1, "recurrent_links": 1,
             "eligible_tickets": 4, "tickets_over_24h": 3, "backlog_current": 1},
            {"links_total": 267, "tickets": 28, "links_affected": 24, "recurrent_links": 4,
             "eligible_tickets": 28, "tickets_over_24h": 6, "backlog_current": 1},
            {"links_total": 267, "tickets": 2, "links_affected": 2, "recurrent_links": 0,
             "eligible_tickets": 2, "tickets_over_24h": 0, "backlog_current": 0},
        ]
        _apply_scores(rows)
        self.assertEqual(rows[0]["risk_score"], 0)
        self.assertEqual(rows[0]["risk_label"], "Sem ocorrências")
        self.assertGreaterEqual(rows[1]["risk_score"], 60)
        self.assertGreater(rows[2]["impact_noc_score"], rows[3]["impact_noc_score"])
        self.assertLess(rows[3]["risk_score"], 20)
        self.assertEqual(rows[2]["risk_components"]["impact_noc"], rows[2]["impact_noc_score"])

    def test_recurrence_aging_and_backlog_each_increase_their_component(self):
        recurrent = {"links_total": 1, "tickets": 5, "links_affected": 1, "recurrent_links": 1,
                     "eligible_tickets": 5, "tickets_over_24h": 4, "backlog_current": 1}
        _apply_scores([recurrent])
        self.assertEqual(recurrent["recurrent_score"], 20)
        self.assertEqual(recurrent["over_24h_score"], 16)

        without_backlog = {"links_total": 10, "tickets": 2, "links_affected": 1, "recurrent_links": 0,
                           "eligible_tickets": 2, "tickets_over_24h": 0, "backlog_current": 0}
        with_backlog = {**without_backlog, "backlog_current": 1}
        _apply_scores([without_backlog, with_backlog])
        self.assertGreater(with_backlog["backlog_score"], without_backlog["backlog_score"])
        self.assertGreater(with_backlog["risk_score"], without_backlog["risk_score"])

    def test_zero_ticket_provider_is_excluded_from_best_but_available_in_stable_view(self):
        rows = [
            {"links_total": 50, "tickets": 0, "links_affected": 0, "recurrent_links": 0,
             "eligible_tickets": 0, "tickets_over_24h": 0, "backlog_current": 0},
            {"links_total": 2, "tickets": 1, "links_affected": 1, "recurrent_links": 0,
             "eligible_tickets": 1, "tickets_over_24h": 0, "backlog_current": 0},
        ]
        _apply_scores(rows)
        self.assertFalse(rows[0]["risk_eligible"])
        self.assertFalse(rows[0]["best_eligible"])
        self.assertTrue(rows[0]["no_occurrence"])
        self.assertTrue(rows[1]["risk_eligible"])
        self.assertFalse(rows[1]["best_eligible"])

    def test_drawer_and_table_explain_comparative_score_and_separate_24h_metrics(self):
        root = Path(__file__).resolve().parents[2]
        script = (root.parent / "frontend_src/js/rankings/rankings.js").read_text(encoding="utf-8")
        template = (root / "templates/rankings/index.html").read_text(encoding="utf-8")
        for label in ("Resolucoes >24h", "Tempo medio de resolucao", "Incidencia / 100 links", "Backlog atual", "Percentil comparativo"):
            self.assertIn(label, script + template)
        self.assertIn("Backlog atual >24h", script + template)
        self.assertNotIn("Tickets / 100", template)
        self.assertNotIn("Impacto no NOC", script)
        for legacy in ("Affected Rate", "Recurrent Rate", "Over 24h Rate", "Criticality Rate"):
            self.assertNotIn(legacy, script + template)


class CockpitRankingPresentationTests(SimpleTestCase):
    def test_collapse_toggles_invert_state_and_uf_flags_are_local_assets(self):
        root = Path(__file__).resolve().parents[2]
        script = (root.parent / "frontend_src/js/painel/cockpit.js").read_text(encoding="utf-8")
        self.assertIn("setDetailCollapsed(key, !detailCollapseState[key])", script)
        self.assertIn("data-details-global", script)
        self.assertIn("aria-expanded", script)
        self.assertIn("/static/imagens/bandeiras/estados/${code.toLowerCase()}.svg", script)
        self.assertTrue((root / "static/imagens/bandeiras/estados/sp.svg").exists())


class CockpitOpsChartColorTests(SimpleTestCase):
    def test_cockpit_evolution_chart_colors_use_arion_orange_for_resolved_series(self):
        root = Path(__file__).resolve().parents[2]
        css = (root / "static/css/painel/cockpit.css").read_text(encoding="utf-8")
        # Ensure closed series use institutional var(--arion-orange)
        self.assertIn(".cockpit-ops-legend i.closed,.cockpit-ops-legend i.support-closed,.cockpit-ops-legend i.cancellation-closed { background:var(--arion-orange); }", css)
        self.assertIn(".cockpit-ops-bar.closed,.cockpit-ops-bar.support-closed { background:var(--arion-orange); }", css)
        self.assertIn("cancellation-closed { background:color-mix(in srgb,var(--arion-orange) 58%,var(--surface-primary)); border:1px solid var(--arion-orange); }", css)
        # Ensure accent-rose is no longer used for closed series
        self.assertNotIn("i.closed,.cockpit-ops-legend i.support-closed,.cockpit-ops-legend i.cancellation-closed { background:var(--accent-rose); }", css)
        self.assertNotIn(".cockpit-ops-bar.closed,.cockpit-ops-bar.support-closed { background:var(--accent-rose); }", css)
        # Ensure novos (green/teal) and backlog (blue) colors are preserved
        self.assertIn(".cockpit-ops-legend i.entries,.cockpit-ops-legend i.support-entry { background:var(--accent-teal); }", css)
        self.assertIn(".cockpit-ops-legend i.backlog-line-key { background:var(--accent-blue); }", css)


class RankingsDynamicSortingAndFilterTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from aplicativos.configuracoes.models import GroupDataScope
        from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror, OTRSTicket, OTRSTicketConfigItem

        self.user = get_user_model().objects.create_user(username="rankings-analyst", password="password123")
        group = Group.objects.get(name="Analista NOC")
        self.user.groups.set([group])
        scope, _ = GroupDataScope.objects.get_or_create(group=group)
        scope.mode = GroupDataScope.Mode.ALL
        scope.save()
        self.client.force_login(self.user)

        # Create providers with distinct metrics
        self.op_claro = CMDBOperatorMirror.objects.create(source_config_item_id="op-claro", name="Claro Telecom")
        self.op_fierce = CMDBOperatorMirror.objects.create(source_config_item_id="op-fierce", name="Fierce Provedor")
        self.op_desktop = CMDBOperatorMirror.objects.create(source_config_item_id="op-desktop", name="Desktop Internet")

        # Claro: 10 links, 2 tickets, 1 backlog, 1 aged (>24h), avg resolution 10h
        links_claro = [
            CMDBLinkMirror.objects.create(
                source_config_item_id=f"ci-claro-{i}", source_number=f"N-CLARO-{i}",
                name=f"Link Claro {i}", operator=self.op_claro, deployment_state="Operacional",
            )
            for i in range(10)
        ]
        now = datetime(2026, 10, 15, 12, tzinfo=datetime_timezone.utc)
        t1 = OTRSTicket.objects.create(
            external_id=101, ticket_number="T101", title="T101",
            queue_external_id=107, queue_name="NOC - ATACADO",
            external_state_id=101, external_state_name="fechado",
            business_status="CLOSED", is_business_open=False, is_present_in_queue=True,
            external_priority_id=2, priority_name="Médio",
            source_created_at=now - timedelta(hours=10),
            terminal_state_at=now, resolved_at=now,
        )
        OTRSTicketConfigItem.objects.create(
            ticket=t1, config_item_id=links_claro[0].source_config_item_id, cmdb_link=links_claro[0],
            relation_type="DependsOn", relation_state="Valid",
        )
        t2 = OTRSTicket.objects.create(
            external_id=102, ticket_number="T102", title="T102",
            queue_external_id=107, queue_name="NOC - ATACADO",
            external_state_id=102, external_state_name="em atendimento",
            business_status="OPEN", is_business_open=True, is_present_in_queue=True,
            external_priority_id=2, priority_name="Médio",
            source_created_at=now - timedelta(hours=30),
        )
        OTRSTicketConfigItem.objects.create(
            ticket=t2, config_item_id=links_claro[1].source_config_item_id, cmdb_link=links_claro[1],
            relation_type="DependsOn", relation_state="Valid",
        )

        # Fierce: 2 links, 4 tickets, 0 backlog, 0 aged, avg resolution 40h (higher tickets/100 links = 200 vs 20)
        links_fierce = [
            CMDBLinkMirror.objects.create(
                source_config_item_id=f"ci-fierce-{i}", source_number=f"N-FIERCE-{i}",
                name=f"Link Fierce {i}", operator=self.op_fierce, deployment_state="Operacional",
            )
            for i in range(2)
        ]
        for i in range(4):
            t = OTRSTicket.objects.create(
                external_id=200 + i, ticket_number=f"T20{i}", title=f"T20{i}",
                queue_external_id=107, queue_name="NOC - ATACADO",
                external_state_id=200 + i, external_state_name="fechado",
                business_status="CLOSED", is_business_open=False, is_present_in_queue=True,
                external_priority_id=2, priority_name="Médio",
                source_created_at=now - timedelta(hours=40),
                terminal_state_at=now, resolved_at=now,
            )
            OTRSTicketConfigItem.objects.create(
                ticket=t, config_item_id=links_fierce[0].source_config_item_id, cmdb_link=links_fierce[0],
                relation_type="DependsOn", relation_state="Valid",
            )

        # Desktop: 5 links, 0 tickets (avg resolution is None / "—")
        for i in range(5):
            CMDBLinkMirror.objects.create(
                source_config_item_id=f"ci-desktop-{i}", source_number=f"N-DESKTOP-{i}",
                name=f"Link Desktop {i}", operator=self.op_desktop, deployment_state="Operacional",
            )

    def names(self, **params):
        base = {"view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24"}
        resp = self.client.get(reverse("rankings:index"), {**base, **params})
        self.assertEqual(resp.status_code, 200)
        return resp, [row["name"] for row in resp.context["page_obj"]]

    def test_links_tab_keeps_primary_order_and_recomputes_positions(self):
        resp, names = self.names()
        self.assertEqual(names, ["Claro Telecom", "Desktop Internet", "Fierce Provedor"])
        self.assertEqual([r["ranking_position"] for r in resp.context["page_obj"]], [1, 2, 3])

    def test_multiple_filters_are_combined_with_and(self):
        # Fierce: 2 links, 4 tickets, 40h average. Claro: 10 links, 1 backlog, 10h average.
        self.assertEqual(self.names(tickets_min="1", avg_time_min="24")[1], ["Fierce Provedor"])
        self.assertEqual(self.names(avg_time_min="5", backlog_min="1")[1], ["Claro Telecom"])
        self.assertEqual(self.names(links_min="3", avg_time_min="5")[1], ["Claro Telecom"])
        self.assertEqual(self.names(links_min="3", avg_time_min="24")[1], [])
        self.assertEqual(self.names(incidence_min="100", tickets_min="4")[1], ["Fierce Provedor"])

    def test_min_and_max_ranges(self):
        self.assertEqual(self.names(links_min="3", links_max="6")[1], ["Desktop Internet"])
        self.assertEqual(self.names(links_max="2")[1], ["Fierce Provedor"])
        self.assertEqual(self.names(tickets_min="2", tickets_max="3")[1], ["Claro Telecom"])
        self.assertEqual(self.names(backlog_max="0")[1], ["Desktop Internet", "Fierce Provedor"])

    def test_average_time_filter_is_numeric_and_excludes_missing_values(self):
        # 10 < 40 numerically; string comparison would wrongly rank "10" before/after "9".
        self.assertEqual(self.names(avg_time_min="9", avg_time_max="12")[1], ["Claro Telecom"])
        self.assertEqual(self.names(avg_time_min="9.5")[1], ["Claro Telecom", "Fierce Provedor"])
        # Desktop has no resolution data: excluded by any active time filter, kept otherwise.
        self.assertNotIn("Desktop Internet", self.names(avg_time_min="0")[1])
        self.assertNotIn("Desktop Internet", self.names(avg_time_max="1000")[1])
        self.assertIn("Desktop Internet", self.names()[1])

    def test_search_and_situation_combine_with_numeric_ranges(self):
        self.assertEqual(self.names(search="claro", situation="with_backlog", links_min="5")[1], ["Claro Telecom"])
        self.assertEqual(self.names(search="claro", backlog_min="2")[1], [])
        self.assertEqual(self.names(situation="with_tickets", avg_time_min="24")[1], ["Fierce Provedor"])
        self.assertEqual(self.names(situation="without_tickets", links_min="1")[1], ["Desktop Internet"])

    def test_invalid_numeric_values_are_ignored(self):
        self.assertEqual(len(self.names(links_min="abc", tickets_max="-3")[1]), 3)

    def test_top_limit_is_applied_after_filters_and_positions_restart(self):
        from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror
        for i in range(12):
            extra = CMDBOperatorMirror.objects.create(source_config_item_id=f"op-extra-{i}", name=f"Extra {i:02d}")
            CMDBLinkMirror.objects.create(
                source_config_item_id=f"ci-extra-{i}", source_number=f"N-EXTRA-{i}",
                name=f"Link Extra {i}", operator=extra, deployment_state="Operacional",
            )
        resp, names = self.names(links_min="1", links_max="1", top="10")
        self.assertEqual(resp.context["page_obj"].paginator.count, 12)  # filtered first
        self.assertEqual(len(names), 10)  # Top 10 afterwards
        self.assertEqual([r["ranking_position"] for r in resp.context["page_obj"]], list(range(1, 11)))
        resp, names = self.names(avg_time_min="24", top="10")
        self.assertEqual(names, ["Fierce Provedor"])
        self.assertEqual(resp.context["page_obj"][0]["ranking_position"], 1)

    def test_tab_identity_chips_and_query_params_are_preserved(self):
        resp, _ = self.names(avg_time_min="24", links_min="2")
        self.assertContains(resp, "Mais links")
        self.assertNotContains(resp, "Ranking por Tempo médio")
        labels = [chip["label"] for chip in resp.context["active_table_filters"]]
        self.assertIn("Tempo médio · ≥ 24h", labels)
        self.assertIn("Links · ≥ 2", labels)
        chip = next(c for c in resp.context["active_table_filters"] if c["label"].startswith("Tempo médio"))
        self.assertNotIn("avg_time_min", chip["remove_url"])
        self.assertIn("links_min=2", chip["remove_url"])
        self.assertIn("avg_time_min=24", resp.context["export_pdf_url"])
        self.assertContains(resp, 'class="ranking-table-toolbar"')
        self.assertContains(resp, 'name="avg_time_min"')
        self.assertContains(resp, "Limpar filtros")
        for other in ("risk", "best", "stable"):
            other_resp = self.client.get(reverse("rankings:index"), {
                "view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24",
                "ranking_view": other, "avg_time_min": "24",
            })
            self.assertEqual(other_resp.status_code, 200)

    def test_export_uses_the_same_cross_filters(self):
        params = {"view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24", "avg_time_min": "24"}
        with patch("aplicativos.rankings.exports.build_xlsx", return_value=b"export") as build_export:
            response = self.client.get(reverse("rankings:export", args=["xlsx"]), params)
        self.assertEqual(response.status_code, 200)
        exported = build_export.call_args.args[2]
        self.assertEqual([row["name"] for row in exported], ["Fierce Provedor"])

    def test_build_metric_ranges_from_real_distribution(self):
        from .filter_ranges import build_metric_ranges
        links = build_metric_ranges([1, 2, 3, 4, 5, 6, 7, 8, 20], "links")
        self.assertEqual([o["label"] for o in links], ["Baixa exposição", "Média exposição", "Alta exposição", "Muito alta exposição"])
        self.assertEqual(links[0]["min"], 1)
        self.assertIsNone(links[-1]["max"])
        for previous, current in zip(links, links[1:]):  # integer bands never overlap or leave gaps
            self.assertEqual(current["min"], previous["max"] + 1)
        # Zero is its own option and level bands use positives only.
        backlog = build_metric_ranges([0, 0, 0, 3, 5, 7, 9, 11], "backlog")
        self.assertEqual([o["label"] for o in backlog[:2]], ["Sem backlog", "Com backlog"])
        self.assertEqual(backlog[0]["value"], "0:0")
        self.assertTrue(all(o["min"] >= 3 for o in backlog[2:]))
        # No variation: no useless bands.
        self.assertEqual([o["label"] for o in build_metric_ranges([0, 0, 0], "backlog")], ["Sem backlog"])
        self.assertEqual(build_metric_ranges([], "tickets"), [])
        # Resolution time uses operational bands, only those with data.
        times = build_metric_ranges([5, 30, 40, 100, None], "avg_time")
        self.assertEqual([o["label"] for o in times], ["Até 12h", "24–48h", "Acima de 72h"])

    def test_smart_select_options_and_cross_selection(self):
        resp, _ = self.names()
        fields = {f["prefix"]: f for f in resp.context["filter_fields"]}
        self.assertIn("Sem chamados", [o["label"] for o in fields["tickets"]["options"]])  # Desktop has 0
        self.assertEqual([o["label"] for o in fields["avg_time"]["options"]], ["Até 12h", "24–48h"])
        self.assertContains(resp, "Personalizado")
        self.assertContains(resp, "provedores encontrados")
        # Several smart selects at once, combined with AND.
        resp, names = self.names(tickets_range="1:", avg_time_range="24:48")
        self.assertEqual(names, ["Fierce Provedor"])
        labels = [c["label"] for c in resp.context["active_table_filters"]]
        self.assertIn("Tempo médio · 24–48h", labels)
        self.assertEqual(self.names(backlog_range="1:", avg_time_range=":12")[1], ["Claro Telecom"])
        self.assertEqual(self.names(tickets_range="0:0")[1], ["Desktop Internet"])
        resp, _ = self.names(avg_time_range="24:48")
        selected = {f["prefix"]: f["selected"] for f in resp.context["filter_fields"]}
        self.assertEqual(selected["avg_time"], "24:48")

    def test_custom_range_and_clearing_a_smart_select(self):
        self.assertEqual(self.names(avg_time_range="custom", avg_time_min="9", avg_time_max="12")[1], ["Claro Telecom"])
        resp, _ = self.names(avg_time_range="custom", avg_time_min="9", avg_time_max="12")
        selected = {f["prefix"]: f["selected"] for f in resp.context["filter_fields"]}
        self.assertEqual(selected["avg_time"], "custom")
        self.assertEqual(len(self.names(avg_time_range="", avg_time_min="24")[1]), 3)  # "Todos" clears

    def test_default_period_is_three_months_and_explicit_period_is_respected(self):
        resp = self.client.get(reverse("rankings:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["filters"]["view"], "3months")
        self.assertContains(resp, 'data-view="3months" aria-pressed="true"')
        for explicit in ("month", "6months", "12months"):
            resp = self.client.get(reverse("rankings:index"), {"view": explicit})
            self.assertEqual(resp.context["filters"]["view"], explicit)
        resp = self.client.get(reverse("rankings:index"), {"view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24"})
        self.assertEqual((resp.context["filters"]["date_from"], resp.context["filters"]["date_to"]), ("2026-10-01", "2026-10-24"))

    def test_direction_inverts_the_tab_metric_and_recomputes_positions(self):
        resp, desc = self.names(direction="desc")
        self.assertEqual(desc, ["Claro Telecom", "Desktop Internet", "Fierce Provedor"])
        self.assertEqual(self.names()[1], desc)  # default is desc
        resp, asc = self.names(direction="asc")
        self.assertEqual(asc, ["Fierce Provedor", "Desktop Internet", "Claro Telecom"])
        self.assertEqual([r["ranking_position"] for r in resp.context["page_obj"]], [1, 2, 3])
        self.assertEqual(self.names(direction="bogus")[1], desc)
        self.assertContains(resp, "Ordenado por Links na CMDB")
        self.assertContains(resp, "Menor → maior")
        self.assertIn("direction=asc", resp.context["direction_urls"]["asc"])

    def test_direction_works_together_with_filters_and_other_tabs(self):
        resp, names = self.names(direction="asc", tickets_min="1", avg_time_range="5:")
        self.assertEqual(names, ["Fierce Provedor", "Claro Telecom"])
        self.assertEqual(resp.context["page_obj"][0]["ranking_position"], 1)
        base = {"view": "custom", "date_from": "2026-10-01", "date_to": "2026-10-24", "ranking_view": "risk"}
        desc = [r["name"] for r in self.client.get(reverse("rankings:index"), {**base, "direction": "desc"}).context["page_obj"]]
        asc = [r["name"] for r in self.client.get(reverse("rankings:index"), {**base, "direction": "asc"}).context["page_obj"]]
        self.assertEqual(asc, desc[::-1])
