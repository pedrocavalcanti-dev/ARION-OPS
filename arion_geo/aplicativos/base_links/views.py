import csv
from collections import Counter, defaultdict
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, CharField, Count, F, Q, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from urllib.parse import urlencode
from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.configuracoes.permissions import access_required

from aplicativos.integracoes.models import (CMDBLinkMirror, CMDBOperatorMirror,
    OTRSTicketConfigItem, OTRSStateMapping)
from aplicativos.integracoes.services.analytics import (
    BACKLOG_STATUSES, OPERATIONAL_RESOLUTION_FIELD, LinkQueryService,
    TicketQueryService, operational_resolution_at,
    operational_resolution_expression,
)
from aplicativos.integracoes.services.otrs_status import (
    OPERATIONAL_VALID_STATUSES, RESOLVED_STATUS,
    business_status_label,
    business_status_query, business_statuses_query, normalize_business_status,
)
from aplicativos.integracoes.services.semantics import LinkSemantics, UNCLASSIFIED
from .search import matching_link_queryset, normalize_search_text


def _safe_csv_value(value):
    """Keep external CMDB values inert when opened in a spreadsheet."""
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text



@login_required
@access_required("view_base")
def index(request):
    scoped = LinkQueryService.base(request.user)
    view = "providers" if request.GET.get("view") == "providers" else "links"
    if view == "providers":
        return _providers_view(request, scoped)
    filters = _read_filters(request)
    links = _filter_links(scoped, filters).select_related("operator")
    sort_options = {"designation": "display_designation", "uf": "state", "provider": "operator__name", "deployment": "deployment_state"}
    sort = request.GET.get("sort", "designation")
    links = links.annotate(display_designation=Case(
        When(designation__gt="", then=F("designation")),
        When(name__gt="", then=F("name")),
        default=F("source_number"), output_field=CharField(),
    )).order_by(sort_options.get(sort, "display_designation"), "source_config_item_id")
    try:
        page_size = int(request.GET.get("page_size", "50"))
    except (TypeError, ValueError):
        page_size = 50
    if page_size not in (25, 50, 100, 200, 300):
        page_size = 50
    page_obj = Paginator(links, page_size).get_page(request.GET.get("page"))

    kpis = scoped.aggregate(
        total=Count("pk"),
        operational=Count("pk", filter=Q(deployment_state__iexact="Operacional")),
        incidents=Count("pk", filter=Q(incident_state__iexact="Incident")),
        canceled=Count("pk", filter=Q(deployment_state__icontains="cancel")),
        providers=Count("operator_id", distinct=True),
    )
    projects = scoped.exclude(business_project="").values_list("business_project", flat=True).distinct().order_by("business_project")
    clients = scoped.exclude(contracting_client="").values_list("contracting_client", flat=True).distinct().order_by("contracting_client")
    provider_rows = list(scoped.exclude(operator__isnull=True).values(
        "operator__source_config_item_id", "operator__name",
    ).distinct())
    provider_rows.sort(key=lambda row: (normalize_search_text(row["operator__name"]), row["operator__source_config_item_id"]))
    providers = [{"config_item_id": row["operator__source_config_item_id"], "name": row["operator__name"]} for row in provider_rows]
    if scoped.filter(operator__isnull=True).exists():
        providers.append({"name": UNCLASSIFIED})
    # Kept for downstream callers that still consume this context key; the page
    # does not present this CMDB provider catalog as contracted operators.
    operators = CMDBOperatorMirror.objects.filter(links__in=scoped).distinct().order_by("name")
    response = render(request, "base_links/index.html", {
        "view": "links", "page_obj": page_obj, "links": page_obj.object_list,
        "filters": filters, "sort": sort, "page_size": page_size,
        "total_links": kpis["total"], "operational_count": kpis["operational"],
        "incident_count": kpis["incidents"], "canceled_count": kpis["canceled"],
        "provider_count": kpis["providers"], "operators": operators,
        "projects": [{"name": name} for name in projects],
        "clients": list(clients),
        "providers": providers,
        "ufs": scoped.exclude(state="").values_list("state", flat=True).distinct().order_by("state"),
        "deployment_states": scoped.exclude(deployment_state="").values_list("deployment_state", flat=True).distinct().order_by("deployment_state"),
        "incident_states": scoped.exclude(incident_state="").values_list("incident_state", flat=True).distinct().order_by("incident_state"),
        "link_types": scoped.exclude(link_type="").values_list("link_type", flat=True).distinct().order_by("link_type"),
    })
    return response


