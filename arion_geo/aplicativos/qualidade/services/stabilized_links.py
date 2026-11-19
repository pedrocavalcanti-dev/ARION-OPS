"""Scoped, deduplicated service for statistically stabilized links in Quality."""
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror, IntegrationSyncState
from aplicativos.integracoes.services.project_names import display_project_name
from aplicativos.integracoes.services.semantics import (
    link_designation,
    provider_filter_queryset,
    provider_name_options,
    provider_values_for_links,
)

from ..models import QualityMonitoringCycle, QualityOrder
from .constants import QUALITY_LIST_PAGE_SIZES, QUALITY_RREIP_DAYS
from .data_scope import QualityDataScopeService
from .filters import dimension_catalog, filter_links, resolve_dimensions
from .priority_links import ACTIVE_ORDER_STATUSES, business_priority
from .recurrence import build_quality_recurrence_analysis


STABILIZED_PERIOD_OPTIONS = (
    ("all", "Todos os estabilizados"),
    ("30", "Estabilizados há até 30 dias"),
    ("60", "Estabilizados há até 60 dias"),
    ("90", "Estabilizados há até 90 dias"),
    ("180", "Estabilizados há até 180 dias"),
    ("365", "Estabilizados há até 365 dias"),
    ("custom", "Período personalizado"),
)
STABILIZED_QUALITY_STATUS_OPTIONS = (
    ("", "Todas as situações"),
    ("with_oq", "Com OQ associada"),
    ("active_oq", "Com OQ ativa"),
    ("without_oq", "Sem OQ associada"),
    ("rreip_active", "Em monitoramento RREIP"),
    ("rreip_homologated", "Homologado no RREIP"),
    ("stat_only", "Somente estabilização estatística"),
)
STABILIZED_SORT_OPTIONS = (
    ("recent_stabilized", "Estabilizados recentemente"),
    ("days_desc", "Mais dias sem ocorrência"),
    ("days_asc", "Menos dias sem ocorrência"),
    ("recurrences_desc", "Mais reincidências anteriores"),
    ("last_occurrence_desc", "Última ocorrência mais recente"),
    ("designation_asc", "Circuito (A–Z)"),
)
ORDER_STATUS_LABELS = {
    QualityOrder.Status.NOVA: "Nova",
    QualityOrder.Status.TRIAGEM: "Triagem",
    QualityOrder.Status.TRATAMENTO: "Em tratamento",
    QualityOrder.Status.AGUARDANDO_TERCEIRO: "Aguardando terceiro",
    QualityOrder.Status.MONITORAMENTO: "Monitoramento",
    QualityOrder.Status.CONCLUIDA: "Concluída",
    QualityOrder.Status.CANCELADA: "Cancelada",
}


def _bounded(value, limit=200):
    return str(value or "").strip()[:limit]


def _stabilized_period_bounds(params, now):
    today = timezone.localtime(now).date()
    period = _bounded(params.get("period", "all"), 20)
    allowed = {key for key, _ in STABILIZED_PERIOD_OPTIONS}
    notices = []
    if period not in allowed:
        period = "all"
    start_date = end_date = None
    cutoff = end_exclusive = None
    if period == "custom":
        try:
            start_date = parse_date(_bounded(params.get("date_from"), 20))
            end_date = parse_date(_bounded(params.get("date_to"), 20))
        except ValueError:
            start_date = end_date = None
        if not start_date or not end_date or start_date > end_date or end_date > today:
            notices.append("Intervalo personalizado inválido. Exibindo todos os links estabilizados.")
            period = "all"
            start_date = end_date = None
        else:
            cutoff = timezone.make_aware(datetime.combine(start_date, time.min))
            end_exclusive = min(
                timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min)),
                now + timedelta(microseconds=1),
            )
    elif period != "all":
        days = int(period)
        start_date = today - timedelta(days=days - 1)
        end_date = today
        cutoff = timezone.make_aware(datetime.combine(start_date, time.min))
        end_exclusive = now + timedelta(microseconds=1)
    label = (
        f"{start_date:%d/%m/%Y} – {end_date:%d/%m/%Y}"
        if period == "custom" and start_date and end_date
        else dict(STABILIZED_PERIOD_OPTIONS).get(period, "Todos os estabilizados")
    )
    return {
        "period": period,
        "period_label": label,
        "date_from": start_date.isoformat() if period == "custom" and start_date else "",
        "date_to": end_date.isoformat() if period == "custom" and end_date else "",
        "cutoff": cutoff,
        "end_exclusive": end_exclusive,
        "notices": notices,
    }


