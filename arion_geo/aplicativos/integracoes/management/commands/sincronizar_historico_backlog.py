import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from aplicativos.integracoes.clients.factory import configured_otrs_client, otrs_configuration_notice
from aplicativos.integracoes.models import Integration, IntegrationRun, IntegrationSyncState
from aplicativos.integracoes.services.otrs_backlog_history import read_history, sync_history
from aplicativos.integracoes.services.sync_lock import Lease


class Command(BaseCommand):
    help = "Reconstrói intervalos de backlog NOC pelo histórico OTRS somente leitura; preview por padrão."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Publica apenas o cache local, atomicamente.")

    def handle(self, *args, **options):
        integration = Integration.objects.filter(key=Integration.Key.OTRS).first()
        if otrs_configuration_notice(integration):
            raise CommandError("OTRS não configurado ou desabilitado.")
        lease = Lease("otrs")
        if not lease.acquire():
            raise CommandError("Histórico adiado: outra leitura OTRS está em execução.")
        heartbeat = lease.keep_alive()
        run = None
        try:
            if options["apply"]:
                state, _ = IntegrationSyncState.objects.get_or_create(key="otrs_backlog_history")
                run = IntegrationRun.objects.create(integration=integration, operation=IntegrationRun.Operation.READ,
                    status=IntegrationRun.Status.RUNNING, message="Backlog history: NOC - ATACADO")
                state.last_started_at = run.started_at
                state.save(update_fields=("last_started_at", "updated_at"))
            client = configured_otrs_client(integration)
            client.timeout = 60  # Bounded historical batches, separate from HTTP/source live sync.
            if options["apply"]:
                result = sync_history(client)
                finished = timezone.now()
                run.status, run.finished_at = IntegrationRun.Status.SUCCESS, finished
                run.records_found = run.records_processed = result["events"]
                run.duration_ms = int((finished - run.started_at).total_seconds() * 1000)
                run.save()
                state.last_success_at, state.last_error_at, state.last_error = finished, None, ""
                state.failure_count, state.records_count = 0, result["events"]
                state.last_duration_ms = run.duration_ms
                state.save()
            else:
                data = read_history(client)
                result = {**data["diagnostics"], "intervals": len(data["intervals"]), "through": data["through"].isoformat()}
            self.stdout.write(json.dumps({"apply": options["apply"], **result}, ensure_ascii=False))
        except Exception as exc:
            if run:
                run.status, run.finished_at = IntegrationRun.Status.FAILED, timezone.now()
                run.records_failed, run.message = 1, f"Backlog history: falha sanitizada ({type(exc).__name__})"
                run.save()
                state.last_error_at, state.last_error = run.finished_at, run.message
                state.failure_count = min(10, state.failure_count + 1)
                state.save()
            raise CommandError(f"Histórico não atualizado ({type(exc).__name__}); cobertura anterior preservada.") from None
        finally:
            heartbeat[0].set()
            heartbeat[1].join(timeout=2)
            lease.release()