def _providers_view(request, scoped):
    query = request.GET.get("q", "").strip()[:160]
    links = scoped.filter(operator__isnull=False)
    rows = CMDBOperatorMirror.objects.filter(
        is_present_in_source=True, links__in=links, links__is_present_in_source=True,
    ).annotate(link_count=Count("links", filter=Q(links__in=links, links__is_present_in_source=True), distinct=True))
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(phone__icontains=query) |
            Q(whatsapp__icontains=query) | Q(cnpj__icontains=query) |
            Q(source_config_item_id__icontains=query))
    rows = rows.order_by("name", "pk")
    count = rows.count()
    page_obj = Paginator(rows, 50).get_page(request.GET.get("page"))
    provider_id = request.GET.get("provider_id", "")
    return render(request, "base_links/index.html", {
        "view": "providers", "providers_page": page_obj, "provider_count": count,
        "query": query, "requested_provider_id": provider_id,
    })


def _read_filters(request):
    return {
        "q": request.GET.get("q", "").strip(), "projeto": request.GET.get("projeto", ""),
        "cliente": request.GET.get("cliente", ""),
        "config_item_id": request.GET.get("config_item_id", "").strip()[:80],
        "provider_id": request.GET.get("provider_id", "").strip(),
        "uf": request.GET.get("uf", "").strip(), "provedor": request.GET.get("provedor", "").strip(),
        "deployment_state": request.GET.get("deployment_state", ""),
        "incident_state": request.GET.get("incident_state", ""), "link_type": request.GET.get("link_type", ""),
    }


def _filter_links(queryset, filters):
    # A technical CI lookup is an exact, scoped destination. Ignore any stale
    # dimensions carried by an older link so contradictory filters cannot hide it.
    if filters.get("config_item_id"):
        return queryset.filter(source_config_item_id=filters["config_item_id"])
    if filters["q"]:
        queryset = matching_link_queryset(queryset, filters["q"])
    if filters["cliente"] == "other":
        queryset = queryset.exclude(contracting_client="").exclude(contracting_client__in=("Claro", "Vivo", "Algar"))
    for key, field in (("projeto", "business_project"), ("cliente", "contracting_client"), ("uf", "state"), ("deployment_state", "deployment_state"), ("incident_state", "incident_state"), ("link_type", "link_type")):
        if filters[key] and not (key == "cliente" and filters[key] == "other"):
            queryset = queryset.filter(**{field: filters[key]})
    if filters["provedor"]:
        from aplicativos.integracoes.services.semantics import provider_filter_queryset
        queryset = provider_filter_queryset(queryset, filters["provedor"])
    if filters["provider_id"].isdigit():
        queryset = queryset.filter(operator_id=int(filters["provider_id"]))
    return queryset


@login_required
@access_required("view_base")
def providers(request):
    return _providers_view(request, LinkQueryService.base(request.user))


@login_required
@access_required("export_base")
def export_csv(request):
    if request.GET.get("view") == "providers":
        scoped = LinkQueryService.base(request.user)
        rows = CMDBOperatorMirror.objects.filter(
            is_present_in_source=True, links__in=scoped, links__is_present_in_source=True,
        ).annotate(link_count=Count("links", filter=Q(links__in=scoped, links__is_present_in_source=True), distinct=True)).order_by("name")
        query = request.GET.get("q", "").strip()[:160]
        if query:
            rows = rows.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(whatsapp__icontains=query)
                | Q(cnpj__icontains=query) | Q(source_config_item_id__icontains=query))
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="base-providers-cmdb.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(["Provedor / ISP", "Telefone", "WhatsApp", "Horário de atendimento", "CNPJ", "Links associados", "Implantação", "Incidente"])
        for provider in rows.iterator(chunk_size=300):
            writer.writerow([_safe_csv_value(value) for value in (provider.name, provider.phone, provider.whatsapp, provider.business_hours,
                provider.cnpj, provider.link_count, provider.deployment_state, provider.incident_state)])
        return response
    queryset = _filter_links(LinkQueryService.base(request.user), _read_filters(request)).select_related("operator")
    queryset = queryset.annotate(display_designation=Case(
        When(designation__gt="", then=F("designation")),
        When(name__gt="", then=F("name")),
        default=F("source_number"), output_field=CharField(),
    )).order_by("display_designation", "source_config_item_id")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="base-links-cmdb.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Designacao", "Nome original CMDB", "Cliente", "Projeto", "Conta", "Business ID", "Provedor / ISP", "Cidade", "UF", "Circuito", "Tipo", "Velocidade", "Range IP", "Implantacao", "Incidente"])
    for link in queryset.iterator(chunk_size=500):
        semantics = LinkSemantics.from_link(link)
        writer.writerow([_safe_csv_value(value) for value in (
            link.display_designation or "Nao informado", link.name,
            link.contracting_client or UNCLASSIFIED, link.business_project or UNCLASSIFIED,
            link.business_account or UNCLASSIFIED, link.business_id or "Nao identificado",
            semantics.provider_name, link.city or "Nao informado", link.state or "Nao informado",
            link.circuit or "Não informado", link.link_type or "Não informado", link.speed_raw or "Não informado",
            link.ip_range or "Não informado", link.deployment_state or "Não informado", link.incident_state or "Não informado",
        )])
    return response