def _order_and_rreip_maps(scoped_orders, candidate_ids):
    """Map each CI to its latest/active OQ and RREIP monitoring status."""
    if not candidate_ids:
        return {}, {}
    orders = list(
        scoped_orders.filter(
            Q(link_id__in=candidate_ids) | Q(ci_snapshot__in=candidate_ids)
        ).order_by("-updated_at", "-pk")
    )
    order_ids = [order.pk for order in orders]
    cycles_by_order = {}
    if order_ids:
        for cycle in QualityMonitoringCycle.objects.filter(
            quality_order_id__in=order_ids,
        ).order_by("quality_order_id", "-cycle_number", "-pk"):
            cycles_by_order.setdefault(cycle.quality_order_id, []).append(cycle)

    orders_by_ci = {}
    for order in orders:
        ci = order.link_id or order.ci_snapshot
        if not ci:
            continue
        existing = orders_by_ci.get(ci)
        # Prefer an active OQ over a closed/canceled historical OQ.
        if existing is None or (
            existing.status not in ACTIVE_ORDER_STATUSES
            and order.status in ACTIVE_ORDER_STATUSES
        ):
            orders_by_ci[ci] = order

    rreip_by_ci = {}
    for ci, order in orders_by_ci.items():
        cycles = cycles_by_order.get(order.pk, ())
        active_cycle = next((c for c in cycles if c.result == QualityMonitoringCycle.Result.IN_PROGRESS), None)
        success_cycle = next((c for c in cycles if c.result == QualityMonitoringCycle.Result.SUCCESS), None)
        failed_cycle = next((c for c in cycles if c.result == QualityMonitoringCycle.Result.RECURRENCE), None)
        if active_cycle:
            rreip_by_ci[ci] = {
                "key": "active",
                "label": f"RREIP em andamento · Ciclo {active_cycle.cycle_number:02d}",
                "kind_label": "Estabilizado (30d) · RREIP em andamento",
                "cycle_number": active_cycle.cycle_number,
                "cycle": active_cycle,
            }
        elif success_cycle and success_cycle.stability_validated:
            rreip_by_ci[ci] = {
                "key": "homologated",
                "label": f"Homologado RREIP · Ciclo {success_cycle.cycle_number:02d}",
                "kind_label": "Homologado no RREIP",
                "cycle_number": success_cycle.cycle_number,
                "cycle": success_cycle,
            }
        elif success_cycle:
            rreip_by_ci[ci] = {
                "key": "homologated",
                "label": f"RREIP concluído · Ciclo {success_cycle.cycle_number:02d}",
                "kind_label": "RREIP concluído com sucesso",
                "cycle_number": success_cycle.cycle_number,
                "cycle": success_cycle,
            }
        elif failed_cycle:
            rreip_by_ci[ci] = {
                "key": "recurred",
                "label": f"RREIP anterior reincidiu · Ciclo {failed_cycle.cycle_number:02d}",
                "kind_label": "Estabilização estatística (30d)",
                "cycle_number": failed_cycle.cycle_number,
                "cycle": failed_cycle,
            }
        else:
            rreip_by_ci[ci] = {
                "key": "none",
                "label": "Sem ciclo RREIP",
                "kind_label": "Estabilização estatística (30d)",
                "cycle_number": None,
                "cycle": None,
            }
    return orders_by_ci, rreip_by_ci


