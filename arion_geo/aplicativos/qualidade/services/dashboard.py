from datetime import date, datetime, time as datetime_time, timedelta
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror
from aplicativos.integracoes.services.project_names import (
    canonical_project_name,
    display_project_name,
    project_alias_groups,
)
from aplicativos.integracoes.services.semantics import (
    clean_link_designation,
    link_designation,
    provider_filter_order_queryset,
    provider_filter_queryset,
    provider_name_options,
    provider_name_options_for_orders,
    provider_values_for_links,
)

from ..models import (
    QUALITY_DEMO_MARKER,
    QualityAuditEvent,
    QualityMonitoringCycle,
    QualityOrder,
)
from .constants import QUALITY_MONITORING_END_WARNING_DAYS, QUALITY_PRIORITY_PERIOD_OPTIONS
from .data_scope import QualityDataScopeService
from .priority_links import ACTIVE_ORDER_STATUSES, business_priority, build_priority_link_analysis
from .recurrence import build_quality_recurrence_analysis

PERIOD_OPTIONS = (
    ("7", "7 dias (Semana)"),
    ("30", "30 dias"),
    ("60", "60 dias"),
    ("90", "90 dias"),
    ("month", "Este mês"),
    ("previous_month", "Mês anterior"),
    ("180", "180 dias"),
    ("365", "365 dias"),
)

_MONTHS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
_ATTENTION_ORDER = {
    "OQ crítica": 0,
    "OQ vencida": 1,
    "RREIP reincidiu": 2,
    "OQ sem responsável": 3,
    "Monitoramento terminando": 4,
    "Link estratégico reincidente sem OQ": 5,
}


def get_operational_pulse(user, *, scope_links=None, base_tickets=None, now=None):
    """Return the current, scoped Quality indicators for operational summaries."""
    now = now or timezone.now()
    scoped_orders = QualityDataScopeService.orders_for(user)
    scope_links = scope_links if scope_links is not None else DataScopeService.filter_queryset(
        user, CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    )
    recurrence = build_quality_recurrence_analysis(
        user, scope_links=scope_links, scoped_orders=scoped_orders,
        base_tickets=base_tickets, now=now,
    )
    active_links = stabilized_links = 0
    for summary in recurrence["links"].values():
        active_links += bool(summary["is_active_recurrence"])
        stabilized_links += bool(summary["is_stabilized"])
    quality_counts = scoped_orders.order_by().aggregate(
        open_orders=Count("pk", filter=Q(status__in=ACTIVE_ORDER_STATUSES), distinct=True),
        monitoring=Count(
            "monitoring_cycles__pk",
            filter=Q(monitoring_cycles__result=QualityMonitoringCycle.Result.IN_PROGRESS),
            distinct=True,
        ),
    )
    return {
        "active_links": active_links,
        "stabilized_links": stabilized_links,
        "open_orders": quality_counts["open_orders"],
        "monitoring": quality_counts["monitoring"],
    }


def _month_start(value):
    return date(value.year, value.month, 1)


def _shift_month(value, offset):
    index = value.year * 12 + value.month - 1 + offset
    year, month_index = divmod(index, 12)
    return date(year, month_index + 1, 1)


def _month_label(value):
    return f"{_MONTHS[value.month - 1]} {value.year}"


def _aware_midnight(value):
    return timezone.make_aware(datetime.combine(value, datetime_time.min), timezone.get_current_timezone())


def _period_bounds(period, now):
    """Resolve a management window without changing the 30-day episode rule."""
    local_today = timezone.localtime(now).date()
    if period == "month":
        start_date = _month_start(local_today)
        end_date = local_today
        end_exclusive = now + timedelta(microseconds=1)
    elif period == "previous_month":
        end_exclusive = _aware_midnight(_month_start(local_today))
        start_date = _shift_month(_month_start(local_today), -1)
        end_date = end_exclusive.date() - timedelta(days=1)
    else:
        days = int(period)
        start_date = local_today - timedelta(days=days - 1)
        end_date = local_today
        end_exclusive = now + timedelta(microseconds=1)
    return _aware_midnight(start_date), end_exclusive, start_date, end_date