@login_required
@access_required("view_base")
@require_GET
def provider_detail(request, pk):
    scoped = LinkQueryService.base(request.user)
    provider = get_object_or_404(CMDBOperatorMirror.objects.filter(
        pk=pk, is_present_in_source=True, links__in=scoped,
    ).distinct())
    links = scoped.filter(operator=provider).order_by("designation", "source_number")
    link_summary = links.aggregate(
        total=Count("pk"), operational=Count("pk", filter=Q(deployment_state__iexact="Operacional")),
        incidents=Count("pk", filter=Q(incident_state__icontains="incident")),
    )
    link_ids = list(links.values_list("pk", flat=True))
    now = timezone.now()
    start = now - timedelta(days=30)
    valid = OPERATIONAL_VALID_STATUSES
    relations = OTRSTicketConfigItem.objects.filter(
        is_present_in_source=True, cmdb_link_id__in=link_ids,
        ticket__in=TicketQueryService.noc_tickets(),
    ).filter(business_statuses_query("ticket__business_status", valid)).select_related("ticket", "cmdb_link")
    recent = relations.filter(ticket__source_created_at__gte=start, ticket__source_created_at__lte=now)
    ticket_ids = recent.values_list("ticket_id", flat=True).distinct()
    from aplicativos.integracoes.models import OTRSTicket
    ticket_qs = OTRSTicket.objects.filter(pk__in=ticket_ids)
    page = Paginator(ticket_qs.order_by("-source_created_at", "-pk"), 10).get_page(request.GET.get("history_page", 1))
    ticket_rows = [{"id": t.pk, "number": t.ticket_number, "title": t.title,
        "created_at": t.source_created_at.isoformat() if t.source_created_at else None,
        "status": business_status_label(t.business_status),
        "responsibility": t.responsibility_normalized}
        for t in page.object_list]
    active = BACKLOG_STATUSES
    active_ids = set(relations.filter(ticket__business_status__in=active).values_list("ticket_id", flat=True).distinct())
    terminal = ticket_qs.annotate(**{
        OPERATIONAL_RESOLUTION_FIELD: operational_resolution_expression(),
    }).filter(business_status_query("business_status", RESOLVED_STATUS), source_created_at__isnull=False).exclude(
            **{f"{OPERATIONAL_RESOLUTION_FIELD}__isnull": True},
        )
    overdue = sum(1 for ticket in OTRSTicket.objects.filter(pk__in=active_ids, source_created_at__isnull=False)
        if now - ticket.source_created_at > timedelta(hours=24))
    overdue += sum(
        1 for ticket in terminal
        if getattr(ticket, OPERATIONAL_RESOLUTION_FIELD) - ticket.source_created_at > timedelta(hours=24)
    )
    counts_by_link = recent.values("cmdb_link_id").annotate(total=Count("ticket_id", distinct=True))
    recurrent = sum(1 for row in counts_by_link if row["total"] >= 2)
    portal_url = provider.portal_url if provider.portal_url.startswith(("https://", "http://")) else ""
    data = {
        "id": provider.pk, "name": provider.name, "config_item_id": provider.source_config_item_id,
        "number": provider.source_number, "phone": provider.phone, "whatsapp": provider.whatsapp,
        "service_hours": provider.business_hours, "opening_email": provider.opening_email,
        "followup_email": provider.followup_email, "portal_url": portal_url,
        "last_sync": provider.source_synced_at.isoformat() if provider.source_synced_at else None,
        "cnpj": provider.cnpj, "category": provider.category,
        "deployment_state": provider.deployment_state, "incident_state": provider.incident_state,
        "link_count": link_summary["total"], "operational_links": link_summary["operational"],
        "incident_links": link_summary["incidents"],
        "tickets_30d": ticket_qs.count(), "backlog": len(active_ids),
        "recurrent_links": recurrent, "over_24h": overdue,
        "tickets": ticket_rows, "history_page": page.number, "history_pages": page.paginator.num_pages,
        "history_total": page.paginator.count,
        "links": [{"id": x.pk, "config_item_id": x.source_config_item_id,
            "designation": x.designation or x.name or x.source_number, "client": x.contracting_client,
            "project": x.business_project, "city": x.city, "state": x.state,
            "speed": x.speed_raw, "deployment_state": x.deployment_state,
            "incident_state": x.incident_state} for x in links[:20]],
        "links_truncated": link_summary["total"] > 20,
        "links_url": f"/base/?view=links&provedor={provider.source_config_item_id}",
        "ranking_url": f"/rankings/?provider={provider.pk}",
    }
    return JsonResponse(data)


