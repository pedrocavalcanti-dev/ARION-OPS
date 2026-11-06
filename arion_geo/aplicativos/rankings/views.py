import calendar
import math
from datetime import date
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.permissions import access_required
from aplicativos.integracoes.models import CMDBOperatorMirror
from aplicativos.integracoes.services.analytics import LinkQueryService, PeriodService
from .filter_ranges import METRIC_META, build_metric_ranges, format_number
from .services import RankingService, _plain

SORT_OPTIONS = (
    ("links_active", "Links na CMDB"),
    ("tickets", "Chamados no período"),
    ("tickets_per_100_active_links", "Chamados / 100 links"),
    ("backlog_current", "Backlog atual"),
    ("tickets_over_24h", "Resolvidos >24h"),
    ("avg_resolution_hours", "Tempo médio"),
    ("operational_score", "Score operacional"),
    ("impact_noc_score", "Maior impacto no NOC"),
    ("affected_rate", "Maior % da base afetada"),
    ("recurrent_links", "Mais reincidentes"),
)
SORT_KEYS = {key for key, _ in SORT_OPTIONS}
PAGE_SIZES = (25, 50, 100)
TOP_OPTIONS = ("10", "20", "30", "50", "all")
MIN_LINK_OPTIONS = ("0", "10", "30", "50", "100")
# Cross-filters applied (AND) inside the active ranking tab: (param prefix, provider field, label, unit, step).
NUMERIC_FILTERS = (
    ("links", "links_active", "Links na CMDB", "", "1"),
    ("tickets", "tickets", "Chamados no período", "", "1"),
    ("incidence", "tickets_per_100_active_links", "Chamados / 100 links", "", "0.1"),
    ("backlog", "backlog_current", "Backlog atual", "", "1"),
    ("aged", "tickets_over_24h", "Resolvidos >24h", "", "1"),
    ("avg_time", "avg_resolution_hours", "Tempo médio", "h", "0.5"),
    ("risk", "operational_score", "Score operacional", "", "1"),
)
NUMERIC_FILTER_PARAMS = tuple(f"{prefix}_{bound}" for prefix, *_ in NUMERIC_FILTERS for bound in ("min", "max"))
# Default period window when the URL does not carry one (explicit ?view= always wins).
DEFAULT_VIEW = "3months"
DIRECTION_OPTIONS = (("desc", "Maior → menor"), ("asc", "Menor → maior"))
DIRECTION_KEYS = {key for key, _ in DIRECTION_OPTIONS}
SITUATION_OPTIONS = (
    ("all", "Todos"),
    ("with_tickets", "Com chamados"),
    ("without_tickets", "Sem chamados"),
    ("with_backlog", "Com backlog"),
    ("without_backlog", "Sem backlog"),
    ("with_aged", "Com resoluções >24h"),
)
SITUATION_KEYS = {key for key, _ in SITUATION_OPTIONS}


def _parse_bound(raw):
    """Normalise a numeric min/max query value; invalid, negative or non-finite input is ignored."""
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return ""
    try:
        number = float(raw)
    except ValueError:
        return ""
    if not math.isfinite(number) or number < 0:
        return ""
    return f"{number:g}"


def _read_range(params, prefix):
    """Min/max for one metric. `<prefix>_range` (smart select: "76:150", "" or "custom") wins
    over the raw `<prefix>_min/_max` inputs, which stay the canonical URL representation."""
    choice = params.get(f"{prefix}_range")
    if choice == "":
        return "", ""
    if choice and choice != "custom" and ":" in choice:
        low, high = choice.split(":", 1)
        return _parse_bound(low), _parse_bound(high)
    return _parse_bound(params.get(f"{prefix}_min")), _parse_bound(params.get(f"{prefix}_max"))


