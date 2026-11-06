"""Analytics over the local CMDB and OTRS mirrors only."""
import calendar
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
import unicodedata
from zoneinfo import ZoneInfo
from urllib.parse import urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.db.models import Case, CharField, Count, Value, When
from django.urls import reverse
from django.utils import timezone

from aplicativos.integracoes.models import (
    IntegrationRun, IntegrationSyncState, OTRSTicket, OTRSTicketConfigItem,
    OTRSStateMapping,
)
from aplicativos.integracoes.otrs_queues import COCKPIT_QUEUE_NAMES, queue_signature
from aplicativos.integracoes.services.analytics import (
    BACKLOG_STATUSES, BUSINESS_OPEN_STATUSES, OPERATIONAL_RESOLUTION_FIELD, LinkQueryService,
    PeriodService, TicketQueryService, count_affected_links,
    operational_resolution_expression,
)
from aplicativos.integracoes.services.otrs_status import (
    OPERATIONAL_VALID_STATUSES, RESOLVED_STATUS,
    TERMINAL_QUERY_STATUSES, TERMINAL_STATUSES, business_status_query,
    business_statuses_query, normalize_business_status, normalized_status_counts,
)
from aplicativos.integracoes.services.link_business_identity import BusinessIdentityRules
from aplicativos.integracoes.services.cancellation import (
    ACTIVE_CANCELLATION_STAGES, CANCELLATION_STAGE_LABELS,
    cancellation_queue_filter, cancellation_stage_expression,
)
from aplicativos.integracoes.services.closing_reason import ClosingReasonCanonicalizer
from aplicativos.integracoes.services.project_names import canonical_project_name, display_project_name
from .provider_logos import provider_logo

STATE_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MG": "Minas Gerais", "MS": "Mato Grosso do Sul", "MT": "Mato Grosso",
    "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco", "PI": "Piauí", "PR": "Paraná",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RO": "Rondônia", "RR": "Roraima",
    "RS": "Rio Grande do Sul", "SC": "Santa Catarina", "SE": "Sergipe", "SP": "São Paulo", "TO": "Tocantins",
}
STATUS_ORDER = (
    OTRSStateMapping.BusinessStatus.OPEN,
    OTRSStateMapping.BusinessStatus.PENDING,
    OTRSStateMapping.BusinessStatus.SCHEDULED,
    OTRSStateMapping.BusinessStatus.RESOLVED,
    OTRSStateMapping.BusinessStatus.CANCELED,
    OTRSStateMapping.BusinessStatus.UNKNOWN,
)

def normalize_closing_reason(value):
    """Compatibility display helper backed by the shared canonicalizer."""
    return ClosingReasonCanonicalizer.display(value)

def _months(start, end):
    result = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return result


def _month_date(value):
    return PeriodService.parse_month(value)


def _date_boundaries(start, end, tz=None):
    """Build local calendar bounds and return UTC instants for DB comparisons."""
    zone = tz or ZoneInfo(settings.TIME_ZONE)
    start_local = datetime.combine(start, time.min, tzinfo=zone)
    end_exclusive_local = datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone)
    return (start_local.astimezone(datetime_timezone.utc),
            end_exclusive_local.astimezone(datetime_timezone.utc))


def _month_buckets(start, end, tz=None):
    zone = tz or ZoneInfo(settings.TIME_ZONE)
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    buckets = []
    while cursor <= last:
        next_month = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
        local_start = datetime.combine(cursor, time.min, tzinfo=zone)
        local_end = datetime.combine(next_month, time.min, tzinfo=zone)
        buckets.append((cursor.strftime("%Y-%m"), local_start.astimezone(datetime_timezone.utc),
                        local_end.astimezone(datetime_timezone.utc)))
        cursor = next_month
    return buckets


def _month_case(field, buckets):
    return Case(*[
        When(**{f"{field}__gte": start, f"{field}__lt": end}, then=Value(key))
        for key, start, end in buckets
    ], default=Value(None), output_field=CharField(max_length=7))


def _time_buckets(start, end, tz=None):
    days = (end - start).days + 1
    if days <= 31:
        grain = "day"
    elif days <= 90:
        grain = "week"
    else:
        grain = "month"
    zone = tz or ZoneInfo(settings.TIME_ZONE)
    buckets = []
    if grain == "day":
        cursor = start
        while cursor <= end:
            following = cursor + timedelta(days=1)
            key = cursor.isoformat()
            label = f"{cursor.day:02d} {('Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez')[cursor.month - 1]}"
            buckets.append((key, label, datetime.combine(cursor, time.min, tzinfo=zone).astimezone(datetime_timezone.utc),
                            datetime.combine(following, time.min, tzinfo=zone).astimezone(datetime_timezone.utc)))
            cursor = following
    elif grain == "week":
        cursor = start - timedelta(days=start.weekday())
        names = ('Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez')
        while cursor <= end:
            following = cursor + timedelta(days=7)
            week_end = following - timedelta(days=1)
            label = f"{cursor.day:02d} {names[cursor.month - 1]}–{week_end.day:02d} {names[week_end.month - 1]}"
            key = f"{cursor.isocalendar().year}-W{cursor.isocalendar().week:02d}"
            buckets.append((key, label, datetime.combine(cursor, time.min, tzinfo=zone).astimezone(datetime_timezone.utc),
                            datetime.combine(following, time.min, tzinfo=zone).astimezone(datetime_timezone.utc)))
            cursor = following
    else:
        for key, bucket_start, bucket_end in _month_buckets(start, end, zone):
            buckets.append((key, AnalyticsQueryService._month_label(key), bucket_start, bucket_end))
    return grain, buckets


def _period_case(field, buckets):
    return Case(*[
        When(**{f"{field}__gte": start, f"{field}__lt": end}, then=Value(key))
        for key, _label, start, end in buckets
    ], default=Value(None), output_field=CharField(max_length=24))


def resolve_time_granularity(start, end):
    """Choose one readable operational time grain for a selected date range."""
    if start > end:
        raise ValueError("A data inicial não pode ser posterior à data final.")
    days = (end - start).days + 1
    if days == 1:
        return "hour"
    if days <= 14:
        return "day"
    if days <= 60:
        return "week"
    return "month"


def _operational_buckets(start, end, tz=None):
    """Build hourly, daily, weekly, or monthly buckets without chart scrolling."""
    zone = tz or ZoneInfo(settings.TIME_ZONE)
    grain = resolve_time_granularity(start, end)
    names = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
    include_year = start.year != end.year
    buckets = []
    if grain == "hour":
        local_start = datetime.combine(start, time.min, tzinfo=zone)
        for hour in range(24):
            bucket_start = local_start + timedelta(hours=hour)
            bucket_end = bucket_start + timedelta(hours=1)
            buckets.append((f"{start.isoformat()}T{hour:02d}", f"{hour:02d}h",
                            bucket_start.astimezone(datetime_timezone.utc),
                            bucket_end.astimezone(datetime_timezone.utc)))
        return grain, buckets
    if grain == "day":
        cursor = start
        while cursor <= end:
            following = cursor + timedelta(days=1)
            label = f"{cursor.day:02d} {names[cursor.month - 1]}"
            if include_year:
                label += f"/{cursor.year % 100:02d}"
            buckets.append((cursor.isoformat(), label,
                            datetime.combine(cursor, time.min, tzinfo=zone).astimezone(datetime_timezone.utc),
                            datetime.combine(following, time.min, tzinfo=zone).astimezone(datetime_timezone.utc)))
            cursor = following
        return grain, buckets
    if grain == "week":
        cursor = start - timedelta(days=start.weekday())
        while cursor <= end:
            following = cursor + timedelta(days=7)
            label_start = max(cursor, start)
            label_end = min(following - timedelta(days=1), end)
            label = f"{label_start.day:02d} {names[label_start.month - 1]}"
            if include_year or label_start.year != label_end.year:
                label += f"/{label_start.year % 100:02d}"
            label += f"\u2013{label_end.day:02d} {names[label_end.month - 1]}"
            if include_year or label_start.year != label_end.year:
                label += f"/{label_end.year % 100:02d}"
            key = f"{cursor.isocalendar().year}-W{cursor.isocalendar().week:02d}"
            buckets.append((key, label,
                            datetime.combine(cursor, time.min, tzinfo=zone).astimezone(datetime_timezone.utc),
                            datetime.combine(following, time.min, tzinfo=zone).astimezone(datetime_timezone.utc)))
            cursor = following
        return grain, buckets
    for key, bucket_start, bucket_end in _month_buckets(start, end, zone):
        year, month = map(int, key.split("-"))
        label = names[month - 1] + (f"/{year % 100:02d}" if include_year else "")
        buckets.append((key, label, bucket_start, bucket_end))
    return grain, buckets


def _operational_closure_expression():
    from django.db.models import Case, DateTimeField, F, Value, When
    from django.db.models.functions import Coalesce

    return Case(
        When(business_status="CANCELED", then=Coalesce(F("canceled_at"), F("terminal_state_at"))),
        When(business_status__in=(RESOLVED_STATUS, "CLOSED"), then=operational_resolution_expression()),
        default=Value(None), output_field=DateTimeField(),
    )

