"""Scoped time-point stock and peak metrics from locally persisted OTRS lifecycle intervals."""
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import OTRSBacklogHistory, OTRSTicket
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAMES, NOC_HISTORY_SCOPE
from aplicativos.integracoes.services.otrs_status import BACKLOG_STATUSES, business_status_query


def _scoped_intervals(coverage, *, user, links, filters, apply_filters):
    intervals = coverage.intervals.all()
    is_global = user is None or DataScopeService.is_global(user)
    # These dimensions have no historical ledger. Use the CURRENT permitted
    # associations explicitly, never the ticket's current state/queue/classification.
    dimension_filters = {k: v for k, v in (filters or {}).items() if v and k in {
        "provider", "uf", "project", "service", "sla", "priority", "termination_reason", "responsible",
    }}
    if dimension_filters or not is_global:
        tickets = OTRSTicket.objects.all()
        if not is_global:
            tickets = tickets.filter(
                config_items__is_present_in_source=True,
                config_items__cmdb_link__in=links,
            )
        tickets = apply_filters(tickets, links, dimension_filters)
        intervals = intervals.filter(ticket_external_id__in=tickets.values("external_id"))
    active = Q(known=True, business_status__in=BACKLOG_STATUSES) & ~Q(responsibility="IMPROCEDENTE")
    if (filters or {}).get("queue"):
        # Queue membership belongs to the historical interval, never today's mirror.
        intervals = intervals.filter(Q(known=False) | Q(queue_name=filters["queue"]))
    if (filters or {}).get("status"):
        active &= business_status_query("business_status", filters["status"])
    if (filters or {}).get("source_state"):
        state = filters["source_state"]
        active &= (
            business_status_query("business_status", state)
            if state in {"OPEN", "PENDING", "RESOLVED", "CLOSED", "CANCELED", "SCHEDULED", "UNKNOWN"}
            else Q(external_state_name=state)
        )
    if (filters or {}).get("responsibility"):
        active &= Q(responsibility=filters["responsibility"])
    return intervals, active


def _bucket_peaks(intervals, active_filter, buckets, instants, *, age_over_24h=False):
    """Compute exact peak simultaneous active tickets inside each multi-hour bucket via sweep-line."""
    if not instants:
        return {}
    min_start = min(start for key, _lbl, start, _end in buckets if key in instants)
    max_end = max(instants[key][1] for key in instants)
    rows = list(
        intervals.filter(active_filter, started_at__lt=max_end, ended_at__gt=min_start)
        .values_list("ticket_external_id", "started_at", "ended_at", "source_created_at")
    )
    if not rows:
        return {}

    peaks = {}
    for key, _lbl, b_start, _b_end in buckets:
        if key not in instants:
            continue
        _idx, b_through, _pos = instants[key]
        if (b_through - b_start) <= timedelta(hours=1):
            continue
        # Merge overlapping intervals per ticket inside [b_start, b_through) so one ticket never counts twice.
        per_ticket = defaultdict(list)
        for tid, st, en, created in rows:
            eff_start = max(st, b_start)
            if age_over_24h and created:
                eff_start = max(eff_start, created + timedelta(hours=24))
            eff_end = min(en, b_through)
            if eff_start < eff_end:
                per_ticket[tid].append((eff_start, eff_end))

        events = []
        for spans in per_ticket.values():
            spans.sort()
            cur_s, cur_e = spans[0]
            for s, e in spans[1:]:
                if s <= cur_e:
                    if e > cur_e:
                        cur_e = e
                else:
                    events.append((cur_s, 1))
                    events.append((cur_e, -1))
                    cur_s, cur_e = s, e
            events.append((cur_s, 1))
            events.append((cur_e, -1))

        if not events:
            continue
        # Process endings (-1) before openings (+1) at identical timestamps.
        events.sort(key=lambda item: (item[0], item[1]))
        current = 0
        best_val = 0
        best_at = None
        i = 0
        n = len(events)
        while i < n:
            ts = events[i][0]
            while i < n and events[i][0] == ts:
                current += events[i][1]
                i += 1
            if current > best_val:
                best_val = current
                best_at = ts
        if best_at is not None:
            peaks[key] = (best_val, best_at)
    return peaks


