from datetime import timedelta
from io import StringIO
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

from django.core.management import CommandError
from django.db import OperationalError
from django.test import TestCase
from django.utils import timezone

from aplicativos.integracoes.management.commands.integration_worker import Command
from aplicativos.integracoes.management.commands.sincronizar_otrs import Command as OTRSSyncCommand
from aplicativos.integracoes.clients.factory import otrs_configuration_notice
from aplicativos.integracoes.models import Integration, IntegrationLease, IntegrationRun, IntegrationSyncState
from aplicativos.qualidade.models import QualityCorrelationCheckpoint
from aplicativos.integracoes.services.sync_lock import Lease
from aplicativos.integracoes.services.errors import OTRSAuthenticationError, OTRSConnectionRefusedError, OTRSTLSRequiredError
from aplicativos.integracoes.services.integration_health import status_after_otrs_failure


_RUN_PROCESS_PATH = Path(__file__).resolve().parents[3] / "deploy" / "windows" / "run_process.py"
_RUN_PROCESS_SPEC = importlib.util.spec_from_file_location("arion_run_process_under_test", _RUN_PROCESS_PATH)
RUN_PROCESS = importlib.util.module_from_spec(_RUN_PROCESS_SPEC)
_RUN_PROCESS_SPEC.loader.exec_module(RUN_PROCESS)


class WindowsSupervisorEnvironmentTests(TestCase):
    def test_project_dotenv_is_loaded_from_explicit_root_and_reports_only_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / ".env").write_text(
                "ARION_OTRS_LEGACY_NO_TLS_HOST=legacy.example.invalid\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                summary = RUN_PROCESS._load_project_environment(project_root)
                self.assertIs(summary["dotenv_present"], True)
                self.assertEqual(summary["otrs_legacy_host_source"], "project_dotenv")
                self.assertEqual(os.environ["ARION_OTRS_LEGACY_NO_TLS_HOST"], "legacy.example.invalid")
                self.assertNotIn("legacy.example.invalid", str(summary))

    def test_process_environment_takes_precedence_and_child_receives_same_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / ".env").write_text(
                "ARION_OTRS_LEGACY_NO_TLS_HOST=dotenv.example.invalid\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "ARION_OTRS_LEGACY_NO_TLS_HOST": "process.example.invalid",
                "ARION_TEST_INHERITED_VALUE": "preserved",
                "ARION_ENV": "testing",
            }, clear=True):
                summary = RUN_PROCESS._load_project_environment(project_root)
                child_environment = RUN_PROCESS._child_environment()
                self.assertEqual(summary["otrs_legacy_host_source"], "process_environment")
                self.assertEqual(child_environment["ARION_OTRS_LEGACY_NO_TLS_HOST"], "process.example.invalid")
                self.assertEqual(child_environment["ARION_TEST_INHERITED_VALUE"], "preserved")
                self.assertEqual(child_environment["ARION_ENV"], "production")
                self.assertEqual(os.environ["ARION_ENV"], "testing")

    def test_status_requires_the_child_service_process_not_only_supervisor_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_run_root = RUN_PROCESS.RUN_ROOT
            RUN_PROCESS.RUN_ROOT = Path(temp_dir)
            RUN_PROCESS.RUN_ROOT.mkdir(parents=True, exist_ok=True)
            RUN_PROCESS._pid_path("worker").write_text("1234", encoding="ascii")
            RUN_PROCESS._child_pid_path("worker").write_text("5678", encoding="ascii")
            try:
                with patch.object(RUN_PROCESS, "_is_locked", return_value=True), \
                     patch.object(RUN_PROCESS, "_pid_is_running", return_value=False), \
                     patch("builtins.print"):
                    self.assertEqual(RUN_PROCESS._print_status("worker"), 2)
                with patch.object(RUN_PROCESS, "_is_locked", return_value=True), \
                     patch.object(RUN_PROCESS, "_pid_is_running", return_value=True), \
                     patch("builtins.print"):
                    self.assertEqual(RUN_PROCESS._print_status("worker"), 0)
            finally:
                RUN_PROCESS.RUN_ROOT = previous_run_root


