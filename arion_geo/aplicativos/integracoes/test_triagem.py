from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone as utc
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase

from aplicativos.chamados.services import TicketQueueService, sanitize_queue_filters
from aplicativos.integracoes.models import (
    CMDBLinkMirror, Integration, OTRSBacklogHistory, OTRSQueueBackfill,
    OTRSTicket, OTRSTicketEvent,
)
from aplicativos.integracoes.otrs_queues import (
    COCKPIT_QUEUE_NAMES, NOC_QUEUE_NAME, TRIAGE_QUEUE_NAME,
)
from aplicativos.integracoes.services.analytics import TicketQueryService
from aplicativos.integracoes.services.otrs_backlog_history import reconstruct_ticket
from aplicativos.integracoes.services.otrs_snapshots import capture_hourly_backlog_checkpoint
from aplicativos.integracoes.services.otrs_sync import OTRSSyncService
from aplicativos.integracoes.test_mirror_sync import FakeOTRSReadOnlyClient
from aplicativos.painel.services.analytics import AnalyticsQueryService, DashboardAnalyticsService
from aplicativos.qualidade.services.ticket_validity import quality_ticket_sets


START = datetime(2026, 11, 1, 12, tzinfo=utc.utc)
END = datetime(2026, 12, 1, tzinfo=utc.utc)


class LifecycleClient(FakeOTRSReadOnlyClient):
    def __init__(self):
        super().__init__(states=[{"id": 10, "name": "em atendimento", "type_id": 1},
                                 {"id": 20, "name": "resolvido", "type_id": 3},
                                 {"id": 30, "name": "cancelado", "type_id": 3}])
        self.events = []
        self.ticket.update(queue_id=181, create_time=START, change_time=START.replace(tzinfo=None))
        self.ticket["title"] = "BRAD - TELEFONICA BRASIL S.A. - RESTAURAÇÃO - INTERNET - BRADESCO - BL100001"
        self.event(1, 181, 10, "NewTicket", START)

    def event(self, pk, queue, state, kind, at):
        self.events.append({"id": pk, "ticket_id": 100, "queue_id": queue,
            "state_id": state, "history_type_id": {"NewTicket": 1, "Move": 16, "StateUpdate": 27, "Merged": 33}[kind],
            "create_time": at})

    def get_all_columns(self, database=None):
        return super().get_all_columns(database) + [
            {"TABLE_NAME": "ticket_history", "COLUMN_NAME": col} for col in ("id", "queue_id")]

    def read_only_snapshot(self):
        return nullcontext()

    def execute_select(self, sql, params=()):
        if "FROM (SELECT ticket_id" in sql:
            return [{"id": row["id"]} for row in self.ticket_rows if row["id"] > params[1]][:params[-1]]
        if "from `ticket_history_type`" in sql.casefold():
            return [{"id": 1, "name": "NewTicket"}, {"id": 16, "name": "Move"},
                    {"id": 27, "name": "StateUpdate"}, {"id": 33, "name": "Merged"}] if " = %s" not in sql else [{"id": 27, "name": "StateUpdate"}]
        if "from `ticket_history`" in sql.casefold() and "from `ticket`" not in sql.casefold() and "MAX(" not in sql:
            return [dict(event) for event in self.events]
        if "FROM `ticket` WHERE `id` IN" in sql:
            return [dict(row) for row in self.ticket_rows if row["id"] in params]
        if "FROM `ticket` WHERE" in sql and "SELECT `ticket_id`" in sql:
            history_ids = {e["ticket_id"] for e in self.events if e["queue_id"] == 181}
            candidates = [row for row in self.ticket_rows if row["queue_id"] in params or row["id"] in history_ids]
            if " >= %s" in sql:
                candidates = [row for row in candidates if row["change_time"] >= params[-1]]
            return [dict(row) for row in candidates]
        return super().execute_select(sql, params)


class TriageIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("triagem", password="test-password")
        cls.link = CMDBLinkMirror.objects.create(source_config_item_id="ci-1", designation="BL100001", name="VIVO - BRADESCO - BL100001",
            contracting_client="Vivo", business_project="Bradesco", state="SP")

    def setUp(self):
        self.source = LifecycleClient()

    def sync(self, **kwargs):
        return OTRSSyncService(self.source).run(queue_names=COCKPIT_QUEUE_NAMES, apply=True, **kwargs)

    def resolve(self, queue=181):
        resolved = START + timedelta(hours=3)
        self.source.ticket.update(queue_id=queue, state_id=20)
        self.source.dynamic_values = [{"object_id": 100, "field_id": 27, "value_date": resolved}]
        self.source.history_rows = [{"ticket_id": 100, "state_id": 20, "terminal_at": resolved}]
        self.source.event(3, queue, 20, "StateUpdate", resolved)

    def query(self, metric="tickets"):
        return AnalyticsQueryService().query({"scope": "support", "metric": metric,
            "start": "2026-11-01", "end": "2026-11-30",
            "include_cancelled": True, "include_improcedente": True}, user=self.user)

    def test_open_triage_is_in_queue_dashboard_cockpit_and_backlog(self):
        self.sync()
        queue = TicketQueueService.build(self.user, {})
        self.assertEqual(queue["active_count"], 1)
        self.assertNotIn("attendance_level", queue["rows"][0])
        dashboard = DashboardAnalyticsService.build(user=self.user, start=date(2026, 11, 1), end=date(2026, 11, 30))
        self.assertEqual(dashboard["backlog_current"], 1)
        self.assertEqual(dashboard["calls"], 1)
        cockpit = AnalyticsQueryService().query({"scope": "support", "metric": "tickets",
            "start": "2026-11-01", "end": "2026-11-30"}, user=self.user)
        self.assertEqual(cockpit["value"], dashboard["calls"])
        self.assertEqual(cockpit["operational"]["current"]["total"], dashboard["backlog_current"])
        self.assertNotIn("attendance", cockpit["operational"])
        self.assertNotIn("attendance", dashboard)

    def test_resolved_only_in_triage_remains_in_created_resolution_and_quality(self):
        self.resolve()
        self.sync()
        self.assertEqual(TicketQueryService.business_open(self.user).count(), 0)
        self.assertEqual(TicketQueryService.base(self.user).count(), 1)
        self.assertEqual(self.query("resolved_tickets")["value"], 1)
        self.assertEqual(self.query()["value"], 1)
        self.assertEqual(quality_ticket_sets(self.user)["valid_tickets"].count(), 1)
        self.assertEqual(TicketQueueService.build(self.user, {})["active_count"], 0)

    def test_transfer_resolution_and_return_keep_one_identity_and_one_incident(self):
        self.sync()
        pk = OTRSTicket.objects.get().pk
        self.source.event(2, 107, 10, "Move", START + timedelta(hours=1))
        self.resolve(queue=107)
        self.sync()
        self.sync()
        self.assertEqual(OTRSTicket.objects.get().pk, pk)
        self.assertEqual(OTRSTicketEvent.objects.count(), 3)
        self.assertEqual(self.query()["value"], 1)
        self.assertEqual(self.query("resolved_tickets")["value"], 1)
        self.assertEqual(quality_ticket_sets(self.user)["valid_tickets"].count(), 1)
        self.source.ticket.update(queue_id=181, state_id=10)
        self.source.event(4, 181, 10, "Move", START + timedelta(hours=4))
        self.sync()
        self.assertEqual(TicketQueryService.business_open(self.user).count(), 1)
        self.assertEqual(self.query("resolved_tickets")["value"], 0)
        self.source.ticket.update(state_id=30)
        self.source.event(5, 181, 30, "StateUpdate", START + timedelta(hours=5))
        self.sync()
        self.assertEqual(TicketQueryService.business_open(self.user).count(), 0)
        self.assertEqual(OTRSTicket.objects.count(), 1)

    def test_resolution_uses_canonical_date_without_requiring_level_evidence(self):
        self.resolve()
        self.source.events = self.source.events[:1]
        self.sync()
        self.assertEqual(self.query("resolved_tickets")["value"], 1)

    def test_explicit_historical_batch_keeps_outside_ticket_without_reconciling_others(self):
        self.source.ticket.update(queue_id=222)
        self.sync(ticket_ids=[100])
        ticket = OTRSTicket.objects.get()
        self.assertTrue(ticket.has_support_history)
        self.assertFalse(ticket.is_present_in_queue)
        self.assertEqual(TicketQueryService.base(self.user).count(), 1)
        self.assertEqual(TicketQueryService.business_open(self.user).count(), 0)
        self.assertEqual(DashboardAnalyticsService.build(user=self.user,
            start=date(2026, 11, 1), end=date(2026, 11, 30))["backlog_current"], 0)

    def test_full_discovers_a_brief_historical_passage_even_outside_today(self):
        self.source.ticket.update(queue_id=222)
        self.sync()
        self.assertTrue(OTRSTicket.objects.get().has_support_history)
        self.assertEqual(TicketQueryService.business_open(self.user).count(), 0)

    def test_ticket_moved_to_cancellation_keeps_history_but_leaves_support_backlog(self):
        self.sync()
        self.source.ticket["queue_id"] = 108
        self.source.event(2, 108, 10, "Move", START + timedelta(hours=1))
        self.sync()
        self.assertEqual(TicketQueryService.base(self.user).count(), 1)
        self.assertEqual(TicketQueryService.business_open(self.user).count(), 0)
        cockpit = AnalyticsQueryService().query({"scope": "support", "metric": "backlog",
            "start": "2026-11-01", "end": "2026-11-30"}, user=self.user)
        self.assertEqual(cockpit["operational"]["current"]["total"], 0)
        self.assertEqual(cockpit["value"], 0)

    def test_geo_normal_and_presentation_count_triage_without_changing_exclusions(self):
        from aplicativos.mapa.services.analytics import MapAnalyticsService
        from aplicativos.mapa.services.presentation import presentation_payload
        self.resolve()
        self.sync()
        params = {"client": "Vivo", "project": "BRADESCO", "period": "2026-11", "mode": "calls", "cohort": "created"}
        normal = MapAnalyticsService.query(self.user, params, fixed_client=False)
        presentation = presentation_payload(self.user, params)
        self.assertEqual(normal["ticket_count"], 1)
        self.assertEqual(presentation["ticket_count"], 1)
        self.assertEqual(normal["affected_link_count"], 1)

    def test_read_only_session_preserves_local_validation_errors(self):
        from unittest.mock import MagicMock
        from aplicativos.integracoes.clients.otrs import OTRSReadOnlyClient
        source = OTRSReadOnlyClient(host="127.0.0.1", database="test", username="test", password="test",
            connection_factory=MagicMock())
        with self.assertRaisesMessage(ValueError, "synthetic validation"):
            with source.read_only_session():
                raise ValueError("synthetic validation")

    def test_queue_filter_is_ignored_and_data_scope_stays_on_backend(self):
        self.sync()
        self.assertEqual(TicketQueueService.build(self.user, {"queue": NOC_QUEUE_NAME})["active_count"], 1)
        self.assertEqual(TicketQueueService.build(self.user, {"queue": TRIAGE_QUEUE_NAME})["active_count"], 1)
        limited = get_user_model().objects.create_user("limited")
        self.assertEqual(TicketQueryService.base(limited).count(), 0)
        self.assertNotIn("queue", sanitize_queue_filters({"queue": "OUTSIDE QUEUE"}))

    def test_current_checkpoint_counts_both_queues_once(self):
        self.sync()
        result = capture_hourly_backlog_checkpoint(captured_at=START)
        self.assertEqual(result["support_backlog"], 1)
        self.assertFalse(capture_hourly_backlog_checkpoint(captured_at=START)["created"])

    def test_single_queue_history_does_not_imply_consolidated_coverage(self):
        from aplicativos.painel.services.backlog_history import reconstructed_backlog_at
        OTRSBacklogHistory.objects.create(queue_name=NOC_QUEUE_NAME, earliest_at=START, through=END)
        result = reconstructed_backlog_at(user=self.user, links=CMDBLinkMirror.objects.all(), filters={},
            target_dt=START + timedelta(days=1), apply_filters=AnalyticsQueryService._apply_filters)
        self.assertFalse(result["covered"])

    def test_pages_keep_the_approved_interface_without_level_controls(self):
        self.sync()
        self.client.force_login(self.user)
        dashboard = self.client.get("/dashboard/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, "dashboard-attendance")
        self.assertNotContains(dashboard, "Atendimento N1")
        queue = self.client.get("/chamados/")
        self.assertEqual(queue.status_code, 200)
        self.assertNotContains(queue, 'name="queue"')
        self.assertNotContains(queue, "Fila de atendimento")

    def test_reconstruction_tracks_triage_and_atacado_intervals_without_overlap(self):
        events = [{"id": e["id"], "at": e["create_time"], "queue_id": e["queue_id"],
            "queue_name": TRIAGE_QUEUE_NAME, "state_id": 10, "status": "OPEN"} for e in self.source.events]
        events.append({"id": 2, "at": START + timedelta(hours=1), "queue_id": 107,
            "queue_name": NOC_QUEUE_NAME, "state_id": 10, "status": "OPEN"})
        rows = reconstruct_ticket({"id": 100, "created_at": START, "queue_id": 107, "state_id": 10},
            events, queue_id=(107,181), through=END)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ended_at"], rows[1]["started_at"])
        self.assertEqual([r["queue_name"] for r in rows], [TRIAGE_QUEUE_NAME, NOC_QUEUE_NAME])

    def test_backfill_resumes_idempotently_and_failed_batch_does_not_advance(self):
        Integration.objects.create(key=Integration.Key.OTRS, name="OTRS", enabled=True)
        module = "aplicativos.integracoes.management.commands.sincronizar_historico_triagem"
        with patch(module + ".configured_otrs_client", return_value=self.source), patch(module + ".otrs_configuration_notice", return_value=""):
            call_command("sincronizar_historico_triagem", apply=True, batch_size=1, max_batches=1, stdout=StringIO())
            self.assertEqual(OTRSQueueBackfill.objects.get().last_ticket_id, 100)
            call_command("sincronizar_historico_triagem", apply=True, stdout=StringIO())
            self.assertIsNotNone(OTRSQueueBackfill.objects.get().completed_at)
            self.assertEqual(OTRSTicket.objects.count(), 1)
            self.source.ticket["id"] = 101
            with patch.object(OTRSSyncService, "run", side_effect=ValueError("synthetic")):
                with self.assertRaises(CommandError):
                    call_command("sincronizar_historico_triagem", apply=True, stdout=StringIO())
            self.assertEqual(OTRSQueueBackfill.objects.get().last_ticket_id, 100)
