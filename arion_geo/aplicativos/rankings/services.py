"""Read-only operational provider rankings from the scoped CMDB/OTRS mirrors."""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone as datetime_timezone
import re
import unicodedata
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from aplicativos.integracoes.models import OTRSTicketConfigItem
from aplicativos.integracoes.services.analytics import (
    BUSINESS_OPEN_STATUSES, LinkQueryService, TicketQueryService,
    operational_resolution_at,
)
from aplicativos.integracoes.services.closing_reason import ClosingReasonCanonicalizer
from aplicativos.integracoes.services.semantics import provider_label
from aplicativos.painel.services.analytics import AnalyticsQueryService
from aplicativos.painel.services.provider_logos import provider_logo


VALID_STATES = AnalyticsQueryService.OPERATIONAL_VALID_STATES
BACKLOG_STATES = BUSINESS_OPEN_STATUSES
TERMINAL_STATES = ("RESOLVED", "CLOSED")
SMALL_SAMPLE_LINK_THRESHOLD = 5
BEST_RANKING_MIN_LINKS = 5
BEST_RANKING_MIN_TICKETS = 3
RESPONSIBILITY_REVIEW_MIN_TICKETS = 6
RESPONSIBILITY_REVIEW_IMPROCEDENT_RATE = 0.60
RANKING_WEIGHTS_V1 = {
    "proportional": {
        "affected_rate": 0.25,
        "tickets_per_100_links": 0.20,
        "recurrent_rate": 0.20,
        "over_24h_rate": 0.20,
        "backlog_per_100_links": 0.10,
        "criticality_rate": 0.05,
    },
    "impact": {
        "tickets": 0.40,
        "affected_links": 0.30,
        "tickets_over_24h": 0.20,
        "backlog_current": 0.10,
    },
    "score": {"proportional": 0.70, "impact": 0.30},
}
RANKING_WEIGHTS = {
    "impact_noc": 0.30,
    "affected_base": 0.20,
    "recurrent": 0.20,
    "over_24h": 0.20,
    "backlog_current": 0.10,
}

PRIORITY_GROUPS = {
    "high": {"alto", "high"},
    "critical": {"critico", "critical"},
}
def _plain(value):
    return "".join(char for char in unicodedata.normalize("NFKD", str(value or "").casefold())
                   if not unicodedata.combining(char)).strip()


def _provider_name_key(value):
    return " ".join(_plain(value).split())


def _local_bound(day):
    zone = ZoneInfo(settings.TIME_ZONE)
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(datetime_timezone.utc)


def _period_bounds(start, end):
    return _local_bound(start), _local_bound(end + timedelta(days=1))


def _previous_month(day):
    first = day.replace(day=1)
    previous_end = first - timedelta(days=1)
    return previous_end.replace(day=1), previous_end


def _period_month(day):
    return day.strftime("%Y-%m")


def _next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _score_band(score, *, no_occurrences=False):
    if no_occurrences or score == 0:
        return "Sem ocorrências", "no-occurrences"
    if score >= 80:
        return "Crítico", "critical"
    if score >= 60:
        return "Alto", "high"
    if score >= 40:
        return "Moderado", "attention"
    if score >= 20:
        return "Baixo", "low-attention"
    return "Muito baixo", "good"


def _percentile_scores(items, key):
    """Average-rank percentile, robust to outliers and ties."""
    ordered = sorted((float(item.get(key, 0) or 0), index) for index, item in enumerate(items))
    if len(ordered) < 2:
        return [0.0] * len(items)
    scores = [0.0] * len(items)
    cursor = 0
    while cursor < len(ordered):
        end = cursor
        while end + 1 < len(ordered) and ordered[end + 1][0] == ordered[cursor][0]:
            end += 1
        percentile = ((cursor + end) / 2) / (len(ordered) - 1) * 100
        for _, original_index in ordered[cursor:end + 1]:
            scores[original_index] = percentile
        cursor = end + 1
    return scores


def _apply_scores_v1(rows):
    """Keep the former index available for same-snapshot historical studies."""
    proportional_keys = tuple(RANKING_WEIGHTS_V1["proportional"])
    impact_keys = tuple(RANKING_WEIGHTS_V1["impact"])
    normalized = {}
    for key in (*proportional_keys, *impact_keys):
        normalized[key] = _percentile_scores(rows, key)
    for index, row in enumerate(rows):
        proportional = sum(normalized[key][index] * weight for key, weight in RANKING_WEIGHTS_V1["proportional"].items())
        impact = sum(normalized[key][index] * weight for key, weight in RANKING_WEIGHTS_V1["impact"].items())
        row["proportional_score_v1"] = round(proportional, 1)
        row["impact_score_v1"] = round(impact, 1)
        row["risk_score_v1"] = round(
            proportional * RANKING_WEIGHTS_V1["score"]["proportional"]
            + impact * RANKING_WEIGHTS_V1["score"]["impact"], 1,
        )
    return rows