def build_stabilized_link_rows(
    *,
    links_queryset,
    recurrence_by_link,
    scoped_orders,
    now,
):
    """Build enriched, strictly deduplicated stabilized link rows (one per CI)."""
    stabilized_ids = {
        ci for ci, summary in recurrence_by_link.items()
        if summary.get("is_stabilized") and summary.get("current_episode")
    }
    if not stabilized_ids:
        return []
    link_rows = list(
        links_queryset.filter(source_config_item_id__in=stabilized_ids).select_related("operator")
    )
    if not link_rows:
        return []

    seen_cis = set()
    unique_links = []
    for link in link_rows:
        if link.source_config_item_id in seen_cis:
            continue
        seen_cis.add(link.source_config_item_id)
        unique_links.append(link)

    semantics_map = provider_values_for_links(unique_links)
    orders_by_ci, rreip_by_ci = _order_and_rreip_maps(scoped_orders, seen_cis)
    rows = []
    for link in unique_links:
        ci = link.source_config_item_id
        summary = recurrence_by_link[ci]
        provider = semantics_map[link.pk]
        project = display_project_name(link.business_project or link.legacy_project)
        order = orders_by_ci.get(ci)
        rreip = rreip_by_ci.get(ci) or {
            "key": "none",
            "label": "Sem RREIP",
            "kind_label": "Estabilização estatística (30d)",
            "cycle_number": None,
            "cycle": None,
        }
        has_active_order = bool(order and order.status in ACTIVE_ORDER_STATUSES)
        detail_url = (
            reverse("qualidade:priority_link_detail", kwargs={"identificador": ci})
            + "?from=stabilized&state=stabilized"
        )
        rows.append({
            "pk": link.pk,
            "designation": link_designation(link),
            "designation_display": link_designation(link),
            "code": ci,
            "source_config_item_id": ci,
            "url": detail_url,
            "client": link.contracting_client or "Não informado",
            "contracting_client": link.contracting_client or "",
            "project": project or "Projeto não informado",
            "project_display": project or "Projeto não informado",
            "provider_id": provider.provider_id,
            "provider_name": provider.provider_name or "Não classificado",
            "uf": link.state or "",
            "state": link.state or "",
            "city": link.city or "",
            "value": summary["current_episode_recurrence_count"],
            "previous_recurrences": summary["current_episode_recurrence_count"],
            "total_recurrences": summary.get("total_recurrence_count", summary["current_episode_recurrence_count"]),
            "occurrence_count": summary["current_episode_occurrence_count"],
            "valid_occurrence_count": summary.get("valid_occurrence_count", summary["current_episode_occurrence_count"]),
            "episode_count": summary.get("episode_count", 1),
            "reopened_count": summary.get("reopened_count", 0),
            "last_occurrence": summary["last_occurrence"],
            "stabilized_at": summary["current_episode_stabilized_at"],
            "days_since_last": summary["current_episode_days_since_last"],
            "days_without_occurrence": summary["current_episode_days_since_last"],
            "circuit_status": (link.deployment_state or link.incident_state or "Não informado"),
            "operational_recurrence_level": summary.get("operational_recurrence_level", "R1"),
            "associated_order_code": order.code if order else "",
            "associated_order_status": order.status if order else "",
            "associated_order_status_label": ORDER_STATUS_LABELS.get(order.status, order.get_status_display()) if order else "",
            "associated_order_url": reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}) if order else "",
            "has_order": bool(order),
            "has_active_order": has_active_order,
            "rreip_status_key": rreip["key"],
            "rreip_status_label": rreip["label"],
            "rreip_cycle_number": rreip["cycle_number"],
            "stabilization_kind_label": rreip["kind_label"],
            "analysis_reference_at": now,
            "business_priority": business_priority(provider.provider_name, project, link.contracting_client),
        })
    rows.sort(key=lambda item: (
        -(item["stabilized_at"].timestamp() if item["stabilized_at"] else 0),
        -item["previous_recurrences"],
        item["business_priority"],
        item["code"],
    ))
    return rows


