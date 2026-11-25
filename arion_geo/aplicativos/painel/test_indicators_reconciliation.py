from datetime import date, datetime, timedelta, timezone as dt_tz
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aplicativos.configuracoes.access_control import bootstrap_access
from aplicativos.integracoes.models import (
    CMDBLinkMirror,
    CMDBOperatorMirror,
    IntegrationSyncState,
    OTRSBacklogCheckpoint,
    OTRSBacklogHistory,
    OTRSBacklogInterval,
    OTRSTicket,
    OTRSTicketConfigItem,
)
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAME, NOC_HISTORY_SCOPE
from aplicativos.integracoes.services.otrs_snapshots import capture_hourly_backlog_checkpoint
from aplicativos.mapa.services.analytics import MapAnalyticsService
from aplicativos.mapa.services.presentation import presentation_payload
from aplicativos.painel.services.analytics import AnalyticsQueryService, DashboardAnalyticsService


class IndicatorsReconciliationTests(TestCase):
    def setUp(self):
        bootstrap_access()
        self.user = get_user_model().objects.create_superuser("ind_admin", "ind@arion.local", "StrongPass123!")
        self.isp = CMDBOperatorMirror.objects.create(source_config_item_id="ISP-IND-1", name="ISP TESTE")
        self.tz = ZoneInfo("America/Sao_Paulo")

    def _ticket(self, ext_id, number, title, created_local, *, status="RESOLVED", closing_code="RompimentoFibra",
                responsibility="INDETERMINADO", resolved_local=None):
        created_utc = created_local.replace(tzinfo=self.tz).astimezone(dt_tz.utc)
        resolved_utc = resolved_local.replace(tzinfo=self.tz).astimezone(dt_tz.utc) if resolved_local else (
            created_utc + timedelta(hours=2) if status == "RESOLVED" else None
        )
        return OTRSTicket.objects.create(
            external_id=ext_id,
            ticket_number=number,
            title=title,
            queue_external_id=1,
            queue_name=NOC_QUEUE_NAME,
            external_state_id=1,
            external_state_name="closed successful" if status == "RESOLVED" else status.lower(),
            business_status=status,
            is_business_open=status in {"OPEN", "PENDING", "SCHEDULED"},
            closing_code_raw=closing_code,
            responsibility_normalized=responsibility,
            source_created_at=created_utc,
            resolved_at=resolved_utc,
            terminal_state_at=resolved_utc,
            is_present_in_queue=True,
        )

    def test_geo_normal_and_presentation_match_even_with_unclassified_link_and_group_financial_safely(self):
        link_vivo = CMDBLinkMirror.objects.create(
            source_config_item_id="GEO-1",
            name="VIVO - BRADESCO - BL100001",
            designation="BL100001",
            contracting_client="Vivo",
            business_project="Bradesco",
            operator=self.isp,
            state="SP",
            city="São Paulo",
        )
        # Link still missing contracting_client in CMDB, identified via OTRS title
        link_unclassified = CMDBLinkMirror.objects.create(
            source_config_item_id="GEO-2",
            name="BRADESCO - BL100002",
            designation="BL100002",
            contracting_client="",
            business_project="Bradesco",
            operator=self.isp,
            state="RJ",
            city="Rio de Janeiro",
        )
        t1 = self._ticket(
            5001, "202610010001",
            "BRAD - TELEFONICA BRASIL S.A. - RESTAURAÇÃO - INTERNET - BRADESCO - BL100001",
            datetime(2026, 10, 5, 10, 0), closing_code="RompimentoFibra",
        )
        t2 = self._ticket(
            5002, "202610010002",
            "BRAD - TELEFONICA BRASIL S.A. - RESTAURAÇÃO - INTERNET - BRADESCO - BL100002",
            datetime(2026, 10, 6, 11, 0), closing_code="PendenciaFinanceira",
        )
        t3 = self._ticket(
            5003, "202610010003",
            "BRAD - TELEFONICA BRASIL S.A. - RESTAURAÇÃO - INTERNET - BRADESCO - BL100002",
            datetime(2026, 10, 12, 14, 0), closing_code="Outros",
        )
        OTRSTicketConfigItem.objects.create(ticket=t1, config_item_id="GEO-1", cmdb_link=link_vivo, relation_type="DependsOn", relation_state="Valid")
        OTRSTicketConfigItem.objects.create(ticket=t2, config_item_id="GEO-2", cmdb_link=link_unclassified, relation_type="DependsOn", relation_state="Valid")
        OTRSTicketConfigItem.objects.create(ticket=t3, config_item_id="GEO-2", cmdb_link=link_unclassified, relation_type="DependsOn", relation_state="Valid")

        params = {"client": "Vivo", "project": "BRADESCO", "period": "2026-10", "mode": "calls", "cohort": "created"}
        normal = MapAnalyticsService.query(self.user, params, fixed_client=False)
        pres = presentation_payload(self.user, params)

        self.assertEqual(normal["ticket_count"], 3)
        self.assertEqual(pres["ticket_count"], 3)
        self.assertEqual(normal["affected_link_count"], 2)
        self.assertEqual(pres["affected_link_count"], 2)
        self.assertEqual(normal["recurrence_link_count"], 1)
        self.assertEqual(pres["recurrence_link_count"], 1)
        self.assertIn("RJ", pres["allowed_ufs"])

        # Presentation groups financial + other non-technical into "Outros motivos" without simulated projections.
        self.assertNotIn("Pendência Financeira", pres["category_counts"])
        self.assertEqual(pres["category_counts"], {"Rompimento": 1, "Outros motivos": 2})
        self.assertEqual(sum(pres["category_counts"].values()), pres["ticket_count"])
        self.assertEqual(pres["distribution_projected"], {})

        # Presentation UF detail masks financial reason as "Outros motivos" and keeps all valid tickets.
        detail_rj = MapAnalyticsService.state_detail(
            self.user, {**params, "uf": "RJ", "tab": "tickets"},
            fixed_client=True, presentation=True, precomputed_state=pres["states"]["RJ"],
        )
        self.assertEqual(len(detail_rj["tickets"]), 2)
        reasons = {row["closing_reason"] for row in detail_rj["tickets"]}
        self.assertNotIn("Pendência Financeira", reasons)
        self.assertIn("Outros motivos", reasons)

    def test_dashboard_and_cockpit_monthly_created_counts_reconcile_by_ticket_id(self):
        link_a = CMDBLinkMirror.objects.create(source_config_item_id="DC-1", name="CLARO - CORREIOS - BD1", state="SP")
        link_b = CMDBLinkMirror.objects.create(source_config_item_id="DC-2", name="CLARO - CORREIOS - BD2", state="SP")

        # 1 technical resolved, 1 Sem Intervenção Técnica resolved, 1 Canceled, 1 Unknown (excluded)
        t_tech = self._ticket(6001, "202512010001", "Ticket Tech", datetime(2025, 12, 5, 9, 0), closing_code="RompimentoFibra")
        t_sem = self._ticket(6002, "202512010002", "Ticket Sem Interv", datetime(2025, 12, 10, 9, 0), closing_code="Sem Intervenção Técnica")
        t_canc = self._ticket(6003, "202512010003", "Ticket Cancelado", datetime(2025, 12, 12, 9, 0), status="CANCELED", closing_code="Cancelado")
        self._ticket(6004, "202512010004", "Ticket Unknown", datetime(2025, 12, 15, 9, 0), status="UNKNOWN", closing_code="")

        # Attach t_tech to TWO links to prove strict TicketID deduplication
        OTRSTicketConfigItem.objects.create(ticket=t_tech, config_item_id="DC-1", cmdb_link=link_a, relation_type="DependsOn", relation_state="Valid")
        OTRSTicketConfigItem.objects.create(ticket=t_tech, config_item_id="DC-2", cmdb_link=link_b, relation_type="DependsOn", relation_state="Valid")
        OTRSTicketConfigItem.objects.create(ticket=t_sem, config_item_id="DC-1", cmdb_link=link_a, relation_type="DependsOn", relation_state="Valid")
        OTRSTicketConfigItem.objects.create(ticket=t_canc, config_item_id="DC-1", cmdb_link=link_a, relation_type="DependsOn", relation_state="Valid")

        dash = DashboardAnalyticsService.build(user=self.user, start=date(2025, 12, 1), end=date(2025, 12, 31))
        cockpit = AnalyticsQueryService().query(
            {"scope": "support", "metric": "tickets", "start": "2025-12-01", "end": "2025-12-31"},
            user=self.user,
        )

        # Both count the exact 3 distinct created operational tickets (tech + sem intervencao + canceled)
        self.assertEqual(dash["calls"], 3)
        self.assertEqual(dash["monthly"][0]["calls"], 3)
        self.assertEqual(cockpit["operational"]["period"]["entries"], 3)
        self.assertEqual(sum(pt["entries"] for pt in cockpit["operational"]["series"]), 3)
        cockpit_multi = AnalyticsQueryService().query(
            {"scope": "support", "metric": "tickets", "start": "2025-12-01", "end": "2026-02-28"},
            user=self.user,
        )
        self.assertEqual(cockpit_multi["operational"]["series"][0]["entries"], 3)
        # Reincident links count only valid non-canceled occurrences: link_a has t_tech + t_sem = 2
        self.assertEqual(dash["repeat"], 1)

    def test_backlog_point_in_time_peak_and_idempotent_hourly_checkpoint(self):
        sync_time = datetime(2026, 11, 18, 12, 0, tzinfo=self.tz).astimezone(dt_tz.utc)
        IntegrationSyncState.objects.update_or_create(
            key="otrs",
            defaults={"last_success_at": sync_time},
        )
        # Current mirror has 1 open ticket created 80h ago
        self._ticket(
            7001, "202611150001", "Ticket Aberto Atual",
            datetime(2026, 11, 15, 4, 0), status="OPEN", closing_code="",
        )
        # Historical intervals: at 2026-11-18 07:00 BRT, 3 tickets were open; by 12:00 BRT only 1 remained!
        coverage = OTRSBacklogHistory.objects.create(
            queue_name=NOC_HISTORY_SCOPE,
            earliest_at=datetime(2026, 11, 1, 0, 0, tzinfo=self.tz).astimezone(dt_tz.utc),
            through=sync_time,
            ticket_count=3,
            event_count=6,
        )
        for idx, end_hour in ((7001, 12), (7002, 10), (7003, 9)):
            OTRSBacklogInterval.objects.create(
                coverage=coverage,
                ticket_external_id=idx,
                source_event_id=idx,
                source_created_at=datetime(2026, 11, 15, 4, 0, tzinfo=self.tz).astimezone(dt_tz.utc),
                started_at=datetime(2026, 11, 18, 6, 0, tzinfo=self.tz).astimezone(dt_tz.utc),
                ended_at=datetime(2026, 11, 18, end_hour, 0, tzinfo=self.tz).astimezone(dt_tz.utc),
                known=True,
                business_status="OPEN",
                external_state_name="open",
                responsibility="PROCEDENTE",
            )

        self.client.force_login(self.user)
        at_07 = self.client.get(reverse("analytics-backlog-at"), {"at": "2026-11-18T07:00"})
        self.assertEqual(at_07.status_code, 200)
        self.assertTrue(at_07.json()["covered"])
        self.assertEqual(at_07.json()["total"], 3)
        self.assertEqual(at_07.json()["over_72h"], 3)

        at_future = self.client.get(reverse("analytics-backlog-at"), {"at": "2026-11-19T07:00"})
        self.assertEqual(at_future.status_code, 200)
        self.assertFalse(at_future.json()["covered"])
        self.assertFalse(at_future.json()["available"])

        # Cockpit multi-day query reports both closing stock (1) and peak (3) for Nov 18
        cockpit = AnalyticsQueryService().query(
            {"scope": "support", "metric": "tickets", "start": "2026-11-17", "end": "2026-11-18"},
            user=self.user,
        )
        nov18 = next(pt for pt in cockpit["operational"]["series"] if pt["key"] == "2026-11-18")
        self.assertEqual(nov18["backlog_reconstructed"], 1)
        self.assertEqual(nov18["backlog_peak"], 3)
        current_kpi = next(k for k in cockpit["operational"]["secondary_kpis"] if k["label"] == "Backlog atual")
        self.assertEqual(current_kpi["value"], 1)
        self.assertIn("Atualizado em", current_kpi["unit"])

        # Hourly checkpoint capture is idempotent within the same hour slot
        first_cp = capture_hourly_backlog_checkpoint(captured_at=sync_time)
        second_cp = capture_hourly_backlog_checkpoint(captured_at=sync_time + timedelta(minutes=15))
        self.assertTrue(first_cp["created"])
        self.assertFalse(second_cp["created"])
        self.assertEqual(OTRSBacklogCheckpoint.objects.count(), 1)
        self.assertEqual(first_cp["support_backlog"], 1)
