from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.models import CMDBLinkMirror, Integration, IntegrationRun, IntegrationSyncState
from aplicativos.integracoes.services.cmdb_sync import CMDBSyncService
from aplicativos.integracoes.services.errors import IntegrationError
from aplicativos.integracoes.views import _cmdb_client
from aplicativos.integracoes.services.sync_lock import Lease


class Command(BaseCommand):
    help = "Sincroniza o espelho operacional local CMDB."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--dry-run", action="store_true", dest="dry_run", help="Calcula as alterações sem gravar no espelho.")
        parser.add_argument("--scheduler-lease", action="store_true", help="Lease held by integration_worker.")
        group.add_argument("--apply", action="store_true", help="Aplica o snapshot local após a leitura e validação completas.")

    def handle(self, *args, **options):
        lease = None if options.get("scheduler_lease") else Lease("cmdb")
        if lease and not lease.acquire():
            raise CommandError("CMDB sync skipped: another run is active.")
        heartbeat = lease.keep_alive() if lease else None
        try:
            return self._execute(*args, **options)
        finally:
            if heartbeat:
                heartbeat[0].set()
                heartbeat[1].join(timeout=2)
            if lease:
                lease.release()

    def _execute(self, *args, **options):
        apply = bool(options.get("apply"))
        try:
            integration = Integration.objects.get(key=Integration.Key.CMDB)
            client = _cmdb_client(integration)
        except Exception as exc:
            raise CommandError(f"Não foi possível preparar o client CMDB ({type(exc).__name__}).") from None

        run = IntegrationRun.objects.create(integration=integration, operation=IntegrationRun.Operation.CMDB_SYNC, status=IntegrationRun.Status.RUNNING, message="Apply" if apply else "Dry-run")
        sync_state, _ = IntegrationSyncState.objects.get_or_create(key="cmdb")
        sync_state.last_started_at = run.started_at
        sync_state.save(update_fields=("last_started_at", "updated_at"))
        AuditService.record(category="INTEGRATION", action="SYNC_STARTED", description="Sincronização CMDB iniciada", obj=run, module="Integrações", metadata={"source": "CMDB", "run_id": run.pk, "mode": run.message})
        service = CMDBSyncService(client, progress=self.stdout.write)
        try:
            result = service.run(apply=apply)
            run.status = IntegrationRun.Status.SUCCESS
            run.records_found = result["operators"]["found"] + result["links"]["found"]
            run.records_processed = result["operators"]["processed"] + result["links"]["processed"]
            run.records_created = result["operators"]["created"] + result["links"]["created"]
            run.records_updated = result["operators"]["updated"] + result["links"]["updated"]
            run.records_unchanged = result["operators"]["unchanged"] + result["links"]["unchanged"]
            run.records_stale = result["operators"]["stale"] + result["links"]["stale"]
            run.duration_ms = result["duration_ms"]
            run.finished_at = timezone.now()
            run.message = self._summary(result, apply)
            run.save(update_fields=("status", "records_found", "records_processed", "records_created", "records_updated", "records_unchanged", "records_stale", "duration_ms", "finished_at", "message"))
            integration.last_success_at = run.finished_at
            integration.last_read_at = run.finished_at
            integration.status = Integration.Status.CONNECTED
            integration.last_error = ""
            integration.save(update_fields=("last_success_at", "last_read_at", "status", "last_error", "updated_at"))
            if apply:
                sync_state.last_success_at = run.finished_at
                sync_state.last_error_at = None
                sync_state.last_error = ""
                sync_state.last_duration_ms = run.duration_ms
                sync_state.records_count = CMDBLinkMirror.objects.filter(is_present_in_source=True).count()
                sync_state.save()
            AuditService.record(category="INTEGRATION", action="SYNC_COMPLETED", description="Sincronização CMDB concluída", obj=run, module="Integrações", metadata={"source": "CMDB", "run_id": run.pk, "counts": {"found": run.records_found, "created": run.records_created, "updated": run.records_updated, "unchanged": run.records_unchanged, "stale": run.records_stale}, "duration_ms": run.duration_ms})
        except Exception as exc:
            run.status = IntegrationRun.Status.FAILED
            run.records_failed = 1
            run.duration_ms = int((timezone.now() - run.started_at).total_seconds() * 1000)
            run.finished_at = timezone.now()
            run.message = f"Falha segura ({type(exc).__name__})"
            run.save(update_fields=("status", "records_failed", "duration_ms", "finished_at", "message"))
            sync_state.last_error_at = run.finished_at
            sync_state.last_error = f"Falha segura ({type(exc).__name__})"
            sync_state.save(update_fields=("last_error_at", "last_error", "updated_at"))
            try:
                integration = Integration.objects.get(key=Integration.Key.CMDB)
                integration.last_error_at = timezone.now()
                integration.last_error = f"Falha segura ({type(exc).__name__})"
                integration.status = Integration.Status.UNAVAILABLE
                integration.save(update_fields=("last_error_at", "last_error", "status", "updated_at"))
            except Exception:
                pass
            AuditService.record(category="INTEGRATION", action="SYNC_FAILED", description="Sincronização CMDB falhou", result="FAILURE", obj=run, module="Integrações", metadata={"source": "CMDB", "run_id": run.pk, "error_type": type(exc).__name__})
            raise CommandError(f"Sincronização CMDB cancelada ({type(exc).__name__}); espelho não reconciliado.") from None

        self.stdout.write(self.style.SUCCESS(f"CMDB {'APLICADO' if apply else 'DRY-RUN'} concluído."))
        for label, key in (("Operadoras", "operators"), ("Links", "links")):
            row = result[key]
            self.stdout.write(f"{label}: encontrados={row['found']}; criados={row['created']}; atualizados={row['updated']}; inalterados={row['unchanged']}; ausentes={row['stale']}; falhas={row['failed']}.")
        self.stdout.write("ISP: " + "; ".join(f"{key}={value}" for key, value in result["isp_resolution"].items()))
        self.stdout.write(f"ConfigItems com Number no snapshot CMDB: {result['links_with_number']}.")
        self.stdout.write(f"Performance: {result['performance']['duration_ms']} ms; HTTP={result['performance']['http_requests']}; Get batches={result['performance']['get_batches']}.")

    @staticmethod
    def _summary(result, apply):
        return ("Apply" if apply else "Dry-run") + "; " + "; ".join(
            f"{name} found={result[key]['found']} created={result[key]['created']} updated={result[key]['updated']} unchanged={result[key]['unchanged']} stale={result[key]['stale']}"
            for name, key in (("operators", "operators"), ("links", "links"))
        )[:210]
