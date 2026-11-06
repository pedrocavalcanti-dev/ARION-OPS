"""Immutable, coverage-scoped observations of the synchronized OTRS mirror."""

from django.db import transaction
from django.utils import timezone

from aplicativos.integracoes.models import (
    OTRSOperationalConfigItemSnapshot, OTRSOperationalSnapshot,
    OTRSOperationalTicketSnapshot, OTRSTicket, OTRSTicketConfigItem,
    IntegrationRun,
)
from aplicativos.integracoes.otrs_queues import queue_signature
from aplicativos.integracoes.services.otrs_status import BACKLOG_STATUSES


def capture_daily_operational_snapshot(run, queue_names):
    """Persist one exact mirror observation per local day and queue coverage.

    Only successful applied FULL runs qualify. Repeated calls are idempotent;
    an existing daily observation is never overwritten by a later sync.
    """
    names = tuple(dict.fromkeys(queue_names))
    signature = queue_signature(names)
    if (
        run.operation != IntegrationRun.Operation.OTRS_FULL_SYNC
        or run.status != IntegrationRun.Status.SUCCESS
        or not run.message.startswith("Apply;")
        or f"queues={signature}" not in run.message
        or not names
    ):
        return {"created": False, "reason": "run_not_eligible", "tickets": 0, "backlog": 0}

    captured_at = run.finished_at or timezone.now()
    snapshot_date = timezone.localtime(captured_at).date()
    source_tickets = list(OTRSTicket.objects.filter(
        is_present_in_queue=True, queue_name__in=names,
    ).order_by("external_id"))
    ticket_count = len(source_tickets)
    backlog_count = sum(
        ticket.business_status in BACKLOG_STATUSES
        and ticket.responsibility_normalized != "IMPROCEDENTE"
        for ticket in source_tickets
    )

    with transaction.atomic():
        snapshot, created = OTRSOperationalSnapshot.objects.get_or_create(
            scope_signature=signature,
            snapshot_date=snapshot_date,
            defaults={
                "captured_at": captured_at,
                "source_run": run,
                "ticket_count": ticket_count,
                "backlog_count": backlog_count,
            },
        )
        if not created:
            return {
                "created": False, "reason": "already_captured",
                "tickets": snapshot.ticket_count, "backlog": snapshot.backlog_count,
                "snapshot_id": snapshot.pk,
            }

        from aplicativos.integracoes.services.cancellation import classify_cancellation_stage

        rows = [OTRSOperationalTicketSnapshot(
            snapshot=snapshot,
            external_id=ticket.external_id,
            ticket_number=ticket.ticket_number,
            queue_name=ticket.queue_name,
            external_state_name=ticket.external_state_name,
            external_state_type=ticket.external_state_type,
            business_status=ticket.business_status,
            source_created_at=ticket.source_created_at,
            resolved_at=ticket.resolved_at,
            canceled_at=ticket.canceled_at,
            responsibility_normalized=ticket.responsibility_normalized,
            closing_code_raw=ticket.closing_code_raw,
            priority_name=ticket.priority_name,
            service_name=ticket.service_name,
            sla_name=ticket.sla_name,
            responsible_user_id=ticket.responsible_user_id,
            cancellation_stage=classify_cancellation_stage(ticket),
        ) for ticket in source_tickets]
        for offset in range(0, len(rows), 500):
            OTRSOperationalTicketSnapshot.objects.bulk_create(rows[offset:offset + 500], batch_size=500)

        ticket_snapshots = {
            row.external_id: row for row in OTRSOperationalTicketSnapshot.objects.filter(snapshot=snapshot)
        }
        mirror_ids = {ticket.external_id: ticket.pk for ticket in source_tickets}
        ci_rows = {}
        mirror_ticket_ids = list(mirror_ids.values())
        for offset in range(0, len(mirror_ticket_ids), 300):
            batch = mirror_ticket_ids[offset:offset + 300]
            relations = OTRSTicketConfigItem.objects.filter(
                ticket_id__in=batch,
                is_present_in_source=True,
                cmdb_link__is_present_in_source=True,
            ).select_related("ticket", "cmdb_link", "cmdb_link__operator").order_by("pk")
            for relation in relations:
                link = relation.cmdb_link
                ticket_snapshot = ticket_snapshots.get(relation.ticket.external_id)
                if not ticket_snapshot:
                    continue
                key = (ticket_snapshot.pk, relation.config_item_id)
                ci_rows.setdefault(key, OTRSOperationalConfigItemSnapshot(
                    ticket_snapshot=ticket_snapshot,
                    config_item_id=relation.config_item_id,
                    provider_name=link.operator.name if link.operator_id else "",
                    uf=link.state,
                    project=link.business_project,
                    legacy_project=link.legacy_project,
                    client=link.contracting_client,
                ))
        ci_values = list(ci_rows.values())
        for offset in range(0, len(ci_values), 500):
            OTRSOperationalConfigItemSnapshot.objects.bulk_create(ci_values[offset:offset + 500], batch_size=500)

    return {
        "created": True, "reason": "captured", "tickets": ticket_count,
        "backlog": backlog_count, "config_items": len(ci_rows),
        "snapshot_id": snapshot.pk,
    }
