"""Attendance levels from scoped tickets and official lifecycle evidence only."""
from django.db.models import Count, Exists, OuterRef, Q, Subquery

from aplicativos.integracoes.models import OTRSTicketEvent
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAME, NOC_QUEUE_NAMES, TRIAGE_QUEUE_NAME
from .analytics import OPERATIONAL_RESOLUTION_FIELD, operational_resolution_expression
from .otrs_status import BACKLOG_STATUSES, BUSINESS_OPEN_STATUSES, business_status_query


def attendance_summary(tickets, *, start_at, end_at, include_stock=False):
    """Current active stock plus distinct TicketIDs with proven period events.

    Resolutions use the current resolved cycle and the canonical effective date.
    Missing queue evidence stays unclassified, never attributed to today's queue.
    """
    tickets = tickets.annotate(**{OPERATIONAL_RESOLUTION_FIELD: operational_resolution_expression()})
    proof = OTRSTicketEvent.objects.filter(ticket_id=OuterRef("pk"),
        occurred_at=OuterRef(OPERATIONAL_RESOLUTION_FIELD), business_status="RESOLVED",
        event_type__in=("StateUpdate", "Merged")).order_by("-external_id")
    previous = OTRSTicketEvent.objects.filter(ticket_id=OuterRef("ticket_id")).filter(
        Q(occurred_at__lt=OuterRef("occurred_at"))
        | Q(occurred_at=OuterRef("occurred_at"), external_id__lt=OuterRef("external_id"))
    ).order_by("-occurred_at", "-external_id")
    transfers = OTRSTicketEvent.objects.filter(ticket_id=OuterRef("pk"),
        event_type="Move", queue_name=NOC_QUEUE_NAME, occurred_at__gte=start_at, occurred_at__lt=end_at,
    ).annotate(_previous_queue=Subquery(previous.values("queue_name")[:1])).filter(_previous_queue=TRIAGE_QUEUE_NAME)
    tickets = tickets.annotate(_resolution_queue=Subquery(proof.values("queue_name")[:1]),
                               _forwarded=Exists(transfers))
    current = Q(is_present_in_queue=True, queue_name__in=NOC_QUEUE_NAMES)
    active = current & Q(business_status__in=BUSINESS_OPEN_STATUSES)
    resolved = Q(operational_resolution_at__gte=start_at, operational_resolution_at__lt=end_at)
    counts = {
        "current_n1": Count("pk", filter=active & Q(queue_name=TRIAGE_QUEUE_NAME)),
        "current_n2": Count("pk", filter=active & Q(queue_name=NOC_QUEUE_NAME)),
        "resolved_n1": Count("pk", filter=resolved & Q(_resolution_queue=TRIAGE_QUEUE_NAME)),
        "resolved_n2": Count("pk", filter=resolved & Q(_resolution_queue=NOC_QUEUE_NAME)),
        "resolution_unclassified": Count("pk", filter=resolved & Q(_resolution_queue__isnull=True)),
        "forwarded_n1_n2": Count("pk", filter=Q(_forwarded=True)),
    }
    if include_stock:
        for status in ("OPEN", "PENDING", "SCHEDULED", "RESOLVED", "CANCELED", "UNKNOWN"):
            counts[f"stock_{status}"] = Count("pk", filter=current & business_status_query("business_status", status))
        counts["stock_backlog"] = Count("pk", filter=current & Q(business_status__in=BACKLOG_STATUSES)
                                       & ~Q(responsibility_normalized="IMPROCEDENTE"))
    return tickets.aggregate(**counts)