def _read_filters(request):
    today = timezone.localdate()
    month = request.GET.get("month", "").strip()
    try:
        selected_month = PeriodService.parse_month(month) if month else today.replace(day=1)
    except ValueError:
        selected_month = today.replace(day=1)
    month = selected_month.strftime("%Y-%m")
    view = request.GET.get("view", DEFAULT_VIEW).strip()
    if view not in {"month", "3months", "6months", "12months", "custom"}:
        view = DEFAULT_VIEW

    if view == "custom":
        try:
            start, end = PeriodService.parse(request.GET)
        except ValueError:
            start, end = None, None
        start = start or selected_month
        end = end or today
    else:
        end_of_month = date(selected_month.year, selected_month.month, calendar.monthrange(selected_month.year, selected_month.month)[1])
        end = min(today, end_of_month) if selected_month.year == today.year and selected_month.month == today.month else end_of_month
        start = selected_month
        month_window = {"month": 1, "3months": 3, "6months": 6, "12months": 12}.get(view, 1)
        if month_window > 1:
            # Keep the selected month at the end of each calendar-month window.
            month_index = selected_month.year * 12 + selected_month.month - 1 - (month_window - 1)
            start = date(month_index // 12, month_index % 12 + 1, 1)

    ranking_view = request.GET.get("ranking_view", "links").strip()
    if ranking_view not in {"links", "risk", "best", "stable"}:
        ranking_view = "links"
    default_sort = "links_active" if ranking_view in {"links", "stable"} else "operational_score"
    sort = request.GET.get("sort", default_sort)
    if sort not in SORT_KEYS:
        sort = default_sort
    direction = request.GET.get("direction", "desc").strip().lower()
    if direction not in DIRECTION_KEYS:
        direction = "desc"
    search = request.GET.get("search", request.GET.get("q", "")).strip()[:100]
    situation = request.GET.get("situation", "all").strip().lower()
    if situation not in SITUATION_KEYS:
        situation = "all"
    top = request.GET.get("top", "20")
    if top not in TOP_OPTIONS:
        top = "20"
    min_links = request.GET.get("min_links", "0")
    if min_links not in MIN_LINK_OPTIONS:
        min_links = "0"
    try:
        page_size = int(request.GET.get("page_size", 25))
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in PAGE_SIZES:
        page_size = 25
    numeric = {}
    for prefix, *_ in NUMERIC_FILTERS:
        low, high = _read_range(request.GET, prefix)
        numeric[f"{prefix}_min"], numeric[f"{prefix}_max"] = low, high
    return {
        **numeric,
        "month": month, "view": view,
        "ranking_view": ranking_view,
        "date_from": start.isoformat(), "date_to": end.isoformat(),
        "operator": request.GET.get("provider", request.GET.get("operator", "")).strip(),
        "client": request.GET.get("client", "").strip(),
        "project": request.GET.get("project", "").strip(),
        "uf": request.GET.get("uf", "").strip().upper(),
        "priority": request.GET.get("priority", "").strip(),
        "reason": request.GET.get("reason", "").strip(),
        "status": request.GET.get("status", "").strip().upper(),
        "only_aged": request.GET.get("only_aged") in {"1", "true", "on"},
        "only_recurrent": request.GET.get("only_recurrent") in {"1", "true", "on"},
        "links_all": request.GET.get("links") == "all",
        "q": search,
        "search": search,
        "sort": sort,
        "situation": situation,
        "direction": direction,
        "top_explicit": "top" in request.GET,
        "page_size": page_size,
        "top": top,
        "min_links": min_links,
    }


def _url(filters, **updates):
    values = {key: value for key, value in filters.items() if value not in ("", None, False)}
    values.update(updates)
    values = {key: value for key, value in values.items() if value not in ("", None)}
    if not values.get("links_all"):
        values.pop("links_all", None)
    if "links_all" in values:
        values["links"] = "all"
        values.pop("links_all", None)
    values.pop("page_size", None)
    return "?" + urlencode(values)


def _build(request, filters, *, detail_provider_id=None, detail_ticket_page=1, detail_ticket_page_size=10, include_export=False):
    return RankingService.build(
        request.user,
        date_from=date.fromisoformat(filters["date_from"]),
        date_to=date.fromisoformat(filters["date_to"]),
        filters=filters,
        detail_provider_id=detail_provider_id,
        detail_ticket_page=detail_ticket_page,
        detail_ticket_page_size=detail_ticket_page_size,
        include_export=include_export,
    )


def _tab_base(all_providers, ranking_view, minimum_links):
    """Eligibility of each ranking tab (unchanged semantics)."""
    if ranking_view == "links":
        return [row for row in all_providers if row["links_active"] > 0 and row["links_active"] >= minimum_links]
    if ranking_view == "best":
        return [row for row in all_providers if row["operational_score"] is not None and row["links_active"] >= minimum_links]
    if ranking_view == "stable":
        return [row for row in all_providers if row["no_occurrence"] and row["links_active"] >= minimum_links]
    return [row for row in all_providers if row["operational_score"] is not None and row["links_active"] >= minimum_links]


def _filter_providers(providers, filters):
    """Refine the active tab: search + situation + numeric ranges, all combined with AND."""
    search = (filters.get("search") or filters.get("q") or "").strip()
    if search:
        search_plain = _plain(search)
        providers = [row for row in providers if search_plain in _plain(row["name"])]
    situation = filters.get("situation", "all")
    if situation == "with_tickets":
        providers = [row for row in providers if row["tickets"] > 0]
    elif situation == "without_tickets":
        providers = [row for row in providers if row["tickets"] == 0]
    elif situation == "with_backlog":
        providers = [row for row in providers if row["backlog_current"] > 0]
    elif situation == "without_backlog":
        providers = [row for row in providers if row["backlog_current"] == 0]
    elif situation == "with_aged":
        providers = [row for row in providers if row["tickets_over_24h"] > 0]
    for prefix, field, *_ in NUMERIC_FILTERS:
        low, high = filters.get(f"{prefix}_min", ""), filters.get(f"{prefix}_max", "")
        if low == "" and high == "":
            continue
        low = float(low) if low != "" else None
        high = float(high) if high != "" else None

        def within(row, field=field, low=low, high=high):
            value = row.get(field)
            if value is None:  # no data never satisfies an active numeric filter
                return False
            value = float(value)
            return (low is None or value >= low) and (high is None or value <= high)

        providers = [row for row in providers if within(row)]
    return providers


def _sort_tab(providers, filters):
    """Primary ordering belongs to the tab; filters never change it."""
    view = filters["ranking_view"]
    if view == "links":
        providers.sort(key=lambda row: (-row["links_active"], -row["tickets"], row["name"].casefold()))
    elif view == "best":
        providers.sort(key=lambda row: (-row["operational_score"], -row["links_total"], -row["resolution_tickets"], row["name"].casefold()))
    elif view == "risk":
        providers.sort(key=lambda row: (row["operational_score"], -row["links_total"], -row["resolution_tickets"], row["name"].casefold()))
    elif view == "stable":
        providers.sort(key=lambda row: (-row["links_active"], -row["links_total"], row["name"].casefold()))
    else:
        providers.sort(key=lambda row: (
            -float(row.get(filters["sort"]) or 0), -row["risk_score"], -row["impact_noc_score"],
            -row["tickets"], -row["links_affected"], row["name"].casefold(),
        ))
    if filters.get("direction") == "asc":  # invert the tab's own metric; the metric never changes
        providers.reverse()
    return providers


def _sort_metric_label(filters):
    view = filters["ranking_view"]
    if view in {"links", "stable"}:
        return "Links na CMDB"
    if view in {"best", "risk"}:
        return "Score operacional"
    return dict(SORT_OPTIONS).get(filters["sort"], "Score operacional")


def _tab_providers(all_providers, filters):
    """Pipeline: tab eligibility -> search/situation/ranges -> tab ordering -> recomputed positions."""
    base = _tab_base(all_providers, filters["ranking_view"], int(filters["min_links"]))
    providers = _sort_tab(_filter_providers(base, filters), filters)
    for position, item in enumerate(providers, 1):
        item["ranking_position"] = position
    return providers


def _podium(ranking_view, providers):
    if ranking_view == "best":
        return [row for row in providers if row["best_eligible"]][:3]
    if ranking_view == "stable":
        return providers[:10]
    if ranking_view == "risk":
        return providers[:3]
    return providers[:20]


def _same_bound(option_value, raw):
    if option_value is None or raw == "":
        return option_value is None and raw == ""
    return float(option_value) == float(raw)


def _build_filter_fields(universe, filters):
    """Smart select options per metric, derived from the current (globally filtered) universe."""
    fields = []
    for prefix, field, label, unit, step in NUMERIC_FILTERS:
        options = build_metric_ranges([row.get(field) for row in universe], prefix)
        low, high = filters[f"{prefix}_min"], filters[f"{prefix}_max"]
        selected, matched = "", None
        if low != "" or high != "":
            selected = "custom"
            for option in options:
                if _same_bound(option["min"], low) and _same_bound(option["max"], high):
                    selected, matched = option["value"], option
                    break
        fields.append({
            "prefix": prefix, "label": label, "unit": unit, "step": step, "options": options,
            "range_name": f"{prefix}_range", "min_name": f"{prefix}_min", "max_name": f"{prefix}_max",
            "min_value": low, "max_value": high, "selected": selected, "matched": matched,
        })
    return fields


def _active_table_filters(filters, fields):
    """Chips with friendly names; each removes only its own condition."""
    chips = []
    situation_labels = dict(SITUATION_OPTIONS)
    if filters["situation"] != "all":
        chips.append({"label": situation_labels[filters["situation"]], "remove_url": _url(filters, situation="", page=1)})
    for field in fields:
        low, high, option = field["min_value"], field["max_value"], field["matched"]
        if low == "" and high == "":
            continue
        meta = METRIC_META[field["prefix"]]
        if option:
            text = option["label"]
            if option["detail"]:
                text = f"{text} · {option['detail']}"
            if field["prefix"] in {"avg_time", "risk"}:  # labels that don't name their metric
                text = f"{meta['chip']} · {text}"
        else:
            unit = meta["unit"].strip() if field["prefix"] == "avg_time" else ""
            if low != "" and high != "":
                span = f"{format_number(float(low))}–{format_number(float(high))}{unit}"
            elif low != "":
                span = f"≥ {format_number(float(low))}{unit}"
            else:
                span = f"≤ {format_number(float(high))}{unit}"
            text = f"{meta['chip']} · {span}"
        chips.append({"label": text, "remove_url": _url(filters, **{field["min_name"]: "", field["max_name"]: "", "page": 1})})
    return chips


@login_required
@access_required("view_rankings")
def index(request):
    filters = _read_filters(request)
    data = _build(request, filters)
    all_providers = list(data["providers"])
    scored_providers = [row for row in all_providers if row["operational_score"] is not None]
    attention_providers = [row for row in scored_providers if row["operational_score"] < 40]
    average_operational_score = (
        sum(row["operational_score"] for row in scored_providers) / len(scored_providers)
        if scored_providers else None
    )
    minimum_links = int(filters["min_links"])
    universe = _tab_base(all_providers, filters["ranking_view"], minimum_links)
    filter_fields = _build_filter_fields(universe, filters)
    providers = _tab_providers(all_providers, filters)
    best_base = _tab_base(all_providers, "best", minimum_links)
    stable_total = len(_tab_base(all_providers, "stable", minimum_links))
    top_providers = [] if filters["ranking_view"] == "links" else _podium(filters["ranking_view"], providers)
    if filters["ranking_view"] == "links":
        page_size = max(50, len(providers)) if filters["top"] == "all" else int(filters["top"])
    elif filters["top_explicit"]:
        page_size = max(50, len(providers)) if filters["top"] == "all" else int(filters["top"])
    else:
        page_size = filters["page_size"]
    page_obj = Paginator(providers, page_size).get_page(request.GET.get("page"))

    scope_links = LinkQueryService.base(request.user)
    operators = CMDBOperatorMirror.objects.filter(links__in=scope_links, is_present_in_source=True).distinct().order_by("name")
    # Filter catalogs use canonical identity fields and the authorized link universe.
    clients = list(scope_links.exclude(contracting_client="").values_list("contracting_client", flat=True).distinct().order_by("contracting_client"))
    projects = list(scope_links.exclude(business_project="").values_list("business_project", flat=True).distinct().order_by("business_project"))
    ufs = list(scope_links.exclude(state="").values_list("state", flat=True).distinct().order_by("state"))
    portuguese_months = ("", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro")
    month_labels = []
    for value in data["available_months"]:
        year, month = value.split("-")
        month_labels.append({"value": value, "label": f"{portuguese_months[int(month)]}/{year}"})
    if not any(option["value"] == filters["month"] for option in month_labels):
        year, month = filters["month"].split("-")
        month_labels.insert(0, {"value": filters["month"], "label": f"{portuguese_months[int(month)]}/{year}"})

    page_filters = filters.copy()
    page_filters["links_all"] = False
    sort_options = [{"value": key, "label": label, "url": _url(page_filters, sort=key, page=1), "active": key == filters["sort"]}
                    for key, label in SORT_OPTIONS]

    active_table_filters = _active_table_filters(filters, filter_fields)
    clear_keys = {name: "" for name in NUMERIC_FILTER_PARAMS}
    clear_table_url = _url(filters, situation="", search="", q="", page=1, **clear_keys)
    situation_options = [
        {"value": key, "label": label, "active": key == filters["situation"]}
        for key, label in SITUATION_OPTIONS
    ]
    top_display_options = [
        {"value": key, "label": "Todos" if key == "all" else f"Top {key}", "active": key == filters["top"]}
        for key in ("10", "20", "50", "all")
    ]
    direction_labels = dict(DIRECTION_OPTIONS)
    if filters["ranking_view"] == "risk":
        direction_labels = {"desc": "Pior → melhor", "asc": "Melhor → pior"}
    return render(request, "rankings/index.html", {
        **data,
        "providers": providers,
        "situation_options": situation_options,
        "top_display_options": top_display_options,
        "active_table_filters": active_table_filters,
        "clear_table_url": clear_table_url,
        "filter_fields": filter_fields,
        "direction_urls": {key: _url(filters, direction=key, page=1) for key, _ in DIRECTION_OPTIONS},
        "sort_metric_label": _sort_metric_label(filters),
        "direction_label": direction_labels[filters["direction"]],
        "direction_labels": direction_labels,
        "page_obj": page_obj,
        "top_providers": top_providers,
        "best_eligible_count": sum(row["best_eligible"] for row in best_base),
        "scored_provider_count": len(scored_providers),
        "attention_provider_count": len(attention_providers),
        "average_operational_score": average_operational_score,
        "average_operational_score_display": f"{average_operational_score:.1f}" if average_operational_score is not None else "—",
        "stable_total": stable_total,
        "ranked_links": data["ranked_links"],
        "filters": filters,
        "sort_options": sort_options,
        "operators_filter": operators,
        "operators": [{"name": operator.name} for operator in operators],
        "clients": clients,
        "projects": projects,
        "ufs": ufs,
        "month_options": month_labels,
        "page_sizes": PAGE_SIZES,
        "top_options": TOP_OPTIONS,
        "min_link_options": MIN_LINK_OPTIONS,
        "provider_detail_url_template": reverse("rankings:provider-detail", args=[0]),
        "base_url": reverse("base_links:index"),
        "cockpit_url": reverse("cockpit"),
        "export_pdf_url": reverse("rankings:export", args=["pdf"]) + _url(filters),
        "export_xlsx_url": reverse("rankings:export", args=["xlsx"]) + _url(filters),
        "ranking_risk_url": _url(filters, ranking_view="risk", sort="operational_score", direction="desc", page=1),
        "ranking_best_url": _url(filters, ranking_view="best", sort="operational_score", direction="desc", page=1),
        "ranking_stable_url": _url(filters, ranking_view="stable", sort="links_active", page=1),
        "ranking_links_url": _url(filters, ranking_view="links", sort="links_active", page=1),
        "full_links_url": _url(filters, links="all"),
        "previous_page_url": _url(filters, page=page_obj.previous_page_number()) if page_obj.has_previous() else "",
        "next_page_url": _url(filters, page=page_obj.next_page_number()) if page_obj.has_next() else "",
        # Compatibility aliases used by existing focused tests and integrations.
        "total_links": data["links_total"],
        "total_tickets": data["tickets_valid"],
        "open_tickets": data["backlog_current"],
        "operator_count": data["provider_count"],
        "affected_links": data["links_affected"],
    })


@login_required
@access_required("view_rankings")
def provider_detail(request, provider_id):
    filters = _read_filters(request)
    data = _build(request, filters, detail_provider_id=provider_id,
                  detail_ticket_page=request.GET.get("ticket_page", 1),
                  detail_ticket_page_size=request.GET.get("ticket_page_size", 10))
    item = data.get("detail")
    if item is None:
        return JsonResponse({"error": "Provedor não encontrado no escopo atual."}, status=404)
    return JsonResponse(item)


@login_required
@access_required("view_rankings")
def export(request, file_format):
    if file_format not in {"pdf", "xlsx"}:
        return JsonResponse({"error": "Formato de exportacao invalido."}, status=404)
    from .exports import build_pdf, build_xlsx

    filters = _read_filters(request)
    filters["links_all"] = True
    data = _build(request, filters, include_export=True)
    all_providers = list(data["providers"])
    providers = _tab_providers(all_providers, filters)
    podium = _podium(filters["ranking_view"], providers)
    generated_at = timezone.localtime()
    generated_by = request.user.get_full_name() or request.user.get_username()
    if file_format == "pdf":
        content = build_pdf(data, filters, providers, podium, generated_at, generated_by)
        content_type = "application/pdf"
    else:
        content = build_xlsx(data, filters, providers, podium, generated_at, generated_by)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="arion-ranking-noc-atacado-{generated_at:%Y%m%d}.{file_format}"'
    AuditService.record(
        request=request, category="EXPORT", action=f"EXPORT_RANKINGS_{file_format.upper()}", module="Rankings",
        description=f"Exportacao do Ranking {file_format.upper()}", result="SUCCESS",
        metadata={
            "format": file_format, "period_start": filters["date_from"], "period_end": filters["date_to"],
            "view": filters["view"], "ranking_view": filters["ranking_view"],
            "provider_count": len(providers), "link_count": len(data["ranked_links_all"]),
            "ticket_rows": len(data["ticket_export_rows"]),
        },
    )
    return response
