from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone
import re

from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.models import Integration, IntegrationRun, IntegrationSyncState, OTRSTicket, OTRSTicketConfigItem, OTRSStateMapping
from aplicativos.integracoes.otrs_queues import COCKPIT_QUEUE_NAMES, queue_signature
from aplicativos.integracoes.services.errors import IntegrationError, OTRSDatabaseNotFoundError
from aplicativos.integracoes.services.integration_health import status_after_otrs_failure
from aplicativos.integracoes.services.otrs_sync import OTRSSyncService
from aplicativos.integracoes.views import _otrs_client, get_integration
from aplicativos.integracoes.services.sync_lock import Lease


class Command(BaseCommand):
    help = "Sincroniza o espelho local das filas OTRS selecionadas, em leitura somente no OTRS."

    def add_arguments(self, parser):
        parser.add_argument("--queue", action="append", dest="queues", help="Nome exato da fila OTRS. Pode ser repetido; por padrão usa as filas do Cockpit.")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--fast", action="store_true", help="Sincroniza somente tickets alterados desde o cursor, com janela de 2 minutos.")
        mode.add_argument("--full", action="store_true", help="Reconcilia as filas selecionadas por completo (padrão).")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--dry-run", action="store_true", dest="dry_run", help="Calcula as alterações sem gravar no espelho.")
        parser.add_argument("--scheduler-lease", action="store_true", help="Lease held by integration_worker.")
        group.add_argument("--apply", action="store_true", help="Aplica o resultado da leitura no espelho local.")

    def handle(self, *args, **options):
        key = "otrs"
        lease = None if options.get("scheduler_lease") else Lease(key)
        if lease and not lease.acquire():
            raise CommandError("OTRS sync skipped: another run is active.")
        heartbeat = lease.keep_alive() if lease else None
        try:
            return self._execute(*args, **options)
        finally:
            if heartbeat:
                heartbeat[0].set()
                heartbeat[1].join(timeout=2)
            if lease:
                lease.release()

    @staticmethod
    def _format_integration_error(prefix, exc, *, suffix="."):
        summary = exc.diagnostic_summary() if isinstance(exc, IntegrationError) else ""
        if summary:
            return f"{prefix} ({type(exc).__name__}){suffix} {summary}"
        return f"{prefix} ({type(exc).__name__}){suffix}"

    def _write_diagnostic(self, options, exc):
        if int(options.get("verbosity") or 1) >= 2 and isinstance(exc, IntegrationError):
            self.stderr.write(
                f"[OTRS][DIAGNÓSTICO] tipo={type(exc).__name__} | {exc.diagnostic_summary()}"
            )

    @staticmethod
    def _safe_error_code(exc):
        error_code = str(getattr(exc, "error_code", "") or "").strip()
        return error_code if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", error_code) else ""

    @classmethod
    def _safe_failure_summary(cls, exc):
        error_code = cls._safe_error_code(exc)
        code_suffix = f": {error_code}" if error_code else ""
        return f"Falha segura ({type(exc).__name__}{code_suffix})"

    @classmethod
    def _record_setup_failure(cls, state, integration, run, exc):
        """Persist a failed attempt even when the client cannot be prepared."""
        failed_at = timezone.now()
        summary = cls._safe_failure_summary(exc)
        run.status = IntegrationRun.Status.FAILED
        run.records_failed = 1
        run.duration_ms = max(0, int((failed_at - run.started_at).total_seconds() * 1000))
        run.finished_at = failed_at
        run.message = summary
        run.save(update_fields=("status", "records_failed", "duration_ms", "finished_at", "message"))
        state.last_started_at = run.started_at
        state.last_error_at = failed_at
        state.last_error = summary
        state.save(update_fields=("last_started_at", "last_error_at", "last_error", "updated_at"))
        integration.last_error_at = failed_at
        integration.last_error = summary
        integration.status = status_after_otrs_failure(exc, integration.status)
        integration.save(update_fields=("last_error_at", "last_error", "status", "updated_at"))

    def _execute(self, *args, **options):
        apply = bool(options.get("apply"))
        fast = bool(options.get("fast"))
        queue_names = tuple(dict.fromkeys(options.get("queues") or COCKPIT_QUEUE_NAMES))
        if set(queue_names) == set(COCKPIT_QUEUE_NAMES):
            queue_names = COCKPIT_QUEUE_NAMES
        queue_label = "|".join(queue_names)
        if fast and queue_names != COCKPIT_QUEUE_NAMES:
            raise CommandError("FAST usa o cursor compartilhado do Cockpit; sincronize o conjunto completo de filas configurado.")
        state, _ = IntegrationSyncState.objects.get_or_create(key="otrs")
        if fast and state.change_cursor is None:
            raise CommandError("Cursor OTRS inexistente; execute primeiro uma reconciliação completa.")
        if fast:
            full_coverage = IntegrationRun.objects.filter(
                operation=IntegrationRun.Operation.OTRS_FULL_SYNC,
                status=IntegrationRun.Status.SUCCESS,
            ).order_by("-started_at", "-pk").first()
            expected_coverage = f"queues={queue_signature(COCKPIT_QUEUE_NAMES)}"
            if not full_coverage or expected_coverage not in full_coverage.message:
                raise CommandError("O conjunto de filas do Cockpit mudou; execute uma sincronização FULL antes do FAST.")
        operation = IntegrationRun.Operation.OTRS_FAST_SYNC if fast else IntegrationRun.Operation.OTRS_FULL_SYNC
        integration = get_integration(Integration.Key.OTRS)
        run = IntegrationRun.objects.create(
            integration=integration,
            operation=operation,
            status=IntegrationRun.Status.RUNNING,
            message=("Fast " if fast else "Full ") + ("Apply " if apply else "Dry-run ")
            + f"queues={queue_signature(queue_names)}"[:210],
        )
        state.last_started_at = run.started_at
        state.save(update_fields=("last_started_at", "updated_at"))
        AuditService.record(category="INTEGRATION", action="SYNC_STARTED", description="Sincronização OTRS iniciada", obj=run, module="Integrações", metadata={"source": "OTRS", "run_id": run.pk, "queues": list(queue_names), "mode": "apply" if apply else "dry-run"})
        try:
            client = _otrs_client(integration)
        except IntegrationError as exc:
            self._write_diagnostic(options, exc)
            self._record_setup_failure(state, integration, run, exc)
            AuditService.record(category="INTEGRATION", action="SYNC_FAILED", description="Sincronização OTRS falhou ao preparar a conexão", result="FAILURE", obj=run, module="Integrações", metadata={"source": "OTRS", "run_id": run.pk, "error_type": type(exc).__name__, "error_code": self._safe_error_code(exc)})
            raise CommandError(
                self._format_integration_error("Não foi possível preparar o client OTRS", exc)
            ) from exc
        except Exception as exc:
            self._record_setup_failure(state, integration, run, exc)
            AuditService.record(category="INTEGRATION", action="SYNC_FAILED", description="Sincronização OTRS falhou ao preparar a conexão", result="FAILURE", obj=run, module="Integrações", metadata={"source": "OTRS", "run_id": run.pk, "error_type": type(exc).__name__})
            raise CommandError(f"Não foi possível preparar o client OTRS ({type(exc).__name__}).") from None
        if not client.database:
            try:
                system = {"information_schema", "mysql", "performance_schema", "sys"}
                applications = [name for name in client.list_databases() if name.casefold() not in system]
                if len(applications) != 1:
                    raise OTRSDatabaseNotFoundError(error_code="database_not_unique")
                client.database = applications[0]
            except IntegrationError as exc:
                self._write_diagnostic(options, exc)
                self._record_setup_failure(state, integration, run, exc)
                AuditService.record(category="INTEGRATION", action="SYNC_FAILED", description="Sincronização OTRS falhou ao descobrir o database", result="FAILURE", obj=run, module="Integrações", metadata={"source": "OTRS", "run_id": run.pk, "error_type": type(exc).__name__, "error_code": self._safe_error_code(exc)})
                raise CommandError(
                    self._format_integration_error("Não foi possível descobrir o database OTRS", exc)
                ) from exc
            except Exception as exc:
                self._record_setup_failure(state, integration, run, exc)
                AuditService.record(category="INTEGRATION", action="SYNC_FAILED", description="Sincronização OTRS falhou ao descobrir o database", result="FAILURE", obj=run, module="Integrações", metadata={"source": "OTRS", "run_id": run.pk, "error_type": type(exc).__name__})
                raise CommandError(f"Não foi possível descobrir o database OTRS ({type(exc).__name__}).") from None

        service = OTRSSyncService(client, progress=self.stdout.write)
        try:
            with client.read_only_session():
                result = service.run(queue_names=queue_names, apply=apply, changed_since=state.change_cursor if fast else None)
            run.status = IntegrationRun.Status.SUCCESS
            run.records_found = result["tickets"]["found"] + result["relations"]["found"]
            run.records_processed = result["tickets"]["processed"] + result["relations"]["processed"]
            run.records_created = result["tickets"]["created"] + result["relations"]["created"]
            run.records_updated = result["tickets"]["updated"] + result["relations"]["updated"]
            run.records_unchanged = result["tickets"]["unchanged"] + result["relations"]["unchanged"]
            run.records_stale = result["tickets"]["stale"] + result["relations"]["stale"]
            run.duration_ms = result["performance"]["duration_ms"]
            run.finished_at = timezone.now()
            run.message = self._summary(result, apply)
            run.save(update_fields=("status", "records_found", "records_processed", "records_created", "records_updated", "records_unchanged", "records_stale", "duration_ms", "finished_at", "message"))
            integration.last_success_at = run.finished_at
            integration.last_read_at = run.finished_at
            integration.status = Integration.Status.CONNECTED
            integration.last_error = ""
            integration.save(update_fields=("last_success_at", "last_read_at", "status", "last_error", "updated_at"))
            if apply:
                state.last_started_at = run.started_at
                state.last_success_at = run.finished_at
                state.last_error_at = None
                state.last_error = ""
                state.last_duration_ms = run.duration_ms
                state.records_count = OTRSTicket.objects.filter(is_present_in_queue=True).count()
                state.change_cursor = result.get("cursor") or state.change_cursor
                if not fast:
                    state.last_full_success_at = run.finished_at
                state.save()
            AuditService.record(category="INTEGRATION", action="SYNC_COMPLETED", description="Sincronização OTRS concluída", obj=run, module="Integrações", metadata={"source": "OTRS", "run_id": run.pk, "queues": list(queue_names), "counts": {"tickets": result["tickets"]["found"], "relations": result["relations"]["found"], "created": run.records_created, "updated": run.records_updated, "stale": run.records_stale}, "duration_ms": run.duration_ms})
        except Exception as exc:
            if isinstance(exc, IntegrationError):
                self._write_diagnostic(options, exc)
            run.status = IntegrationRun.Status.FAILED
            run.records_failed = 1
            run.duration_ms = int((timezone.now() - run.started_at).total_seconds() * 1000)
            run.finished_at = timezone.now()
            safe_summary = self._safe_failure_summary(exc)
            run.message = safe_summary
            run.save(update_fields=("status", "records_failed", "duration_ms", "finished_at", "message"))
            state.last_error_at = timezone.now()
            state.last_error = safe_summary
            state.save(update_fields=("last_error_at", "last_error", "updated_at"))
            integration.last_error_at = timezone.now()
            integration.last_error = safe_summary
            integration.status = status_after_otrs_failure(exc, integration.status)
            integration.save(update_fields=("last_error_at", "last_error", "status", "updated_at"))
            AuditService.record(category="INTEGRATION", action="SYNC_FAILED", description="Sincronização OTRS falhou", result="FAILURE", obj=run, module="Integrações", metadata={"source": "OTRS", "run_id": run.pk, "error_type": type(exc).__name__, "error_code": self._safe_error_code(exc)})
            if isinstance(exc, IntegrationError):
                raise CommandError(
                    self._format_integration_error(
                        "Sincronização OTRS cancelada",
                        exc,
                        suffix="; snapshot local anterior preservado.",
                    )
                ) from exc
            raise CommandError(f"Sincronização OTRS cancelada ({type(exc).__name__}); snapshot local anterior preservado.") from None

        self.stdout.write(self.style.SUCCESS(f"OTRS {'APLICADO' if apply else 'DRY-RUN'} concluído: filas={queue_label}."))
        self.stdout.write(f"Tickets: encontrados={result['tickets']['found']}; criados={result['tickets']['created']}; atualizados={result['tickets']['updated']}; inalterados={result['tickets']['unchanged']}; ausentes da fila={result['tickets']['stale']}.")
        self.stdout.write("Tickets por fila: " + "; ".join(
            f"{item['name']}={item['tickets']}" for item in result.get("tickets_by_queue", ())
        ))
        self.stdout.write(f"Ticket-CI: {result['relations']['found']}; criadas={result['relations']['created']}; atualizadas={result['relations']['updated']}; ausentes={result['relations']['stale']}; ConfigItem sem Link local={result['relations']['unresolved_link']}.")
        terminal = result["ticket_terminal_fields"]
        self.stdout.write(f"Campos de fechamento sincronizados: CodigoFechamento={terminal['closing_code_filled']}; ticketResolvido={terminal['resolved_at_filled']}; data terminal StateUpdate={terminal['terminal_at_filled']}.")
        relation_stats = result["relation_stats"]
        self.stdout.write(f"Cardinalidade: tickets com CI={relation_stats['tickets_with_ci']}; sem CI={relation_stats['tickets_without_ci']}; exatamente 1 CI={relation_stats['tickets_one_ci']}; múltiplos CIs={relation_stats['tickets_multiple_ci']}; ConfigItems únicos={relation_stats['unique_config_items']}; ConfigItems em múltiplos tickets={relation_stats['configitems_multiple_tickets']}.")
        self.stdout.write(f"CI-para-Link local: resolvidos={relation_stats['resolved_local_links']}; não resolvidos={relation_stats['unresolved_local_links']}.")
        self.stdout.write("Business status: " + "; ".join(f"{key}={value}" for key, value in result["business_status"].items()) + f"; total business-open={result['business_open']}.")
        self.stdout.write(f"Performance: {result['performance']['duration_ms']} ms; SELECT={result['performance']['select_queries']}; tickets={result['performance']['ticket_query_rows']}; batches de relação={result['performance']['relation_batches']}.")
        self.stdout.write(f"Warnings de estado ({len(result['warnings'])}): " + (" | ".join(result["warnings"]) if result["warnings"] else "nenhum"))
        if apply:
            self._print_local_proof()
        else:
            self.stdout.write("Prova e KPIs locais: execute com --apply para consultar exclusivamente o snapshot local.")

    @staticmethod
    def _summary(result, apply):
        queues = queue_signature(tuple(item["name"] for item in result["queues"]))
        return ("Apply" if apply else "Dry-run") + f"; queues={queues}; tickets={result['tickets']['found']}; relations={result['relations']['found']}; open={result['business_open']}; warnings={len(result['warnings'])}"

    def _print_local_proof(self):
        current = OTRSTicket.objects.filter(is_present_in_queue=True)
        responsibility = {row["responsibility_normalized"]: row["total"] for row in current.values("responsibility_normalized").annotate(total=Count("id"))}
        self.stdout.write(
            "Procedência local: "
            f"PROCEDENTE={responsibility.get('PROCEDENTE', 0)}; "
            f"IMPROCEDENTE={responsibility.get('IMPROCEDENTE', 0)}; "
            f"INDETERMINADO={responsibility.get('INDETERMINADO', 0)}."
        )
        from aplicativos.integracoes.services.closing_reason import ClosingReasonCanonicalizer
        coded = current.exclude(closing_code_raw="")
        reasons = {ClosingReasonCanonicalizer.key(value) for value in coded.values_list("closing_code_raw", flat=True).distinct()}
        self.stdout.write(f"Fechamento local: CodigoFechamento preenchido={coded.count()}; motivos distintos normalizados={len(reasons)}; ticketResolvido={current.filter(resolved_at__isnull=False).count()}; data terminal StateUpdate={current.filter(terminal_state_at__isnull=False).count()}; cancelamento datado={current.filter(canceled_at__isnull=False).count()}.")
        statuses = {row["business_status"]: row["total"] for row in current.values("business_status").annotate(total=Count("id"))}
        self.stdout.write(f"Snapshot local: tickets atuais na fila={current.count()}.")
        self.stdout.write("Status local: " + "; ".join(f"{status}={statuses.get(status, 0)}" for status in OTRSStateMapping.BusinessStatus.values))
        local_open = current.filter(is_business_open=True)
        related_open = OTRSTicketConfigItem.objects.filter(ticket__in=local_open, is_present_in_source=True)
        tickets_with_ci = related_open.values("ticket_id").distinct().count()
        tickets_with_link = related_open.exclude(cmdb_link__isnull=True).values("ticket_id").distinct().count()
        links_open = related_open.exclude(cmdb_link__isnull=True).values("cmdb_link_id").distinct().count()
        operators_open = related_open.exclude(cmdb_link__operator_id__isnull=True).values("cmdb_link__operator_id").distinct().count()
        self.stdout.write(f"Business-open={local_open.count()}; com CI={tickets_with_ci}; sem CI={max(0, local_open.count() - tickets_with_ci)}; com Link local={tickets_with_link}; links únicos={links_open}; operadoras={operators_open}.")
        unresolved = OTRSTicketConfigItem.objects.filter(ticket__in=current, is_present_in_source=True, cmdb_link__isnull=True).count()
        self.stdout.write(f"Relações Ticket-CI sem Link local={unresolved}.")
        resolved_state = current.filter(external_state_name__iexact="resolvido")
        self.stdout.write(f"resolvido: quantidade atual={resolved_state.count()}; business-open=NAO ({resolved_state.filter(is_business_open=True).count()} aberto[s]).")