@login_required
@access_required("view_base")
@require_GET
def detail(request, pk):
    link = get_object_or_404(LinkQueryService.base(request.user).select_related("operator"), pk=pk)
    operator = link.operator
    semantics = LinkSemantics.from_link(link)
    data = {
        "id": link.pk, "config_item_id": link.source_config_item_id,
        "config_item_number": link.source_number, "version": link.version_id,
        "last_version": link.last_version_id, "definition_id": link.definition_id,
        "synced_at": link.source_synced_at.isoformat() if link.source_synced_at else None,
        "designation_arion": link.designation or link.name or link.source_number,
        "name": link.name, "name_original": link.name, "operadora": link.contracting_client or UNCLASSIFIED,
        "projeto": link.business_project or UNCLASSIFIED,
        "cliente": link.contracting_client, "cliente_raw": link.contracting_client_raw,
        "conta": link.business_account, "business_id": link.business_id,
        "identity_confidence": link.business_identity_confidence,
        "identity_source": "Nome original do CMDB" if link.business_identity_source == "CMDB_NAME_ORIGINAL" else link.business_identity_source,
        "project_legacy": link.legacy_project,
        "classification_status": link.business_identity_confidence, "deployment_state": link.deployment_state,
        "incident_state": link.incident_state, "provider": semantics.provider_name,
        "provider_id": operator.pk if operator else None,
        "provider_cmdb_config_item_id": semantics.provider_config_item_id,
        "provider_cmdb_number": operator.source_number if operator else "",
        "provider_resolution_method": link.operator_resolution_method,
        "isp_raw": link.isp_raw, "speed_raw": link.speed_raw, "link_type": link.link_type,
        "range_ip": link.ip_range, "circuit": link.circuit, "equipment": link.equipment,
        "mac_address": link.mac_address, "operator_designation": link.operator_designation,
        "address": link.address, "city": link.city, "uf": link.state, "client_raw": link.customer_id,
        "contract_number": link.contract_number, "client_identification": link.identification,
        "cnpj_arion": link.cnpj, "business_hours": link.business_hours, "notes": link.isp_notes,
        "changes": [],
    }
    _add_link_history(data, link, request)
    return JsonResponse(data)


def _ticket_duration_hours(ticket, now):
    if not ticket.source_created_at:
        return None
    if normalize_business_status(ticket.business_status) == RESOLVED_STATUS:
        end_at = operational_resolution_at(ticket)
        if not end_at:
            return None
    else:
        end_at = ticket.terminal_state_at or now
    return round((end_at - ticket.source_created_at).total_seconds() / 3600, 1)


