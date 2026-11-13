"""Scoped server-side work queue for local Quality Orders."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror
from aplicativos.integracoes.services.project_names import canonical_project_name, display_project_name, project_alias_groups
from aplicativos.integracoes.services.semantics import (
    UNCLASSIFIED,
    clean_link_designation,
    link_designation,
    provider_filter_order_queryset,
    provider_name_options_for_orders,
    provider_names_for_values,
    provider_values_for_links,
)

from ..models import QualityAuditEvent, QualityMonitoringCycle, QualityOrder
from .constants import QUALITY_LIST_PAGE_SIZES
from .filters import dimension_catalog, resolve_dimensions, filter_orders
from .data_scope import QualityDataScopeService

UserModel = get_user_model()
PAGE_SIZES = QUALITY_LIST_PAGE_SIZES


QUICK_TABS = (
    ("all", "Todas"),
    ("open", "Abertas"),
    ("new", "Novas"),
    ("treatment", "Tratamento"),
    ("third_party", "Aguardando terceiro"),
    ("monitoring", "Monitoramento"),
    ("critical", "Críticas"),
    ("overdue", "Vencidas"),
    ("completed", "Concluídas"),
)
TAB_STATUSES = {
    "new": QualityOrder.Status.NOVA,
    "treatment": QualityOrder.Status.TRATAMENTO,
    "third_party": QualityOrder.Status.AGUARDANDO_TERCEIRO,
    "monitoring": QualityOrder.Status.MONITORAMENTO,
    "completed": QualityOrder.Status.CONCLUIDA,
}
ACTIVE_STATUSES = (
    QualityOrder.Status.NOVA,
    QualityOrder.Status.TRIAGEM,
    QualityOrder.Status.TRATAMENTO,
    QualityOrder.Status.AGUARDANDO_TERCEIRO,
    QualityOrder.Status.MONITORAMENTO,
)


def _clean(value, limit=300):
    return str(value or "").strip()[:limit]


def _filter_options(queryset, field):
    return list(queryset.exclude(**{field: ""}).order_by(field).values_list(field, flat=True).distinct())


def get_quality_orders_queue(user, params):
    now = timezone.now()
    selected = {
        key: _clean(params.get(key), 160 if key == "q" else 300)
        for key in ("q", "status", "priority", "level", "owner", "client", "project", "isp", "region", "uf", "due", "rreip")
    }
    status_values = {value for value, _label in QualityOrder.Status.choices}
    priority_values = {value for value, _label in QualityOrder.Priority.choices}
    level_values = {value for value, _label in QualityOrder.RecurrenceLevel.choices}
    if selected["status"] not in status_values:
        selected["status"] = ""
    if selected["priority"] not in priority_values:
        selected["priority"] = ""
    if selected["level"] not in level_values:
        selected["level"] = ""
    selected_owner_id = None
    if selected["owner"] and selected["owner"] != "unassigned":
        try:
            selected_owner_id = int(selected["owner"])
        except (TypeError, ValueError):
            selected_owner_id = None
        if selected_owner_id is None or not 0 < selected_owner_id <= (2**63 - 1):
            selected["owner"] = ""
            selected_owner_id = None
    if selected["due"] not in {"overdue", "soon", "none"}:
        selected["due"] = ""
    if selected["rreip"] not in {"active", "none"}:
        selected["rreip"] = ""
    if selected["isp"].isdigit():
        selected["isp"] = ""

    tab = _clean(params.get("tab", "all"), 20)
    if tab not in {value for value, _label in QUICK_TABS}:
        tab = "all"
    page_size = params.get("per_page", "25")
    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in PAGE_SIZES:
        page_size = 25

    scoped = QualityDataScopeService.orders_for(user).select_related("owner", "link")
    scope_links = DataScopeService.filter_queryset(user, CMDBLinkMirror.objects.filter(is_present_in_source=True))
    catalog=dimension_catalog(scope_links,scoped)
    dimensions=resolve_dimensions(selected,catalog)
    project_options=catalog["projects_by_client"].get(selected["client"],[]) if selected["client"] else catalog["projects"]
    cycle_query = QualityMonitoringCycle.objects.filter(
        quality_order_id=OuterRef("pk"), result=QualityMonitoringCycle.Result.IN_PROGRESS,
    )
    latest_event = QualityAuditEvent.objects.filter(quality_order_id=OuterRef("pk")).order_by("-created_at", "-pk")
    base = scoped.annotate(
        has_active_rreip=Exists(cycle_query),
        last_activity=Coalesce(Subquery(latest_event.values("created_at")[:1]), F("updated_at")),
    )

    base=filter_orders(base,dimensions)
    if selected["q"]:
        term = selected["q"]
        base = base.filter(
            Q(code__icontains=term)
            | Q(designation_snapshot__icontains=term)
            | Q(ci_snapshot__icontains=term)
            | Q(client_snapshot__icontains=term)
            | Q(project_snapshot__icontains=term)
            | Q(isp_snapshot__icontains=term)
            | Q(link__source_number__icontains=term)
            | Q(link__source_config_item_id__icontains=term)
            | Q(link__operator__name__icontains=term)
        )
    filter_fields = {
        "client": "client_snapshot",
        "project": "project_snapshot",
        "uf": "uf_snapshot",
        "status": "status",
        "priority": "priority",
        "level": "recurrence_level",
    }
    for key, field in filter_fields.items():
        if selected[key] and key not in {"project","client","uf"}:
            base = base.filter(**{field: selected[key]})
    if selected["isp"]:
        base = provider_filter_order_queryset(base, selected["isp"])
    if selected["owner"] == "unassigned":
        base = base.filter(owner__isnull=True)
    elif selected["owner"]:
        base = base.filter(owner_id=selected_owner_id)
    if selected["due"] == "overdue":
        base = base.filter(status__in=ACTIVE_STATUSES, treatment_deadline__lt=now)
    elif selected["due"] == "soon":
        base = base.filter(status__in=ACTIVE_STATUSES, treatment_deadline__gte=now, treatment_deadline__lte=now + timedelta(days=3))
    elif selected["due"] == "none":
        base = base.filter(status__in=ACTIVE_STATUSES, treatment_deadline__isnull=True)
    if selected["rreip"] == "active":
        base = base.filter(has_active_rreip=True)
    elif selected["rreip"] == "none":
        base = base.filter(has_active_rreip=False)

    counts = base.order_by().aggregate(
        all=Count("pk", distinct=True),
        new=Count("pk", filter=Q(status=QualityOrder.Status.NOVA), distinct=True),
        treatment=Count("pk", filter=Q(status=QualityOrder.Status.TRATAMENTO), distinct=True),
        third_party=Count("pk", filter=Q(status=QualityOrder.Status.AGUARDANDO_TERCEIRO), distinct=True),
        monitoring=Count("pk", filter=Q(status=QualityOrder.Status.MONITORAMENTO), distinct=True),
        critical=Count("pk", filter=Q(status__in=ACTIVE_STATUSES, priority=QualityOrder.Priority.CRITICA), distinct=True),
        overdue=Count("pk", filter=Q(status__in=ACTIVE_STATUSES, treatment_deadline__lt=now), distinct=True),
        completed=Count("pk", filter=Q(status=QualityOrder.Status.CONCLUIDA), distinct=True),
    )

    queue = base
    if tab in TAB_STATUSES:
        queue = queue.filter(status=TAB_STATUSES[tab])
    elif tab == "open":
        queue = queue.filter(status__in=ACTIVE_STATUSES)
    elif tab == "critical":
        queue = queue.filter(status__in=ACTIVE_STATUSES, priority=QualityOrder.Priority.CRITICA)
    elif tab == "overdue":
        queue = queue.filter(status__in=ACTIVE_STATUSES, treatment_deadline__lt=now)
    active_first = Case(
        When(status__in=ACTIVE_STATUSES, then=Value(0)), default=Value(1), output_field=IntegerField(),
    )
    priority_first = Case(
        When(priority=QualityOrder.Priority.CRITICA, then=Value(0)),
        When(priority=QualityOrder.Priority.ALTA, then=Value(1)),
        When(priority=QualityOrder.Priority.MEDIA, then=Value(2)),
        When(priority=QualityOrder.Priority.BAIXA, then=Value(3)),
        default=Value(4), output_field=IntegerField(),
    )
    queue = queue.annotate(
        _active_first=active_first,
        _priority_first=priority_first,
        _overdue=Case(
            When(status__in=ACTIVE_STATUSES, treatment_deadline__lt=now, then=Value(0)),
            default=Value(1), output_field=IntegerField(),
        ),
    ).order_by("_active_first", "_overdue", "_priority_first", "treatment_deadline", "-last_activity", "-pk")
    page = Paginator(queue, page_size).get_page(params.get("page"))
    rows = list(page.object_list)
    live_links = [order.link for order in rows if order.link_id and order.link.is_present_in_source]
    link_semantics = provider_values_for_links(live_links)
    snapshot_names = provider_names_for_values(order.isp_snapshot for order in rows if order.isp_snapshot)
    today = timezone.localdate(now)
    for order in rows:
        order.status_label = {
            QualityOrder.Status.NOVA: "Nova",
            QualityOrder.Status.TRIAGEM: "Triagem",
            QualityOrder.Status.TRATAMENTO: "Em tratamento",
            QualityOrder.Status.AGUARDANDO_TERCEIRO: "Aguardando terceiro",
            QualityOrder.Status.MONITORAMENTO: "Monitoramento",
            QualityOrder.Status.CONCLUIDA: "Concluída",
            QualityOrder.Status.CANCELADA: "Cancelada",
        }.get(order.status, order.get_status_display())
        order.priority_label = order.get_priority_display()
        order.level_label = order.get_recurrence_level_display() if order.recurrence_level else "A classificar"
        order.owner_label = (order.owner.get_full_name() or order.owner.get_username()) if order.owner_id else "Não atribuído"
        has_live_link = bool(order.link_id and order.link.is_present_in_source)
        order.link_label = link_designation(order.link) if has_live_link else clean_link_designation(order.designation_snapshot)
        order.project_display = display_project_name(
            (order.link.business_project or order.link.legacy_project) if has_live_link else order.project_snapshot,
        )
        order.provider_id = link_semantics[order.link.pk].provider_id if has_live_link else None
        order.provider_name = (
            link_semantics[order.link.pk].provider_name if has_live_link
            else snapshot_names.get(order.isp_snapshot, UNCLASSIFIED)
        )
        order.deadline_days = (timezone.localdate(order.treatment_deadline) - today).days if order.treatment_deadline else None
        order.days_overdue = abs(order.deadline_days) if order.deadline_days is not None and order.deadline_days < 0 else 0
        order.is_overdue = bool(order.treatment_deadline and order.status in ACTIVE_STATUSES and order.treatment_deadline < now)

    scoped_owner_ids = scoped.exclude(owner_id__isnull=True).values("owner_id")
    owner_options = list(UserModel.objects.filter(pk__in=scoped_owner_ids).order_by("first_name", "last_name", "username"))
    scoped_links = DataScopeService.filter_queryset(
        user, CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    )
    provider_options = provider_name_options_for_orders(scoped)
    return {
        "filter_catalog":catalog, "filter_notices":dimensions["notices"],
        "page_obj": page,
        "rows": rows,
        "counts": counts,
        "tabs": [{"key": key, "label": label, "count": counts.get(key, 0)} for key, label in QUICK_TABS],
        "selected_tab": tab,
        "selected": selected,
        "page_size": page_size,
        "page_sizes": PAGE_SIZES,
        "status_options": QualityOrder.Status.choices,
        "priority_options": QualityOrder.Priority.choices,
        "level_options": QualityOrder.RecurrenceLevel.choices,
        "filter_options": {
            "clients": catalog["clients"],
            "projects": project_options,
            "isps": provider_options,
            "ufs":catalog["ufs_by_region"].get(selected["region"],[]) if selected["region"] else catalog["ufs"], "regions":catalog["regions"],
            "owners": owner_options,
        },
        "links_for_create": scoped_links,
    }