class IntegrationLeaseTests(TestCase):
    def test_active_lease_skips_and_expired_lease_can_be_reclaimed(self):
        first = Lease("cmdb")
        second = Lease("cmdb")
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        IntegrationLease.objects.filter(key="cmdb").update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(second.acquire())
        second.release()

    def test_sync_lease_heartbeat_retries_a_transient_sqlite_lock(self):
        class StopAfterTwoIntervals:
            calls = 0

            def wait(self, _seconds):
                self.calls += 1
                return self.calls > 2

        class TemporarilyLockedLease:
            calls = 0

            def heartbeat(self):
                self.calls += 1
                if self.calls == 1:
                    raise OperationalError("database is locked")
                return False

        lease = TemporarilyLockedLease()
        Lease._heartbeat_loop(lease, StopAfterTwoIntervals())
        self.assertEqual(lease.calls, 2)


class IntegrationWorkerTests(TestCase):
    def setUp(self):
        self.command = Command(stdout=StringIO(), stderr=StringIO())
        Integration.objects.create(
            key=Integration.Key.OTRS, name="OTRS", kind="MySQL", enabled=True,
        )

    def test_due_sync_updates_success_without_overwriting_command_cursor(self):
        cursor = timezone.now() - timedelta(minutes=3)
        state, _ = IntegrationSyncState.objects.get_or_create(key="otrs", defaults={"change_cursor": cursor})
        now = timezone.now()
        called = []
        self.command._due("otrs_full", now, lambda current: (called.append(True) or {"records": 7, "cursor": now}))
        state.refresh_from_db()
        self.assertEqual(called, [True])
        self.assertEqual(state.records_count, 7)
        self.assertEqual(state.change_cursor, cursor)
        self.assertEqual(state.last_full_success_at, state.last_success_at)

    def test_worker_cadences_and_failed_run_retry(self):
        now = timezone.now()
        recent_success = SimpleNamespace(status="success", finished_at=now - timedelta(minutes=1), started_at=now - timedelta(minutes=1))
        recent_failure = SimpleNamespace(status="failed", finished_at=now - timedelta(minutes=1), started_at=now - timedelta(minutes=1))
        retry_due = SimpleNamespace(status="failed", finished_at=now - timedelta(minutes=3), started_at=now - timedelta(minutes=3))
        old_success = SimpleNamespace(status="success", finished_at=now - timedelta(minutes=31), started_at=now - timedelta(minutes=31))
        stale_running = SimpleNamespace(status="running", started_at=now - timedelta(minutes=4))
        self.assertFalse(self.command._run_due(recent_success, now - timedelta(hours=1), timedelta(minutes=2), now))
        self.assertFalse(self.command._run_due(recent_failure, None, timedelta(minutes=2), now))
        self.assertTrue(self.command._run_due(retry_due, None, timedelta(minutes=2), now))
        self.assertTrue(self.command._run_due(old_success, None, timedelta(minutes=30), now))
        self.assertTrue(self.command._run_due(stale_running, None, timedelta(minutes=30), now))
        self.assertTrue(self.command._run_due(None, None, timedelta(minutes=30), now))

    def test_persisted_backoff_overrides_old_successful_full_run(self):
        now = timezone.now()
        state = IntegrationSyncState.objects.create(
            key="otrs", last_error_at=now - timedelta(minutes=1), failure_count=1,
        )
        old_success = SimpleNamespace(
            status=IntegrationRun.Status.SUCCESS,
            finished_at=now - timedelta(hours=4), started_at=now - timedelta(hours=4),
        )
        self.assertFalse(self.command._run_due(
            old_success, now - timedelta(hours=4), timedelta(minutes=30), now, retry_state=state,
        ))
        state.last_error_at = now - timedelta(minutes=3)
        self.assertTrue(self.command._run_due(
            old_success, now - timedelta(hours=4), timedelta(minutes=30), now, retry_state=state,
        ))

    def test_backoff_grows_and_is_capped(self):
        self.assertEqual(self.command._retry_delay(1), timedelta(minutes=2))
        self.assertEqual(self.command._retry_delay(3), timedelta(minutes=8))
        self.assertEqual(self.command._retry_delay(10), timedelta(minutes=30))

    def test_command_error_diagnostic_keeps_only_known_code(self):
        error = CommandError(
            "Sincronização OTRS cancelada (OTRSTLSHandshakeError: server_tls_unsupported). "
            "password=hunter2 mysql://user:hunter2@host/db"
        )
        summary = self.command._safe_error_summary(error)
        self.assertEqual(summary, "OTRSTLSHandshakeError code=server_tls_unsupported")
        self.assertNotIn("hunter2", summary)

    def test_disabled_otrs_skips_otrs_and_quality_jobs(self):
        Integration.objects.filter(key=Integration.Key.OTRS).update(enabled=False)
        now = timezone.now()
        with patch.object(self.command, "_due") as run_job, patch.object(self.command, "_quality_tq_correlation") as correlate:
            self.command._schedule_once(now)
        self.assertFalse(any(call.args[0].startswith("otrs") for call in run_job.call_args_list))
        correlate.assert_not_called()

    def test_enabled_but_incomplete_otrs_configuration_is_reported_and_skipped(self):
        integration = Integration.objects.get(key=Integration.Key.OTRS)
        self.assertEqual(
            otrs_configuration_notice(integration),
            "OTRS não configurado ou desabilitado neste ambiente.",
        )
        now = timezone.now()
        with patch.object(self.command, "_due") as run_job, patch.object(self.command, "_quality_tq_correlation") as correlate:
            self.command._schedule_once(now)
        self.assertFalse(any(call.args[0].startswith("otrs") for call in run_job.call_args_list))
        correlate.assert_not_called()

    def test_quality_correlation_does_not_record_runtime_error_when_otrs_disabled(self):
        Integration.objects.filter(key=Integration.Key.OTRS).update(enabled=False)
        self.command._quality_tq_correlation(timezone.now())
        self.assertFalse(QualityCorrelationCheckpoint.objects.exists())

    def test_runtime_error_diagnostic_omits_exception_text(self):
        summary = self.command._safe_error_summary(RuntimeError("article body must not be logged"))
        self.assertEqual(summary, "RuntimeError · falha sanitizada")
        self.assertNotIn("article body", summary)

    def test_heartbeat_retries_sqlite_database_locked(self):
        class StopAfterTwoIntervals:
            calls = 0

            def wait(self, _seconds):
                self.calls += 1
                return self.calls > 2

        class TemporarilyLockedLease:
            calls = 0

            def heartbeat(self):
                self.calls += 1
                if self.calls == 1:
                    raise OperationalError("database is locked")
                return False

        lease = TemporarilyLockedLease()
        self.command._heartbeat(lease, StopAfterTwoIntervals())
        self.assertEqual(lease.calls, 2)

    def test_failed_sync_preserves_cursor_and_records_safe_failure(self):
        state, _ = IntegrationSyncState.objects.get_or_create(key="otrs", defaults={"change_cursor": timezone.now() - timedelta(hours=1)})
        cursor = state.change_cursor
        now = timezone.now()
        def fail(_state):
            raise RuntimeError("source unavailable")
        self.command._due("otrs_fast", now, fail)
        state.refresh_from_db()
        self.assertEqual(state.change_cursor, cursor)
        self.assertIn("RuntimeError", state.last_error)
        self.assertIsNotNone(state.last_error_at)

    def test_worker_schedules_quality_correlation_after_full_otrs_pass(self):
        Integration.objects.filter(key=Integration.Key.OTRS).update(
            config={"host": "127.0.0.1", "database": "otrs"},
            username="reader", secret_ciphertext="test-only-ciphertext",
        )
        now = timezone.now()
        with patch.object(self.command, "_due") as run_job, patch.object(self.command, "_quality_tq_correlation") as correlate:
            self.command._schedule_once(now)
        self.assertTrue(any(call.args[0] == "otrs_full" for call in run_job.call_args_list))
        correlate.assert_called_once_with(now)

    def test_processing_failure_does_not_claim_current_otrs_connectivity_failure(self):
        integration = Integration.objects.get(key=Integration.Key.OTRS)
        integration.status = Integration.Status.CONNECTED
        self.assertEqual(
            status_after_otrs_failure(RuntimeError("local mirror write failed"), integration.status),
            Integration.Status.CONNECTED,
        )
        self.assertEqual(
            status_after_otrs_failure(OTRSTLSRequiredError(), integration.status),
            Integration.Status.ATTENTION,
        )
        self.assertEqual(
            status_after_otrs_failure(OTRSConnectionRefusedError(), integration.status),
            Integration.Status.UNAVAILABLE,
        )

    def test_sync_processing_failure_records_sync_error_without_overwriting_connection_status_or_cursor(self):
        integration = Integration.objects.get(key=Integration.Key.OTRS)
        integration.config = {"host": "127.0.0.1", "database": "otrs"}
        integration.username = "reader"
        integration.secret_ciphertext = "test-only-ciphertext"
        integration.status = Integration.Status.CONNECTED
        integration.save()
        cursor = timezone.now() - timedelta(hours=1)
        state, _ = IntegrationSyncState.objects.get_or_create(
            key="otrs", defaults={"change_cursor": cursor, "last_success_at": cursor},
        )
        state.change_cursor = cursor
        state.last_success_at = cursor
        state.save()

        class ReadOnlySessionClient:
            database = "otrs"

            def read_only_session(self):
                from contextlib import nullcontext
                return nullcontext(self)

        command = OTRSSyncCommand(stdout=StringIO(), stderr=StringIO())
        with patch(
            "aplicativos.integracoes.management.commands.sincronizar_otrs._otrs_client",
            return_value=ReadOnlySessionClient(),
        ), patch(
            "aplicativos.integracoes.management.commands.sincronizar_otrs.OTRSSyncService",
        ) as sync_service:
            sync_service.return_value.run.side_effect = RuntimeError("private row detail")
            with self.assertRaises(CommandError):
                command.handle(
                    apply=True, fast=False, full=True, dry_run=False, queues=None,
                    scheduler_lease=True, verbosity=1,
                )

        integration.refresh_from_db()
        state.refresh_from_db()
        failed_run = IntegrationRun.objects.get(operation=IntegrationRun.Operation.OTRS_FULL_SYNC)
        self.assertEqual(integration.status, Integration.Status.CONNECTED)
        self.assertIn("RuntimeError", integration.last_error)
        self.assertNotIn("private row detail", integration.last_error)
        self.assertEqual(state.change_cursor, cursor)
        self.assertIsNotNone(state.last_error_at)
        self.assertEqual(failed_run.status, IntegrationRun.Status.FAILED)
        self.assertNotIn("private row detail", failed_run.message)

    def test_sync_client_preflight_failure_creates_run_and_updates_connection_health(self):
        integration = Integration.objects.get(key=Integration.Key.OTRS)
        integration.status = Integration.Status.CONNECTED
        integration.save(update_fields=("status",))
        state, _ = IntegrationSyncState.objects.get_or_create(key="otrs")
        command = OTRSSyncCommand(stdout=StringIO(), stderr=StringIO())
        with patch(
            "aplicativos.integracoes.management.commands.sincronizar_otrs._otrs_client",
            side_effect=OTRSAuthenticationError(error_code="access_denied"),
        ):
            with self.assertRaises(CommandError):
                command.handle(
                    apply=True, fast=False, full=True, dry_run=False, queues=None,
                    scheduler_lease=True, verbosity=1,
                )

        integration.refresh_from_db()
        state.refresh_from_db()
        failed_run = IntegrationRun.objects.get(operation=IntegrationRun.Operation.OTRS_FULL_SYNC)
        self.assertEqual(integration.status, Integration.Status.ATTENTION)
        self.assertIn("OTRSAuthenticationError", integration.last_error)
        self.assertIn("access_denied", integration.last_error)
        self.assertEqual(state.last_started_at, failed_run.started_at)
        self.assertIsNotNone(state.last_error_at)
        self.assertEqual(failed_run.status, IntegrationRun.Status.FAILED)
        self.assertIn("access_denied", failed_run.message)

    def test_sync_failure_summary_rejects_untrusted_error_code_text(self):
        error = OTRSAuthenticationError(error_code="password=do-not-store")
        self.assertEqual(
            OTRSSyncCommand._safe_failure_summary(error),
            "Falha segura (OTRSAuthenticationError)",
        )

    def test_quality_correlation_checkpoint_uses_backoff_after_failure(self):
        now = timezone.now()
        checkpoint = QualityCorrelationCheckpoint(
            queue_name="NOC - QUALIDADE ATACADO", failure_count=2,
            last_error_at=now - timedelta(minutes=1),
        )
        self.assertFalse(Command._quality_checkpoint_due(checkpoint, 120, now))
        checkpoint.last_error_at = now - timedelta(minutes=5)
        self.assertTrue(Command._quality_checkpoint_due(checkpoint, 120, now))