def _add_link_history(data, link, request):
    from aplicativos.integracoes.models import OTRSTicket

    period_days = {"30": 30, "90": 90, "365": 365}.get(request.GET.get("period", "30"), 30)
    now = timezone.now()
    start = now - timedelta(days=period_days)
    valid = OPERATIONAL_VALID_STATUSES
    relation_base = OTRSTicketConfigItem.objects.filter(
        is_present_in_source=True, cmdb_link=link,
        ticket__in=TicketQueryService.noc_tickets(),
    ).filter(business_statuses_query("ticket__business_status", valid))
    ticket_ids = relation_base.values_list("ticket_id", flat=True).distinct()
    period_ids = relation_base.filter(ticket__source_created_at__gte=start,
        ticket__source_created_at__lte=now).values_list("ticket_id", flat=True).distinct()
    status_backlog = BACKLOG_STATUSES
    all_tickets = OTRSTicket.objects.filter(pk__in=ticket_ids).order_by("-source_created_at", "-pk")
    in_period = OTRSTicket.objects.filter(pk__in=period_ids)
    period_ticket_rows = list(in_period.filter(source_created_at__isnull=False).only(
        "business_status", "source_created_at", "resolved_at", "terminal_state_at",
    ))
    overdue = sum(1 for ticket in period_ticket_rows if (
        ticket.business_status in status_backlog and now - ticket.source_created_at > timedelta(hours=24)
    ) or (
        normalize_business_status(ticket.business_status) == RESOLVED_STATUS
        and operational_resolution_at(ticket)
        and operational_resolution_at(ticket) - ticket.source_created_at > timedelta(hours=24)
    ))
    trend_start = timezone.localtime(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(5):
        trend_start = trend_start.replace(day=1) - timedelta(days=1)
        trend_start = trend_start.replace(day=1)
    trend_dates = OTRSTicket.objects.filter(pk__in=ticket_ids, source_created_at__gte=trend_start,
        source_created_at__lte=now).values_list("source_created_at", flat=True)
    monthly_counts = Counter(timezone.localtime(created).strftime("%Y-%m") for created in trend_dates.iterator(chunk_size=500))
    monthly = {}
    cursor = trend_start
    for _ in range(6):
        key = cursor.strftime("%Y-%m")
        monthly[key] = monthly_counts.get(key, 0)
        cursor = cursor.replace(day=28) + timedelta(days=4)
        cursor = cursor.replace(day=1)
    page = Paginator(all_tickets, 10).get_page(request.GET.get("history_page", 1))
    data["history"] = {
        "period_days": period_days, "tickets_period": in_period.count(),
        "backlog": all_tickets.filter(business_status__in=status_backlog).count(),
        "recurrent": 1 if in_period.count() >= 2 else 0, "over_24h": overdue,
        "tickets": [{"id": t.pk, "number": t.ticket_number, "created_at": t.source_created_at.isoformat() if t.source_created_at else None,
            "status": normalize_business_status(t.business_status), "title": t.title, "responsibility": t.responsibility_normalized,
            "client": link.contracting_client, "project": link.business_project,
            "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
            "terminal_at": t.terminal_state_at.isoformat() if t.terminal_state_at else None,
            "duration_hours": _ticket_duration_hours(t, now),
            "closing_reason": t.closing_code_raw,
            "over_24h": bool(t.source_created_at and ((t.business_status in status_backlog and now - t.source_created_at > timedelta(hours=24)) or
                (normalize_business_status(t.business_status) == RESOLVED_STATUS
                 and operational_resolution_at(t)
                 and operational_resolution_at(t) - t.source_created_at > timedelta(hours=24))))}
            for t in page.object_list],
        "page": page.number, "pages": page.paginator.num_pages, "total": page.paginator.count,
        "monthly": monthly,
    }


@login_required
@access_required("view_map")
@require_GET
def map_aggregates(request):
    from aplicativos.mapa.services.analytics import MapAnalyticsService

    try:
        data = MapAnalyticsService.query(request.user, request.GET)
        month = request.GET.get("period") or "last_month"
        if request.GET.get("include_intelligence") == "1" and data["mode"] == "calls" and (month in {"last_month","this_month"} or len(month)==7):
            from aplicativos.mapa.services.geo_intelligence import intelligence_payload
            data["geo_intelligence"] = intelligence_payload(request.user,request.GET,summary={**data})
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse(data)


@login_required
@access_required("view_map")
@require_GET
def map_state_detail(request):
    from django.core.exceptions import PermissionDenied
    from aplicativos.mapa.services.analytics import MapAnalyticsService

    try:
        payload = MapAnalyticsService.state_detail(request.user, request.GET)
    except PermissionDenied as error:
        return JsonResponse({"error": str(error)}, status=403)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse(payload)

@login_required
def import_cmdb(request):
    if not request.user.is_superuser:
        return JsonResponse({"error": "A importacao XLSX esta desativada como fonte operacional."}, status=403)
    return render(request, "base_links/import.html", {"legacy_disabled": True})


@login_required
@require_POST
def confirm_import(request):
    if not request.user.is_superuser:
        return JsonResponse({"error": "Rota XLSX LEGACY restrita ao administrador."}, status=403)
    return JsonResponse({"error": "A importacao XLSX esta desativada como fonte operacional."}, status=410)
