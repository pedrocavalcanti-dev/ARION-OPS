from datetime import date, datetime, timedelta, timezone as utc
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import OTRSBacklogHistory, OTRSBacklogInterval, OTRSTicket
from aplicativos.integracoes.services.analytics import LinkQueryService
from aplicativos.integracoes.services.otrs_backlog_history import reconstruct_ticket
from .services.analytics import AnalyticsQueryService, _operational_buckets
from .services.backlog_history import reconstructed_backlog


def instant(month, day=1):
    return datetime(2026, month, day, 12, tzinfo=utc.utc)


def ticket(pk=1, state=2, queue=107, created=None):
    return {"id": pk, "created_at": created or instant(1), "queue_id": queue,
            "state_id": state, "responsibility": "INDETERMINADO"}


def event(pk, at, status="OPEN", state=1, queue=107, change=None):
    result = {"id": pk, "at": at, "status": status, "state_id": state,
              "state_name": status, "queue_id": queue}
    if change is not None:
        result["classification"] = change
    return result


class LifecycleTests(SimpleTestCase):
    def test_source_coverage_clock_is_utc_even_with_local_application_timezone(self):
        from aplicativos.integracoes.services.otrs_backlog_history import coverage_datetime
        self.assertEqual(coverage_datetime(datetime(2026, 11, 10, 12)),
                         datetime(2026, 11, 10, 12, tzinfo=utc.utc))

    def test_read_only_snapshot_preserves_validation_errors_and_rolls_back(self):
        from aplicativos.integracoes.clients.otrs import OTRSReadOnlyClient
        connection = MagicMock()
        client = OTRSReadOnlyClient(host="127.0.0.1", database="synthetic", username="test",
                                    password="synthetic", connection_factory=lambda: connection)
        with self.assertRaisesMessage(ValueError, "synthetic validation"):
            with client.read_only_snapshot():
                raise ValueError("synthetic validation")
        connection.rollback.assert_called_once()
        commands = [args[0] for args, _kwargs in connection.cursor.return_value.__enter__.return_value.execute.call_args_list]
        self.assertIn("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY", commands)

    def test_cross_month_creation_resolution_and_still_open(self):
        rows = reconstruct_ticket(ticket(), [event(1, instant(1)), event(2, instant(2), "RESOLVED", 2)],
                                  queue_id=107, through=instant(3))
        self.assertEqual([(r["started_at"], r["ended_at"]) for r in rows], [(instant(1), instant(2))])
        opened = reconstruct_ticket(ticket(state=1), [event(1, instant(1))], queue_id=107, through=instant(3))
        self.assertEqual(opened[-1]["ended_at"], instant(3))

    def test_reopened_ticket_keeps_both_actual_open_intervals_and_deduplicates(self):
        events = [event(1, instant(1)), event(2, instant(1, 10), "RESOLVED", 2),
                  event(3, instant(2), "PENDING", 3)]
        rows = reconstruct_ticket(ticket(state=3), events + [events[0]], queue_id=107, through=instant(3))
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["business_status"] for r in rows], ["OPEN", "PENDING"])
        self.assertEqual([r["started_at"] for r in rows], [instant(1), instant(2)])

    def test_cancel_scheduled_unknown_and_queue_exit_are_not_backlog(self):
        for status in ("CANCELED", "SCHEDULED", "UNKNOWN"):
            with self.subTest(status=status):
                rows = reconstruct_ticket(ticket(), [event(1, instant(1)), event(2, instant(2), status, 2)],
                                          queue_id=107, through=instant(3))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["ended_at"], instant(2))
        rows = reconstruct_ticket(ticket(state=1, queue=999),
                                  [event(1, instant(1)), event(2, instant(2), queue=999)],
                                  queue_id=107, through=instant(3))
        self.assertEqual(rows[0]["ended_at"], instant(2))
        self.assertEqual(len(rows), 1)

    def test_procedence_is_evaluated_at_each_transition_not_current_value(self):
        current = {**ticket(state=1), "responsibility": "IMPROCEDENTE"}
        rows = reconstruct_ticket(current, [event(1, instant(1)), event(2, instant(2),
                                  change=("INDETERMINADO", "IMPROCEDENTE"))], queue_id=107, through=instant(3))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ended_at"], instant(2))

    def test_missing_initial_evidence_and_unexplained_current_state_remain_unknown(self):
        rows = reconstruct_ticket(ticket(), [event(1, instant(2))], queue_id=107, through=instant(3))
        self.assertFalse(rows[0]["known"])
        self.assertEqual(rows[0]["started_at"], instant(1))
        self.assertFalse(rows[-1]["known"])


class HistoricalQueryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("backlog-test", password="synthetic-test-password")
        self.coverage = OTRSBacklogHistory.objects.create(queue_name="NOC - ATACADO",
            earliest_at=instant(1), through=instant(3, 2), ticket_count=2)
        self.persist(ticket(), [event(1, instant(1)), event(2, instant(2), "RESOLVED", 2)])
        self.persist(ticket(pk=2, state=1), [event(3, instant(1, 15))])

    def persist(self, current, events):
        for row in reconstruct_ticket(current, events, queue_id=107, through=self.coverage.through):
            OTRSBacklogInterval.objects.create(coverage=self.coverage, **row)

    def query(self, start, end, filters=None, user=None):
        from .services.analytics import _date_boundaries
        user = user or self.user
        _, buckets = _operational_buckets(start, end)
        result = reconstructed_backlog(user=user, links=LinkQueryService.base(user), filters=filters or {},
            buckets=buckets, period_end=_date_boundaries(start, end)[1], apply_filters=AnalyticsQueryService._apply_filters)
        return result

    def test_monthly_stock_has_nonzero_baseline_and_only_real_closing_values(self):
        result = self.query(date(2026, 1, 1), date(2026, 3, 31))
        self.assertEqual([p["value"] for p in result["points"].values()], [2, 1, 1])
        self.assertEqual(result["point_count"], 3)

    def test_daily_history_does_not_use_the_current_ticket_status(self):
        OTRSTicket.objects.create(external_id=1, ticket_number="synthetic-1", queue_external_id=107,
            queue_name="NOC - ATACADO", external_state_id=2, external_state_name="RESOLVED", business_status="RESOLVED")
        result = self.query(date(2026, 1, 20), date(2026, 1, 22), {"status": "OPEN"})
        self.assertEqual([p["value"] for p in result["points"].values()], [2, 2, 2])

    def test_partial_history_is_null_not_zero_and_future_buckets_are_absent(self):
        OTRSBacklogInterval.objects.create(coverage=self.coverage, ticket_external_id=3,
            source_created_at=instant(1), started_at=instant(1), ended_at=instant(2), known=False)
        result = self.query(date(2025, 12, 1), date(2026, 4, 1))
        self.assertNotIn("2025-12", result["points"])
        self.assertIsNone(result["points"]["2026-01"]["value"])
        self.assertEqual(result["points"]["2026-02"]["value"], 1)
        self.assertNotIn("2026-04", result["points"])

    def test_data_scope_without_authorized_links_does_not_expose_global_history(self):
        user = get_user_model().objects.create_user("backlog-restricted")
        group = Group.objects.create(name="Backlog synthetic restricted")
        GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.CUSTOM)
        user.groups.add(group)
        result = self.query(date(2026, 1, 20), date(2026, 1, 22), user=user)
        self.assertEqual([p["value"] for p in result["points"].values()], [0, 0, 0])
        self.assertNotIn("ticket_count", result)

    def test_improper_or_wrong_queue_filters_do_not_increase_stock(self):
        for filters in ({"responsibility": "IMPROCEDENTE"}, {"queue": "OTHER"}, {"status": "CANCELED"}):
            result = self.query(date(2026, 1, 20), date(2026, 1, 22), filters)
            self.assertEqual([p["value"] for p in result["points"].values()], [0, 0, 0])

    def test_authorized_client_and_project_intersect_and_multiple_links_count_once(self):
        from aplicativos.base_links.models import Operadora, Projeto
        from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror, OTRSTicketConfigItem
        user = get_user_model().objects.create_user("history-scope-client")
        group = Group.objects.create(name="History synthetic scope")
        scope = GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.add(Operadora.objects.create(name="Client A", normalized_name="client-a"))
        scope.allowed_projects.add(Projeto.objects.create(name="Project A", normalized_name="project-a"))
        user.groups.add(group)
        provider = CMDBOperatorMirror.objects.create(source_config_item_id="hist-provider", name="Provider A")
        for pk, project in ((1, "Project A"), (2, "Project B")):
            mirror = OTRSTicket.objects.create(external_id=pk, ticket_number=f"hist-{pk}", queue_external_id=999,
                queue_name="OTHER", external_state_id=2, external_state_name="RESOLVED", business_status="RESOLVED",
                is_present_in_queue=False)
            for suffix in ("a", "b"):
                link = CMDBLinkMirror.objects.create(source_config_item_id=f"hist-{pk}-{suffix}", operator=provider,
                    contracting_client="Client A", business_project=project, state="SP")
                OTRSTicketConfigItem.objects.create(ticket=mirror, config_item_id=link.source_config_item_id, cmdb_link=link)
        allowed = self.query(date(2026, 1, 20), date(2026, 1, 22), {"project": "Project A"}, user=user)
        denied = self.query(date(2026, 1, 20), date(2026, 1, 22), {"project": "Project B"}, user=user)
        self.assertEqual([p["value"] for p in allowed["points"].values()], [1, 1, 1])
        self.assertEqual([p["value"] for p in denied["points"].values()], [0, 0, 0])

    def test_publishing_history_is_idempotent_and_does_not_need_full_snapshots(self):
        from aplicativos.integracoes.services.otrs_backlog_history import sync_history
        intervals = list(self.coverage.intervals.values("ticket_external_id", "source_event_id", "source_created_at",
            "started_at", "ended_at", "known", "business_status", "external_state_name", "responsibility"))
        data = {"earliest_at": self.coverage.earliest_at, "through": self.coverage.through,
                "intervals": intervals, "diagnostics": {"tickets": 2, "events": 3}}
        with patch("aplicativos.integracoes.services.otrs_backlog_history.read_history", return_value=data):
            sync_history(None)
            sync_history(None)
        self.assertEqual(OTRSBacklogHistory.objects.count(), 1)
        self.assertEqual(OTRSBacklogInterval.objects.count(), len(intervals))
        with patch("aplicativos.integracoes.clients.factory.configured_otrs_client", side_effect=AssertionError("HTTP must be local")):
            result = AnalyticsQueryService().query({"scope": "support", "metric": "tickets", "group_by": "status",
                "start": "2026-01-01", "end": "2026-03-31", "filters": {}}, user=self.user)
        self.assertEqual(result["operational"]["backlog_history"]["point_count"], 3)
        self.assertEqual([p["backlog_reconstructed"] for p in result["operational"]["series"]], [2, 1, 1])
        self.assertFalse(result["operational"]["backlog"]["available"])

    def test_failed_extraction_keeps_previous_intervals_and_coverage(self):
        from aplicativos.integracoes.services.otrs_backlog_history import sync_history
        before = list(self.coverage.intervals.values())
        with patch("aplicativos.integracoes.services.otrs_backlog_history.read_history", side_effect=ValueError("synthetic incomplete source")):
            with self.assertRaises(ValueError):
                sync_history(None)
        self.coverage.refresh_from_db()
        self.assertEqual(before, list(self.coverage.intervals.values()))
        self.assertEqual(self.coverage.through, instant(3, 2))

    def test_worker_refresh_is_hourly_and_respects_failure_backoff(self):
        from aplicativos.integracoes.models import Integration, IntegrationSyncState
        from aplicativos.integracoes.management.commands.integration_worker import Command
        now = timezone.now()
        Integration.objects.create(key="otrs", enabled=True, username="synthetic", secret_ciphertext="synthetic",
            config={"host": "example.invalid", "database": "synthetic"})
        state = IntegrationSyncState.objects.create(key="otrs_backlog_history", last_success_at=now)
        with patch("aplicativos.integracoes.management.commands.integration_worker.call_command") as command:
            Command()._backlog_history(now)
            command.assert_not_called()
            state.last_success_at = now - timedelta(hours=2)
            state.save()
            Command()._backlog_history(now)
            command.assert_called_once()
            command.reset_mock()
            state.last_error_at, state.failure_count = now, 1
            state.save()
            Command()._backlog_history(now)
            command.assert_not_called()