class AnalyticsQueryService:
    """Ticket analytics constrained to the scoped, current NOC queue mirror."""

    OPERATIONAL_VALID_STATES = OPERATIONAL_VALID_STATUSES

    METRICS = {
        "tickets": "Tickets criados",
        "open_tickets": "Em atendimento",
        "pending_tickets": "Pendentes",
        "scheduled_tickets": "Agendados",
        "resolved_tickets": "Resolvidos no per\u00edodo",
        "closed_tickets": "Resolvidos no per\u00edodo",
        "resolved_closed_tickets": "Resolvidos no per\u00edodo",
        "canceled_tickets": "Cancelados no per\u00edodo",
        "links_affected": "Links afetados no per\u00edodo",
        "recurrences": "Links reincidentes",
        "backlog": "Backlog atual",
    }
    DIMENSIONS = {
        "provider": "Provedor",
        "uf": "UF",
        "status": "Status",
        "source_state": "Situa\u00e7\u00e3o OTRS",
        "priority": "Prioridade",
        "termination_reason": "Motivo de encerramento",
        "sla": "SLA",
        "month": "Per\u00edodo",
    }
    FILTERS = {"provider", "uf", "status", "source_state", "service", "sla", "priority", "termination_reason"}
    LINK_FILTERS = {
        "provider": "config_items__cmdb_link__operator__name",
        "uf": "config_items__cmdb_link__state",
    }
    LINK_GROUPS = {
        "provider": "config_items__cmdb_link__operator__name",
        "uf": "config_items__cmdb_link__state",
    }
    STATUS_LABELS = {
        "OPEN": "Em atendimento",
        "PENDING": "Pendente",
        "SCHEDULED": "Agendado",
        "RESOLVED": "Resolvido",
        "CANCELED": "Cancelado",
        "UNKNOWN": "Não classificado",
    }
    SOURCE_STATE_LABELS = {
        "em atendimento": "Em atendimento",
        "pendente": "Pendente",
        "agendado": "Agendado",
        "resolvido": "Resolvido",
        "closed successful": "Encerrado com sucesso",
        "encerrado com viabilidade": "Encerrado com viabilidade",
        "cancelado": "Cancelado",
    }
    METRIC_STATUS = {
        "open_tickets": "OPEN",
        "pending_tickets": "PENDING",
        "scheduled_tickets": "SCHEDULED",
        "resolved_tickets": "RESOLVED",
        "closed_tickets": RESOLVED_STATUS,
        "canceled_tickets": "CANCELED",
    }
    LEGACY_FILTERS = {"operator": "provider", "sla_status": "sla", "contract_operator": None}
    LEGACY_DIMENSIONS = {"operator": "provider", "project": "provider", "service": "provider", "sla_status": "sla", "contract_operator": None, "isp": None}

    FILTER_ALIASES = {"closing_reason": "termination_reason", "closing_code_raw": "termination_reason"}
    SNAPSHOT_METRICS = {"backlog"}
    SCOPES = {"support", "cancellation", "general"}
    CANCELLATION_STAGES = tuple(
        (key, label, queue) for key, label, queue, _states in ACTIVE_CANCELLATION_STAGES
    )
    CANCELLATION_QUEUE_NAMES = TicketQueryService.CANCELLATION_QUEUE_NAMES
    STAGE_LABELS = CANCELLATION_STAGE_LABELS
    ORIGIN_LABELS = {"SUPPORT": "Suporte", "CANCELLATION": "Cancelamentos"}
    FILTERS = FILTERS | {"stage", "origin", "project", "age_bucket", "responsibility", "responsible", "queue"}
    DIMENSIONS = DIMENSIONS | {"cancellation_stage": "Etapa do cancelamento", "operational_origin": "Origem", "project": "Projeto / cliente"}
    LINK_FILTERS = LINK_FILTERS | {"project": "config_items__cmdb_link__business_project"}
    LINK_GROUPS = LINK_GROUPS | {"project": "config_items__cmdb_link__business_project"}

    def query(self, payload, user=None, *, include_all_rows=False):
        from django.db.models import Count, Q

        query = self.normalize_query(payload)
        metric, group, scope = query["metric"], query["group_by"], query["scope"]
        include_cancelled = query["include_cancelled"]
        include_improcedente = query["include_improcedente"]
        start, end, filters = query["start"], query["end"], query["filters"]
        start_at, end_exclusive = _date_boundaries(start, end)
        grain, buckets = _time_buckets(start, end)

        # Scope is applied before user filters, counts, groups, pagination, and export.
        allowed_links = LinkQueryService.base(user)
        all_queues = scope != "support"
        scoped_tickets = TicketQueryService.base(
            user, links=allowed_links, all_queues=all_queues,
        ).order_by()
        scoped_tickets = self._scope_tickets(scoped_tickets, scope)
        operational_rows = self._apply_filters(scoped_tickets, allowed_links, filters)
        # The service splits manager-visible flow from the current operational
        # stock while keeping both on this same scoped and filtered queryset.
        stock_rows = operational_rows.exclude(responsibility_normalized="IMPROCEDENTE")

        metric_universe = self._metric_universe(scoped_tickets, metric)
        if metric == "tickets" or (include_cancelled and metric in {"links_affected", "recurrences"}):
            included_states = self.OPERATIONAL_VALID_STATES + (("CANCELED",) if include_cancelled else ())
            valid_scope = business_statuses_query("business_status", included_states)
            if scope != "support":
                valid_scope |= Q(_operational_stage="UNKNOWN")
            metric_universe = scoped_tickets.filter(valid_scope)
        elif scope != "support" and metric == "backlog":
            metric_universe = scoped_tickets.filter(
                is_present_in_queue=True,
                business_status__in=BACKLOG_STATUSES,
            )
        if not include_improcedente or metric == "backlog":
            metric_universe = metric_universe.exclude(responsibility_normalized="IMPROCEDENTE")
        if metric in {"resolved_tickets", "closed_tickets", "resolved_closed_tickets"}:
            metric_universe = metric_universe.annotate(**{
                OPERATIONAL_RESOLUTION_FIELD: operational_resolution_expression(),
            })
        options = self._options(metric_universe, allowed_links)
        filtered = self._apply_filters(metric_universe, allowed_links, filters)
        history_meta = self._backlog_history_meta(stock_rows)
        operational = self._operational_summary(
            operational_rows, scope, start, end, metric, history_meta,
            stock_rows=stock_rows,
            include_cancelled=include_cancelled,
            include_improcedente=include_improcedente,
        )
        date_field = self._metric_date_field(metric)
        if metric in self.SNAPSHOT_METRICS:
            temporal_rows = filtered
        else:
            temporal_rows = filtered.filter(**{f"{date_field}__gte": start_at,
                                               f"{date_field}__lt": end_exclusive})
        analysis_rows = self._metric_rows(filtered, temporal_rows, metric)
        selected_value = self._metric_value(analysis_rows, metric, allowed_links)
        comparison = self._comparison(filtered, query, allowed_links, selected_value)
        series = self._series(analysis_rows, metric, buckets, allowed_links, selected_value)
        temporal = self._monthly_calendar(
            analysis_rows, metric, start, end, query["selected_month"], series, allowed_links,
        )
        grouped = self._grouped(analysis_rows, metric, group, allowed_links, buckets)
        provider_quality = self._provider_quality(analysis_rows, allowed_links)
        bucket_labels = {key: label for key, label, _start, _end in buckets}

        total_rows = len(grouped) if isinstance(grouped, list) else grouped.count()
        page_size = 25
        try:
            requested_page = max(1, int(payload.get("detail_page", 1)))
        except (TypeError, ValueError):
            requested_page = 1
        page_count = max(1, (total_rows + page_size - 1) // page_size)
        current_page = min(requested_page, page_count)
        if include_all_rows:
            detail_rows = list(grouped)
        else:
            offset = (current_page - 1) * page_size
            detail_rows = list(grouped[offset:offset + page_size])
        denominator = (provider_quality["identified"] if group == "provider" and metric not in {"links_affected", "recurrences"}
                       else selected_value) or 0

        def present(row):
            raw = row["dimension"] if row["dimension"] not in (None, "") else "N\u00e3o classificado"
            label = bucket_labels.get(raw, raw) if group == "month" else self._dimension_label(group, raw)
            return {"key": str(raw), "dimension": label, "value": row["value"],
                    "share": round(row["value"] / denominator * 100, 1) if denominator else 0}

        detail = [present(row) for row in detail_rows]
        top = [present(row) for row in list(grouped[:10])]
        context = self._context_kpis(metric, selected_value, analysis_rows, provider_quality)
        description = self._query_description(metric, group, filters, start, end)
        previous_quality = comparison.pop("provider_quality", None) if comparison else None
        previous_series = comparison.pop("previous_series", []) if comparison else []
        previous_grouped = comparison.pop("previous_grouped", []) if comparison else []
        self._add_kpi_trends(context["kpis"], comparison, previous_quality)
        chart_specs = self._chart_specs(
            metric, group, filters, series, top, grain, selected_value,
            previous_series, previous_grouped, rows=analysis_rows, links=allowed_links,
        )
        if temporal:
            chart_specs.insert(1, temporal.pop("chart_spec"))
        ticket_data = self._ticket_results(
            analysis_rows, allowed_links, payload, include_all_rows=include_all_rows,
            metric=metric, filters=filters, start=start, end=end, user=user, group=group,
        )
        period_status_counts = normalized_status_counts(
            temporal_rows.values("business_status").annotate(total=Count("pk", distinct=True))
        )
        period_from = start.strftime("%Y-%m")
        period_to = end.strftime("%Y-%m")
        return {
            "scope": scope, "metric": metric, "metric_label": self.METRICS[metric],
            "group_by": group, "group_by_label": self.DIMENSIONS[group],
            "source": "real", "period": period_to, "period_from": period_from,
            "period_to": period_to, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "compare_to": query["compare_to"], "value": selected_value,
            "comparison": comparison, "series": series, "breakdown": top,
            "provider_ranking": top if group == "provider" else [],
            "detail": detail, "detail_page": current_page,
            "detail_page_size": page_size, "detail_total": total_rows,
            "detail_pages": page_count,
            "kpis": context["kpis"] if scope == "support" else operational["primary_kpis"],
            "operational": operational,
            "provider_quality": provider_quality,
            "chart_specs": chart_specs,
            "time_grain": grain,
            "temporal": temporal,
            "description": description,
            **ticket_data,
            "period_status_counts": period_status_counts,
            "source_sync": self._source_sync_status(),
            "metric_semantics": self._metric_semantics(
                metric, scope=scope, include_cancelled=include_cancelled,
                include_improcedente=include_improcedente,
            ),
            "period_date_label": ("snapshot atual" if metric == "backlog"
                                  else "data de resolu\u00e7\u00e3o" if metric in {"resolved_tickets", "closed_tickets", "resolved_closed_tickets"}
                                  else "data de cancelamento" if metric == "canceled_tickets"
                                  else "data de cria\u00e7\u00e3o"),
            "provider_count": len(options["provider"]), "options": options,
            "normalized_query": {"scope": scope, "metric": metric, "start": start.isoformat(), "end": end.isoformat(),
                                 "group_by": group, "filters": filters,
                                 "include_cancelled": include_cancelled,
                                 "include_improcedente": include_improcedente,
                                 "selected_month": temporal["selected_month"] if temporal else None},
            "meta": {"demo": False, "period_semantics": date_field,
                     "backlog_is_current_snapshot": metric in self.SNAPSHOT_METRICS,
                     "backlog_statuses": BACKLOG_STATUSES,
                     "operational_valid_states": self.OPERATIONAL_VALID_STATES,
                     "excluded_by_default": (
                         ("SCHEDULED", "RESOLVED", "CANCELED", "UNKNOWN") if metric == "backlog"
                         else ("CANCELED", "UNKNOWN") if scope == "support"
                         else ()
                     ),
                     "unknown_active_included": scope != "support",
                     "ticket_totals_are_distinct": True,
                     "provider_groups_can_count_one_ticket_in_multiple_providers": True,
                     "termination_reason_available": True},
        }

    @staticmethod
    def _source_sync_status():
        """Read the local sync ledger; never contact OTRS while querying analytics."""
        state = IntegrationSyncState.objects.filter(key="otrs").first()
        expected_coverage = f"queues={queue_signature(COCKPIT_QUEUE_NAMES)}"
        full_success = IntegrationRun.objects.filter(
            operation=IntegrationRun.Operation.OTRS_FULL_SYNC,
            status=IntegrationRun.Status.SUCCESS,
            message__startswith="Apply;",
            message__contains=expected_coverage,
        ).order_by("-started_at", "-pk").first()
        available = bool(state and state.last_full_success_at and full_success)
        last_success = state.last_success_at if available else None
        return {
            "available": available,
            "last_sync_at": last_success.isoformat() if last_success else None,
            "coverage": expected_coverage.removeprefix("queues="),
        }

    @classmethod
    def _cancellation_predicate(cls):
        return cancellation_queue_filter()

    @classmethod
    def _scope_tickets(cls, tickets, scope):
        from django.db.models import Case, CharField, Q, Value, When

        cancellation = cls._cancellation_predicate()
        tickets = tickets.annotate(_operational_stage=cancellation_stage_expression()).annotate(
            _operational_origin=Case(
                When(cancellation, then=Value("CANCELLATION")),
                default=Value("SUPPORT"), output_field=CharField(max_length=16),
            ),
        )
        if scope == "support":
            return tickets.filter(queue_name=TicketQueryService.QUEUE_NAME)
        if scope == "cancellation":
            return tickets.filter(cancellation)
        return tickets.filter(Q(queue_name=TicketQueryService.QUEUE_NAME) | cancellation).distinct()

    @staticmethod
    def _backlog_history_meta(tickets):
        from django.db.models import Count, F, Min, Q
        from django.utils import timezone

        terminal_states = TERMINAL_QUERY_STATUSES
        history_rows = tickets.annotate(_history_closed_at=_operational_closure_expression())
        result = history_rows.aggregate(
            first_ticket_at=Min("source_created_at"),
            missing_created_at=Count("pk", filter=Q(source_created_at__isnull=True), distinct=True),
            missing_terminal_at=Count(
                "pk", filter=Q(business_status__in=terminal_states, _history_closed_at__isnull=True),
                distinct=True,
            ),
            invalid_terminal_at=Count(
                "pk", filter=Q(business_status__in=terminal_states)
                & Q(_history_closed_at__lt=F("source_created_at")), distinct=True,
            ),
        )
        first_ticket_at = result["first_ticket_at"]
        result["history_start"] = timezone.localtime(first_ticket_at).date() if first_ticket_at else None
        result["history_complete"] = bool(first_ticket_at) and not any((
            result["missing_created_at"], result["missing_terminal_at"], result["invalid_terminal_at"],
        ))
        return result

    @classmethod
    def _operational_summary(cls, rows, scope, start, end, metric, history_meta, *,
                             stock_rows=None, include_cancelled=False, include_improcedente=False):
        from django.db.models import Avg, Case, CharField, Count, DateTimeField, DurationField, ExpressionWrapper, F, Q, Value, When
        from django.utils import timezone

        active_filter = Q(business_status__in=BACKLOG_STATUSES)
        stock_rows = stock_rows if stock_rows is not None else rows.exclude(
            responsibility_normalized="IMPROCEDENTE",
        )
        active = stock_rows.filter(active_filter)
        status_rows = list(active.values("_operational_origin", "business_status").annotate(
            total=Count("pk", distinct=True),
        ))
        by_origin_status = {origin: {} for origin in cls.ORIGIN_LABELS}
        for row in status_rows:
            by_origin_status[row["_operational_origin"]][row["business_status"]] = row["total"]
        stage_counts = {key: 0 for key, _label, _queue in cls.CANCELLATION_STAGES}
        unknown_active = 0
        for row in active.values("_operational_stage").annotate(total=Count("pk", distinct=True)):
            if row["_operational_stage"] in stage_counts:
                stage_counts[row["_operational_stage"]] = row["total"]
            elif row["_operational_stage"] == "UNKNOWN":
                unknown_active = row["total"]

        start_at, end_at = _date_boundaries(start, end)
        grain, buckets = _operational_buckets(start, end)
        as_of = timezone.localtime(timezone.now())
        as_of_utc = as_of.astimezone(datetime_timezone.utc)
        observed_end = min(end_at, as_of_utc)
        valid_statuses = (*BUSINESS_OPEN_STATUSES, *TERMINAL_STATUSES)
        ledger_filter = business_statuses_query("business_status", valid_statuses)
        if scope != "support":
            ledger_filter |= Q(_operational_stage="UNKNOWN")
        ledger_rows = rows.filter(ledger_filter)
        closing_date = _operational_closure_expression()
        stock_ledger = stock_rows.filter(ledger_filter).annotate(_operational_closed_at=closing_date)
        flow_ledger = ledger_rows.annotate(_operational_closed_at=closing_date)

        history_start = history_meta.get("history_start")
        history_available = bool(
            history_meta.get("history_complete") and history_start
            and start >= history_start and end <= as_of.date()
        )
        baseline = stock_ledger.filter(business_statuses_query(
            "business_status", (*BACKLOG_STATUSES, *TERMINAL_STATUSES),
        )).filter(source_created_at__lt=start_at).filter(
            Q(_operational_closed_at__isnull=True)
            | (Q(_operational_closed_at__gte=start_at)
               & Q(_operational_closed_at__gte=F("source_created_at")))
        )
        baseline_count = baseline.values("pk").distinct().count() if history_available else None

        def aggregate_event(event_rows, field):
            if observed_end <= start_at:
                return {}
            key_case = _period_case(field, buckets)
            return {
                (item["bucket"], item["_operational_origin"], normalize_business_status(item["business_status"]),
                 item["responsibility_normalized"]): item["total"]
                for item in event_rows.annotate(bucket=key_case).exclude(bucket__isnull=True)
                .values("bucket", "_operational_origin", "business_status", "responsibility_normalized")
                .annotate(total=Count("pk", distinct=True))
            }

        entries = flow_ledger.filter(source_created_at__gte=start_at, source_created_at__lt=observed_end)
        entry_groups = aggregate_event(entries, "source_created_at")
        terminal_rows = flow_ledger.filter(business_statuses_query("business_status", TERMINAL_STATUSES))
        terminal_rows = terminal_rows.filter(_operational_closed_at__gte=start_at, _operational_closed_at__lt=observed_end)
        terminal_rows = terminal_rows.filter(_operational_closed_at__gte=F("source_created_at"))
        terminal_groups = aggregate_event(terminal_rows, "_operational_closed_at")

        def collect_event(groups, states, *, allow_improper):
            totals = {}
            for (bucket, origin, status, responsibility), count in groups.items():
                if states is not None and status not in states:
                    continue
                if responsibility == "IMPROCEDENTE" and not allow_improper:
                    continue
                key = (bucket, origin)
                totals[key] = totals.get(key, 0) + count
            return totals

        entry_states = set(valid_statuses)
        if scope != "support":
            entry_states.add("UNKNOWN")
        if not include_cancelled and metric != "canceled_tickets":
            entry_states.discard("CANCELED")
        flow_improper = include_improcedente and metric != "backlog"
        entry_values = collect_event(entry_groups, entry_states, allow_improper=flow_improper)
        closed_values = collect_event(terminal_groups, {RESOLVED_STATUS}, allow_improper=flow_improper)
        canceled_values = collect_event(
            terminal_groups, {"CANCELED"} if include_cancelled or metric == "canceled_tickets" else set(),
            allow_improper=flow_improper,
        )
        stock_entry_values = collect_event(
            entry_groups, {*BACKLOG_STATUSES, *TERMINAL_STATUSES}, allow_improper=False,
        )
        stock_closure_values = collect_event(terminal_groups, None, allow_improper=False)
        entries_total = sum(entry_values.values())
        closed_total = sum(closed_values.values())
        canceled_total = sum(canceled_values.values())
        resolved = flow_ledger.filter(business_status_query("business_status", RESOLVED_STATUS))
        resolved = resolved.filter(_operational_closed_at__gte=start_at, _operational_closed_at__lt=observed_end)
        resolved = resolved.filter(_operational_closed_at__gte=F("source_created_at"))
        if not flow_improper:
            resolved = resolved.exclude(responsibility_normalized="IMPROCEDENTE")
        average_duration = resolved.aggregate(
            average=Avg(ExpressionWrapper(
                F("_operational_closed_at") - F("source_created_at"),
                output_field=DurationField(),
            )),
        )["average"]
        average_hours = round(average_duration.total_seconds() / 3600, 1) if average_duration else None

        series = []
        running_backlog = baseline_count or 0
        for key, label, bucket_start, _bucket_end in buckets:
            entry_support = entry_values.get((key, "SUPPORT"), 0)
            entry_cancel = entry_values.get((key, "CANCELLATION"), 0)
            closed_support = closed_values.get((key, "SUPPORT"), 0)
            closed_cancel = closed_values.get((key, "CANCELLATION"), 0)
            canceled_support = canceled_values.get((key, "SUPPORT"), 0)
            canceled_cancel = canceled_values.get((key, "CANCELLATION"), 0)
            entry_count = entry_support + entry_cancel
            closed_count = closed_support + closed_cancel
            canceled_count = canceled_support + canceled_cancel
            backlog_start = running_backlog
            stock_entry_count = stock_entry_values.get((key, "SUPPORT"), 0) + stock_entry_values.get((key, "CANCELLATION"), 0)
            stock_closure_count = stock_closure_values.get((key, "SUPPORT"), 0) + stock_closure_values.get((key, "CANCELLATION"), 0)
            running_backlog += stock_entry_count - stock_closure_count
            point_has_observed_data = bucket_start < observed_end
            series.append({
                "key": key, "label": label,
                "entries": entry_count, "closed": closed_count, "canceled": canceled_count,
                "balance": entry_count - closed_count - canceled_count,
                "support_entries": entry_support, "support_closed": closed_support,
                "cancellation_entries": entry_cancel, "cancellation_closed": closed_cancel,
                "support_canceled": canceled_support, "cancellation_canceled": canceled_cancel,
                "backlog_start": backlog_start if history_available and point_has_observed_data else None,
                "backlog": running_backlog if history_available and point_has_observed_data else None,
            })
        now = timezone.now()
        age_edges = (4, 8, 24, 48, 72)
        age_labels = ("0-4h", "4-8h", "8-24h", "24-48h", "48-72h", ">72h")
        age_cases = [
            When(source_created_at__gte=now - timedelta(hours=age_edges[0]), then=Value("0-4")),
            When(source_created_at__gte=now - timedelta(hours=age_edges[1]), then=Value("4-8")),
            When(source_created_at__gte=now - timedelta(hours=age_edges[2]), then=Value("8-24")),
            When(source_created_at__gte=now - timedelta(hours=age_edges[3]), then=Value("24-48")),
            When(source_created_at__gte=now - timedelta(hours=age_edges[4]), then=Value("48-72")),
        ]
        age_keys = ("0-4", "4-8", "8-24", "24-48", "48-72", "72+")
        age_rows = active.filter(source_created_at__lte=now).annotate(
            _age_bucket=Case(*age_cases, default=Value("72+"), output_field=CharField(max_length=8)),
        ).values("_operational_origin", "_age_bucket").annotate(total=Count("pk", distinct=True))
        age_counts = {(row["_operational_origin"], row["_age_bucket"]): row["total"] for row in age_rows}
        aging = [{
            "key": key, "label": label,
            "support": age_counts.get(("SUPPORT", key), 0),
            "cancellation": age_counts.get(("CANCELLATION", key), 0),
        } for key, label in zip(age_keys, age_labels)]
        for bucket in aging:
            bucket["total"] = bucket["support"] + bucket["cancellation"]

        current_support = sum(by_origin_status["SUPPORT"].values())
        current_cancellation = sum(by_origin_status["CANCELLATION"].values())
        total_active = current_support + current_cancellation
        open_count = sum(item.get("OPEN", 0) for item in by_origin_status.values())
        pending_count = sum(item.get("PENDING", 0) for item in by_origin_status.values())
        older_than_day = sum(item["total"] for item in aging[3:])
        balance = entries_total - closed_total - canceled_total
        stock_balance = sum(stock_entry_values.values()) - sum(stock_closure_values.values())
        backlog_initial = baseline_count if history_available else None
        backlog_final = running_backlog if history_available else None
        history_message = ""
        if not history_meta.get("history_complete"):
            history_message = "O mirror não tem datas suficientes para reconstruir todo o estoque histórico."
        elif not history_start:
            history_message = "Não há data inicial confiável no mirror para este recorte."
        elif start < history_start:
            history_message = f"O histórico disponível começa em {history_start:%d/%m/%Y}."
        elif end > as_of.date():
            history_message = "O período termina no futuro; o Cockpit não projeta backlog."
        if history_available and end == as_of.date() and backlog_final != total_active:
            history_available = False
            backlog_initial = None
            backlog_final = total_active
            history_message = "O fluxo histórico não reconciliou com o estoque atual; a linha foi ocultada."
            for item in series:
                item["backlog_start"] = None
                item["backlog"] = None
        elif not history_available and end == as_of.date():
            backlog_final = total_active

        variation_percent = (
            round(stock_balance * 100 / backlog_initial, 1)
            if backlog_initial not in (None, 0)
            else (0 if backlog_initial == 0 and stock_balance == 0 else None)
        )
        final_label = "Backlog atual" if end == as_of.date() else "Backlog final"

        def kpi(label, value, unit="tickets", *, stage=None, title=None, help_text=None):
            result = {"label": label, "value": value if value is not None else "\u2014", "unit": unit}
            if stage:
                result["stage"] = stage
            if title:
                result["title"] = title
            if help_text:
                result["help_text"] = help_text
            return result

        flow_primary = [
            kpi("Backlog inicial", backlog_initial, "tickets no início do período"),
            kpi("Novos", entries_total, "tickets criados no período"),
            kpi("Resolvidos", closed_total, "categoria operacional RESOLVED"),
            *([kpi("Cancelados", canceled_total, "tickets cancelados no período")] if include_cancelled or metric == "canceled_tickets" else []),
            kpi(final_label, backlog_final, "tickets no fim do período"),
        ]
        if scope == "cancellation":
            primary = [kpi(label, stage_counts[key], stage=key,
                           title=cls._stage_title(key, queue))
                       for key, label, queue in cls.CANCELLATION_STAGES]
            if unknown_active:
                primary.append(kpi(
                    cls.STAGE_LABELS["UNKNOWN"], unknown_active, stage="UNKNOWN",
                    title=cls._stage_title("UNKNOWN", ""),
                ))
            secondary = [
                kpi("Backlog inicial", backlog_initial, "tickets no início do período"),
                kpi("Novos", entries_total, "tickets criados no período"),
                kpi("Resolvidos", closed_total, "categoria operacional RESOLVED"),
                *([kpi("Cancelados", canceled_total, "tickets cancelados no período")] if include_cancelled or metric == "canceled_tickets" else []),
                kpi(final_label, backlog_final, "tickets no fim do período"),
                kpi("Variação líquida", balance, "Novos − encerrados", help_text=(
                    "Diferença entre tickets criados e encerrados no período. "
                    "Valor positivo: mais tickets entraram do que saíram. "
                    "Valor negativo: mais tickets foram resolvidos ou cancelados do que criados."
                )),
                kpi("Variação %", variation_percent, "% do backlog inicial"),
                kpi("Tempo médio de resolução", average_hours, "horas"),
                kpi("Tickets acima de 24h", older_than_day),
            ]
        else:
            primary = flow_primary
            secondary = [
                kpi("Variação líquida", balance, "Novos − encerrados", help_text=(
                    "Diferença entre tickets criados e encerrados no período. "
                    "Valor positivo: mais tickets entraram do que saíram. "
                    "Valor negativo: mais tickets foram resolvidos ou cancelados do que criados."
                )),
                kpi("Variação %", variation_percent, "% do backlog inicial"),
                kpi("Tempo médio de resolução", average_hours, "horas"),
                kpi("Tickets acima de 24h", older_than_day),
            ]
            if scope == "general":
                secondary.extend((
                    kpi("Suporte ativo", current_support),
                    kpi("Cancelamentos ativos", current_cancellation),
                ))

        if scope == "support":
            aging = [{**item, "cancellation": 0, "total": item["support"]} for item in aging]
        elif scope == "cancellation":
            aging = [{**item, "support": 0, "total": item["cancellation"]} for item in aging]
        period = {
            "entries": entries_total, "closed": closed_total, "canceled": canceled_total, "balance": balance,
            "net": balance, "variation_percent": variation_percent,
            "average_resolution_hours": average_hours,
            "backlog_initial": backlog_initial, "backlog_final": backlog_final,
            "backlog_history_available": history_available,
        }
        return {
            "primary_kpis": primary, "secondary_kpis": secondary,
            "include_cancelled": include_cancelled, "include_improcedente": include_improcedente,
            "series": series, "grain": grain, "granularity": grain, "aging": aging,
            "stages": [{"key": key, "label": label, "queue": queue,
                        "value": stage_counts[key]}
                       for key, label, queue in cls.CANCELLATION_STAGES],
            "stage_total": sum(stage_counts.values()),
            "unknown_active": unknown_active,
            "stage_reconciliation": sum(stage_counts.values()) + unknown_active == current_cancellation,
            "current": {"total": total_active, "support": current_support,
                        "cancellation": current_cancellation, "open": open_count,
                        "pending": pending_count},
            "period": period,
            "backlog": {
                "available": history_available,
                "history_start": history_start.isoformat() if history_start else None,
                "message": history_message,
                "initial": backlog_initial,
                "final": backlog_final,
                "current_snapshot": total_active,
            },
        }

    @staticmethod
    def _stage_title(stage, queue):
        if stage == "UNKNOWN":
            return "Ticket ativo em uma fila de Cancelamentos sem regra de Profile/estado reconhecida."
        if stage == "REQUEST":
            return f"Fila OTRS: {queue}; estado do Profile 681_58: em atendimento."
        if stage == "TREATMENT":
            return f"Fila OTRS: {queue}; estados pendentes do Profile 725_58."
        profile = "680_58" if stage == "EQUIPMENT_REMOVAL" else "697_58"
        return f"Fila OTRS: {queue}; estados ativos do Profile {profile}."
    @classmethod
    def _metric_universe(cls, scoped_tickets, metric):
        if metric == "canceled_tickets":
            return scoped_tickets.filter(business_status="CANCELED")
        return scoped_tickets.filter(business_statuses_query("business_status", cls.OPERATIONAL_VALID_STATES))

    @classmethod
    def _provider_quality(cls, rows, links):
        from django.db.models import Count, Q
        from aplicativos.integracoes.models import OTRSTicket, OTRSTicketConfigItem

        # Reduce the filtered ticket set to unique IDs before joining its CMDB
        # relations. The former four correlated EXISTS count queries became
        # expensive on wider date ranges and could exceed MySQL's read timeout.
        quality_rows = OTRSTicket.objects.filter(
            pk__in=rows.order_by().values("pk"),
        ).order_by().values("pk").annotate(
            _relation_count=Count(
                "config_items__pk",
                filter=Q(config_items__is_present_in_source=True),
                distinct=True,
            ),
            _link_count=Count(
                "config_items__pk",
                filter=Q(config_items__is_present_in_source=True, config_items__cmdb_link__isnull=False),
                distinct=True,
            ),
            _valid_link_count=Count(
                "config_items__pk",
                filter=Q(
                    config_items__is_present_in_source=True,
                    config_items__cmdb_link__is_present_in_source=True,
                    config_items__cmdb_link__in=links,
                ),
                distinct=True,
            ),
            _provider_count=Count(
                "config_items__pk",
                filter=(
                    Q(
                        config_items__is_present_in_source=True,
                        config_items__cmdb_link__is_present_in_source=True,
                        config_items__cmdb_link__in=links,
                        config_items__cmdb_link__operator__isnull=False,
                    ) & ~Q(config_items__cmdb_link__operator__name="")
                ),
                distinct=True,
            ),
        )
        total = identified = 0
        causes = {
            "no_config_item": 0,
            "missing_mirror": 0,
            "invalid_link": 0,
            "link_without_provider": 0,
        }
        for item in quality_rows.iterator(chunk_size=1000):
            total += 1
            if item["_provider_count"]:
                identified += 1
            elif not item["_relation_count"]:
                causes["no_config_item"] += 1
            elif not item["_link_count"]:
                causes["missing_mirror"] += 1
            elif not item["_valid_link_count"]:
                causes["invalid_link"] += 1
            else:
                causes["link_without_provider"] += 1
        unclassified = max(total - identified, 0)
        provider_groups = OTRSTicketConfigItem.objects.filter(
            ticket_id__in=rows.values("pk"), is_present_in_source=True,
            cmdb_link__is_present_in_source=True, cmdb_link__in=links,
        ).exclude(cmdb_link__operator__isnull=True).exclude(cmdb_link__operator__name="").values(
            "cmdb_link__operator__name",
        ).annotate(value=Count("ticket_id", distinct=True)).order_by("-value", "cmdb_link__operator__name")
        top = next(iter(provider_groups), None)
        return {
            "total": total, "identified": identified, "unresolved": unclassified,
            "coverage_percent": round(identified * 100 / total, 1) if total else 0,
            "unresolved_causes": causes,
            "top_provider": ({"name": top["cmdb_link__operator__name"], "value": top["value"]} if top else None),
            "provider_groups_can_overlap": True,
        }

    @classmethod
    def _context_kpis(cls, metric, total, rows, provider_quality):
        from django.db.models import Count, Q

        if metric == "backlog":
            counts = rows.aggregate(
                open=Count("pk", filter=Q(business_status="OPEN"), distinct=True),
                pending=Count("pk", filter=Q(business_status="PENDING"), distinct=True),
            )
            return {"kpis": [
                {"key": "total", "label": "Backlog total", "value": total, "unit": "Em atendimento + Pendente"},
                {"label": "Em atendimento", "value": counts["open"], "unit": ""},
                {"label": "Pendentes", "value": counts["pending"], "unit": ""},
            ]}
        if metric in {"links_affected", "recurrences"}:
            return {"kpis": [{"key": "total", "label": cls.METRICS[metric], "value": total, "unit": ""}]}
        return {"kpis": [
            {"key": "total", "label": "Tickets encontrados" if metric == "tickets" else cls.METRICS[metric],
             "value": total, "unit": "Criados no per\u00edodo conforme os filtros ativos" if metric == "tickets" else ""},
            {"key": "identified", "label": "Com provedor identificado", "value": provider_quality["identified"], "unit": ""},
            {"key": "unresolved", "label": "Sem provedor identificado", "value": provider_quality["unresolved"], "unit": ""},
            {"key": "coverage", "label": "Cobertura de provedor", "value": provider_quality["coverage_percent"], "unit": "% dos tickets"},
        ]}

    @staticmethod
    def _add_kpi_trends(kpis, comparison, previous_quality):
        if not comparison:
            return
        previous = {"total": comparison["previous_value"]}
        if previous_quality:
            previous.update({"identified": previous_quality["identified"],
                             "unresolved": previous_quality["unresolved"],
                             "coverage": previous_quality["coverage_percent"]})
        for item in kpis:
            key = item.get("key")
            if key not in previous:
                continue
            current, before = item["value"], previous[key]
            change = current - before
            percentage = round(change * 100 / before, 1) if before else (0 if not change else None)
            item["trend"] = {
                "current_value": current, "previous_value": before,
                "absolute_change": round(change, 1) if key == "coverage" else change,
                "percentage_change": percentage,
                "direction": "up" if change > 0 else "down" if change < 0 else "neutral",
                "unit": "p.p." if key == "coverage" else "",
                "favorable": (change > 0 if key == "coverage" else change < 0 if key == "unresolved" else None),
            }

    @staticmethod
    def _previous_period(start, end):
        if start.day == 1 and end == AnalyticsQueryService._month_end(end):
            previous_end = start - timedelta(days=1)
            previous_start = previous_end.replace(day=1)
            return previous_start, previous_end
        length = (end - start).days + 1
        previous_end = start - timedelta(days=1)
        return previous_end - timedelta(days=length - 1), previous_end

    @classmethod
    def _query_description(cls, metric, group, filters, start, end):
        fmt = lambda value: value.strftime("%d/%m/%Y")
        period = f"{fmt(start)} a {fmt(end)}"
        reason = filters.get("termination_reason")
        if metric == "backlog":
            title = f"Backlog atual por {cls.DIMENSIONS[group].lower()}"
            subtitle = "Fila atual: em atendimento ou pendente"
            period = "Atual"
        elif reason and metric == "tickets":
            title = f"An\u00e1lise de {ClosingReasonCanonicalizer.display(reason)}"
            subtitle = f"Tickets v\u00e1lidos criados entre {fmt(start)} e {fmt(end)} conforme os filtros ativos"
        elif reason:
            metric_word = {"closed_tickets": "resolvidos", "resolved_tickets": "resolvidos",
                           "resolved_closed_tickets": "resolvidos", "canceled_tickets": "cancelados"}.get(
                               metric, cls.METRICS[metric].lower())
            title = f"Tickets {metric_word} por {ClosingReasonCanonicalizer.display(reason)}"
            subtitle = f"{fmt(start)} a {fmt(end)}"
        else:
            metric_word = cls.METRICS[metric].lower()
            title = f"An\u00e1lise de {metric_word}"
            subtitle = f"{fmt(start)} a {fmt(end)}"
        return {"title": title, "subtitle": subtitle, "period": period,
                "metric_label": cls.METRICS[metric], "group_label": cls.DIMENSIONS[group]}

    @staticmethod
    def _ticket_pagination(payload):
        try:
            page = max(1, int(payload.get("ticket_page", 1)))
            size = int(payload.get("ticket_page_size", 25))
        except (TypeError, ValueError):
            page, size = 1, 25
        return page, size if size in {25, 50, 100} else 25

    @classmethod
    def _ticket_results(cls, rows, links, payload, *, include_all_rows, metric, filters, start, end, user, group):
        from django.db.models import Prefetch, Q
        from django.utils import timezone

        page, page_size = cls._ticket_pagination(payload)
        search = str(payload.get("ticket_search") or "").strip()[:120]
        ticket_day = str(payload.get("ticket_day") or "").strip()
        day_rows = rows
        if ticket_day:
            try:
                selected_day = date.fromisoformat(ticket_day)
            except ValueError:
                raise ValueError("Data do ticket inv\u00e1lida.") from None
            if metric in cls.SNAPSHOT_METRICS or not start <= selected_day <= end:
                raise ValueError("O dia selecionado est\u00e1 fora da an\u00e1lise atual.")
            day_start, day_end = _date_boundaries(selected_day, selected_day)
            date_field = cls._metric_date_field(metric)
            day_rows = rows.filter(**{f"{date_field}__gte": day_start, f"{date_field}__lt": day_end})
        tickets = day_rows.order_by()
        if search:
            relation_search = Q(config_items__is_present_in_source=True, config_items__cmdb_link__in=links)
            if user is None or getattr(user, "is_superuser", False):
                relation_search |= Q(config_items__is_present_in_source=True, config_items__cmdb_link__isnull=True)
            tickets = tickets.filter(
                Q(ticket_number__icontains=search)
                | (relation_search & Q(config_items__config_item_id__icontains=search))
                | (relation_search & Q(config_items__cmdb_link__source_number__icontains=search))
                | (relation_search & Q(config_items__cmdb_link__name__icontains=search))
                | (relation_search & Q(config_items__cmdb_link__designation__icontains=search))
                | (relation_search & Q(config_items__cmdb_link__circuit__icontains=search))
                | (relation_search & Q(config_items__cmdb_link__operator__name__icontains=search))
            ).distinct()
        result_count = rows.values("pk").distinct().count()
        found_count = tickets.values("pk").distinct().count()
        page_count = max(1, (found_count + page_size - 1) // page_size)
        page = min(page, page_count)
        relation_qs = OTRSTicketConfigItem.objects.filter(is_present_in_source=True)
        if user is not None and not getattr(user, "is_superuser", False):
            relation_qs = relation_qs.filter(cmdb_link__in=links)
        else:
            relation_qs = relation_qs.filter(Q(cmdb_link__in=links) | Q(cmdb_link__isnull=True))
        relation_qs = relation_qs.select_related("cmdb_link__operator").order_by("pk")
        prefetch = Prefetch("config_items", queryset=relation_qs, to_attr="_cockpit_relations")
        date_field = cls._metric_date_field(metric)
        tickets = tickets.order_by(f"-{date_field}", "-pk").prefetch_related(prefetch)
        selection = tickets if include_all_rows else tickets[(page - 1) * page_size:page * page_size]
        rows_json = [cls._present_ticket(ticket, metric, filters, start, end, timezone, ticket_day=ticket_day) for ticket in selection]
        return {"tickets": rows_json, "ticket_total": found_count, "ticket_result_total": result_count,
                "ticket_page": page, "ticket_page_size": page_size, "ticket_pages": page_count,
                "ticket_search": search, "ticket_day": ticket_day or None,
                "ticket_day_total": day_rows.values("pk").distinct().count() if ticket_day else None}

    @staticmethod
    def _otrs_ticket_url(ticket):
        base = urlsplit(str(getattr(settings, "OTRS_WEB_BASE_URL", "") or ""))
        if base.scheme not in {"http", "https"} or not base.netloc or base.username or base.password:
            return ""
        query = urlencode({"Action": "AgentTicketZoom", "TicketID": ticket.external_id})
        return urlunsplit((base.scheme, base.netloc, base.path, query, ""))

    @classmethod
    def _present_ticket(cls, ticket, metric, filters, start, end, timezone, *, ticket_day=None):
        def stamp(value):
            return timezone.localtime(value).strftime("%d/%m/%Y %H:%M") if value else "—"
        relations = []
        providers, states = set(), set()
        for relation in getattr(ticket, "_cockpit_relations", []):
            link = relation.cmdb_link
            detail = {"config_item_id": relation.config_item_id, "resolved": bool(link), "number": link.source_number if link else "",
                      "designation": link.designation if link else "", "circuit": link.circuit if link else "",
                      "project": (link.business_project or link.legacy_project) if link else "",
                      "client": link.contracting_client if link else "",
                      "provider": link.operator.name if link and link.operator else "", "uf": link.state if link else ""}
            relations.append(detail)
            if detail["provider"]: providers.add(detail["provider"])
            if detail["uf"]: states.add(detail["uf"])
        ticket_status = normalize_business_status(ticket.business_status)
        status = cls.STATUS_LABELS.get(ticket_status, ticket_status)
        reason = normalize_closing_reason(ticket.closing_code_raw)
        projects = sorted({item.get("project") for item in relations if item.get("project")})
        clients = sorted({item.get("client") for item in relations if item.get("client")})
        customer_project = ", ".join(projects or clients) or ticket.customer_id
        terminal_status = ticket_status in TERMINAL_STATUSES
        if ticket_status == "CANCELED":
            closed_at = ticket.canceled_at or ticket.terminal_state_at or ticket.resolved_at
        elif terminal_status:
            closed_at = ticket.resolved_at or ticket.terminal_state_at or ticket.canceled_at
        else:
            closed_at = None
        age_end = closed_at or timezone.now()
        age_hours = max(0, round((age_end - ticket.source_created_at).total_seconds() / 3600, 1)) if ticket.source_created_at else None
        age_label = ""
        if age_hours is not None:
            whole_hours = int(age_hours)
            age_label = f"{whole_hours}h" if whole_hours < 48 else f"{whole_hours // 24}d {whole_hours % 24}h"
        date_field = cls._metric_date_field(metric)
        base_labels = {
            "tickets": "Criado entre", "open_tickets": "Criado entre", "pending_tickets": "Criado entre",
            "scheduled_tickets": "Criado entre", "resolved_tickets": "Data de resolu\u00e7\u00e3o entre",
            "closed_tickets": "Data de resolu\u00e7\u00e3o entre", "resolved_closed_tickets": "Data de resolu\u00e7\u00e3o entre",
            "canceled_tickets": "Cancelado entre",
        }
        base_evidence = (f"{base_labels.get(metric, 'Data base entre')} {start:%d/%m/%Y} e {end:%d/%m/%Y}"
                         if metric != "backlog" else "Ticket presente no backlog atual")
        evidence = [base_evidence, f"Estado atual: {status}"]
        if getattr(ticket, "_operational_stage", ""):
            stage = ticket._operational_stage
            evidence.append(f"Processo: Cancelamento · etapa: {cls.STAGE_LABELS.get(stage, stage)}")
        evidence.append(f"Fila OTRS atual: {ticket.queue_name}")
        if ticket_day:
            evidence.append(f"Dia selecionado na an\u00e1lise: {date.fromisoformat(ticket_day):%d/%m/%Y}")
        if metric == "canceled_tickets":
            evidence.append("M\u00e9trica espec\u00edfica de cancelamentos")
        elif metric in {"resolved_tickets", "closed_tickets", "resolved_closed_tickets"}:
            evidence.append(f"Data de resolu\u00e7\u00e3o operacional e estado OTRS correspondente: {status}")
            if ticket.resolved_at:
                evidence.append("Data usada: ticketResolvido")
            elif ticket.terminal_state_at:
                evidence.append("Fallback usado: StateUpdate terminal, pois ticketResolvido n\u00e3o tem data")
        if ticket_status in cls.OPERATIONAL_VALID_STATES:
            evidence.append("Estado operacional v\u00e1lido")
        for key, value in filters.items():
            if key == "termination_reason": evidence.append(f"Motivo = {reason}")
            elif key == "provider" and value in providers: evidence.append(f"Provedor = {value}")
            elif key == "uf" and value in states: evidence.append(f"UF = {value}")
            elif key == "status" and normalize_business_status(value) == ticket_status: evidence.append(f"Status = {status}")
            elif key == "source_state" and value == ticket.external_state_name: evidence.append(f"Situa\u00e7\u00e3o OTRS = {ticket.external_state_name}")
            elif key == "priority" and value == ticket.priority_name: evidence.append(f"Prioridade = {ticket.priority_name}")
            elif key == "priority" and value == "high-critical":
                normalized_priority = unicodedata.normalize("NFKD", ticket.priority_name).encode("ascii", "ignore").decode().casefold()
                if normalized_priority in {"alto", "critico"}: evidence.append("Prioridade = Alto / Cr\u00edtico")
            elif key == "sla" and value == ticket.sla_name: evidence.append(f"SLA = {ticket.sla_name}")
            elif key == "service" and value == ticket.service_name: evidence.append(f"Servi\u00e7o = {ticket.service_name}")
        evidence.append("Dentro do Data Scope aplicado")
        has_link = any(item["resolved"] for item in relations)
        base_date = "Atual" if metric == "backlog" else stamp(getattr(ticket, date_field))
        return {"id": ticket.pk, "ticket": ticket.ticket_number, "base_date": base_date,
                "title": ticket.title, "customer_project": customer_project,
                "age_hours": age_hours, "age_label": age_label,
                "closed_at": stamp(closed_at), "otrs_url": cls._otrs_ticket_url(ticket),
                "created_at": stamp(ticket.source_created_at), "changed_at": stamp(ticket.source_changed_at),
                "terminal_at": stamp(ticket.terminal_state_at), "resolved_at": stamp(ticket.resolved_at),
                "canceled_at": stamp(ticket.canceled_at), "status": status, "status_code": ticket_status,
                "source_state": ticket.external_state_name, "reason": reason,
                "closing_code_raw": ticket.closing_code_raw,
                "queue": ticket.queue_name,
                "operational_stage": getattr(ticket, "_operational_stage", ""),
                "operational_stage_label": cls.STAGE_LABELS.get(getattr(ticket, "_operational_stage", ""), ""),
                "origin": cls.ORIGIN_LABELS.get(getattr(ticket, "_operational_origin", ""), ""),
                "provider": ", ".join(sorted(providers)) or "N\u00e3o identificado", "uf": ", ".join(sorted(states)),
                "priority": ticket.priority_name, "sla": ticket.sla_name, "service": ticket.service_name,
                "source": "OTRS + CMDB" if has_link else "OTRS", "links": relations, "evidence": evidence}

    @classmethod
    def _chart_specs(cls, metric, group, filters, series, grouped, grain, total,
                     previous_series=(), previous_grouped=(), *, rows=None, links=None):
        specs = []
        prior_time = [row["value"] for row in previous_series]
        if metric not in cls.SNAPSHOT_METRICS:
            grain_label = {"day": "di\u00e1ria", "week": "semanal", "month": "mensal"}[grain]
            values = [row["value"] for row in series]
            if len(set(values)) > 1:
                base_label = {"source_created_at": "data de cria\u00e7\u00e3o", OPERATIONAL_RESOLUTION_FIELD: "data de resolu\u00e7\u00e3o",
                              "canceled_at": "data de cancelamento"}.get(cls._metric_date_field(metric), "data de cria\u00e7\u00e3o")
                specs.append({"key": "temporal", "type": "temporal_overview", "grain": grain,
                              "title": "Vis\u00e3o geral do per\u00edodo",
                              "subtitle": f"Granularidade {grain_label} \u00b7 Base temporal: {base_label}",
                              "dimension": "time", "metric": metric, "unit": cls._chart_unit(metric),
                              "labels": [row["label"] for row in series], "values": values,
                              "details": [{"key": row["key"], "label": row.get("long_label", row["label"]), "value": row["value"],
                                           "share": round(row["value"] * 100 / total, 1) if total else 0,
                                           "previous_value": prior_time[index] if index < len(prior_time) else None}
                                          for index, row in enumerate(series)]})
        fixed = {"provider": "provider", "uf": "uf", "status": "status", "source_state": "source_state",
                 "priority": "priority", "sla": "sla", "termination_reason": "termination_reason"}
        def dimension_spec(dimension, dimension_rows, *, complementary=False):
            denominator = total
            if dimension == "provider" and metric not in {"links_affected", "recurrences"} and not dimension_rows[0].get("share"):
                denominator = cls._provider_quality(rows, links)["identified"] if rows is not None else total
            entries = []
            for row in dimension_rows[:10]:
                raw = row.get("dimension", "")
                label = cls._dimension_label(dimension, raw)
                value = row["value"]
                logo = provider_logo(raw) if dimension == "provider" else None
                entries.append({"key": str(raw), "label": label, "value": value,
                                "share": row.get("share", round(value * 100 / denominator, 1) if denominator else 0),
                                **({"logo_url": logo["url"], "logo_initials": logo["initials"]} if logo else {}),
                                "previous_value": next((item["value"] for item in previous_grouped if item["label"] == label), None)
                                if not complementary and dimension == group else None})
            if not entries:
                return None
            title = ("Ranking de provedores" if dimension == "provider" else "Ranking de UFs") if dimension in {"provider", "uf"} else f"Distribui\u00e7\u00e3o por {cls.DIMENSIONS[dimension].lower()}"
            chart_type = "ranking" if dimension in {"provider", "uf"} else "compact_distribution" if dimension == "status" else "horizontal_bar"
            subtitle = ("Participa\u00e7\u00e3o sobre tickets com provedor identificado" if dimension == "provider"
                        else cls.METRICS[metric])
            spec_key = "provider-ranking" if dimension == "provider" else "uf-ranking" if dimension == "uf" else f"dimension-{dimension}"
            return {"key": spec_key, "type": chart_type, "title": title,
                    "subtitle": subtitle, "dimension": dimension, "metric": metric,
                    "unit": cls._chart_unit(metric), "labels": [item["label"] for item in entries],
                    "values": [item["value"] for item in entries], "details": entries,
                    "complementary": complementary}

        primary_is_fixed = bool(fixed.get(group) and filters.get(fixed[group]))
        if group != "month" and not primary_is_fixed and grouped:
            primary_spec = dimension_spec(group, grouped)
            if primary_spec:
                specs.append(primary_spec)

        if rows is not None and links is not None:
            complementary_count = 0
            for dimension in ("provider", "uf", "status", "termination_reason", "priority", "source_state", "sla"):
                if dimension == group or filters.get(fixed.get(dimension, "")):
                    continue
                rows_by_dimension = cls._grouped(rows, metric, dimension, links, ())
                if len(rows_by_dimension) <= 1:
                    continue
                spec = dimension_spec(dimension, rows_by_dimension, complementary=True)
                if spec:
                    specs.append(spec)
                    complementary_count += 1
                if complementary_count == 3:
                    break
        return specs[:5]

    @staticmethod
    def _chart_unit(metric):
        if metric == "links_affected":
            return "links"
        if metric == "recurrences":
            return "links reincidentes"
        return "tickets"

    @classmethod
    def _monthly_calendar(cls, rows, metric, start, end, requested_month, overview, links):
        if metric in cls.SNAPSHOT_METRICS:
            return None

        month_keys = _months(start, end)
        month_buckets = [(key, cls._month_label(key), bucket_start, bucket_end)
                         for key, bucket_start, bucket_end in _month_buckets(start, end)]
        if overview and all(len(item["key"]) == 7 for item in overview):
            month_values = {item["key"]: item["value"] for item in overview}
        else:
            month_values = cls._aggregate_period_values(rows, metric, month_buckets, links)

        available = []
        for key in month_keys:
            year, month_number = map(int, key.split("-"))
            long_label = cls._month_label(key, long=True)
            available.append({
                "key": key, "label": f"{long_label.split(' de ')[0]}/{year}",
                "short_label": cls._month_label(key), "full_label": long_label,
                "value": month_values.get(key, 0),
            })
        latest_with_data = next((item for item in reversed(available) if item["value"] > 0), None)
        selected = next((item for item in available if item["key"] == requested_month), None)
        selected = selected or latest_with_data or (available[-1] if available else None)
        if not selected:
            return None

        year, month_number = map(int, selected["key"].split("-"))
        month_start = date(year, month_number, 1)
        month_end = cls._month_end(month_start)
        _grain, day_buckets = _time_buckets(month_start, month_end)
        day_values = cls._aggregate_period_values(rows, metric, day_buckets, links)
        calendar_rows = []
        in_range_values = []
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month_number):
            row = []
            for day in week:
                inside_month = day.month == month_number
                inside_period = inside_month and start <= day <= end
                value = day_values.get(day.isoformat(), 0) if inside_period else 0
                if inside_period:
                    in_range_values.append((day, value))
                row.append({
                    "date": day.isoformat(), "day": day.day if inside_month else None,
                    "value": value if inside_month else None,
                    "inside_month": inside_month, "inside_query_period": inside_period,
                    "has_data": bool(inside_period and value),
                })
            calendar_rows.append(row)

        active_days = [(day, value) for day, value in in_range_values if value > 0]
        peak_day, peak_value = max(active_days, key=lambda item: (item[1], -item[0].day)) if active_days else (None, 0)
        month_total = month_values.get(selected["key"], 0)
        query_day_count = sum(1 for day, _value in in_range_values if start <= day <= end)
        daily_sum = sum(value for _day, value in in_range_values)
        unit = cls._chart_unit(metric)
        summary = {
            "total": month_total,
            "daily_total": daily_sum,
            "average_per_day": round(month_total / query_day_count, 1) if query_day_count else 0,
            "peak_day": peak_day.isoformat() if peak_day else None,
            "peak_value": peak_value,
            "days_with_occurrence": len(active_days),
            "days_in_query_period": query_day_count,
            "unit": unit,
        }
        flat_days = [day for week in calendar_rows for day in week]
        max_daily = max((item["value"] or 0 for item in flat_days if item["inside_query_period"]), default=0)
        for item in flat_days:
            value = item["value"] or 0
            item["share_of_month"] = round(value * 100 / daily_sum, 1) if item["inside_query_period"] and daily_sum else 0
            item["intensity"] = round(value / max_daily, 3) if item["inside_query_period"] and max_daily else 0
            item["accessible_label"] = (
                f"{item['day']} de {cls._month_label(selected['key'], long=True).split(' de ')[0]} de {year}, "
                f"{value} {unit}" if item["inside_month"] and item["inside_query_period"]
                else "Fora do período analisado" if item["inside_month"] else ""
            )

        calendar_spec = {
            "key": "monthly-calendar", "type": "monthly_calendar",
            "title": f"Detalhe de {selected['label']}", "month": selected["key"],
            "month_title": selected["full_label"], "unit": unit,
            "weekdays": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
            "weeks": calendar_rows, "summary": summary,
        }
        return {
            "overview": overview, "available_months": available,
            "selected_month": selected["key"], "selected_month_label": selected["label"],
            "calendar_days": flat_days, "calendar_weeks": calendar_rows,
            "weekdays": calendar_spec["weekdays"], "month_summary": summary, "unit": unit, "chart_spec": calendar_spec,
        }

    @classmethod
    def _aggregate_period_values(cls, rows, metric, buckets, links):
        from django.db.models import Count

        if metric in cls.SNAPSHOT_METRICS or not buckets:
            return {}
        period_case = _period_case(cls._metric_date_field(metric), buckets)
        if metric == "recurrences":
            relation = OTRSTicketConfigItem.objects.filter(
                ticket_id__in=rows.values("pk"), is_present_in_source=True,
                cmdb_link__is_present_in_source=True, cmdb_link__in=links,
            ).annotate(period=period_case)
            grouped = relation.exclude(period__isnull=True).values("period", "cmdb_link_id").annotate(
                ticket_count=Count("ticket_id", distinct=True),
            ).filter(ticket_count__gte=2).values("period").annotate(
                value=Count("cmdb_link_id", distinct=True),
            )
        elif metric == "links_affected":
            relation = OTRSTicketConfigItem.objects.filter(
                ticket_id__in=rows.values("pk"), is_present_in_source=True,
                cmdb_link__is_present_in_source=True, cmdb_link__in=links,
            ).annotate(period=period_case)
            grouped = relation.exclude(period__isnull=True).values("period").annotate(
                value=Count("cmdb_link_id", distinct=True),
            )
        else:
            grouped = rows.annotate(period=period_case).exclude(period__isnull=True).values("period").annotate(
                value=Count("pk", distinct=True),
            )
        return {item["period"]: item["value"] for item in grouped}

    @classmethod
    def normalize_query(cls, payload):
        start_value, end_value = payload.get("start") or payload.get("date_from"), payload.get("end") or payload.get("date_to")
        try:
            if start_value or end_value:
                start = date.fromisoformat(start_value) if start_value else _month_date(payload.get("period_from"))
                end = date.fromisoformat(end_value) if end_value else cls._month_end(_month_date(payload.get("period_to")))
            else:
                start = _month_date(payload.get("period_from"))
                end = cls._month_end(_month_date(payload.get("period_to")))
        except (TypeError, ValueError):
            raise ValueError("Data ou per\u00edodo inv\u00e1lido.") from None
        if start > end:
            raise ValueError("A data inicial n\u00e3o pode ser posterior \u00e0 data final.")

        scope = str(payload.get("scope", "support") or "support").strip().lower()
        if scope not in cls.SCOPES:
            raise ValueError("Contexto do Cockpit inválido.")
        metric = payload.get("metric", "tickets")
        if metric == "links":
            metric = "tickets"
        default_group = {"support": "provider", "cancellation": "cancellation_stage",
                         "general": "operational_origin"}[scope]
        raw_group = payload.get("group_by") or default_group
        if raw_group == "project" and scope in {"cancellation", "general"}:
            group = raw_group
        else:
            group = cls.LEGACY_DIMENSIONS.get(raw_group, raw_group)
        if metric not in cls.METRICS or group not in cls.DIMENSIONS:
            raise ValueError("M\u00e9trica ou dimens\u00e3o n\u00e3o autorizada.")

        raw_filters = payload.get("filters", {})
        if not isinstance(raw_filters, dict):
            raise ValueError("Filtros inv\u00e1lidos.")
        raw_filters = dict(raw_filters)
        for alias, canonical in cls.FILTER_ALIASES.items():
            if alias in raw_filters and canonical not in raw_filters:
                raw_filters[canonical] = raw_filters[alias]
            raw_filters.pop(alias, None)
        unknown = set(raw_filters) - cls.FILTERS - set(cls.LEGACY_FILTERS)
        if unknown:
            raise ValueError("Filtro n\u00e3o autorizado.")
        filters = {}
        for key, value in raw_filters.items():
            canonical = cls.LEGACY_FILTERS.get(key, key)
            if not canonical or value in (None, "", "all"):
                continue
            if not isinstance(value, (str, int)):
                raise ValueError("Valor de filtro inv\u00e1lido.")
            normalized = str(value).strip()
            if not normalized or normalized.casefold() == "all":
                continue
            if canonical in {"uf", "status", "stage", "origin", "age_bucket"}:
                normalized = normalized.upper()
                if canonical == "status":
                    normalized = normalize_business_status(normalized)
            elif canonical == "responsibility":
                normalized = normalized.upper()
            elif canonical == "termination_reason":
                normalized = ClosingReasonCanonicalizer.key(normalized)
            filters[canonical] = normalized
        if filters.get("stage") and filters["stage"] not in cls.STAGE_LABELS:
            raise ValueError("Etapa do cancelamento inválida.")
        if filters.get("origin") and filters["origin"] not in cls.ORIGIN_LABELS:
            raise ValueError("Origem operacional inválida.")
        if filters.get("age_bucket") and (
            filters["age_bucket"] != "OVER_24H" or scope not in {"support", "cancellation", "general"} or metric != "backlog"
        ):
            raise ValueError("O filtro de idade acima de 24h s\u00f3 est\u00e1 dispon\u00edvel no backlog atual.")
        if filters.get("responsibility") not in (None, "PROCEDENTE", "IMPROCEDENTE"):
            raise ValueError("Proced\u00eancia inv\u00e1lida.")
        if filters.get("responsible") not in (None, "assigned", "unassigned"):
            raise ValueError("Filtro de respons\u00e1vel inv\u00e1lido.")
        if filters.get("queue") and len(filters["queue"]) > 250:
            raise ValueError("Fila inv\u00e1lida.")
        compare = payload.get("compare_to") or None
        if compare:
            _month_date(compare)
        selected_month = payload.get("selected_month") or None
        if selected_month:
            selected_month = _month_date(selected_month).strftime("%Y-%m")
        def option_enabled(key):
            value = payload.get(key, True)
            if isinstance(value, bool):
                return value
            if value in (1, "1", "true", "True", "on", "yes"):
                return True
            if value in (0, "0", "false", "False", "off", "no", None, ""):
                return False
            raise ValueError("Op\u00e7\u00e3o gerencial inv\u00e1lida.")

        include_cancelled = option_enabled("include_cancelled")
        include_improcedente = option_enabled("include_improcedente")
        if metric == "backlog":
            include_improcedente = False
        elif filters.get("responsibility") == "IMPROCEDENTE":
            include_improcedente = True
        return {"scope": scope, "metric": metric, "group_by": group, "start": start, "end": end,
                "filters": filters, "compare_to": compare, "selected_month": selected_month,
                "include_cancelled": include_cancelled,
                "include_improcedente": include_improcedente}

    @staticmethod
    def _month_end(month_start):
        next_month = date(month_start.year + (month_start.month == 12),
                          1 if month_start.month == 12 else month_start.month + 1, 1)
        return next_month - timedelta(days=1)

    @classmethod
    def _metric_semantics(cls, metric, *, scope="support", include_cancelled=True,
                          include_improcedente=True):
        note = ""
        if not include_cancelled or (not include_improcedente and metric != "backlog"):
            excluded = []
            if not include_cancelled:
                excluded.append("cancelados")
            if not include_improcedente and metric != "backlog":
                excluded.append("improcedentes")
            note = " Registros exclu\u00eddos: " + "; ".join(excluded) + "."
        if metric == "tickets":
            if scope != "support":
                return "Data de cria\u00e7\u00e3o; inclui estados operacionais v\u00e1lidos e cancelados conforme a op\u00e7\u00e3o. Tickets ativos sem etapa reconhecida permanecem no recorte." + note
            return "Data de cria\u00e7\u00e3o; inclui estados operacionais v\u00e1lidos, cancelados e improcedentes por padr\u00e3o, conforme a sem\u00e2ntica da m\u00e9trica." + note
        if metric in {"open_tickets", "pending_tickets", "scheduled_tickets"}:
            status = cls.METRIC_STATUS[metric]
            return f"Data de cria\u00e7\u00e3o no per\u00edodo; tickets atualmente no estado {status}. CANCELED e UNKNOWN ficam de fora." + note
        if metric in {"resolved_tickets", "closed_tickets", "resolved_closed_tickets"}:
            return "Data de resolu\u00e7\u00e3o operacional no per\u00edodo; estados operacionais RESOLVED, incluindo estados OTRS brutos Resolvido e Encerrado, sem CANCELED." + note
        if metric == "canceled_tickets":
            return "Data de cancelamento no per\u00edodo; estado CANCELED, em m\u00e9trica espec\u00edfica." + note
        if metric == "backlog":
            return "Snapshot atual; inclui somente tickets no estado operacional OPEN ou PENDING. SCHEDULED, CANCELED e UNKNOWN ficam de fora." + note
        if metric in cls.SNAPSHOT_METRICS:
            return "Snapshot atual da fila; datas n\u00e3o restringem o resultado." + note
        return "Data de cria\u00e7\u00e3o no per\u00edodo." + note

    @staticmethod
    def _comparison(filtered, query, links, current_value):
        metric = query["metric"]
        if metric in AnalyticsQueryService.SNAPSHOT_METRICS:
            return None
        if query["compare_to"]:
            previous_start = _month_date(query["compare_to"])
            previous_end = previous_start + (query["end"] - query["start"])
        else:
            previous_start, previous_end = AnalyticsQueryService._previous_period(query["start"], query["end"])
        previous_from, previous_to = _date_boundaries(previous_start, previous_end)
        date_field = AnalyticsQueryService._metric_date_field(metric)
        previous_rows = filtered.filter(**{f"{date_field}__gte": previous_from, f"{date_field}__lt": previous_to})
        previous_rows = AnalyticsQueryService._metric_rows(filtered, previous_rows, metric)
        previous_value = AnalyticsQueryService._metric_value(previous_rows, metric, links)
        _, previous_buckets = _time_buckets(previous_start, previous_end)
        previous_series = AnalyticsQueryService._series(previous_rows, metric, previous_buckets, links, previous_value)
        grouped = AnalyticsQueryService._grouped(previous_rows, metric, query["group_by"], links, previous_buckets)
        previous_grouped = []
        denominator = previous_value
        quality = None if metric in {"links_affected", "recurrences"} else AnalyticsQueryService._provider_quality(previous_rows, links)
        if quality and query["group_by"] == "provider" and metric not in {"links_affected", "recurrences"}:
            denominator = quality["identified"]
        for row in list(grouped[:10]):
            raw = row["dimension"] if row["dimension"] not in (None, "") else "N\\u00e3o classificado"
            label = AnalyticsQueryService._dimension_label(query["group_by"], raw)
            previous_grouped.append({"label": label, "value": row["value"],
                                     "share": round(row["value"] * 100 / denominator, 1) if denominator else 0})
        difference = current_value - previous_value
        return {"current_value": current_value, "previous_value": previous_value,
                "absolute_change": difference,
                "percentage_change": round(difference * 100 / previous_value, 1) if previous_value else (0 if difference == 0 else None),
                "value": previous_value, "difference": difference,
                "percent": round(difference * 100 / previous_value, 1) if previous_value else (0 if difference == 0 else None),
                "direction": "up" if difference > 0 else "down" if difference < 0 else "neutral",
                "previous_start": previous_start.isoformat(), "previous_end": previous_end.isoformat(),
                "provider_quality": quality, "previous_series": previous_series,
                "previous_grouped": previous_grouped}

    @classmethod
    def _apply_filters(cls, tickets, links, filters):
        from django.db.models import Q
        from django.utils import timezone

        query = {}
        for key, field in (("status", "business_status"), ("source_state", "external_state_name"),
                           ("service", "service_name"), ("sla", "sla_name"),
                           ("stage", "_operational_stage"), ("origin", "_operational_origin"),
                           ("queue", "queue_name"), ("responsibility", "responsibility_normalized")):
            if filters.get(key) and key != "status":
                query[field] = filters[key]
        filtered = tickets.filter(**query) if query else tickets
        if filters.get("status"):
            filtered = filtered.filter(business_status_query("business_status", filters["status"]))
        if filters.get("responsible") == "unassigned":
            filtered = filtered.filter(Q(responsible_user_id__isnull=True) | Q(responsible_user_id=0))
        elif filters.get("responsible") == "assigned":
            filtered = filtered.filter(responsible_user_id__gt=0)
        if filters.get("age_bucket") == "OVER_24H":
            filtered = filtered.filter(
                business_status__in=BACKLOG_STATUSES,
                source_created_at__lte=timezone.now() - timedelta(hours=24),
            )
        priority = filters.get("priority")
        if priority == "high-critical":
            import unicodedata
            names = list(filtered.exclude(priority_name="").values_list("priority_name", flat=True).distinct())
            selected = [name for name in names if unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold() in {"alto", "critico"}]
            filtered = filtered.filter(priority_name__in=selected)
        elif priority:
            filtered = filtered.filter(priority_name=priority)
        reason = filters.get("termination_reason")
        if reason:
            filtered = filtered.filter(closing_code_raw__in=ClosingReasonCanonicalizer.raw_values(reason))
        link_filters = {cls.LINK_FILTERS[key]: filters[key] for key in cls.LINK_FILTERS
                        if key != "project" and filters.get(key)}
        if link_filters:
            filtered = filtered.filter(
                config_items__is_present_in_source=True,
                config_items__cmdb_link__is_present_in_source=True,
                config_items__cmdb_link__in=links,
                **link_filters,
            )
        if filters.get("project"):
            filtered = filtered.filter(
                Q(config_items__is_present_in_source=True,
                  config_items__cmdb_link__is_present_in_source=True,
                  config_items__cmdb_link__in=links,
                  config_items__cmdb_link__business_project=filters["project"])
                | Q(config_items__is_present_in_source=True,
                    config_items__cmdb_link__is_present_in_source=True,
                    config_items__cmdb_link__in=links,
                    config_items__cmdb_link__contracting_client=filters["project"])
                | Q(config_items__is_present_in_source=True,
                    config_items__cmdb_link__is_present_in_source=True,
                    config_items__cmdb_link__in=links,
                    config_items__cmdb_link__legacy_project=filters["project"])
            )
        return filtered.distinct()

    @staticmethod
    def _metric_date_field(metric):
        if metric == "canceled_tickets":
            return "canceled_at"
        if metric in {"resolved_tickets", "closed_tickets", "resolved_closed_tickets"}:
            return OPERATIONAL_RESOLUTION_FIELD
        return "source_created_at"

    @classmethod
    def _metric_rows(cls, filtered, historical, metric):
        from django.db.models import Q

        if metric in {"resolved_tickets", "closed_tickets", "resolved_closed_tickets"}:
            return historical.filter(business_status_query("business_status", RESOLVED_STATUS))
        if metric in {"open_tickets", "pending_tickets", "scheduled_tickets"}:
            rows = historical
            if metric == "open_tickets":
                rows = rows.filter(business_status="OPEN")
            elif metric == "pending_tickets":
                rows = rows.filter(business_status="PENDING")
            return rows.filter(business_status="SCHEDULED") if metric == "scheduled_tickets" else rows
        if metric == "backlog":
            active = Q(business_status__in=BACKLOG_STATUSES)
            return filtered.filter(is_present_in_queue=True).filter(active)
        rows = historical
        if metric in cls.METRIC_STATUS:
            rows = rows.filter(business_status_query("business_status", cls.METRIC_STATUS[metric]))
        return rows

    @classmethod
    def _metric_value(cls, rows, metric, links):
        from django.db.models import Count
        from aplicativos.integracoes.models import OTRSTicketConfigItem

        if metric == "recurrences":
            relation = OTRSTicketConfigItem.objects.filter(
                ticket_id__in=rows.values("pk"), is_present_in_source=True,
                cmdb_link__in=links,
            ).values("cmdb_link_id").annotate(ticket_count=Count("ticket_id", distinct=True)).filter(ticket_count__gte=2)
            return relation.count()
        if metric == "links_affected":
            return OTRSTicketConfigItem.objects.filter(
                ticket_id__in=rows.values("pk"), is_present_in_source=True,
                cmdb_link__in=links,
            ).values("cmdb_link_id").distinct().count()
        return rows.values("pk").distinct().count()

    @classmethod
    def _grouped(cls, rows, metric, group, links, buckets):
        from django.db.models import Case, CharField, Count, F, Value, When
        from django.db.models.functions import Coalesce, NullIf
        from aplicativos.integracoes.models import OTRSTicketConfigItem

        if metric == "recurrences":
            relations = OTRSTicketConfigItem.objects.filter(
                ticket_id__in=rows.values("pk"), is_present_in_source=True,
                cmdb_link__is_present_in_source=True, cmdb_link__in=links,
            )
            if group == "month":
                period_case = _period_case("ticket__source_created_at", buckets)
                return relations.annotate(dimension=period_case).exclude(dimension__isnull=True).values(
                    "dimension", "cmdb_link_id",
                ).annotate(ticket_count=Count("ticket_id", distinct=True)).filter(
                    ticket_count__gte=2,
                ).values("dimension").annotate(value=Count("cmdb_link_id", distinct=True)).order_by("-value", "dimension")
            field = {
                "provider": "cmdb_link__operator__name",
                "uf": "cmdb_link__state",
                "status": "ticket__business_status",
                "source_state": "ticket__external_state_name",
                "service": "ticket__service_name",
                "sla": "ticket__sla_name",
                "priority": "ticket__priority_name",
                "termination_reason": "ticket__closing_code_raw",
                "cancellation_stage": "ticket__queue_name",
                "operational_origin": "ticket__queue_name",
                "project": "cmdb_link__business_project",
            }[group]
            if group == "termination_reason":
                relations = ClosingReasonCanonicalizer.annotate_key(
                    relations, field="ticket__closing_code_raw", output="dimension",
                )
                by_link = relations.values("dimension", "cmdb_link_id")
            elif group == "project":
                project_name = Coalesce(
                    NullIf(F("cmdb_link__business_project"), Value("")),
                    NullIf(F("cmdb_link__contracting_client"), Value("")),
                    NullIf(F("cmdb_link__legacy_project"), Value("")),
                    Value("N\u00e3o classificado"),
                )
                by_link = relations.annotate(dimension=project_name).values("dimension", "cmdb_link_id")
            else:
                by_link = relations.annotate(
                    dimension=Coalesce(NullIf(F(field), Value("")), Value("N\u00e3o classificado")),
                ).values(
                    "dimension", "cmdb_link_id",
                )
            by_link = by_link.annotate(ticket_count=Count("ticket_id", distinct=True)).filter(ticket_count__gte=2)
            grouped = by_link.values("dimension").annotate(value=Count("cmdb_link_id", distinct=True)).order_by("-value", "dimension")
            if group == "termination_reason":
                return [{"dimension": ClosingReasonCanonicalizer.display(item["dimension"]), "value": item["value"]}
                        for item in grouped]
            return grouped

        if group == "termination_reason":
            counts = ClosingReasonCanonicalizer.annotate_key(rows).values(
                "closing_reason_key",
            ).annotate(value=Count("pk", distinct=True))
            return [{"dimension": ClosingReasonCanonicalizer.display(item["closing_reason_key"]), "value": item["value"]}
                    for item in counts.order_by("-value", "closing_reason_key")]

        amount = Count("config_items__cmdb_link_id", distinct=True) if metric == "links_affected" else Count("pk", distinct=True)
        if group == "month":
            if metric in cls.SNAPSHOT_METRICS:
                return [{"dimension": "Atual", "value": cls._metric_value(rows, metric, links)}]
            if metric == "links_affected":
                rows = rows.filter(config_items__is_present_in_source=True, config_items__cmdb_link__is_present_in_source=True, config_items__cmdb_link__in=links)
            period_case = _period_case(cls._metric_date_field(metric), buckets)
            return rows.annotate(dimension=period_case).exclude(dimension__isnull=True).values("dimension").annotate(value=amount).order_by("-value", "dimension")
        field = {
            "provider": "config_items__cmdb_link__operator__name",
            "uf": "config_items__cmdb_link__state",
            "status": "business_status",
            "source_state": "external_state_name",
            "service": "service_name",
            "sla": "sla_name",
            "priority": "priority_name",
            "termination_reason": "closing_code_raw",
            "cancellation_stage": "_operational_stage",
            "operational_origin": "_operational_origin",
            "project": "config_items__cmdb_link__business_project",
        }[group]
        if group in cls.LINK_GROUPS:
            rows = rows.filter(config_items__is_present_in_source=True, config_items__cmdb_link__is_present_in_source=True, config_items__cmdb_link__in=links)
            if group == "provider":
                rows = rows.exclude(config_items__cmdb_link__operator__isnull=True).exclude(config_items__cmdb_link__operator__name="")
        if group == "termination_reason":
            rows = ClosingReasonCanonicalizer.annotate_key(rows, field=field, output="dimension")
        elif group == "status":
            rows = rows.annotate(dimension=Case(
                When(business_status="CLOSED", then=Value(RESOLVED_STATUS)),
                default=F("business_status"), output_field=CharField(max_length=16),
            ))
        elif group == "project":
            rows = rows.annotate(dimension=Coalesce(
                NullIf(F("config_items__cmdb_link__business_project"), Value("")),
                NullIf(F("config_items__cmdb_link__contracting_client"), Value("")),
                NullIf(F("config_items__cmdb_link__legacy_project"), Value("")),
                Value("N\u00e3o classificado"),
            ))
        else:
            rows = rows.annotate(dimension=Coalesce(NullIf(F(field), Value("")), Value("N\u00e3o classificado")))
        if group == "termination_reason":
            return [{"dimension": ClosingReasonCanonicalizer.display(item["dimension"]), "value": item["value"]}
                    for item in rows.values("dimension").annotate(value=Count("pk", distinct=True)).order_by("-value", "dimension")]
        return list(rows.values("dimension").annotate(value=amount).order_by("-value", "dimension"))

    @classmethod
    def _series(cls, rows, metric, buckets, links, total_value):
        if metric in cls.SNAPSHOT_METRICS:
            return [{"key": "current", "label": "Atual", "long_label": "Situa\u00e7\u00e3o atual", "value": total_value}]
        values = cls._aggregate_period_values(rows, metric, buckets, links)
        details = []
        for key, label, bucket_start, bucket_end in buckets:
            if len(key) == 10 and key[4] == "-":
                long_label = date.fromisoformat(key).strftime("%d/%m/%Y")
            elif "-W" in key:
                local_start = bucket_start.astimezone(ZoneInfo(settings.TIME_ZONE)).date()
                local_end = (bucket_end - timedelta(microseconds=1)).astimezone(ZoneInfo(settings.TIME_ZONE)).date()
                long_label = f"Semana de {local_start:%d/%m/%Y} a {local_end:%d/%m/%Y}"
            else:
                long_label = cls._month_label(key, long=True)
            details.append({"key": key, "label": label, "long_label": long_label, "value": values.get(key, 0)})
        return details

    @staticmethod
    def _month_label(value, long=False):
        year, number = map(int, value.split("-"))
        names = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
        full_names = ("Janeiro", "Fevereiro", "Mar\u00e7o", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro")
        return f"{full_names[number - 1]} de {year}" if long else f"{names[number - 1]}/{str(year)[-2:]}"

    @classmethod
    def _dimension_label(cls, group, value):
        if group == "status":
            return cls.STATUS_LABELS.get(value, "N\u00e3o classificado")
        if group == "cancellation_stage":
            known_queues = {queue for _key, _label, queue in cls.CANCELLATION_STAGES}
            return cls.STAGE_LABELS.get(value, value if value in known_queues else "N\u00e3o classificado")
        if group == "operational_origin":
            return cls.ORIGIN_LABELS.get(value, value if value == TicketQueryService.QUEUE_NAME else "N\u00e3o classificado")
        if group == "source_state":
            return cls.SOURCE_STATE_LABELS.get(str(value).casefold(), value)
        if group == "month":
            if value == "Atual":
                return value
            return cls._month_label(value)
        return value

    @classmethod
    def _options(cls, tickets, links):
        result = {key: [] for key in cls.FILTERS}
        result["stage"] = [{"value": key, "label": label} for key, label in cls.STAGE_LABELS.items()]
        result["origin"] = [{"value": key, "label": label} for key, label in cls.ORIGIN_LABELS.items()]
        result["age_bucket"] = [{"value": "OVER_24H", "label": "Mais de 24h"}]
        result["responsibility"] = [
            {"value": "IMPROCEDENTE", "label": "Improcedente"},
            {"value": "PROCEDENTE", "label": "Procedente"},
        ]
        result["responsible"] = [
            {"value": "assigned", "label": "Atribu\u00eddo"},
            {"value": "unassigned", "label": "Sem respons\u00e1vel"},
        ]
        result["status"] = [
            {"value": value, "label": cls.STATUS_LABELS[value]}
            for value in sorted({normalize_business_status(item) for item in
                                 tickets.values_list("business_status", flat=True).distinct()})
            if value in cls.STATUS_LABELS
        ]
        result["source_state"] = [
            {"value": value, "label": cls.SOURCE_STATE_LABELS.get(value.casefold(), value)}
            for value in tickets.exclude(external_state_name="").values_list("external_state_name", flat=True).distinct().order_by("external_state_name")
        ]
        result["queue"] = list(tickets.exclude(queue_name="").values_list("queue_name", flat=True).distinct().order_by("queue_name"))
        result["service"] = list(tickets.exclude(service_name="").values_list("service_name", flat=True).distinct().order_by("service_name"))
        result["priority"] = list(tickets.exclude(priority_name="").values_list("priority_name", flat=True).distinct().order_by("priority_name"))
        result["sla"] = list(tickets.exclude(sla_name="").values_list("sla_name", flat=True).distinct().order_by("sla_name"))
        raw_reasons = tickets.exclude(closing_code_raw="").order_by().values_list("closing_code_raw", flat=True).distinct()
        result["termination_reason"] = ClosingReasonCanonicalizer.options(raw_reasons)
        relation = OTRSTicketConfigItem.objects.filter(
            ticket_id__in=tickets.values("pk"), is_present_in_source=True,
            cmdb_link__in=links,
        )
        result["provider"] = list(relation.exclude(cmdb_link__operator__name="").exclude(cmdb_link__operator__isnull=True).values_list("cmdb_link__operator__name", flat=True).distinct().order_by("cmdb_link__operator__name"))
        result["uf"] = list(relation.exclude(cmdb_link__state="").values_list("cmdb_link__state", flat=True).distinct().order_by("cmdb_link__state"))
        project_values = set()
        for field in ("business_project", "contracting_client", "legacy_project"):
            project_values.update(
                value for value in relation.order_by().exclude(**{f"cmdb_link__{field}": ""})
                .values_list(f"cmdb_link__{field}", flat=True).distinct() if value
            )
        result["project"] = sorted(project_values, key=str.casefold)
        return result

class DashboardAnalyticsService:
    """Build the executive Dashboard from scoped local CMDB and NOC mirrors."""

    @staticmethod
    def _percent_label(value, total):
        percent = (value * 100 / total) if total else 0
        return f"{percent:.1f}".replace(".", ",") + "%"

    @staticmethod
    def _age_label(seconds):
        total_hours = max(int(seconds // 3600), 0)
        days, hours = divmod(total_hours, 24)
        if days:
            return f"{days}d {hours}h" if hours else f"{days}d"
        if total_hours:
            return f"{total_hours}h"
        return "<1h"

    @classmethod
    def _aging_snapshot(cls, open_tickets, now):
        labels = ("Até 24h", "24–48h", "48–72h", "Acima de 72h")
        counts = [0, 0, 0, 0]
        open_count = missing_dates = 0
        oldest_seconds = None
        hours_24 = 24 * 60 * 60
        for created_at in open_tickets.order_by().values_list("source_created_at", flat=True).iterator(chunk_size=1000):
            open_count += 1
            if created_at is None:
                missing_dates += 1
                continue
            age_seconds = max((now - created_at).total_seconds(), 0)
            oldest_seconds = age_seconds if oldest_seconds is None else max(oldest_seconds, age_seconds)
            if age_seconds <= hours_24:
                counts[0] += 1
            elif age_seconds <= hours_24 * 2:
                counts[1] += 1
            elif age_seconds <= hours_24 * 3:
                counts[2] += 1
            else:
                counts[3] += 1
        over_72 = counts[3]
        return {
            "open_count": open_count,
            "over_72": over_72,
            "over_72_percent": over_72 * 100 / open_count if open_count else 0,
            "over_72_percent_label": cls._percent_label(over_72, open_count),
            "oldest_label": cls._age_label(oldest_seconds) if oldest_seconds is not None else "—",
            "missing_dates": missing_dates,
            "bands": [
                {"label": label, "value": count, "tone": tone}
                for label, count, tone in zip(
                    labels, counts, ("green", "teal", "orange", "rose"),
                )
            ],
        }

    @classmethod
    def _client_distribution(cls, links):
        counts = {
            "claro": 0, "vivo": 0, "algar": 0, "other": 0,
            "unidentified": 0,
        }
        for row in links.order_by().values("contracting_client").annotate(value=Count("pk")):
            raw_client = row["contracting_client"] or ""
            client = BusinessIdentityRules.client_for_token(raw_client) or raw_client.strip()
            key = client.casefold() if client else "unidentified"
            bucket = key if key in {"claro", "vivo", "algar"} else "other" if client else "unidentified"
            counts[bucket] += row["value"]

        total_links = sum(counts.values())

        labels = {
            "claro": "Claro", "vivo": "Vivo", "algar": "Algar",
            "other": "Outras", "unidentified": "Cliente não identificado",
        }
        return [
            {
                "key": key,
                "label": labels[key],
                "value": value,
                "share_label": cls._percent_label(value, total_links),
                "share": value * 100 / total_links if total_links else 0,
            }
            for key, value in counts.items()
        ]

    @classmethod
    def _project_rows(cls, period_tickets, links, total_tickets):
        relations = OTRSTicketConfigItem.objects.filter(
            ticket_id__in=period_tickets.order_by().values("pk"),
            is_present_in_source=True,
            cmdb_link__is_present_in_source=True,
            cmdb_link__in=links,
        ).order_by().values_list(
            "ticket_id", "cmdb_link__business_project", "cmdb_link__contracting_client",
            "cmdb_link__legacy_project",
        ).distinct()
        ticket_projects = defaultdict(set)
        for ticket_id, project, client, legacy_project in relations.iterator(chunk_size=1000):
            raw_name = project or legacy_project or client
            canonical = canonical_project_name(raw_name) if raw_name else ""
            ticket_projects[ticket_id].add(canonical)

        counts = defaultdict(int)
        unclassified = max(total_tickets - len(ticket_projects), 0)
        for project_names in ticket_projects.values():
            display_names = {
                display_project_name(name) for name in project_names if name
            }
            classified = {
                name for name in display_names
                if name.casefold() not in {"não classificado", "nao classificado"}
            }
            if not classified:
                unclassified += 1
                continue
            for name in classified:
                counts[name] += 1
        if unclassified:
            counts["Não classificado"] += unclassified

        rows = [
            {"name": name, "value": value}
            for name, value in counts.items()
        ]
        rows.sort(key=lambda item: (-item["value"], item["name"].casefold()))
        visible = rows[:5]
        unclassified_row = next(
            (row for row in rows if row["name"].casefold() == "não classificado"), None,
        )
        if unclassified_row and unclassified_row not in visible:
            visible.append(unclassified_row)
        return [
            {
                **row,
                "share_label": cls._percent_label(row["value"], total_tickets),
                "share": row["value"] * 100 / total_tickets if total_tickets else 0,
            }
            for row in visible
        ]

    @classmethod
    def _reason_rows(cls, period_tickets, total_tickets, start, end):
        annotated = ClosingReasonCanonicalizer.annotate_key(
            period_tickets.order_by(), field="closing_code_raw", output="dashboard_reason_key",
        )
        grouped = annotated.values("dashboard_reason_key").annotate(
            value=Count("pk", distinct=True),
        ).order_by("dashboard_reason_key")
        counts = defaultdict(int)
        keys = {}
        for row in grouped:
            key = row["dashboard_reason_key"] or ""
            label = ClosingReasonCanonicalizer.display(key)
            counts[label] += row["value"]
            keys[label] = key

        rows = [{"name": name, "value": value, "key": keys[name]} for name, value in counts.items()]
        rows.sort(key=lambda item: (-item["value"], item["name"].casefold()))
        visible = rows[:5]
        unclassified = next(
            (row for row in rows if row["name"].casefold() == "não classificado"), None,
        )
        if unclassified and unclassified not in visible:
            visible.append(unclassified)

        cockpit_path = reverse("cockpit")
        result = []
        for row in visible:
            query = {
                "scope": "support",
                "metric": "tickets",
                "group_by": "termination_reason",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "termination_reason": row["key"],
            }
            result.append({
                **row,
                "share_label": cls._percent_label(row["value"], total_tickets),
                "share": row["value"] * 100 / total_tickets if total_tickets else 0,
                "cockpit_url": f"{cockpit_path}?{urlencode(query)}" if row["key"] else "",
            })
        return result

    @staticmethod
    def build(*, user, start, end):
        now = timezone.now()
        links = LinkQueryService.base(user)
        tickets = TicketQueryService.base(user, links=links)
        start_at, end_at = _date_boundaries(start, end)
        buckets = _month_buckets(start, end)
        period_tickets = tickets.filter(source_created_at__gte=start_at, source_created_at__lt=end_at)
        current_tickets = tickets.filter(is_present_in_queue=True)
        business_open = TicketQueryService.business_open(queryset=current_tickets)
        backlog_tickets = current_tickets.filter(business_status__in=BACKLOG_STATUSES)
        aging = DashboardAnalyticsService._aging_snapshot(backlog_tickets, now)

        client_distribution = DashboardAnalyticsService._client_distribution(links)
        total_links = sum(item["value"] for item in client_distribution)
        state_rows = list(
            links.filter(state__in=STATE_NAMES)
            .values("state").annotate(value=Count("pk"))
            .order_by("-value", "state")
        )
        top_states = [
            {"code": row["state"], "name": STATE_NAMES[row["state"]], "value": row["value"],
             "flag": f"imagens/bandeiras/estados/{row['state'].lower()}.svg"}
            for row in state_rows[:10]
        ]

        provider_rows = list(
            links.filter(operator__isnull=False)
            .exclude(operator__name="")
            .values("operator__name")
            .annotate(value=Count("pk", distinct=True))
            .order_by("-value", "operator__name")[:10]
        )
        top_providers = []
        for row in provider_rows:
            logo = provider_logo(row["operator__name"])
            top_providers.append({
                "name": row["operator__name"],
                "value": row["value"],
                "logo_url": logo["url"],
                "logo_initials": logo["initials"],
            })

        snapshot_status = normalized_status_counts(current_tickets.values(
            "business_status"
        ).annotate(total=Count("pk")))
        status_counts = {status: snapshot_status.get(status, 0) for status in STATUS_ORDER}
        open_count = status_counts[OTRSStateMapping.BusinessStatus.OPEN]
        pending_count = status_counts[OTRSStateMapping.BusinessStatus.PENDING]
        scheduled_count = status_counts[OTRSStateMapping.BusinessStatus.SCHEDULED]

        links_affected = count_affected_links(business_open, links)

        calls = period_tickets.count()
        top_projects = DashboardAnalyticsService._project_rows(period_tickets, links, calls)
        top_reasons = DashboardAnalyticsService._reason_rows(period_tickets, calls, start, end)
        recurrence_base = OTRSTicketConfigItem.objects.filter(
            is_present_in_source=True,
            ticket__queue_name=TicketQueryService.QUEUE_NAME,
            ticket__is_present_in_queue=True,
            ticket__source_created_at__gte=start_at,
            ticket__source_created_at__lt=end_at,
            cmdb_link__in=links,
        )
        recurrence_total = recurrence_base.values("cmdb_link_id").annotate(
            ticket_count=Count("ticket_id", distinct=True),
        ).filter(ticket_count__gte=2).count()

        calls_by_month = {
            row["month"]: row["total"]
            for row in period_tickets.annotate(month=_month_case("source_created_at", buckets))
            .exclude(month__isnull=True).values("month").annotate(total=Count("pk"))
        }
        recurring_by_month = {}
        recurring_month_links = (
            recurrence_base.annotate(month=_month_case("ticket__source_created_at", buckets))
            .exclude(month__isnull=True)
            .values("month", "cmdb_link_id")
            .annotate(ticket_count=Count("ticket_id", distinct=True))
            .filter(ticket_count__gte=2)
        )
        for row in recurring_month_links:
            key = row["month"]
            recurring_by_month[key] = recurring_by_month.get(key, 0) + 1

        monthly = []
        for month_key, _month_start, _month_end in buckets:
            year, month = (int(part) for part in month_key.split("-"))
            month_label = AnalyticsQueryService._month_label(month_key).split("/")[0]
            monthly.append({
                "month": month_label,
                "label": f"{month_label}/{str(year)[-2:]}",
                "calls": calls_by_month.get(month_key, 0),
                "repeat": recurring_by_month.get(month_key, 0),
            })

        quality_pulse = None
        if user.has_perm("qualidade.view_quality"):
            from aplicativos.qualidade.services.dashboard import get_operational_pulse

            quality_pulse = get_operational_pulse(
                user, scope_links=links, base_tickets=tickets, now=now,
            )

        return {
            "total_links": total_links,
            "client_distribution": client_distribution,
            "state_count": len(state_rows),
            "top_states": top_states,
            "top_providers": top_providers,
            "calls": calls,
            "repeat": recurrence_total,
            "business_open": open_count + pending_count + scheduled_count,
            "open_count": open_count,
            "pending_count": pending_count,
            "scheduled_count": scheduled_count,
            "resolved_count": status_counts[OTRSStateMapping.BusinessStatus.RESOLVED],
            "closed_count": 0,
            "canceled_count": status_counts[OTRSStateMapping.BusinessStatus.CANCELED],
            "unknown_count": status_counts[OTRSStateMapping.BusinessStatus.UNKNOWN],
            "links_affected": links_affected,
            "monthly": monthly,
            "aging": aging,
            "quality_pulse": quality_pulse,
            "top_projects": top_projects,
            "top_reasons": top_reasons,
        }