def _bucket_key(value, granularity):
    if granularity == "day":
        return value
    if granularity == "week":
        return value - timedelta(days=value.weekday())
    return _month_start(value)


def _allowed_granularities(period_days):
    if period_days <= 7:
        return ("day", "week")
    if period_days <= 120:
        return ("day", "week", "month")
    if period_days <= 180:
        return ("week", "month")
    return ("month",)


def _resolve_granularity(period_days, requested):
    allowed = _allowed_granularities(period_days)
    automatic = "day" if period_days <= 31 else "week" if period_days <= 120 else "month"
    selected = str(requested or "auto").strip().lower()
    if selected != "auto" and selected not in allowed:
        selected = "auto"
    return selected, automatic if selected == "auto" else selected, allowed


def _compact_week_label(start_date, end_date):
    start_month = _MONTHS[start_date.month - 1]
    end_month = _MONTHS[end_date.month - 1]
    if start_date.year == end_date.year and start_date.month == end_date.month:
        return f"{start_date.day:02d}–{end_date.day:02d} {end_month}"
    if start_date.year == end_date.year:
        return f"{start_date.day:02d} {start_month}–{end_date.day:02d} {end_month}"
    return f"{start_date.day:02d} {start_month} {start_date.year}–{end_date.day:02d} {end_month} {end_date.year}"


def _make_buckets(start_date, end_date, granularity):
    cursor = _bucket_key(start_date, granularity)
    last = _bucket_key(end_date, granularity)
    buckets = []
    while cursor <= last:
        if granularity == "day":
            label = f"{cursor.day:02d}/{cursor.month:02d}"
            tooltip = f"{cursor:%d/%m/%Y}"
            next_cursor = cursor + timedelta(days=1)
        elif granularity == "week":
            interval_start = max(cursor, start_date)
            interval_end = min(cursor + timedelta(days=6), end_date)
            label = _compact_week_label(interval_start, interval_end)
            tooltip = f"{interval_start:%d/%m/%Y} – {interval_end:%d/%m/%Y}"
            next_cursor = cursor + timedelta(days=7)
        else:
            label = _month_label(cursor)
            tooltip = _month_label(cursor)
            next_cursor = _shift_month(cursor, 1)
        buckets.append({"key": cursor, "label": label, "tooltip": tooltip})
        cursor = next_cursor
    return buckets


def _priority_links_url(selected, overrides=None):
    params = {}
    supported_periods = {value for value, _label in QUALITY_PRIORITY_PERIOD_OPTIONS}
    if selected["period"] in supported_periods:
        params["period"] = selected["period"]
    for dashboard_key, links_key in (("cliente", "client"), ("projeto", "project"), ("isp", "isp"), ("uf", "uf")):
        value = selected.get(dashboard_key)
        if value:
            params[links_key] = value
    params.update({key: value for key, value in (overrides or {}).items() if value})
    url = reverse("qualidade:links_prioritarios")
    return f"{url}?{urlencode(params)}" if params else url


def _project_options(raw_names):
    return sorted({display_project_name(value) for value in raw_names if value}, key=str.casefold)


def _attention_item(order, kind, detail, target_url):
    has_live_link = bool(order.link_id and order.link.is_present_in_source)
    designation = link_designation(order.link) if has_live_link else clean_link_designation(order.designation_snapshot)
    return {
        "code": order.code,
        "kind": kind,
        "label": designation,
        "detail": detail,
        "url": target_url,
        "is_demo": order.is_demo,
    }


