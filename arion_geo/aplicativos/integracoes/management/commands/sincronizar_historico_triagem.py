"""Bounded historical discovery using the existing OTRS integration and mirror."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.clients.factory import configured_otrs_client, otrs_configuration_notice
from aplicativos.integracoes.models import Integration, OTRSQueueBackfill
from aplicativos.integracoes.otrs_queues import COCKPIT_QUEUE_NAMES, TRIAGE_QUEUE_NAME
from aplicativos.integracoes.services.otrs_sync import OTRSSyncService
from aplicativos.integracoes.services.sync_lock import Lease


class Command(BaseCommand):
    help = "Descobre tickets que passaram pela Triagem, inclusive fora da fila; incremental e idempotente."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--max-batches", type=int, default=10)
        parser.add_argument("--restart", action="store_true", help="Revisa desde o início, preservando tickets/eventos já importados.")

    def handle(self, *args, **options):
        if not 1 <= options["batch_size"] <= 400 or not 1 <= options["max_batches"] <= 100:
            raise CommandError("Use batch-size entre 1 e 400 e max-batches entre 1 e 100.")
        integration = Integration.objects.filter(key=Integration.Key.OTRS).first()
        if otrs_configuration_notice(integration):
            raise CommandError("OTRS não configurado ou desabilitado.")
        lease = Lease("otrs")
        if not lease.acquire():
            raise CommandError("Backfill adiado: outra sincronização OTRS está em execução.")
        heartbeat = lease.keep_alive()
        try:
            client = configured_otrs_client(integration)
            service = OTRSSyncService(client)
            service._discover()
            queue_id, queue_name = service._queue(TRIAGE_QUEUE_NAME)
            progress = OTRSQueueBackfill.objects.filter(queue_external_id=queue_id).first()
            cursor = 0 if options["restart"] or not progress else progress.last_ticket_id
            found, complete = 0, False
            for _ in range(options["max_batches"]):
                with client.read_only_snapshot():
                    # Keyset pagination over official IDs; no status restriction.
                    rows = client.execute_select(
                        "SELECT id FROM (SELECT ticket_id AS id FROM ticket_history WHERE queue_id=%s AND ticket_id>%s "
                        "UNION SELECT id FROM ticket WHERE queue_id=%s AND id>%s) candidates "
                        "ORDER BY id LIMIT %s", (queue_id, cursor, queue_id, cursor, options["batch_size"]))
                    ids = [int(row["id"]) for row in rows]
                    if not ids:
                        complete = True
                        break
                    with transaction.atomic():
                        service.run(queue_names=COCKPIT_QUEUE_NAMES, ticket_ids=ids, apply=options["apply"])
                        cursor = max(ids)
                        if options["apply"]:
                            progress, _ = OTRSQueueBackfill.objects.update_or_create(
                                queue_external_id=queue_id,
                                defaults={"queue_name": queue_name, "last_ticket_id": cursor, "completed_at": None})
                found += len(ids)
                self.stdout.write(f"Triagem: lote={len(ids)}; TicketID até {cursor}; acumulado={found}")
            if options["apply"]:
                OTRSQueueBackfill.objects.update_or_create(queue_external_id=queue_id,
                    defaults={"queue_name": queue_name, "last_ticket_id": cursor,
                              "completed_at": timezone.now() if complete else None})
                AuditService.record(category="INTEGRATION", action="SYNC_COMPLETED",
                    description="Backfill incremental da Triagem", module="Integrações",
                    metadata={"source": "OTRS", "queue_id": queue_id, "tickets": found,
                              "last_ticket_id": cursor, "complete": complete})
            self.stdout.write(json.dumps({"apply": options["apply"], "found": found,
                "last_ticket_id": cursor, "complete": complete}))
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(f"Backfill interrompido ({type(exc).__name__}); último lote confirmado preservado.") from None
        finally:
            heartbeat[0].set()
            heartbeat[1].join(timeout=2)
            lease.release()
