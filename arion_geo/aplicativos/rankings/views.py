import calendar
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
from .services import RankingService

SORT_OPTIONS = (
    ("links_active", "Mais links na CMDB"),
    ("tickets_per_100_active_links", "Chamados / 100 links"),
    ("risk_score", "Indicador operacional"),
    ("impact_noc_score", "Maior impacto no NOC"),
    ("tickets", "Mais chamados"),
    ("affected_rate", "Maior % da base afetada"),
    ("recurrent_links", "Mais reincidentes"),
    ("tickets_over_24h", "Mais chamados >24h"),
    ("backlog_current", "Maior backlog"),
    ("avg_resolution_hours", "Maior tempo médio"),
)
SORT_KEYS = {key for key, _ in SORT_OPTIONS}
PAGE_SIZES = (25, 50, 100)
TOP_OPTIONS = ("10", "20", "30", "50", "all")
MIN_LINK_OPTIONS = ("0", "10", "30", "50", "100")


def _read_filters(request):
    today = timezone.localdate()
    month = request.GET.get("month", "").strip()
    try:
        selected_month = PeriodService.parse_month(month) if month else today.replace(day=1)
    except ValueError:
        selected_month = today.replace(day=1)
    month = selected_month.strftime("%Y-%m")
    view = request.GET.get("view", "month").strip()
    if view not in {"month", "3months", "6months", "12months", "custom"}:
        view = "month"

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
    default_sort = "links_active" if ranking_view == "links" else "risk_score"
    sort = request.GET.get("sort", default_sort)
    if sort not in SORT_KEYS:
        sort = default_sort
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
    return {
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
        "q": request.GET.get("q", "").strip()[:100],
        "sort": sort, "page_size": page_size, "top": top, "min_links": min_links,
    }


def _url(filters, **updates):
    values = {key: value for key, value in filters.items() if value not in ("", None, False)}
    values.update(updates)
    if not values.get("links_all"):
        values.pop("links_all", None)
    if "links_all" in values:
        values["links"] = "all"
        values.pop("links_all", None)
    values.pop("page_size", None)
    return "?" + urlencode(values)


def _build(request, filters, *, detail_provider_id=None, detail_ticket_page=1, detail_ticket_page_size=10):
    return RankingService.build(
        request.user,
        date_from=date.fromisoformat(filters["date_from"]),
        date_to=date.fromisoformat(filters["date_to"]),
        filters=filters,
        detail_provider_id=detail_provider_id,
        detail_ticket_page=detail_ticket_page,
        detail_ticket_page_size=detail_ticket_page_size,
    )


@login_required
@access_required("view_rankings")
def index(request):
    filters = _read_filters(request)
    data = _build(request, filters)
    all_providers = list(data["providers"])
    minimum_links = int(filters["min_links"])
    risk_providers = [row for row in all_providers if row["risk_eligible"] and row["links_active"] >= minimum_links]
    best_providers = [row for row in all_providers if row["tickets"] >= 1 and row["links_active"] >= minimum_links]
    stable_providers = [row for row in all_providers if row["no_occurrence"] and row["links_active"] >= minimum_links]
    risk_providers.sort(key=lambda row: (
        -float(row.get(filters["sort"]) or 0), -row["risk_score"], -row["impact_noc_score"],
        -row["tickets"], -row["links_affected"], row["name"].casefold(),
    ))
    best_providers.sort(key=lambda row: (
        row["risk_score"], -row["links_total"], -row["eligible_tickets"], row["name"].casefold(),
    ))
    stable_providers.sort(key=lambda row: (-row["links_active"], -row["links_total"], row["name"].casefold()))
    if filters["ranking_view"] == "links":
        providers = [row for row in all_providers if row["links_active"] > 0 and row["links_active"] >= minimum_links]
        providers.sort(key=lambda row: (
            -float(row.get(filters["sort"]) or 0), -row["links_active"], -row["tickets"], row["name"].casefold(),
        ))
        top_providers = []
        page_size = 25 if filters["top"] == "all" else int(filters["top"])
    elif filters["ranking_view"] == "best":
        providers = best_providers
        top_providers = [row for row in providers if row["best_eligible"]][:3]
        page_size = filters["page_size"]
    elif filters["ranking_view"] == "stable":
        providers = stable_providers
        top_providers = providers[:10]
        page_size = filters["page_size"]
    else:
        providers = risk_providers
        top_providers = providers[:3]
        page_size = filters["page_size"]
    for position, item in enumerate(providers, 1):
        item["ranking_position"] = position
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
    return render(request, "rankings/index.html", {
        **data,
        "providers": providers,
        "page_obj": page_obj,
        "top_providers": top_providers,
        "best_eligible_count": sum(row["best_eligible"] for row in best_providers),
        "stable_total": len(stable_providers),
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
        "ranking_risk_url": _url(filters, ranking_view="risk", sort="risk_score", page=1),
        "ranking_best_url": _url(filters, ranking_view="best", sort="risk_score", page=1),
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
    data = _build(request, filters)
    all_providers = list(data["providers"])
    if filters["ranking_view"] == "links":
        providers = [row for row in all_providers if row["links_active"] > 0 and row["links_active"] >= int(filters["min_links"])]
        providers.sort(key=lambda row: (
            -float(row.get(filters["sort"]) or 0), -row["links_active"], -row["tickets"], row["name"].casefold(),
        ))
        podium = providers[:20]
    elif filters["ranking_view"] == "best":
        providers = [row for row in all_providers if row["tickets"] >= 1]
        providers.sort(key=lambda row: (row["risk_score"], -row["links_total"], -row["eligible_tickets"], row["name"].casefold()))
        podium = [row for row in providers if row["best_eligible"]][:3]
    elif filters["ranking_view"] == "stable":
        providers = [row for row in all_providers if row["no_occurrence"]]
        providers.sort(key=lambda row: (-row["links_total"], row["name"].casefold()))
        podium = providers[:10]
    else:
        providers = [row for row in all_providers if row["risk_eligible"]]
        providers.sort(key=lambda row: (
            -float(row.get(filters["sort"]) or 0), -row["risk_score"], -row["impact_noc_score"],
            -row["tickets"], -row["links_affected"], row["name"].casefold(),
        ))
        podium = providers[:3]
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
