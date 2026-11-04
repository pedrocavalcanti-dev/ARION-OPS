"""Dedicated persisted scheduler; run independently from HTTP workers."""
import threading
import time
import os
import re
from datetime import timedelta
from io import StringIO

from django.core.management import BaseCommand, call_command, CommandError
from django.conf import settings
from django.db import OperationalError
from django.utils import timezone

from aplicativos.integracoes.models import (
    CMDBLinkMirror, CMDBOperatorMirror, Integration, IntegrationRun, IntegrationSyncState,
    OTRSTicket, OTRSTicketConfigItem,
)
from aplicativos.integracoes.otrs_queues import COCKPIT_QUEUE_NAMES, queue_signature
from aplicativos.integracoes.services.sync_lock import LEASE_SECONDS, Lease
from aplicativos.integracoes.services.errors import IntegrationError


class Command(BaseCommand):
    help = "Executa sincronizações CMDB/OTRS em processo dedicado."

    FAST_CADENCE = timedelta(minutes=2)
    FULL_CADENCE = timedelta(minutes=30)
    CMDB_CADENCE = timedelta(minutes=10)
    RETRY_CADENCE = timedelta(minutes=2)
    MAX_RETRY_CADENCE = timedelta(minutes=30)

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Avalia os jobs uma vez e encerra.")
        parser.add_argument("--interval", type=int, default=5, help="Intervalo de verificação em segundos.")

    def handle(self, *args, **options):
        interval = max(1, min(options["interval"], 60))
        worker = Lease("worker")
        if not worker.acquire():
            self.stderr.write(self.style.ERROR("An integration_worker is already active."))
            return
        worker_stop = threading.Event()
        worker_heartbeat = threading.Thread(target=self._heartbeat, args=(worker, worker_stop), daemon=True)
        worker_heartbeat.start()
        self.stdout.write(
            "ARION Integration Worker\n\n"
            "OTRS fast: 2 min\nOTRS full: 30 min\nCMDB:      10 min\n\n"
            "Worker iniciado."
        )
        try:
            while True:
                if not worker.heartbeat():
                    self.stderr.write(self.style.ERROR("Worker lease lost; stopping to avoid duplicate scheduling."))
                    break
                self._schedule_once(timezone.now())
                if options["once"]:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("Stopping integration_worker.")
        finally:
            worker_stop.set()
            worker_heartbeat.join(timeout=2)
            worker.release()

    def _schedule_once(self, now):
        cmdb_state, _ = IntegrationSyncState.objects.get_or_create(key="cmdb")
        cmdb_run = IntegrationRun.objects.filter(
            operation=IntegrationRun.Operation.CMDB_SYNC,
        ).order_by("-started_at", "-pk").first()
        if self._run_due(cmdb_run, cmdb_state.last_success_at, self.CMDB_CADENCE, now, retry_state=cmdb_state):
            self._due("cmdb", now, self._cmdb)

        otrs_state, _ = IntegrationSyncState.objects.get_or_create(key="otrs")
        otrs_integration = Integration.objects.filter(key=Integration.Key.OTRS).first()
        from aplicativos.integracoes.clients.factory import otrs_configuration_notice

        if otrs_configuration_notice(otrs_integration):
            return
        full_run = IntegrationRun.objects.filter(
            operation=IntegrationRun.Operation.OTRS_FULL_SYNC,
        ).order_by("-started_at", "-pk").first()
        full_success = otrs_state.last_full_success_at
        full_due = self._run_due(full_run, full_success, self.FULL_CADENCE, now, retry_state=otrs_state)
        expected_coverage = f"queues={queue_signature(COCKPIT_QUEUE_NAMES)}"
        if not full_run or (
            full_run.status == IntegrationRun.Status.SUCCESS
            and expected_coverage not in full_run.message
        ):
            # Missing/stale coverage requires an initial sync. Recorded failures
            # take precedence so an old run cannot bypass the retry backoff.
            full_due = full_due or otrs_state.last_error_at is None
        if full_due:
            self._due("otrs_full", now, self._otrs_full)
            self._quality_tq_correlation(now)
            return  # FULL owns the OTRS lease for this pass; FAST never overlaps it.

        fast_run = IntegrationRun.objects.filter(
            operation=IntegrationRun.Operation.OTRS_FAST_SYNC,
        ).order_by("-started_at", "-pk").first()
        if self._run_due(fast_run, full_success, self.FAST_CADENCE, now, retry_state=otrs_state):
            self._due("otrs_fast", now, self._otrs_fast)
        self._quality_tq_correlation(now)

    def _quality_tq_correlation(self, now):
        from aplicativos.qualidade.models import QualityCorrelationCheckpoint
        from aplicativos.qualidade.services.tq_otrs_correlation import quality_queue_names, reconcile_quality_articles
        from aplicativos.integracoes.clients.factory import configured_otrs_client, otrs_configuration_notice

        try:
            interval = max(30, int(getattr(
                settings, "QUALITY_OTRS_CORRELATION_INTERVAL_SECONDS",
                os.getenv("ARION_QUALITY_OTRS_CORRELATION_INTERVAL_SECONDS", "120"),
            )))
        except (TypeError, ValueError):
            interval = 120
        try:
            batch_size = max(1, min(200, int(getattr(
                settings, "QUALITY_OTRS_CORRELATION_BATCH_SIZE",
                os.getenv("ARION_QUALITY_OTRS_CORRELATION_BATCH_SIZE", "100"),
            ))))
        except (TypeError, ValueError):
            batch_size = 100
        checkpoints = [QualityCorrelationCheckpoint.objects.filter(queue_name=name).first() for name in quality_queue_names()]
        due = not checkpoints or any(checkpoint is None or self._quality_checkpoint_due(checkpoint, interval, now) for checkpoint in checkpoints)
        if not due:
            return
        lease = Lease("otrs")
        if not lease.acquire():
            return
        try:
            integration = Integration.objects.filter(key=Integration.Key.OTRS).first()
            if otrs_configuration_notice(integration):
                return
            client = configured_otrs_client(integration)
            outcome = reconcile_quality_articles(client, limit=batch_size)
            self.stdout.write(self.style.SUCCESS(
                "[QUALITY OTRS] " + " ".join(f"{key}={value}" for key, value in outcome.items())
            ))
        except Exception as exc:
            safe_error = self._safe_error_summary(exc)
            failed_at = timezone.now()
            retry_minutes = 2
            for queue_name in quality_queue_names():
                checkpoint, _ = QualityCorrelationCheckpoint.objects.get_or_create(queue_name=queue_name)
                checkpoint.failure_count = min(checkpoint.failure_count + 1, 10)
                checkpoint.last_error_at = failed_at
                checkpoint.last_error = safe_error
                checkpoint.save(update_fields=("failure_count", "last_error_at", "last_error", "updated_at"))
                retry_minutes = min(30, 2 ** min(max(checkpoint.failure_count, 1), 5))
            retry_at = failed_at + timedelta(minutes=retry_minutes)
            detail = (
                f"[QUALITY OTRS] etapa=leitura_incremental {safe_error}; "
                f"horario={failed_at.isoformat()} proxima_tentativa={retry_at.isoformat()}; cursor preservado."
            )
            self.stderr.write(self.style.WARNING(detail))
        finally:
            lease.release()

    @staticmethod
    def _quality_checkpoint_due(checkpoint, interval_seconds, now):
        if checkpoint.last_error_at:
            delay_minutes = min(30, 2 ** min(max(checkpoint.failure_count, 1), 5))
            return checkpoint.last_error_at <= now - timedelta(minutes=delay_minutes)
        return checkpoint.last_success_at is None or checkpoint.last_success_at <= now - timedelta(seconds=interval_seconds)

    @staticmethod
    def _cadence_due(last, cadence, now):
        return last is None or last <= now - cadence

    def _run_due(self, run, last_success, cadence, now, retry_state=None):
        if retry_state and retry_state.last_error_at:
            return self._retry_due(retry_state, now)
        if run is None:
            return self._cadence_due(last_success, cadence, now)
        if run.status == IntegrationRun.Status.FAILED:
            return self._cadence_due(run.finished_at or run.started_at, self.RETRY_CADENCE, now)
        if run.status in (IntegrationRun.Status.RUNNING, IntegrationRun.Status.PENDING):
            return now >= run.started_at + timedelta(seconds=LEASE_SECONDS)
        return self._cadence_due(run.finished_at or last_success or run.started_at, cadence, now)

    def _retry_delay(self, failure_count):
        minutes = min(30, 2 ** min(max(int(failure_count or 1), 1), 5))
        return min(self.MAX_RETRY_CADENCE, timedelta(minutes=minutes))

    def _retry_due(self, state, now):
        return state.last_error_at <= now - self._retry_delay(state.failure_count)

    @staticmethod
    def _safe_error_summary(exc):
        cause = exc
        for _ in range(4):
            if isinstance(cause, IntegrationError):
                code = cause.error_code if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", cause.error_code or "") else ""
                return f"{type(cause).__name__}" + (f" code={code} · {cause.safe_message}" if code else f" · {cause.safe_message}")
            next_cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
            if next_cause is None or next_cause is cause:
                break
            cause = next_cause
        if isinstance(exc, CommandError):
            # CommandError may contain driver output; retain only an approved
            # exception class and machine-readable code from the OTRS wrapper.
            match = re.search(
                r"\b(OTRS[A-Za-z]+Error|IntegrationNotConfigured)\b(?:\s*:\s*([a-zA-Z0-9_-]{1,64}))?",
                str(exc),
            )
            if match:
                return f"{match.group(1)}" + (f" code={match.group(2)}" if match.group(2) else "")
            return "CommandError · comando de integração falhou; detalhes brutos omitidos"
        return f"{type(exc).__name__} · falha sanitizada"

    def _due(self, key, now, callback):
        state, _ = IntegrationSyncState.objects.get_or_create(key="cmdb" if key == "cmdb" else "otrs")
        lease = Lease("cmdb" if key == "cmdb" else "otrs")
        if not lease.acquire():
            return
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(lease, stop), daemon=True)
        heartbeat.start()
        start = timezone.now()
        state.last_started_at = start
        state.save(update_fields=("last_started_at", "updated_at"))
        try:
            details = callback(state) or {}
            state.refresh_from_db()
            state.last_success_at = timezone.now()
            state.last_error_at = None
            state.last_error = ""
            state.failure_count = 0
            state.last_duration_ms = int((timezone.now() - start).total_seconds() * 1000)
            if details.get("records") is not None:
                state.records_count = details["records"]
            if key == "otrs_full":
                state.last_full_success_at = state.last_success_at
            state.save()
            self.stdout.write(self.style.SUCCESS(self._format_summary(key, details, state.last_duration_ms)))
        except Exception as exc:
            state.refresh_from_db()
            state.last_error_at = timezone.now()
            state.failure_count = min(state.failure_count + 1, 10)
            state.last_error = self._safe_error_summary(exc)[:240]
            state.last_duration_ms = int((timezone.now() - start).total_seconds() * 1000)
            retry_at = state.last_error_at + self._retry_delay(state.failure_count)
            state.save(update_fields=("last_error_at", "last_error", "failure_count", "last_duration_ms", "updated_at"))
            detail = (
                f"{key}: etapa=command {state.last_error}; horario={state.last_error_at.isoformat()} "
                f"proxima_tentativa={retry_at.isoformat()}; snapshot e cursor preservados."
            )
            self.stderr.write(self.style.ERROR(detail))
        finally:
            stop.set()
            heartbeat.join(timeout=2)
            lease.release()

    @staticmethod
    def _format_summary(key, details, duration):
        label = {"otrs_fast": "OTRS FAST", "otrs_full": "OTRS FULL", "cmdb": "CMDB"}[key]
        fields = [f"{name}={details[name]}" for name in ("tickets", "changed", "created", "updated", "relations", "links", "providers") if details.get(name) is not None]
        if details.get("records") is not None and not fields:
            fields.append(f"records={details['records']}")
        fields.append(f"duração={duration}ms")
        return f"[{label}] " + " ".join(fields)

    @staticmethod
    def _run_command(*args):
        # Management commands emit detail-oriented diagnostics; keep the worker log to one summary per cycle.
        call_command(*args, stdout=StringIO(), stderr=StringIO(), verbosity=0)

    @classmethod
    def _run_details(cls, operation):
        run = IntegrationRun.objects.filter(operation=operation).order_by("-started_at", "-pk").first()
        if not run:
            return {}
        details = {
            "created": run.records_created,
            "updated": run.records_updated,
            "changed": run.records_created + run.records_updated,
            "duration_ms": run.duration_ms,
        }
        if operation == IntegrationRun.Operation.CMDB_SYNC:
            details.update(
                links=CMDBLinkMirror.objects.filter(is_present_in_source=True).count(),
                providers=CMDBOperatorMirror.objects.filter(is_present_in_source=True).count(),
            )
        else:
            details.update(
                tickets=OTRSTicket.objects.filter(is_present_in_queue=True).count(),
                relations=OTRSTicketConfigItem.objects.filter(is_present_in_source=True).count(),
            )
        return details

    @staticmethod
    def _heartbeat(lease, stop):
        while not stop.wait(20):
            try:
                if not lease.heartbeat():
                    return
            except OperationalError:
                # SQLite may briefly reject this write while a mirror transaction is committing.
                # The lease expires only after 180 s; retry on the next heartbeat interval.
                continue

    @classmethod
    def _cmdb(cls, _state):
        cls._run_command("sincronizar_cmdb", "--apply", "--scheduler-lease")
        return cls._run_details(IntegrationRun.Operation.CMDB_SYNC)

    @classmethod
    def _otrs_full(cls, _state):
        cls._run_command("sincronizar_otrs", "--apply", "--full", "--scheduler-lease")
        return cls._run_details(IntegrationRun.Operation.OTRS_FULL_SYNC)

    @classmethod
    def _otrs_fast(cls, _state):
        cls._run_command("sincronizar_otrs", "--apply", "--fast", "--scheduler-lease")
        return cls._run_details(IntegrationRun.Operation.OTRS_FAST_SYNC)