def _nearest_rank_p95(values):
    """Nearest-rank P95. Empty/zero distributions stay zero without a score floor."""
    ordered = sorted(max(0.0, float(value or 0)) for value in values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, (95 * len(ordered) + 99) // 100 - 1))
    return ordered[index]


def _apply_scores(rows, *, normalization_rows=None):
    """Apply ARION V2 using the current provider set and robust P95 caps."""
    reference_rows = rows if normalization_rows is None else normalization_rows
    p95_tickets = _nearest_rank_p95(row.get("tickets", 0) for row in reference_rows)
    p95_affected = _nearest_rank_p95(row.get("links_affected", 0) for row in reference_rows)
    for row in rows:
        tickets = max(0, row.get("tickets", 0) or 0)
        links_total = max(0, row.get("links_total", 0) or 0)
        affected = max(0, row.get("links_affected", 0) or 0)
        recurrent = max(0, row.get("recurrent_links", 0) or 0)
        eligible = max(0, row.get("eligible_tickets", 0) or 0)
        over_24h = max(0, row.get("tickets_over_24h", 0) or 0)
        backlog = max(0, row.get("backlog_current", 0) or 0)

        ticket_impact = min(tickets / p95_tickets, 1.0) if p95_tickets else 0.0
        affected_impact = min(affected / p95_affected, 1.0) if p95_affected else 0.0
        impact_noc = 30 * (0.60 * ticket_impact + 0.40 * affected_impact)
        affected_base = 20 * min(affected / links_total, 1.0) if links_total else 0.0
        recurrent_score = 20 * min(recurrent / affected, 1.0) if affected else 0.0
        over_24h_score = 20 * min(over_24h / eligible, 1.0) if eligible else 0.0
        backlog_score = 10 * min(backlog / max(tickets, 1), 1.0)

        components = {
            "impact_noc": round(impact_noc, 1),
            "affected_base": round(affected_base, 1),
            "recurrent": round(recurrent_score, 1),
            "over_24h": round(over_24h_score, 1),
            "backlog_current": round(backlog_score, 1),
        }
        row["impact_noc_score"] = components["impact_noc"]
        row["affected_base_score"] = components["affected_base"]
        row["recurrent_score"] = components["recurrent"]
        row["over_24h_score"] = components["over_24h"]
        row["backlog_score"] = components["backlog_current"]
        row["risk_components"] = components
        row["impact_score"] = row["impact_noc_score"]
        row["proportional_score"] = round(sum(components[key] for key in (
            "affected_base", "recurrent", "over_24h", "backlog_current",
        )), 1)
        row["risk_score"] = round(sum(components.values()), 1)
        row["tickets_per_link"] = round(tickets / links_total, 2) if links_total else 0.0
        row["risk_eligible"] = tickets >= 1 or backlog > 0
        row["best_eligible"] = tickets >= 1 and (
            links_total >= BEST_RANKING_MIN_LINKS or tickets >= BEST_RANKING_MIN_TICKETS
        )
        row["no_occurrence"] = tickets == 0 and backlog == 0
        row["risk_label"], row["risk_class"] = _score_band(
            row["risk_score"], no_occurrences=row["no_occurrence"],
        )
    return rows


class RankingService:
    """Build monthly or rolling-three-month metrics in a bounded number of queries."""

    @classmethod
    def build(cls, user, *, date_from, date_to, filters=None, now=None, detail_provider_id=None, detail_ticket_page=1, detail_ticket_page_size=10):
        filters = dict(filters or {})
        if filters.get("reason"):
            filters["reason"] = ClosingReasonCanonicalizer.key(filters["reason"])
        now = now or timezone.now()
        links_qs = LinkQueryService.base(user)
        if filters.get("operator"):
            links_qs = links_qs.filter(operator_id=filters["operator"])
        if filters.get("project"):
            links_qs = links_qs.filter(business_project=filters["project"])
        if filters.get("client"):
            links_qs = links_qs.filter(contracting_client=filters["client"])
        if filters.get("uf"):
            links_qs = links_qs.filter(state__iexact=filters["uf"])
        if filters.get("q"):
            links_qs = links_qs.filter(
                Q(operator__name__icontains=filters["q"]) | Q(isp_raw__icontains=filters["q"])
                | Q(name__icontains=filters["q"]) | Q(source_number__icontains=filters["q"])
            )

        # Link scope and canonical business dimensions are applied before counts.
        links = list(links_qs.select_related("operator").order_by("pk"))
        links_by_id = {link.pk: link for link in links}
        provider_links = defaultdict(list)
        provider_names = {}
        provider_ids = defaultdict(set)
        for link in links:
            provider_name = (link.operator.name or "").strip() if link.operator_id else ""
            if not provider_name:
                continue
            provider_key = _provider_name_key(provider_name)
            provider_links[provider_key].append(link)
            provider_names.setdefault(provider_key, provider_name)
            provider_ids[provider_key].add(link.operator_id)

        # Match the Dashboard's current CMDB snapshot: every present link counts,
        # grouped by the resolved provider name, regardless of deployment state.
        inventory_groups = list(
            links_qs.filter(operator__isnull=False).exclude(operator__name="")
            .values("operator__name").annotate(links=Count("pk", distinct=True)).order_by()
        )
        deployment_active_links_total = links_qs.filter(
            Q(deployment_state__iexact="Operacional")
            | Q(deployment_state__iexact="Operational")
        ).count()
        inventory_links_by_provider = defaultdict(int)
        for group in inventory_groups:
            provider_key = _provider_name_key(group["operator__name"])
            inventory_links_by_provider[provider_key] += group["links"]
            provider_names[provider_key] = group["operator__name"].strip()
        operational_groups = list(
            links_qs.filter(
                Q(deployment_state__iexact="Operacional")
                | Q(deployment_state__iexact="Operational"),
                operator__isnull=False,
            ).exclude(operator__name="").values("operator__name")
            .annotate(links=Count("pk", distinct=True)).order_by()
        )
        deployment_active_links_by_provider = defaultdict(int)
        for group in operational_groups:
            deployment_active_links_by_provider[_provider_name_key(group["operator__name"])] += group["links"]
        provider_id_by_name = {
            key: min(ids) for key, ids in provider_ids.items() if ids
        }
        provider_primary_id = {
            operator_id: provider_id_by_name[key]
            for key, ids in provider_ids.items() for operator_id in ids
        }
        inventory_links_total = len(links)
        identified_inventory_links = sum(inventory_links_by_provider.values())
        active_links_unidentified = max(0, inventory_links_total - identified_inventory_links)
        top10_links = sum(sorted(inventory_links_by_provider.values(), reverse=True)[:10])
        top10_link_concentration = (
            round(top10_links / inventory_links_total * 100, 1) if inventory_links_total else 0.0
        )

        start_at, end_exclusive = _period_bounds(date_from, date_to)
        trend_month_start = date_to.replace(day=1)
        prev_month_start, prev_month_end = _previous_month(trend_month_start)
        month_index = trend_month_start.year * 12 + trend_month_start.month - 1 - 2
        chart_history_start = datetime(month_index // 12, month_index % 12 + 1, 1).date()
        history_start = min(date_from.replace(day=1), chart_history_start)
        history_start_at = _local_bound(history_start)
        filter_ticket_ids = cls._filtered_ticket_ids(links_qs, filters)
        resolved_in_history = (
            Q(resolved_at__gte=history_start_at, resolved_at__lt=end_exclusive)
            | Q(resolved_at__isnull=True, terminal_state_at__gte=history_start_at, terminal_state_at__lt=end_exclusive)
        )
        relevant_tickets = TicketQueryService.noc_tickets().filter(
            business_status__in=VALID_STATES,
        ).filter(
            Q(source_created_at__gte=history_start_at, source_created_at__lt=end_exclusive)
            | Q(business_status__in=BACKLOG_STATES)
            | resolved_in_history
        )
        relevant_tickets = cls._apply_ticket_filters(relevant_tickets, filters)
        if filter_ticket_ids is not None:
            relevant_tickets = relevant_tickets.filter(pk__in=filter_ticket_ids)

        relations = OTRSTicketConfigItem.objects.filter(
            is_present_in_source=True,
            cmdb_link_id__in=links_by_id,
            ticket_id__in=relevant_tickets.values("pk"),
        ).select_related("ticket", "cmdb_link", "cmdb_link__operator").order_by("ticket_id", "cmdb_link_id").iterator(chunk_size=2000)

        tickets = {}
        link_tickets = defaultdict(set)
        for relation in relations:
            ticket, link = relation.ticket, relation.cmdb_link
            if not link or link.pk not in links_by_id:
                continue
            tickets[ticket.pk] = ticket
            link_tickets[link.pk].add(ticket.pk)

        active_ids = {
            ticket_id for ticket_id, ticket in tickets.items()
            if ticket.business_status in BACKLOG_STATES
        }
        active_with_created_ids = {
            ticket_id for ticket_id in active_ids if tickets[ticket_id].source_created_at
        }
        open_aged_ids = {
            ticket_id for ticket_id in active_ids
            if ticket.source_created_at and now - ticket.source_created_at > timedelta(hours=24)
        }

        def in_window(ticket, start, end):
            return bool(ticket.source_created_at and start <= ticket.source_created_at < end)

        def raw_for(start, end):
            start_bound, end_bound = _period_bounds(start, end)
            selected_links = {}
            selected_ticket_ids = set()
            recurrent_link_ids = set()
            terminal_aged_ids = set()
            eligible_ids = set()
            high_critical_ids = set()
            for link_id, ids in link_tickets.items():
                chosen = {ticket_id for ticket_id in ids if in_window(tickets[ticket_id], start_bound, end_bound)}
                if not chosen:
                    continue
                selected_links[link_id] = chosen
                selected_ticket_ids.update(chosen)
                if len(chosen) >= 2:
                    recurrent_link_ids.add(link_id)
            for ticket_id in selected_ticket_ids:
                ticket = tickets[ticket_id]
                operational_end = operational_resolution_at(ticket)
                if ticket.business_status in TERMINAL_STATES and ticket.source_created_at and operational_end:
                    eligible_ids.add(ticket_id)
                    if operational_end - ticket.source_created_at > timedelta(hours=24):
                        terminal_aged_ids.add(ticket_id)
                elif ticket.business_status in BACKLOG_STATES and ticket.source_created_at:
                    eligible_ids.add(ticket_id)
                if _plain(ticket.priority_name) in PRIORITY_GROUPS["high"] | PRIORITY_GROUPS["critical"]:
                    high_critical_ids.add(ticket_id)

            providers = {}
            for provider_key, links_for_provider in provider_links.items():
                operator_id = provider_id_by_name[provider_key]
                provider_ticket_ids = set()
                related_ticket_ids = set()
                affected_link_ids = set()
                recurrent_for_provider = set()
                for link in links_for_provider:
                    related_ticket_ids.update(link_tickets.get(link.pk, set()))
                    ids = selected_links.get(link.pk, set())
                    if ids:
                        affected_link_ids.add(link.pk)
                        provider_ticket_ids.update(ids)
                    if link.pk in recurrent_link_ids:
                        recurrent_for_provider.add(link.pk)
                backlog_ids = set().union(*(
                    link_tickets.get(link.pk, set()) & active_ids for link in links_for_provider
                )) if links_for_provider else set()
                aged_open_for_provider = backlog_ids & open_aged_ids
                terminal_aged_for_provider = provider_ticket_ids & terminal_aged_ids
                over_ids = aged_open_for_provider | terminal_aged_for_provider
                eligible_for_provider = set()
                for link in links_for_provider:
                    ids = link_tickets.get(link.pk, set())
                    eligible_for_provider.update(ids & active_with_created_ids)
                    eligible_for_provider.update(ids & eligible_ids)
                # Provider rows deduplicate tickets across its own CIs.
                total = len(links_for_provider)
                tickets_count = len(provider_ticket_ids)
                affected = len(affected_link_ids)
                over_count = len(over_ids)
                eligible_count = len(eligible_for_provider)
                backlog_count = len(backlog_ids)
                critical_count = len(provider_ticket_ids & high_critical_ids)
                recurrent_count = len(recurrent_for_provider)
                responsibility_counts = defaultdict(int)
                for ticket_id in provider_ticket_ids:
                    normalized = tickets[ticket_id].responsibility_normalized or "INDETERMINADO"
                    if normalized not in {"PROCEDENTE", "IMPROCEDENTE", "INDETERMINADO"}:
                        normalized = "INDETERMINADO"
                    responsibility_counts[normalized] += 1
                responsibility_known = responsibility_counts["PROCEDENTE"] + responsibility_counts["IMPROCEDENTE"]
                resolution_durations = []
                for ticket_id in related_ticket_ids:
                    ticket = tickets[ticket_id]
                    operational_end = operational_resolution_at(ticket)
                    if (
                        ticket.business_status in TERMINAL_STATES
                        and ticket.source_created_at and operational_end
                        and start_bound <= operational_end < end_bound
                    ):
                        duration = (operational_end - ticket.source_created_at).total_seconds() / 3600
                        if duration >= 0:
                            resolution_durations.append(duration)
                providers[operator_id] = {
                    "id": operator_id,
                    "name": provider_names.get(provider_key) or links_for_provider[0].operator.name,
                    "links_total": inventory_links_by_provider.get(provider_key, total),
                    "links_active": inventory_links_by_provider.get(provider_key, total),
                    "deployment_active_links": deployment_active_links_by_provider.get(provider_key, 0),
                    "tickets": tickets_count,
                    "links_affected": affected,
                    "affected_rate": affected / total if total else 0,
                    "tickets_per_100_links": tickets_count / total * 100 if total else 0,
                    "tickets_per_100_active_links": (
                        round(tickets_count / inventory_links_by_provider[provider_key] * 100, 1)
                        if inventory_links_by_provider.get(provider_key) else None
                    ),
                    "avg_resolution_hours": (
                        sum(resolution_durations) / len(resolution_durations) if resolution_durations else None
                    ),
                    "resolution_tickets": len(resolution_durations),
                    "recurrent_links": recurrent_count,
                    "recurrent_rate": recurrent_count / affected if affected else 0,
                    "backlog_current": backlog_count,
                    "backlog_per_100_links": backlog_count / total * 100 if total else 0,
                    "aged_open": len(aged_open_for_provider),
                    "terminal_over_24h": len(terminal_aged_for_provider),
                    "tickets_over_24h": over_count,
                    "eligible_tickets": eligible_count,
                    "over_24h_rate": over_count / eligible_count if eligible_count else 0,
                    "criticality_rate": critical_count / tickets_count if tickets_count else 0,
                    "high_critical_tickets": critical_count,
                    "procedentes": responsibility_counts["PROCEDENTE"],
                    "improcedentes": responsibility_counts["IMPROCEDENTE"],
                    "indeterminados": responsibility_counts["INDETERMINADO"],
                    "responsibility_known": responsibility_known,
                    "procedure_rate": responsibility_counts["PROCEDENTE"] / responsibility_known if responsibility_known else 0,
                    "improcedent_rate": responsibility_counts["IMPROCEDENTE"] / responsibility_known if responsibility_known else 0,
                    "review_responsibility": (
                        responsibility_known >= RESPONSIBILITY_REVIEW_MIN_TICKETS
                        and responsibility_counts["IMPROCEDENTE"] / responsibility_known >= RESPONSIBILITY_REVIEW_IMPROCEDENT_RATE
                    ),
                    "impact_tickets": tickets_count,
                    "impact_affected_links": affected,
                    "impact_tickets_over_24h": over_count,
                    "impact_backlog": backlog_count,
                    "period_ticket_ids": provider_ticket_ids,
                    "affected_link_ids": affected_link_ids,
                    "recurrent_link_ids": recurrent_for_provider,
                    "backlog_ticket_ids": backlog_ids,
                    "over_24h_ticket_ids": over_ids,
                    "eligible_ticket_ids": eligible_for_provider,
                    "links_for_provider": links_for_provider,
                    "selected_link_tickets": {link.pk: selected_links.get(link.pk, set()) for link in links_for_provider},
                }
            return providers

        current_rows_map = raw_for(date_from, date_to)
        trend_rows_map = raw_for(trend_month_start, date_to)
        previous_rows_map = raw_for(prev_month_start, prev_month_end)
        history_periods = []
        cursor = chart_history_start
        for _ in range(3):
            month_end = _next_month(cursor) - timedelta(days=1)
            history_periods.append((cursor, min(month_end, date_to)))
            cursor = _next_month(cursor)
        history_maps = [raw_for(start, end) for start, end in history_periods if start <= end]
        def rows_in_active_filter(provider_rows):
            return [row for row in provider_rows.values()
                    if (not filters.get("only_aged") or row["tickets_over_24h"])
                    and (not filters.get("only_recurrent") or row["recurrent_links"])]

        for month_rows in history_maps:
            _apply_scores(list(month_rows.values()), normalization_rows=rows_in_active_filter(month_rows))
        history_by_provider = defaultdict(list)
        month_labels = []
        for (history_start_day, _), month_rows in zip(history_periods, history_maps):
            month_label = history_start_day.strftime("%Y-%m")
            month_labels.append(month_label)
            for provider_id, month_row in month_rows.items():
                history_by_provider[provider_id].append({"month": month_label, "score": month_row["risk_score"]})
        rows = list(current_rows_map.values())
        trend_rows = list(trend_rows_map.values())
        previous_rows = list(previous_rows_map.values())
        _apply_scores(rows, normalization_rows=rows_in_active_filter(current_rows_map))
        _apply_scores(trend_rows, normalization_rows=rows_in_active_filter(trend_rows_map))
        _apply_scores(previous_rows, normalization_rows=rows_in_active_filter(previous_rows_map))
        trend_by_id = {row["id"]: row for row in trend_rows}
        previous_by_id = {row["id"]: row for row in previous_rows}
        for row in rows:
            trend_current = trend_by_id.get(row["id"])
            previous = previous_by_id.get(row["id"])
            current_month_score = trend_current["risk_score"] if trend_current else 0.0
            previous_score = previous["risk_score"] if previous else 0.0
            delta = round(current_month_score - previous_score, 1)
            row["previous_score"] = previous_score
            row["monthly_score"] = current_month_score
            row["history"] = history_by_provider.get(row["id"], [])
            row["trend_delta"] = delta
            row["trend"] = "worse" if delta > 2 else "better" if delta < -2 else "stable"
            row["trend_label"] = "Piorou" if row["trend"] == "worse" else "Melhorou" if row["trend"] == "better" else "Estável"
            row["sample_small"] = row["links_total"] < SMALL_SAMPLE_LINK_THRESHOLD
            row["share"] = round(row["links_total"] / inventory_links_total * 100, 1) if inventory_links_total else 0
            row["active_share"] = round(row["links_active"] / inventory_links_total * 100, 1) if inventory_links_total else 0
            row["affected_rate_pct"] = round(row["affected_rate"] * 100, 1)
            row["recurrent_rate_pct"] = round(row["recurrent_rate"] * 100, 1)
            row["over_24h_rate_pct"] = round(row["over_24h_rate"] * 100, 1)
            row["tickets_per_100_links"] = round(row["tickets_per_100_links"], 1)
            row["tickets_per_100_active_links"] = (
                round(row["tickets_per_100_active_links"], 1)
                if row["tickets_per_100_active_links"] is not None else None
            )
            if row["avg_resolution_hours"] is not None:
                resolution_minutes = round(row["avg_resolution_hours"] * 60)
                resolution_hours, resolution_minutes = divmod(resolution_minutes, 60)
                row["avg_resolution_display"] = f"{resolution_hours}h {resolution_minutes:02d}m"
            else:
                row["avg_resolution_display"] = "—"
            logo = provider_logo(row["name"])
            row["logo"] = logo["initials"]
            row["logo_url"] = logo["url"]
            row["projects"] = sorted({link.business_project for link in row["links_for_provider"] if link.business_project})
            row["clients"] = sorted({link.contracting_client for link in row["links_for_provider"] if link.contracting_client})
            row["states"] = sorted({link.state for link in row["links_for_provider"] if link.state})
            row["trend_delta"] = delta

        def sort_key(row):
            if filters.get("only_aged") and not row["tickets_over_24h"]:
                return None
            if filters.get("only_recurrent") and not row["recurrent_links"]:
                return None
            return row

        rows = [row for row in rows if sort_key(row)]
        rows.sort(key=lambda row: (
            -row["risk_score"], -row["impact_noc_score"], -row["tickets"],
            -row["links_affected"], row["name"].casefold(),
        ))
        for position, row in enumerate(rows, 1):
            row["ranking_position"] = position

        # The national KPI follows Cockpit's ticket universe for an unfiltered view.
        scoped_tickets = TicketQueryService.base(user, links=LinkQueryService.base(user)).filter(
            business_status__in=VALID_STATES,
            source_created_at__gte=start_at,
            source_created_at__lt=end_exclusive,
        )
        scoped_tickets = cls._apply_ticket_filters(scoped_tickets, filters)
        if filter_ticket_ids is not None:
            scoped_tickets = scoped_tickets.filter(pk__in=filter_ticket_ids)
        national_ticket_ids = set(scoped_tickets.values_list("pk", flat=True))
        if filters.get("only_aged"):
            national_ticket_ids &= {ticket_id for row in current_rows_map.values() for ticket_id in row["over_24h_ticket_ids"]}
        if filters.get("only_recurrent"):
            national_ticket_ids &= {ticket_id for row in current_rows_map.values()
                                    for link_id in row["recurrent_link_ids"]
                                    for ticket_id in row["selected_link_tickets"].get(link_id, set())}

        total_tickets = len(national_ticket_ids)
        period_ids_by_link = {
            link_id: {ticket_id for ticket_id in ids
                      if tickets[ticket_id].source_created_at and start_at <= tickets[ticket_id].source_created_at < end_exclusive}
            for link_id, ids in link_tickets.items()
        }
        total_affected_links = {link_id for link_id, ids in period_ids_by_link.items() if ids}
        total_recurrent_links = {link_id for link_id, ids in period_ids_by_link.items() if len(ids) >= 2}
        total_backlog_ids = set(active_ids)
        terminal_over_ids = {
            ticket_id for ids in period_ids_by_link.values() for ticket_id in ids
            if tickets[ticket_id].business_status in TERMINAL_STATES
            and tickets[ticket_id].source_created_at and operational_resolution_at(tickets[ticket_id])
            and operational_resolution_at(tickets[ticket_id]) - tickets[ticket_id].source_created_at > timedelta(hours=24)
        }
        total_over_ids = open_aged_ids | terminal_over_ids
        if filters.get("only_recurrent"):
            total_affected_links &= total_recurrent_links
            total_over_ids &= {ticket_id for link_id in total_recurrent_links for ticket_id in period_ids_by_link[link_id]}
        if filters.get("only_aged"):
            total_affected_links &= {link_id for link_id, ids in link_tickets.items() if ids & total_over_ids}
            total_recurrent_links &= total_affected_links

        link_rows = cls._ranked_links(links, link_tickets, tickets, current_rows_map, start_at, end_exclusive, filters)
        link_limit = None if filters.get("links_all") else 20
        ticket_export_rows = []
        for provider_row in rows:
            for ticket_row in cls._provider_ticket_rows(provider_row, tickets, link_tickets, now=now):
                if not (ticket_row["in_period"] or ticket_row["backlog"]):
                    continue
                if filters.get("only_aged") and not ticket_row["over_24h"]:
                    continue
                if filters.get("only_recurrent") and not ticket_row["on_recurrent_link"]:
                    continue
                ticket_export_rows.append(ticket_row)
        ticket_export_rows.sort(key=lambda item: (item["created_sort"], item["provider"].casefold()), reverse=True)
        return {
            "providers": rows,
            "all_providers": list(current_rows_map.values()),
            "ranked_links": link_rows if link_limit is None else link_rows[:link_limit],
            "ranked_links_total": len(link_rows),
            "ranked_links_all": link_rows,
            "ticket_export_rows": ticket_export_rows,
            "links_total": inventory_links_total,
            "links_active": inventory_links_total,
            "deployment_active_links_total": deployment_active_links_total,
            "active_links_unidentified": active_links_unidentified,
            "active_provider_count": len(inventory_links_by_provider),
            "top10_links": top10_links,
            "top10_link_concentration": top10_link_concentration,
            "provider_count": len(rows),
            "tickets_valid": total_tickets,
            "links_affected": len(total_affected_links),
            "links_recurrent": len(total_recurrent_links),
            "tickets_over_24h": len(total_over_ids),
            "aged_open": len(open_aged_ids),
            "terminal_over_24h": len(terminal_over_ids),
            "backlog_current": len(total_backlog_ids),
            "previous_month": _period_month(prev_month_start),
            "selected_month": _period_month(date_from),
            "history_months": month_labels,
            "start": date_from,
            "end": date_to,
            "total_tickets_by_provider": sum(row["tickets"] for row in rows),
            "detail": cls._provider_detail(
                current_rows_map.get(provider_primary_id.get(int(detail_provider_id), int(detail_provider_id)))
                if detail_provider_id and str(detail_provider_id).isdigit() else None,
                tickets, link_tickets, ticket_page=detail_ticket_page, ticket_page_size=detail_ticket_page_size, now=now,
            ) if detail_provider_id else None,
            "available_months": cls._available_months(user),
            "filter_options": cls._filter_options(links, tickets.values()),
            "scoring": {
                "weights": RANKING_WEIGHTS,
                "weights_v1": RANKING_WEIGHTS_V1,
                "small_sample_threshold": SMALL_SAMPLE_LINK_THRESHOLD,
                "best_ranking_min_links": BEST_RANKING_MIN_LINKS,
                "best_ranking_min_tickets": BEST_RANKING_MIN_TICKETS,
            },
        }

    @staticmethod
    def _apply_ticket_filters(queryset, filters):
        if filters.get("status"):
            queryset = queryset.filter(business_status=filters["status"])
        if filters.get("priority"):
            queryset = queryset.filter(priority_name=filters["priority"])
        if filters.get("reason"):
            queryset = queryset.filter(closing_code_raw__in=ClosingReasonCanonicalizer.raw_values(filters["reason"]))
        return queryset

    @staticmethod
    def _filtered_ticket_ids(links_qs, filters):
        if not any(filters.get(key) for key in ("operator", "project", "client", "uf", "q")):
            return None
        relation = OTRSTicketConfigItem.objects.filter(
            ticket_id=OuterRef("pk"), is_present_in_source=True, cmdb_link__in=links_qs,
        )
        ids = TicketQueryService.noc_tickets().annotate(_has_selected_link=Exists(relation)).filter(
            _has_selected_link=True,
        ).values("pk")
        return ids

    @classmethod
    def _available_months(cls, user):
        queryset = TicketQueryService.base(user, links=LinkQueryService.base(user)).filter(
            source_created_at__isnull=False,
        ).values_list("source_created_at", flat=True)
        months = {_period_month(timezone.localtime(stamp).date()) for stamp in queryset.iterator(chunk_size=2000) if stamp}
        return sorted(months, reverse=True)

    @staticmethod
    def _filter_options(links, tickets):
        tickets = list(tickets)
        raw_reasons = {
            ticket.closing_code_raw for ticket in tickets
            if ticket.closing_code_raw and ticket.business_status in TERMINAL_STATES
        }
        return {
            "clients": sorted({link.contracting_client for link in links if link.contracting_client}, key=str.casefold),
            "projects": sorted({link.business_project for link in links if link.business_project}, key=str.casefold),
            "ufs": sorted({link.state for link in links if link.state}, key=str.casefold),
            "priorities": sorted({ticket.priority_name for ticket in tickets if ticket.priority_name}, key=str.casefold),
            "statuses": VALID_STATES,
            "reasons": [item["label"] for item in ClosingReasonCanonicalizer.options(raw_reasons)],
        }

    @staticmethod
    def _ranked_links(links, link_tickets, tickets, provider_rows, start_at, end_exclusive, filters):
        rows = []
        over_by_link = defaultdict(set)
        current_link_tickets = {}
        for provider in provider_rows.values():
            current_link_tickets.update(provider["selected_link_tickets"])
            for link in provider["links_for_provider"]:
                over_by_link[link.pk].update(link_tickets.get(link.pk, set()) & provider["over_24h_ticket_ids"])
        for link in links:
            period_ids = current_link_tickets.get(link.pk)
            if period_ids is None:
                period_ids = {ticket_id for ticket_id in link_tickets.get(link.pk, set())
                              if tickets[ticket_id].source_created_at and start_at <= tickets[ticket_id].source_created_at < end_exclusive}
            if not period_ids:
                continue
            recurrence_count = len(period_ids) >= 2
            aged_count = len(over_by_link[link.pk])
            if filters.get("only_aged") and not aged_count:
                continue
            if filters.get("only_recurrent") and not recurrence_count:
                continue
            rows.append({
                "id": link.pk,
                "name": link.designation or link.name or link.source_number or link.source_config_item_id,
                "config_item_id": link.source_config_item_id,
                "number": link.source_number or "",
                "provider": provider_label(link),
                "provider_id": link.operator_id,
                "client": link.contracting_client,
                "project": link.business_project,
                "state": link.state,
                "city": link.city,
                "city": link.city,
                "tickets": len(period_ids),
                "recurrent": recurrence_count,
                "over_24h": aged_count,
            })
        rows.sort(key=lambda row: (-row["tickets"], -row["over_24h"], row["name"].casefold()))
        return rows

    @staticmethod
    def _provider_ticket_rows(row, tickets, link_tickets, *, now=None):
        if not row:
            return []
        now = now or timezone.now()
        records = []
        visible_ids = row["period_ticket_ids"] | row["backlog_ticket_ids"]
        related_links_by_ticket = defaultdict(list)
        for link in row["links_for_provider"]:
            for ticket_id in link_tickets.get(link.pk, set()) & visible_ids:
                related_links_by_ticket[ticket_id].append(link)
        for ticket_id in visible_ids:
            ticket = tickets.get(ticket_id)
            if not ticket:
                continue
            linked = related_links_by_ticket.get(ticket_id, [])
            link_data = [{
                "name": link.designation or link.name or link.source_number or link.source_config_item_id,
                "config_item_id": link.source_config_item_id,
                "client": link.contracting_client,
                "project": link.business_project,
                "state": link.state,
                "city": link.city,
            } for link in linked]
            created = ticket.source_created_at
            terminal = ticket.terminal_state_at
            is_terminal = ticket.business_status in TERMINAL_STATES
            operational_end = operational_resolution_at(ticket) if is_terminal else now
            duration = (operational_end - created) if created and operational_end else None
            in_period = ticket_id in row["period_ticket_ids"]
            recurrent_link = any(link.pk in row["recurrent_link_ids"] for link in linked)
            normalized = ticket.responsibility_normalized or "INDETERMINADO"
            if normalized not in {"PROCEDENTE", "IMPROCEDENTE", "INDETERMINADO"}:
                normalized = "INDETERMINADO"
            why = [
                "Estado operacional válido: " + ticket.business_status,
                "Relacionado a link deste Provider dentro do Data Scope atual",
                "Contabilizado uma única vez neste Provider",
            ]
            if in_period:
                why.insert(0, "Criado no período selecionado")
            elif ticket_id in row["backlog_ticket_ids"]:
                why.insert(0, "Backlog atual em OPEN/PENDING/SCHEDULED")
            if is_terminal and ticket.resolved_at:
                why.append("Duração encerrada em ticketResolvido")
            elif is_terminal and terminal:
                why.append("Fallback de duração: StateUpdate terminal, pois ticketResolvido não tem data")
            records.append({
                "id": ticket.pk,
                "external_id": ticket.external_id,
                "number": ticket.ticket_number,
                "title": RankingService._safe_ticket_title(ticket.title),
                "provider": row["name"],
                "client": ", ".join(sorted({link["client"] for link in link_data if link["client"]}, key=str.casefold)),
                "project": ", ".join(sorted({link["project"] for link in link_data if link["project"]}, key=str.casefold)),
                "state": ", ".join(sorted({link["state"] for link in link_data if link["state"]})),
                "city": ", ".join(sorted({link["city"] for link in link_data if link["city"]}, key=str.casefold)),
                "links": link_data,
                "status": ticket.business_status,
                "priority": ticket.priority_name,
                "created_at": timezone.localtime(created).strftime("%d/%m/%Y %H:%M") if created else "",
                "created_sort": created.isoformat() if created else "",
                "resolved_at": timezone.localtime(ticket.resolved_at).strftime("%d/%m/%Y %H:%M") if ticket.resolved_at else "",
                "terminal_at": timezone.localtime(terminal).strftime("%d/%m/%Y %H:%M") if terminal else "",
                "operational_end_at": timezone.localtime(operational_end).strftime("%d/%m/%Y %H:%M") if is_terminal and operational_end else "",
                "operational_end_source": (
                    "resolved_at" if is_terminal and ticket.resolved_at else
                    "terminal_state_at" if is_terminal and terminal else ""
                ),
                "duration_hours": round(duration.total_seconds() / 3600, 1) if duration is not None else None,
                "over_24h": ticket_id in row["over_24h_ticket_ids"],
                "backlog": ticket_id in row["backlog_ticket_ids"],
                "in_period": in_period,
                "on_recurrent_link": recurrent_link,
                "reason": ClosingReasonCanonicalizer.display(ticket.closing_code_raw),
                "closing_code_raw": ticket.closing_code_raw,
                "responsibility_raw": ticket.responsibility_raw,
                "responsibility_class": normalized,
                "why": why,
            })
        records.sort(key=lambda item: item["created_sort"], reverse=True)
        return records

    @staticmethod
    def _safe_ticket_title(value):
        """Keep the operational title while removing contact details before display."""
        title = str(value or "")
        title = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[e-mail removido]", title)
        title = re.sub(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)", "[telefone removido]", title)
        return title[:500]

    @staticmethod
    def _provider_detail(row, tickets, link_tickets, *, ticket_page=1, ticket_page_size=10, now=None):
        if not row:
            return None
        now = now or timezone.now()
        provider_links = row["links_for_provider"]
        selected = row["selected_link_tickets"]
        project_counts, client_counts, state_counts, reason_counts = (defaultdict(set) for _ in range(4))
        project_links, client_links, state_links = (defaultdict(set) for _ in range(3))
        project_over, client_over, state_over = (defaultdict(set) for _ in range(3))
        project_proc, client_proc, state_proc = (defaultdict(set) for _ in range(3))
        project_improc, client_improc, state_improc = (defaultdict(set) for _ in range(3))
        procedure_ids = {ticket_id for ticket_id, ticket in tickets.items()
                         if ticket.responsibility_normalized == "PROCEDENTE"}
        improper_ids = {ticket_id for ticket_id, ticket in tickets.items()
                        if ticket.responsibility_normalized == "IMPROCEDENTE"}
        link_rows = []
        for link in provider_links:
            ticket_ids = selected.get(link.pk, set())
            for ticket_id in ticket_ids:
                ticket = tickets[ticket_id]
                for label, ticket_counts, link_counts, over_counts, proc_counts, improc_counts in (
                    (link.business_project, project_counts, project_links, project_over, project_proc, project_improc),
                    (link.contracting_client, client_counts, client_links, client_over, client_proc, client_improc),
                    (link.state, state_counts, state_links, state_over, state_proc, state_improc),
                ):
                    if not label:
                        continue
                    ticket_counts[label].add(ticket_id)
                    link_counts[label].add(link.pk)
                    if ticket_id in row["over_24h_ticket_ids"]:
                        over_counts[label].add(ticket_id)
                    if ticket.responsibility_normalized == "PROCEDENTE":
                        proc_counts[label].add(ticket_id)
                    elif ticket.responsibility_normalized == "IMPROCEDENTE":
                        improc_counts[label].add(ticket_id)
                reason = ClosingReasonCanonicalizer.display(ticket.closing_code_raw)
                if reason and ticket.business_status in TERMINAL_STATES:
                    reason_counts[reason].add(ticket_id)
            if ticket_ids:
                link_rows.append({
                    "id": link.pk, "name": link.designation or link.name or link.source_number or link.source_config_item_id,
                    "config_item_id": link.source_config_item_id, "number": link.source_number or "",
                    "project": link.business_project, "client": link.contracting_client, "state": link.state,
                    "tickets": len(ticket_ids), "recurrent": len(ticket_ids) >= 2,
                    "over_24h": len(ticket_ids & row["over_24h_ticket_ids"]),
                    "procedentes": len(ticket_ids & procedure_ids),
                    "improcedentes": len(ticket_ids & improper_ids),
                })
        link_rows.sort(key=lambda item: (-item["tickets"], -item["over_24h"], item["name"].casefold()))
        ticket_rows = RankingService._provider_ticket_rows(row, tickets, link_tickets, now=now)
        by_class = defaultdict(int)
        for item in ticket_rows:
            if item["in_period"]:
                by_class[item["responsibility_class"]] += 1
        known = by_class["PROCEDENTE"] + by_class["IMPROCEDENTE"]
        improcedent_rate = by_class["IMPROCEDENTE"] / known if known else 0
        review_threshold = RESPONSIBILITY_REVIEW_MIN_TICKETS
        review_rate = RESPONSIBILITY_REVIEW_IMPROCEDENT_RATE
        try:
            page_size = int(ticket_page_size)
        except (TypeError, ValueError):
            page_size = 10
        if page_size not in {10, 50}:
            page_size = 10
        try:
            ticket_page = max(1, int(ticket_page))
        except (TypeError, ValueError):
            ticket_page = 1
        ticket_total = len(ticket_rows)
        ticket_pages = max(1, (ticket_total + page_size - 1) // page_size)
        ticket_page = min(ticket_page, ticket_pages)
        page_start = (ticket_page - 1) * page_size
        result = {key: value for key, value in row.items() if not key.endswith("_ids") and key != "links_for_provider" and key != "selected_link_tickets"}
        result.update({
            "availability_supported": False,
            "projects_top": RankingService._dimension_rows(project_counts, project_links, project_over, project_proc, project_improc),
            "clients_top": RankingService._dimension_rows(client_counts, client_links, client_over, client_proc, client_improc),
            "ufs_top": RankingService._dimension_rows(state_counts, state_links, state_over, state_proc, state_improc),
            "reasons_top": RankingService._responsibility_rows(reason_counts, tickets),
            "problematic_links": link_rows[:20],
            "over_24h_tickets": [item for item in ticket_rows if item["over_24h"]][:100],
            "backlog_tickets": [item for item in ticket_rows if item["backlog"]][:100],
            "ticket_records": ticket_rows[page_start:page_start + page_size],
            "ticket_records_total": ticket_total,
            "ticket_page": ticket_page,
            "ticket_pages": ticket_pages,
            "ticket_page_size": page_size,
            "responsibility": {
                "procedentes": by_class["PROCEDENTE"],
                "improcedentes": by_class["IMPROCEDENTE"],
                "indeterminados": by_class["INDETERMINADO"],
                "known_total": known,
                "procedure_rate": by_class["PROCEDENTE"] / known if known else 0,
                "improcedent_rate": improcedent_rate,
                "review": known >= review_threshold and improcedent_rate >= review_rate,
                "review_min_tickets": review_threshold,
                "review_rate": review_rate,
            },
        })
        result["risk_label"], result["risk_class"] = _score_band(result["risk_score"])
        return result

    @staticmethod
    def _count_rows(items):
        return [{"name": name, "tickets": len(ids)} for name, ids in sorted(items.items(), key=lambda pair: (-len(pair[1]), pair[0].casefold()))[:10]]

    @staticmethod
    def _dimension_rows(ticket_counts, link_counts, over_counts, proc_counts, improc_counts):
        return [{
            "name": name,
            "tickets": len(ids),
            "links_affected": len(link_counts[name]),
            "over_24h": len(over_counts[name]),
            "procedentes": len(proc_counts[name]),
            "improcedentes": len(improc_counts[name]),
        } for name, ids in sorted(ticket_counts.items(), key=lambda pair: (-len(pair[1]), pair[0].casefold()))[:10]]

    @staticmethod
    def _responsibility_rows(reason_counts, tickets):
        result = []
        for name, ids in sorted(reason_counts.items(), key=lambda pair: (-len(pair[1]), pair[0].casefold()))[:10]:
            result.append({
                "name": name,
                "tickets": len(ids),
                "procedentes": sum(tickets[ticket_id].responsibility_normalized == "PROCEDENTE" for ticket_id in ids),
                "improcedentes": sum(tickets[ticket_id].responsibility_normalized == "IMPROCEDENTE" for ticket_id in ids),
            })
        return result