@transaction.atomic
def reconstructed_backlog(*, user, links, filters, buckets, period_end, apply_filters):
    coverage = OTRSBacklogHistory.objects.select_for_update().filter(queue_name=NOC_HISTORY_SCOPE).first()
    if coverage is None:
        return {
            "loaded": False, "available": False, "points": {}, "kind": "reconstructed",
            "message": "Histórico de eventos OTRS ainda não sincronizado; capturas FULL são observações separadas.",
        }
    intervals, active = _scoped_intervals(coverage, user=user, links=links, filters=filters, apply_filters=apply_filters)
    impossible = (
        filters.get("queue") not in (None, "", *NOC_QUEUE_NAMES)
        or filters.get("origin") not in (None, "", "SUPPORT")
        or bool(filters.get("stage"))
    )
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
    peaks = (
        {}
        if impossible
        else _bucket_peaks(
            intervals, active, buckets, instants,
            age_over_24h=(filters.get("age_bucket") == "OVER_24H"),
        )
    )
    points = {}
    for key, (index, instant, position) in instants.items():
        unknown = 0 if impossible else totals[f"u{index}"]
        value = None if unknown else (0 if impossible else totals[f"v{index}"])
        peak_val, peak_at = peaks.get(key, (None, None))
        if unknown or value is None:
            peak_val, peak_at = None, None
        elif peak_val is not None and peak_val < value:
            peak_val, peak_at = value, instant - timedelta(microseconds=1)
        points[key] = {
            "value": value,
            "unknown_tickets": unknown,
            "at": (instant - timedelta(microseconds=1)).isoformat(),
            "position": position,
            "peak_value": peak_val,
            "peak_at": peak_at.isoformat() if peak_at else None,
        }
    known_points = [p for p in points.values() if p["value"] is not None]
    missing = len(buckets) - len(known_points)
    earliest_local = timezone.localtime(coverage.earliest_at).strftime("%d/%m/%Y %H:%M")
    through_local = timezone.localtime(coverage.through).strftime("%d/%m/%Y %H:%M")
    message = f"Histórico indisponível em {missing} período(s)." if missing else ""
    coverage_note = (
        f"Histórico indisponível em {missing} período(s). Cobertura de eventos OTRS: {earliest_local} até {through_local}."
        if missing else
        f"Reconstruído por eventos OTRS (cobertura até {through_local})."
    )
    return {
        "loaded": True,
        "available": bool(known_points),
        "kind": "reconstructed",
        "points": points,
        "point_count": len(known_points),
        "missing_intervals": missing,
        "earliest_at": coverage.earliest_at.isoformat(),
        "earliest_label": earliest_local,
        "through": coverage.through.isoformat(),
        "through_label": through_local,
        "message": message,
        "coverage_note": coverage_note,
        "source": "OTRS ticket_history: estados, filas e procedência",
    }


def reconstructed_backlog_at(*, user, links, filters, target_dt, apply_filters):
    """Return exact reconstructed NOC backlog at a specific past timestamp, or explicit coverage gap."""
    coverage = OTRSBacklogHistory.objects.filter(queue_name=NOC_HISTORY_SCOPE).first()
    if coverage is None:
        return {
            "available": False,
            "covered": False,
            "message": "Histórico de eventos OTRS ainda não sincronizado.",
            "requested_at": timezone.localtime(target_dt).isoformat(),
            "requested_label": timezone.localtime(target_dt).strftime("%d/%m/%Y %H:%M"),
        }
    earliest_local = timezone.localtime(coverage.earliest_at).strftime("%d/%m/%Y %H:%M")
    through_local = timezone.localtime(coverage.through).strftime("%d/%m/%Y %H:%M")
    req_local = timezone.localtime(target_dt)
    if target_dt < coverage.earliest_at or target_dt > coverage.through:
        return {
            "available": False,
            "covered": False,
            "earliest_at": coverage.earliest_at.isoformat(),
            "earliest_label": earliest_local,
            "through": coverage.through.isoformat(),
            "through_label": through_local,
            "requested_at": req_local.isoformat(),
            "requested_label": req_local.strftime("%d/%m/%Y %H:%M"),
            "message": f"Horário consultado ({req_local.strftime('%d/%m/%Y %H:%M')}) fora da cobertura de eventos OTRS ({earliest_local} até {through_local}).",
        }

    intervals, active = _scoped_intervals(coverage, user=user, links=links, filters=filters, apply_filters=apply_filters)
    at = Q(started_at__lt=target_dt, ended_at__gte=target_dt)
    known_filter = active & at
    counts = intervals.aggregate(
        total=Count("ticket_external_id", distinct=True, filter=known_filter),
        open_count=Count("ticket_external_id", distinct=True, filter=known_filter & Q(business_status="OPEN")),
        pending_count=Count("ticket_external_id", distinct=True, filter=known_filter & Q(business_status="PENDING")),
        over_24h=Count(
            "ticket_external_id", distinct=True,
            filter=known_filter & Q(source_created_at__lte=target_dt - timedelta(hours=24)),
        ),
        over_72h=Count(
            "ticket_external_id", distinct=True,
            filter=known_filter & Q(source_created_at__lte=target_dt - timedelta(hours=72)),
        ),
        unknown_tickets=Count("ticket_external_id", distinct=True, filter=at & Q(known=False)),
    )
    unknown = counts["unknown_tickets"] or 0
    return {
        "available": unknown == 0,
        "covered": True,
        "total": None if unknown else (counts["total"] or 0),
        "open": None if unknown else (counts["open_count"] or 0),
        "pending": None if unknown else (counts["pending_count"] or 0),
        "over_24h": None if unknown else (counts["over_24h"] or 0),
        "over_72h": None if unknown else (counts["over_72h"] or 0),
        "unknown_tickets": unknown,
        "requested_at": req_local.isoformat(),
        "requested_label": req_local.strftime("%d/%m/%Y %H:%M"),
        "earliest_at": coverage.earliest_at.isoformat(),
        "earliest_label": earliest_local,
        "through": coverage.through.isoformat(),
        "through_label": through_local,
        "source": "OTRS ticket_history: estados, filas e procedência",
        "message": (
            f"Há {unknown} ticket(s) com estado indeterminado neste instante."
            if unknown else
            f"Observado em {req_local.strftime('%d/%m/%Y %H:%M')} (reconstruído por eventos OTRS)."
        ),
    }