def _completion_dates(selected_orders, start_at, end_exclusive):
    """Return one real completion timestamp per OQ from its event or stored timestamp."""
    order_ids = selected_orders.order_by().values("pk")
    events = QualityAuditEvent.objects.filter(
        quality_order_id__in=order_ids,
        created_at__gte=start_at,
        created_at__lt=end_exclusive,
        event_type__in=(QualityAuditEvent.EventType.ORDER_CLOSED, QualityAuditEvent.EventType.STATUS_CHANGED),
    ).values("quality_order_id", "event_type", "metadata", "created_at")
    completed = {}
    for event in events.iterator(chunk_size=500):
        metadata = event["metadata"] if isinstance(event["metadata"], dict) else {}
        is_conclusion = (
            event["event_type"] == QualityAuditEvent.EventType.ORDER_CLOSED
            or metadata.get("status") == QualityOrder.Status.CONCLUIDA
        )
        if not is_conclusion:
            continue
        order_id = event["quality_order_id"]
        completed[order_id] = max(completed.get(order_id, event["created_at"]), event["created_at"])

    stored_completions = selected_orders.filter(
        completed_at__gte=start_at,
        completed_at__lt=end_exclusive,
    ).values_list("pk", "completed_at")
    for order_id, completed_at in stored_completions.iterator(chunk_size=500):
        completed.setdefault(order_id, completed_at)
    return completed


def _display_percent(success, total):
    if not total:
        return ""
    return f"{(success / total) * 100:.1f}".replace(".", ",")


