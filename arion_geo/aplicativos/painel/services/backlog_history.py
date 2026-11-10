"""Scoped time-point stock from locally persisted OTRS lifecycle intervals."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import OTRSBacklogHistory, OTRSTicket
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAME
from aplicativos.integracoes.services.otrs_status import BACKLOG_STATUSES, business_status_query


@transaction.atomic
def reconstructed_backlog(*, user, links, filters, buckets, period_end, apply_filters):
    coverage = OTRSBacklogHistory.objects.select_for_update().filter(queue_name=NOC_QUEUE_NAME).first()
    if coverage is None:
        return {"loaded": False, "available": False, "points": {}, "kind": "reconstructed",
                "message": "Histórico de eventos OTRS ainda não sincronizado; capturas FULL são observações separadas."}
    intervals = coverage.intervals.all()
    is_global = user is None or DataScopeService.is_global(user)
    # These dimensions have no historical ledger. Use the CURRENT permitted
    # associations explicitly, never the ticket's current state/queue/classification.
    dimension_filters = {k: v for k, v in filters.items() if v and k in {
        "provider", "uf", "project", "service", "sla", "priority", "termination_reason", "responsible"}}
    if dimension_filters or not is_global:
        tickets = OTRSTicket.objects.all()
        if not is_global:
            tickets = tickets.filter(config_items__is_present_in_source=True,
                                     config_items__cmdb_link__in=links)
        tickets = apply_filters(tickets, links, dimension_filters)
        intervals = intervals.filter(ticket_external_id__in=tickets.values("external_id"))
    active = Q(known=True, business_status__in=BACKLOG_STATUSES) & ~Q(responsibility="IMPROCEDENTE")
    if filters.get("status"):
        active &= business_status_query("business_status", filters["status"])
    if filters.get("source_state"):
        state = filters["source_state"]
        active &= (business_status_query("business_status", state) if state in {
            "OPEN", "PENDING", "RESOLVED", "CLOSED", "CANCELED", "SCHEDULED", "UNKNOWN"
        } else Q(external_state_name=state))
    if filters.get("responsibility"):
        active &= Q(responsibility=filters["responsibility"])
    impossible = (filters.get("queue") not in (None, "", NOC_QUEUE_NAME)
                  or filters.get("origin") not in (None, "", "SUPPORT") or bool(filters.get("stage")))
    expressions, instants = {}, {}
    now = timezone.now()
    for index, (key, _label, start, end) in enumerate(buckets):
        through = min(end, period_end, coverage.through, now)
        if start >= through or through <= coverage.earliest_at:
            continue
        instants[key] = (index, through, (through - start).total_seconds() / (end - start).total_seconds())
        # Evaluate immediately BEFORE the exclusive boundary. An interval ending
        # exactly at midnight belongs to that day's closing stock; one starting
        # at midnight belongs to the next day, not the previous bucket.
        at = Q(started_at__lt=through, ended_at__gte=through)
        known_filter = active & at
        if filters.get("age_bucket") == "OVER_24H":
            known_filter &= Q(source_created_at__lte=through - timedelta(hours=24))
        expressions[f"v{index}"] = Count("ticket_external_id", distinct=True, filter=known_filter)
        expressions[f"u{index}"] = Count("ticket_external_id", distinct=True, filter=at & Q(known=False))
    totals = intervals.aggregate(**expressions) if expressions else {}
    points = {}
    for key, (index, instant, position) in instants.items():
        unknown = 0 if impossible else totals[f"u{index}"]
        value = None if unknown else (0 if impossible else totals[f"v{index}"])
        points[key] = {"value": value, "unknown_tickets": unknown,
                       "at": (instant - timedelta(microseconds=1)).isoformat(), "position": position}
    known_points = [p for p in points.values() if p["value"] is not None]
    missing = len(buckets) - len(known_points)
    message = "Backlog reconstruído pelos eventos de estado, fila e procedência do OTRS; OPEN e PENDING."
    if dimension_filters or not is_global:
        message += " Recorte por vínculos/dimensões atualmente autorizados; não comprova sua atribuição histórica."
    if missing:
        message += f" {missing} intervalo(s) sem cobertura suficiente, indicados como indisponíveis."
    return {"loaded": True, "available": bool(known_points), "kind": "reconstructed", "points": points,
            "point_count": len(known_points), "missing_intervals": missing,
            "through": coverage.through.isoformat(), "message": message,
            "source": "OTRS ticket_history: estados, filas e procedência"}