def get_stabilized_links(user, params=None):
    """Return the complete, filterable, sortable, paginated view of stabilized links."""
    params = params or {}
    now = timezone.now()
    scoped_orders = QualityDataScopeService.orders_for(user)
    scope_links = DataScopeService.filter_queryset(
        user,
        CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    )
    recurrence = build_quality_recurrence_analysis(
        user, scope_links=scope_links, scoped_orders=scoped_orders, now=now,
    )
    catalog = dimension_catalog(scope_links, scoped_orders)
    window = _stabilized_period_bounds(params, now)

    selected = {
        key: _bounded(params.get(key), 160 if key == "q" else 200)
        for key in ("q", "client", "project", "isp", "region", "uf", "quality_status")
    }
    if selected["isp"].isdigit():
        selected["isp"] = ""
    if selected["quality_status"] not in {key for key, _ in STABILIZED_QUALITY_STATUS_OPTIONS}:
        selected["quality_status"] = ""

    sort = _bounded(params.get("sort", "recent_stabilized"), 32)
    if sort not in {key for key, _ in STABILIZED_SORT_OPTIONS}:
        sort = "recent_stabilized"

    try:
        page_size = int(params.get("per_page", 25))
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in QUALITY_LIST_PAGE_SIZES:
        page_size = 25

    dimensions = resolve_dimensions(selected, catalog)
    filtered_links = filter_links(scope_links, dimensions)
    if selected["isp"]:
        filtered_links = provider_filter_queryset(filtered_links, selected["isp"])
    if selected["q"]:
        term = selected["q"]
        filtered_links = filtered_links.filter(
            Q(source_number__icontains=term)
            | Q(source_config_item_id__icontains=term)
            | Q(designation__icontains=term)
            | Q(name__icontains=term)
            | Q(contracting_client__icontains=term)
            | Q(business_project__icontains=term)
            | Q(legacy_project__icontains=term)
            | Q(isp_raw__icontains=term)
            | Q(operator__name__icontains=term)
        )

    rows = build_stabilized_link_rows(
        links_queryset=filtered_links,
        recurrence_by_link=recurrence["links"],
        scoped_orders=scoped_orders,
        now=now,
    )

    if window["cutoff"] is not None and window["end_exclusive"] is not None:
        rows = [
            row for row in rows
            if row["stabilized_at"] and window["cutoff"] <= row["stabilized_at"] < window["end_exclusive"]
        ]

    q_status = selected["quality_status"]
    if q_status == "with_oq":
        rows = [row for row in rows if row["has_order"]]
    elif q_status == "active_oq":
        rows = [row for row in rows if row["has_active_order"]]
    elif q_status == "without_oq":
        rows = [row for row in rows if not row["has_order"]]
    elif q_status == "rreip_active":
        rows = [row for row in rows if row["rreip_status_key"] == "active"]
    elif q_status == "rreip_homologated":
        rows = [row for row in rows if row["rreip_status_key"] == "homologated"]
    elif q_status == "stat_only":
        rows = [row for row in rows if row["rreip_status_key"] not in {"active", "homologated"}]

    if sort == "days_desc":
        rows.sort(key=lambda r: (-r["days_without_occurrence"], -r["previous_recurrences"], r["code"]))
    elif sort == "days_asc":
        rows.sort(key=lambda r: (r["days_without_occurrence"], -r["previous_recurrences"], r["code"]))
    elif sort == "recurrences_desc":
        rows.sort(key=lambda r: (-r["previous_recurrences"], -r["days_without_occurrence"], r["code"]))
    elif sort == "last_occurrence_desc":
        rows.sort(key=lambda r: (-(r["last_occurrence"].timestamp() if r["last_occurrence"] else 0), r["code"]))
    elif sort == "designation_asc":
        rows.sort(key=lambda r: (r["designation_display"].casefold(), r["code"]))
    else:
        rows.sort(key=lambda r: (
            -(r["stabilized_at"].timestamp() if r["stabilized_at"] else 0),
            -r["previous_recurrences"],
            r["business_priority"],
            r["code"],
        ))

    total_stabilized = len(rows)
    with_oq_count = sum(1 for r in rows if r["has_order"])
    active_oq_count = sum(1 for r in rows if r["has_active_order"])
    rreip_active_count = sum(1 for r in rows if r["rreip_status_key"] == "active")
    rreip_homologated_count = sum(1 for r in rows if r["rreip_status_key"] == "homologated")
    stat_only_count = sum(1 for r in rows if r["rreip_status_key"] not in {"active", "homologated"})
    avg_days = round(sum(r["days_without_occurrence"] for r in rows) / total_stabilized) if total_stabilized else 0

    page_obj = Paginator(rows, page_size).get_page(params.get("page", 1))
    page_rows = list(page_obj.object_list)
    for index, row in enumerate(page_rows, start=(page_obj.number - 1) * page_size + 1):
        row["rank"] = index

    otrs_sync = IntegrationSyncState.objects.filter(
        key__in=("otrs_full", "otrs_fast", "otrs"),
    ).order_by("-last_success_at").first()
    project_options = catalog["projects_by_client"].get(selected["client"], []) if selected["client"] else catalog["projects"]

    return {
        "rows": page_rows,
        "page_obj": page_obj,
        "page_size": page_size,
        "page_sizes": QUALITY_LIST_PAGE_SIZES,
        "sort": sort,
        "sort_options": STABILIZED_SORT_OPTIONS,
        "selected": selected,
        "period": window["period"],
        "period_label": window["period_label"],
        "period_options": STABILIZED_PERIOD_OPTIONS,
        "date_from": window["date_from"],
        "date_to": window["date_to"],
        "quality_status_options": STABILIZED_QUALITY_STATUS_OPTIONS,
        "filter_catalog": catalog,
        "filter_notices": window["notices"] + dimensions["notices"],
        "filter_options": {
            "clients": catalog["clients"],
            "projects": project_options,
            "ufs": catalog["ufs_by_region"].get(selected["region"], []) if selected["region"] else catalog["ufs"],
            "regions": catalog["regions"],
            "isps": provider_name_options(scope_links),
        },
        "summary": {
            "total": total_stabilized,
            "with_oq": with_oq_count,
            "active_oq": active_oq_count,
            "rreip_active": rreip_active_count,
            "rreip_homologated": rreip_homologated_count,
            "stat_only": stat_only_count,
            "avg_days": avg_days,
            "window_days": QUALITY_RREIP_DAYS,
        },
        "analysis_reference_at": now,
        "coverage_earliest_at": recurrence.get("coverage_earliest_at"),
        "otrs_last_sync_at": otrs_sync.last_success_at if otrs_sync else None,
    }


def stabilized_links_url(selected=None):
    """Build a deep link to the dedicated stabilized links page preserving filters."""
    selected = selected or {}
    params = {}
    for source_key, target_key in (
        ("cliente", "client"),
        ("client", "client"),
        ("projeto", "project"),
        ("project", "project"),
        ("isp", "isp"),
        ("uf", "uf"),
        ("region", "region"),
    ):
        value = selected.get(source_key)
        if value and target_key not in params:
            params[target_key] = value
    url = reverse("qualidade:links_estabilizados")
    return f"{url}?{urlencode(params)}" if params else url
