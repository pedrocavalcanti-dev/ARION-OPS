"""Project the authorized local mirror. No remote access and no process creation."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from aplicativos.integracoes.models import Integration, IntegrationRun, IntegrationSyncState, IntegrationLease, OTRSTicket
from aplicativos.integracoes.otrs_queues import CANCELLATION_REQUEST_QUEUE_NAME, COCKPIT_QUEUE_NAMES, queue_signature
from aplicativos.integracoes.services.semantics import link_designation
from aplicativos.integracoes.services.sync_lock import Lease
from aplicativos.integracoes.services.cancellation import cancellation_stage_expression, classify_cancellation_stage

from ..models import CancellationIntake, IntakeSettings


KEY = "cancellation_intake"


def intake_settings():
    return IntakeSettings.objects.first() or IntakeSettings()


def source_status():
    config = intake_settings()
    integration = Integration.objects.filter(key=Integration.Key.OTRS, enabled=True).first()
    state = IntegrationSyncState.objects.filter(key="otrs").first()
    full = IntegrationRun.objects.filter(
        integration=integration, operation=IntegrationRun.Operation.OTRS_FULL_SYNC,
        status=IntegrationRun.Status.SUCCESS, message__startswith="Apply; queues=",
        message__contains=f"queues={queue_signature(COCKPIT_QUEUE_NAMES)};",
    ).first() if integration else None
    if not config.enabled:
        return False, "Entrada OTRS pausada na configuração do módulo.", state
    if not integration or not full or not state or not state.last_full_success_at:
        return False, "Aguardando integração OTRS habilitada e FULL aplicado com a cobertura existente do Cockpit.", state
    if not state.last_success_at or state.last_success_at < timezone.now() - timedelta(minutes=config.max_age_minutes):
        return False, "Espelho OTRS desatualizado. Aguarde uma sincronização válida.", state
    return True, "Espelho OTRS local disponível.", state


def ticket_is_eligible(ticket):
    # The KPI's queue/state definition already backs the Cockpit. No saved-profile ID is a QueueID.
    return bool(ticket and ticket.is_present_in_queue and classify_cancellation_stage(ticket) == "REQUEST"
                and ticket.business_status in {"OPEN", "PENDING", "SCHEDULED"})


def eligible_tickets():
    return OTRSTicket.objects.filter(is_present_in_queue=True, business_status__in=("OPEN", "PENDING", "SCHEDULED")).annotate(
        _intake_stage=cancellation_stage_expression(),
    ).filter(_intake_stage="REQUEST")


def current_intakes(queryset):
    """Consult the live mirror as well as the projection, including while the worker lags."""
    return queryset.filter(eligible=True, ticket__in=eligible_tickets())


def synchronization_indicator():
    ok, notice, source = source_status()
    projection = IntegrationSyncState.objects.filter(key=KEY).first()
    now = timezone.now()
    running = IntegrationLease.objects.filter(key__in=("otrs", KEY), expires_at__gt=now).exclude(owner_token="").exists()
    failed = any(s and s.last_error_at and (not s.last_success_at or s.last_error_at > s.last_success_at) for s in (source, projection))
    if running:
        code, label = "running", "Sincronizando"
    elif failed:
        code, label = "failed", "Falha de sincronização"
    elif not ok or not projection or not projection.last_success_at or projection.last_success_at < now - timedelta(minutes=intake_settings().max_age_minutes):
        code, label = "stale", "OTRS desatualizado"
    else:
        code, label = "fresh", "OTRS atualizado"
    return {"code": code, "label": label, "source_at": source.last_success_at if source else None,
            "projection_at": projection.last_success_at if projection else None,
            "notice": "O worker repetirá a atualização. Consulte a integração se a falha persistir." if failed else
                      "Aguardando atualização válida do espelho e da triagem." if code == "stale" else
                      "A atualização da origem não altera decisões ou etapas locais."}


def project_intakes(*, force=False):
    """Each committed row is a checkpoint; replays reconcile all known TicketIDs."""
    now = timezone.now()
    ok, notice, source = source_status()
    if not ok:
        return {"skipped": notice}
    state, _ = IntegrationSyncState.objects.get_or_create(key=KEY)
    delay = min(1800, 120 * 2 ** min(state.failure_count, 4))
    last_attempt = state.last_error_at if state.failure_count else state.last_success_at
    if not force and last_attempt and now < last_attempt + timedelta(seconds=delay):
        return {"skipped": "Aguardando próxima projeção local."}
    lease = Lease(KEY)
    if not lease.acquire():
        return {"skipped": "Projeção já em execução."}
    run = None
    try:
        integration = Integration.objects.get(key=Integration.Key.OTRS)
        run = IntegrationRun.objects.create(integration=integration, operation=IntegrationRun.Operation.RECONCILE,
                                             request_id=KEY, status=IntegrationRun.Status.RUNNING)
        state.last_started_at = now
        state.save(update_fields=("last_started_at", "updated_at"))
        rows = OTRSTicket.objects.filter(
            Q(queue_name=CANCELLATION_REQUEST_QUEUE_NAME, is_present_in_queue=True)
            | Q(external_id__in=CancellationIntake.objects.values("ticket_external_id"))
        ).order_by("pk")
        queue_ids = list(rows.filter(queue_name=CANCELLATION_REQUEST_QUEUE_NAME, is_present_in_queue=True).order_by()
                         .values_list("queue_external_id", flat=True).distinct())
        if len(queue_ids) > 1:
            raise ValueError("Fila de entrada com identidade ambígua no espelho local.")
        created = updated = found = 0
        last_pk = 0
        while True:
            batch = list(rows.filter(pk__gt=last_pk).prefetch_related("config_items__cmdb_link__operator")[:200])
            if not batch:
                break
            if not lease.heartbeat():
                raise RuntimeError("Lease perdida.")
            for ticket in batch:
                with transaction.atomic():
                    eligible = ticket_is_eligible(ticket)
                    entry = CancellationIntake.objects.select_for_update().filter(ticket_external_id=ticket.external_id).first()
                    if not entry and not eligible:
                        continue
                    new = entry is None
                    entry = entry or CancellationIntake(ticket_external_id=ticket.external_id)
                    relations = [r for r in ticket.config_items.all() if r.is_present_in_source]
                    link = relations[0].cmdb_link if len(relations) == 1 else None
                    ready = bool(link and link.is_present_in_source and link.contracting_client and (link.business_project or link.legacy_project))
                    values = {
                        "ticket": ticket, "ticket_number": ticket.ticket_number, "title": ticket.title,
                        "queue_external_id": ticket.queue_external_id, "queue_name": ticket.queue_name,
                        "source_created_at": ticket.source_created_at, "source_changed_at": ticket.source_changed_at,
                        "source_synced_at": ticket.source_synced_at, "eligible": eligible,
                        "link": link, "scope_ready": ready,
                    }
                    # Decision evidence is retained in events/process snapshots. Scope follows current context conservatively.
                    values.update(client_snapshot=link.contracting_client if link else "",
                                  project_snapshot=(link.business_project or link.legacy_project) if link else "",
                                  designation_snapshot=link_designation(link) if link else "")
                    changed = any(getattr(entry, field) != value for field, value in values.items())
                    if new or changed:
                        for field, value in values.items():
                            setattr(entry, field, value)
                        entry.last_seen_at = now
                        entry.revision += 1
                        entry.save()
                        created += int(new)
                        updated += int(not new)
                    found += 1
            last_pk = batch[-1].pk
        # Lost mirror rows remain documented but cannot be approved.
        CancellationIntake.objects.filter(ticket__isnull=True, eligible=True).update(eligible=False)
        finished = timezone.now()
        run.status = IntegrationRun.Status.SUCCESS
        run.records_found = found
        run.records_processed = found
        run.records_created = created
        run.records_updated = updated
        run.records_unchanged = found - created - updated
        run.finished_at = finished
        run.duration_ms = int((finished - now).total_seconds() * 1000)
        run.message = "Projeção local de triagem; nenhum pedido criado automaticamente."
        run.save()
        state.last_success_at = finished
        state.last_error_at = None
        state.last_error = ""
        state.failure_count = 0
        state.records_count = CancellationIntake.objects.count()
        state.change_cursor = source.last_success_at
        state.last_duration_ms = run.duration_ms
        state.save()
        return {"found": found, "created": created, "updated": updated}
    except Exception as exc:
        failed = timezone.now()
        summary = f"Falha na projeção local ({type(exc).__name__}); reexecução idempotente disponível."
        state.last_error_at = failed
        state.last_error = summary
        state.failure_count = min(state.failure_count + 1, 10)
        state.save(update_fields=("last_error_at", "last_error", "failure_count", "updated_at"))
        if run:
            run.status = IntegrationRun.Status.FAILED
            run.records_failed = 1
            run.finished_at = failed
            run.message = summary
            run.save(update_fields=("status", "records_failed", "finished_at", "message"))
        raise
    finally:
        lease.release()