def get_dashboard_data(user, requested_filters=None):
    requested_filters = requested_filters or {}
    now = timezone.now()
    scoped_orders = QualityDataScopeService.orders_for(user).select_related("link", "link__operator", "owner")
    # The management period controls dashboard aggregation only. Recurrence remains
    # based on the complete, scoped mirror sequence and its fixed 30-day episodes.
    priority_analysis = build_priority_link_analysis(user, "60")
    scope_links = priority_analysis["scope_links"]

    raw_projects = list(scope_links.values_list("business_project", flat=True))
    raw_projects.extend(scope_links.values_list("legacy_project", flat=True))
    raw_projects.extend(scoped_orders.exclude(project_snapshot="").values_list("project_snapshot", flat=True))
    project_groups = project_alias_groups(raw_projects)
    project_options = _project_options(raw_projects)
    client_options = sorted(
        set(scope_links.exclude(contracting_client="").values_list("contracting_client", flat=True))
        | set(scoped_orders.exclude(client_snapshot="").values_list("client_snapshot", flat=True)),
        key=str.casefold,
    )
    isp_options = sorted(
        set(provider_name_options(scope_links)) | set(provider_name_options_for_orders(scoped_orders)),
        key=str.casefold,
    )
    uf_options = sorted(
        set(scope_links.exclude(state="").values_list("state", flat=True))
        | set(scoped_orders.exclude(uf_snapshot="").values_list("uf_snapshot", flat=True)),
        key=str.casefold,
    )
    filter_options = {
        "cliente": client_options,
        "projeto": project_options,
        "isp": isp_options,
        "uf": uf_options,
    }

    selected = {
        "period": str(requested_filters.get("period", "60")),
        "cliente": str(requested_filters.get("cliente", "")).strip(),
        "projeto": str(requested_filters.get("projeto", "")).strip(),
        "isp": str(requested_filters.get("isp", "")).strip(),
        "uf": str(requested_filters.get("uf", "")).strip(),
        "concentration_by": str(requested_filters.get("concentration_by", "project")).strip(),
        "group_by": str(requested_filters.get("group_by", "auto")).strip().lower(),
    }
    if selected["period"] not in {value for value, _label in PERIOD_OPTIONS}:
        selected["period"] = "60"
    if selected["concentration_by"] not in {"project", "isp"}:
        selected["concentration_by"] = "project"
    if selected["cliente"] not in client_options:
        selected["cliente"] = ""
    project_key = canonical_project_name(selected["projeto"])
    valid_project_keys = {canonical_project_name(value) for value in project_options}
    if project_key not in valid_project_keys:
        selected["projeto"] = ""
        project_key = ""
    else:
        selected["projeto"] = display_project_name(project_key)
    if selected["isp"].isdigit() or selected["isp"] not in isp_options:
        selected["isp"] = ""
    if selected["uf"] not in uf_options:
        selected["uf"] = ""

    selected_links = scope_links
    selected_orders = scoped_orders
    if selected["cliente"]:
        selected_links = selected_links.filter(contracting_client__iexact=selected["cliente"])
        selected_orders = selected_orders.filter(
            Q(client_snapshot__iexact=selected["cliente"])
            | Q(link__contracting_client__iexact=selected["cliente"]),
        )
    if project_key:
        aliases = project_groups.get(project_key, ())
        selected_links = selected_links.filter(
            Q(business_project__in=aliases) | Q(legacy_project__in=aliases),
        )
        selected_orders = selected_orders.filter(
            Q(project_snapshot__in=aliases)
            | Q(link__business_project__in=aliases)
            | Q(link__legacy_project__in=aliases),
        )
    if selected["isp"]:
        selected_links = provider_filter_queryset(selected_links, selected["isp"])
        selected_orders = provider_filter_order_queryset(selected_orders, selected["isp"])
    if selected["uf"]:
        selected_links = selected_links.filter(state__iexact=selected["uf"])
        selected_orders = selected_orders.filter(
            Q(uf_snapshot__iexact=selected["uf"]) | Q(link__state__iexact=selected["uf"]),
        )

    start_at, end_exclusive, start_date, end_date = _period_bounds(selected["period"], now)
    period_days = (end_date - start_date).days + 1
    selected["group_by"], granularity, allowed_granularities = _resolve_granularity(
        period_days, selected["group_by"],
    )
    granularity_labels = {"auto": "Automático", "day": "Dia", "week": "Semana", "month": "Mês"}
    granularity_options = [("auto", granularity_labels["auto"])] + [
        (value, granularity_labels[value]) for value in allowed_granularities
    ]
    buckets = _make_buckets(start_date, end_date, granularity)
    bucket_by_key = {bucket["key"]: index for index, bucket in enumerate(buckets)}
    series = {
        "recurrences": [0] * len(buckets),
        "stabilized": [0] * len(buckets),
        "completed": [0] * len(buckets),
        "post_treatment": [0] * len(buckets),
        "opened": [0] * len(buckets),
    }

    active_orders = selected_orders.filter(status__in=ACTIVE_ORDER_STATUSES)
    order_ids = selected_orders.order_by().values("pk")
    cycles = QualityMonitoringCycle.objects.filter(quality_order_id__in=order_ids)

    opened_orders = selected_orders.filter(created_at__gte=start_at, created_at__lt=end_exclusive)
    kpis = {
        "new_recurrences": 0,
        "active_links": 0,
        "stabilized_links": 0,
        "reopened_after_stability": 0,
        "open_orders": active_orders.count(),
        "opened": opened_orders.count(),
        "completed": 0,
        "monitoring": cycles.filter(result=QualityMonitoringCycle.Result.IN_PROGRESS).count(),
        "rreip_success": 0,
        "post_treatment_recurrence": 0,
        "critical": active_orders.filter(priority=QualityOrder.Priority.CRITICA).count(),
        "overdue": active_orders.filter(treatment_deadline__lt=now).count(),
    }

    def add_to_series(name, occurred_at):
        occurred_date = timezone.localtime(occurred_at).date()
        index = bucket_by_key.get(_bucket_key(occurred_date, granularity))
        if index is not None and start_date <= occurred_date <= end_date:
            series[name][index] += 1

    selected_link_ids = set(selected_links.values_list("source_config_item_id", flat=True))
    stabilized_by_link = {}
    stabilized_event_links = set()
    for config_item_id in selected_link_ids:
        summary = priority_analysis["recurrence"]["links"].get(config_item_id, {})
        if summary.get("is_active_recurrence"):
            kpis["active_links"] += 1
        current_episode = summary.get("current_episode")
        if summary.get("is_stabilized") and current_episode:
            stabilized_by_link[config_item_id] = summary
        for episode in summary.get("episodes", ()):
            if episode.recurrence_count <= 0 or not episode.stabilized_at:
                continue
            if start_at <= episode.stabilized_at < end_exclusive:
                stabilized_event_links.add(config_item_id)
                add_to_series("stabilized", episode.stabilized_at)
        for item in summary.get("occurrences", ()):
            occurred_at = item["occurred_at"]
            occurred_date = timezone.localtime(occurred_at).date()
            if (
                item.get("is_new_episode_after_stability")
                and start_date <= occurred_date <= end_date
                and occurred_at <= now
            ):
                kpis["reopened_after_stability"] += 1
            if start_date <= occurred_date <= end_date and occurred_at <= now and item.get("is_recurrence"):
                kpis["new_recurrences"] += 1
                add_to_series("recurrences", occurred_at)
    kpis["stabilized_links"] = len(stabilized_event_links)

    for created_at in opened_orders.values_list("created_at", flat=True).iterator(chunk_size=500):
        add_to_series("opened", created_at)

    completion_dates = _completion_dates(selected_orders, start_at, end_exclusive)
    kpis["completed"] = len(completion_dates)
    for completed_at in completion_dates.values():
        add_to_series("completed", completed_at)

    concluded_cycles = cycles.filter(
        ended_at__gte=start_at,
        ended_at__lt=end_exclusive,
        result__in=(QualityMonitoringCycle.Result.SUCCESS, QualityMonitoringCycle.Result.RECURRENCE),
    ).values_list("result", "ended_at")
    for result, ended_at in concluded_cycles.iterator(chunk_size=500):
        if result == QualityMonitoringCycle.Result.SUCCESS:
            kpis["rreip_success"] += 1
        else:
            kpis["post_treatment_recurrence"] += 1
            add_to_series("post_treatment", ended_at)

    completed_monitorings = kpis["rreip_success"] + kpis["post_treatment_recurrence"]
    effectiveness = {
        "has_data": completed_monitorings > 0,
        "success": kpis["rreip_success"],
        "recurrence": kpis["post_treatment_recurrence"],
        "total": completed_monitorings,
        "percentage": _display_percent(kpis["rreip_success"], completed_monitorings),
        "progress": f"{(kpis['rreip_success'] / completed_monitorings) * 100:.1f}" if completed_monitorings else "0",
        "failure_progress": f"{(kpis['post_treatment_recurrence'] / completed_monitorings) * 100:.1f}" if completed_monitorings else "0",
    }

    evolution = {
        "labels": [bucket["label"] for bucket in buckets],
        "tooltip_labels": [bucket["tooltip"] for bucket in buckets],
        "recurrences": series["recurrences"],
        "stabilized": series["stabilized"],
        "completed": series["completed"],
        "post_treatment": series["post_treatment"],
        "granularity": {"day": "Diária", "week": "Semanal", "month": "Mensal"}[granularity],
    }

    status_labels = dict(QualityOrder.Status.choices)
    status_distribution = [
        {"name": status_labels[row["status"]], "value": row["total"]}
        for row in selected_orders.values("status").annotate(total=Count("pk")).order_by("status")
        if row["total"]
    ]

    selected_link_rows = list(selected_links.select_related("operator"))
    selected_semantics = provider_values_for_links(selected_link_rows)
    concentration_counts = {}
    for link in selected_link_rows:
        summary = priority_analysis["recurrence"]["links"].get(link.source_config_item_id, {})
        if selected["concentration_by"] == "project":
            group_name = display_project_name(link.business_project or link.legacy_project) or "Projeto não informado"
        else:
            group_name = selected_semantics[link.pk].provider_name or "Não classificado"
        for item in summary.get("occurrences", ()):
            occurred_at = item["occurred_at"]
            occurred_date = timezone.localtime(occurred_at).date()
            if (
                item.get("is_recurrence")
                and occurred_at <= now
                and start_date <= occurred_date <= end_date
            ):
                concentration_counts[group_name] = concentration_counts.get(group_name, 0) + 1
    concentration_total = sum(concentration_counts.values())
    concentration = sorted(
        ({"name": name, "value": value} for name, value in concentration_counts.items()),
        key=lambda item: (-item["value"], item["name"].casefold()),
    )[:5]
    concentration_max = max((item["value"] for item in concentration), default=0)
    for item in concentration:
        dimension_key = "project" if selected["concentration_by"] == "project" else "isp"
        item["bar_percent"] = round((item["value"] / concentration_max) * 100) if concentration_max else 0
        item["share_percent"] = _display_percent(item["value"], concentration_total)
        item["can_filter"] = item["name"] not in {"Projeto não informado", "Não classificado"}
        item["url"] = _priority_links_url(selected, {dimension_key: item["name"]}) if item["can_filter"] else ""

    candidate_rows = list(
        priority_analysis["links"].filter(source_config_item_id__in=selected_link_ids)
    )
    candidate_semantics = provider_values_for_links(candidate_rows)
    recurring_links = []
    for link in candidate_rows:
        summary = priority_analysis["recurrence"]["links"].get(link.source_config_item_id, {})
        if not summary.get("is_active_recurrence"):
            continue
        provider = candidate_semantics[link.pk]
        project = display_project_name(link.business_project or link.legacy_project)
        recurring_links.append({
            "label": link_designation(link),
            "designation": link_designation(link),
            "code": link.source_config_item_id,
            "url": reverse("qualidade:priority_link_detail", kwargs={"identificador": link.source_config_item_id}),
            "client": link.contracting_client or "Cliente não informado",
            "project": project or "Projeto não informado",
            "provider_name": provider.provider_name,
            "value": summary["recurrence_count"],
            "occurrence_count": summary["current_episode_occurrence_count"],
            "last_occurrence": summary["last_occurrence"],
            "business_priority": business_priority(provider.provider_name, project, link.contracting_client),
        })
    recurring_links.sort(key=lambda item: (
        -item["value"], item["business_priority"],
        -item["last_occurrence"].timestamp(), item["code"],
    ))
    recurring_links = recurring_links[:5]

    stabilized_rows = list(
        selected_links.filter(source_config_item_id__in=stabilized_by_link).select_related("operator")
    )
    stabilized_semantics = provider_values_for_links(stabilized_rows)
    stabilized_links = []
    for link in stabilized_rows:
        summary = stabilized_by_link[link.source_config_item_id]
        provider = stabilized_semantics[link.pk]
        project = display_project_name(link.business_project or link.legacy_project)
        stabilized_links.append({
            "designation": link_designation(link),
            "code": link.source_config_item_id,
            "url": reverse("qualidade:priority_link_detail", kwargs={"identificador": link.source_config_item_id}),
            "client": link.contracting_client or "Cliente não informado",
            "project": project or "Projeto não informado",
            "provider_name": provider.provider_name,
            "value": summary["current_episode_recurrence_count"],
            "occurrence_count": summary["current_episode_occurrence_count"],
            "last_occurrence": summary["last_occurrence"],
            "stabilized_at": summary["current_episode_stabilized_at"],
            "days_since_last": summary["current_episode_days_since_last"],
            "business_priority": business_priority(provider.provider_name, project, link.contracting_client),
        })
    stabilized_links.sort(key=lambda item: (
        -item["stabilized_at"].timestamp(), -item["value"], item["business_priority"], item["code"],
    ))
    stabilized_links = stabilized_links[:5]

    order_url = reverse("qualidade:ordens")
    monitoring_url = reverse("qualidade:monitoramento")
    priority_url = reverse("qualidade:links_prioritarios")
    concentration_detail_url = _priority_links_url(selected)
    dashboard_links = {
        "open": f"{order_url}?tab=active",
        "new_recurrences": _priority_links_url(selected, {"state": "all"}),
        "active_links": _priority_links_url(selected, {"state": "active"}),
        "stabilized_links": "#quality-stabilized-title",
        "opened": order_url,
        "completed": f"{order_url}?status={QualityOrder.Status.CONCLUIDA}",
        "monitoring": f"{monitoring_url}?tab=active",
        "critical": f"{order_url}?tab=critical",
        "overdue": f"{order_url}?tab=overdue",
        "rreip_success": f"{monitoring_url}?result={QualityMonitoringCycle.Result.SUCCESS}",
        "rreip_failed": f"{monitoring_url}?tab=recurrence",
    }

    window_end = now + timedelta(days=QUALITY_MONITORING_END_WARNING_DAYS)
    attention = []
    for order in active_orders.filter(priority=QualityOrder.Priority.CRITICA).select_related("link", "link__operator").order_by("-updated_at")[:3]:
        attention.append(_attention_item(order, "OQ crítica", "Prioridade crítica em aberto", dashboard_links["critical"]))
    for order in active_orders.filter(treatment_deadline__lt=now).select_related("link", "link__operator").order_by("treatment_deadline")[:3]:
        attention.append(_attention_item(order, "OQ vencida", "Prazo de tratamento ultrapassado", dashboard_links["overdue"]))
    failed_cycles = cycles.filter(result=QualityMonitoringCycle.Result.RECURRENCE).select_related(
        "quality_order__link", "quality_order__link__operator",
    ).order_by("-ended_at", "-pk")[:3]
    for cycle in failed_cycles:
        attention.append(_attention_item(
            cycle.quality_order, "RREIP reincidiu", f"Ciclo {cycle.cycle_number} registrou nova ocorrência",
            dashboard_links["rreip_failed"],
        ))
    for order in active_orders.filter(owner__isnull=True).select_related("link", "link__operator").order_by("created_at")[:3]:
        attention.append(_attention_item(order, "OQ sem responsável", "Defina uma pessoa responsável pela tratativa", f"{order_url}?owner=unassigned"))
    ending_cycles = cycles.filter(
        result=QualityMonitoringCycle.Result.IN_PROGRESS,
        expected_end_at__gte=now,
        expected_end_at__lte=window_end,
    ).select_related("quality_order__link", "quality_order__link__operator").order_by("expected_end_at")[:3]
    for cycle in ending_cycles:
        attention.append(_attention_item(
            cycle.quality_order,
            "Monitoramento terminando",
            f"Ciclo {cycle.cycle_number} · {timezone.localtime(cycle.expected_end_at):%d/%m/%Y}",
            f"{monitoring_url}?tab=ending",
        ))
    active_link_ids = set(active_orders.values_list("link_id", flat=True))
    for item in recurring_links:
        if item["business_priority"] < 90:
            link_id = item["code"]
            if link_id and link_id not in active_link_ids:
                attention.append({
                    "code": item["code"], "kind": "Link estratégico reincidente sem OQ",
                    "label": item["designation"], "detail": f"{item['provider_name']} · {item['project']}",
                    "url": item["url"], "is_demo": False,
                })
    attention.sort(key=lambda item: (
        _ATTENTION_ORDER.get(item["kind"], 99),
        item.get("detail", "").casefold(),
        item.get("code", ""),
    ))

    return {
        "kpis": kpis,
        "kpi_urls": dashboard_links,
        "evolution": evolution,
        "status_distribution": status_distribution,
        "effectiveness": effectiveness,
        "concentration": concentration,
        "concentration_total": concentration_total,
        "concentration_detail_url": concentration_detail_url,
        "concentration_by": selected["concentration_by"],
        "recurring_links": recurring_links,
        "stabilized_links": stabilized_links,
        "attention": attention[:8],
        "attention_count": len(attention),
        "attention_summary": {"critical": kpis["critical"], "overdue": kpis["overdue"]},
        "demo_order_count": selected_orders.filter(internal_notes__startswith=QUALITY_DEMO_MARKER).count(),
        "filters": selected,
        "filter_options": filter_options,
        "period_options": PERIOD_OPTIONS,
        "granularity_options": granularity_options,
        "has_evolution": any(
            any(series[name])
            for name in ("recurrences", "stabilized", "completed", "post_treatment")
        ),
        "has_status_distribution": bool(status_distribution),
        "has_recurring_links": bool(recurring_links),
        "has_stabilized_links": bool(stabilized_links),
        "has_stabilized_events": bool(stabilized_event_links),
        "has_concentration": bool(concentration),
        "period_label": dict(PERIOD_OPTIONS)[selected["period"]],
    }
