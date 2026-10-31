"""Scoped, local-only derived queue of links needing Quality review."""
from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Case, CharField, Exists, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.utils import timezone

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicket, OTRSTicketConfigItem
from aplicativos.integracoes.services.project_names import canonical_project_name, display_project_name, project_alias_groups
from aplicativos.integracoes.services.semantics import provider_filter_queryset, provider_name_options, provider_values_for_links

from ..models import QualityOccurrence, QualityOrder
from .constants import QUALITY_BUSINESS_PRIORITY, QUALITY_LIST_PAGE_SIZES, QUALITY_PRIORITY_DEFAULT_PERIOD, QUALITY_PRIORITY_PERIOD_OPTIONS
from .data_scope import QualityDataScopeService
from .recurrence import ACTIVE_RECURRENCE, build_quality_recurrence_analysis
from .ticket_validity import quality_ticket_sets


ACTIVE_ORDER_STATUSES = (
    QualityOrder.Status.NOVA,
    QualityOrder.Status.TRIAGEM,
    QualityOrder.Status.TRATAMENTO,
    QualityOrder.Status.AGUARDANDO_TERCEIRO,
    QualityOrder.Status.MONITORAMENTO,
)
PAGE_SIZES = QUALITY_LIST_PAGE_SIZES
CRITICAL_ORDER = Case(
    When(active_priority=QualityOrder.Priority.CRITICA, then=Value(0)),
    default=Value(1), output_field=IntegerField(),
)
FORMAL_PRIORITY_ORDER = Case(
    When(active_priority=QualityOrder.Priority.CRITICA, then=Value(0)),
    When(active_priority=QualityOrder.Priority.ALTA, then=Value(1)),
    When(active_priority=QualityOrder.Priority.MEDIA, then=Value(2)),
    When(active_priority=QualityOrder.Priority.BAIXA, then=Value(3)),
    default=Value(4), output_field=IntegerField(),
)


def _bounded(value, limit=300):
    return str(value or "").strip()[:limit]


def _link_project_expression():
    return Case(
        When(business_project="", then=F("legacy_project")),
        default=F("business_project"), output_field=CharField(),
    )


def _scoped_orders(user):
    return QualityDataScopeService.orders_for(user)


def business_priority(provider_name, project_name, client_name=""):
    """Return an explicit attention rank; it never changes recurrence metrics."""
    from aplicativos.integracoes.services.project_names import canonical_project_name, project_comparison_key

    provider = project_comparison_key(provider_name)
    project = project_comparison_key(canonical_project_name(project_name))
    client = project_comparison_key(client_name)
    if client == "FUST" or project == "FUST":
        return QUALITY_BUSINESS_PRIORITY[("FUST", "")]
    return QUALITY_BUSINESS_PRIORITY.get((provider, project), 99)


def build_priority_link_analysis(
    user, period_value=QUALITY_PRIORITY_DEFAULT_PERIOD, *, state="active",
):
    """Build the shared, scoped source and annotations for priority link views."""
    now = timezone.now()
    allowed_periods = {value for value, _label in QUALITY_PRIORITY_PERIOD_OPTIONS}
    period = _bounded(period_value, 4)
    if period not in allowed_periods:
        period = QUALITY_PRIORITY_DEFAULT_PERIOD
    cutoff = now - timedelta(days=int(period))

    scoped_orders = _scoped_orders(user)
    scope_links = DataScopeService.filter_queryset(
        user,
        CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    )
    recurrence = build_quality_recurrence_analysis(
        user, scope_links=scope_links, scoped_orders=scoped_orders, now=now,
    )
    period_tickets = recurrence["period_tickets"].filter(source_created_at__gte=cutoff)
    valid_tickets = recurrence["valid_tickets"].filter(source_created_at__gte=cutoff)

    active_orders = scoped_orders.filter(status__in=ACTIVE_ORDER_STATUSES).order_by(
        Case(
            When(priority=QualityOrder.Priority.CRITICA, then=Value(0)),
            When(priority=QualityOrder.Priority.ALTA, then=Value(1)),
            When(priority=QualityOrder.Priority.MEDIA, then=Value(2)),
            When(priority=QualityOrder.Priority.BAIXA, then=Value(3)),
            default=Value(4), output_field=IntegerField(),
        ), "-updated_at", "-pk",
    )
    active_order = active_orders.filter(
        Q(link_id=OuterRef("source_config_item_id"))
        | Q(ci_snapshot=OuterRef("source_config_item_id"))
    )
    active_order_ids = list(active_orders.values_list("link_id", flat=True))
    active_order_ids.extend(active_orders.exclude(ci_snapshot="").values_list("ci_snapshot", flat=True))
    if state == "stabilized":
        candidate_ids = {
            config_item_id for config_item_id, summary in recurrence["links"].items()
            if summary["is_stabilized"]
        }
    elif state == "all":
        candidate_ids = set(recurrence["links"])
        candidate_ids.update(value for value in active_order_ids if value)
    else:
        candidate_ids = {
            config_item_id for config_item_id, summary in recurrence["links"].items()
            if summary["is_active_recurrence"]
        }
    recurrence_ticket_ids = set()
    for config_item_id, summary in recurrence["links"].items():
        if state == "active":
            episodes = (summary["current_episode"],) if summary["is_active_recurrence"] else ()
        elif state == "stabilized":
            episodes = (summary["current_episode"],) if summary["is_stabilized"] else ()
        else:
            episodes = summary["episodes"]
        recurrence_ticket_ids.update(
            (config_item_id, item["external_id"])
            for episode in episodes
            for item in episode.occurrences[1:]
        )
    same_cause_by_link = {}
    if recurrence_ticket_ids:
        review_rows = QualityOccurrence.objects.filter(
            quality_order__in=scoped_orders,
            excluded=False,
            same_root_cause=True,
            ticket_external_id_snapshot__in={ticket_id for _ci, ticket_id in recurrence_ticket_ids},
        ).values_list(
            "ticket_external_id_snapshot", "quality_order__link_id", "quality_order__ci_snapshot",
        )
        for ticket_id, linked_ci, snapshot_ci in review_rows:
            config_item_id = linked_ci or snapshot_ci
            if (config_item_id, ticket_id) in recurrence_ticket_ids:
                same_cause_by_link[config_item_id] = same_cause_by_link.get(config_item_id, 0) + 1

    links = scope_links.filter(source_config_item_id__in=candidate_ids).annotate(
        has_active_order=Exists(active_order),
        active_order_code=Subquery(active_order.values("code")[:1]),
        active_order_status=Subquery(active_order.values("status")[:1]),
        active_priority=Subquery(active_order.values("priority")[:1]),
        active_level=Subquery(active_order.values("recurrence_level")[:1]),
    )
    return {
        "now": now,
        "cutoff": cutoff,
        "period": period,
        "scoped_orders": scoped_orders,
        "scope_links": scope_links,
        "period_tickets": period_tickets,
        "valid_tickets": valid_tickets,
        "recurrence": recurrence,
        "same_cause_by_link": same_cause_by_link,
        "links": links,
        "state": state,
    }


def get_priority_links(user, params):
    """Return candidates without automating formal recurrence or root-cause labels."""
    selected_state = _bounded(params.get("state", "active"), 20)
    if selected_state not in {"active", "stabilized", "all"}:
        selected_state = "active"
    analysis = build_priority_link_analysis(
        user,
        params.get("period", QUALITY_PRIORITY_DEFAULT_PERIOD),
        state=selected_state,
    )
    now = analysis["now"]
    period = analysis["period"]

    selected = {
        key: _bounded(params.get(key), 160 if key == "q" else 300)
        for key in ("q", "client", "project", "isp", "uf", "level", "priority", "oq", "status", "reason", "portfolio")
    }
    selected["state"] = selected_state
    if selected["level"] not in {value for value, _label in QualityOrder.RecurrenceLevel.choices} | {"UNCLASSIFIED"}:
        selected["level"] = ""
    if selected["priority"] not in {value for value, _label in QualityOrder.Priority.choices} | {"UNCLASSIFIED"}:
        selected["priority"] = ""
    if selected["oq"] not in {"WITH", "WITHOUT"}:
        selected["oq"] = ""
    if selected["status"] not in set(ACTIVE_ORDER_STATUSES):
        selected["status"] = ""
    if selected["isp"].isdigit():
        selected["isp"] = ""
    if selected["portfolio"] not in {"claro", "vivo", "other"}:
        selected["portfolio"] = ""

    page_size = params.get("per_page", "25")
    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in PAGE_SIZES:
        page_size = 25
    ordering = _bounded(params.get("sort", "recent" if selected_state == "stabilized" else "attention"), 20)
    if ordering not in {"attention", "recent", "occurrences", "recurrences"}:
        ordering = "attention"

    scoped_orders = analysis["scoped_orders"]
    links = analysis["links"]
    valid_tickets = analysis["valid_tickets"]
    scope_links = analysis["scope_links"]
    raw_projects = list(scope_links.values_list("business_project", flat=True))
    raw_projects.extend(scope_links.values_list("legacy_project", flat=True))
    project_groups = project_alias_groups(raw_projects)
    project_aliases = project_groups.get(canonical_project_name(selected["project"]), ()) if selected["project"] else ()
    if selected["project"] and project_aliases:
        selected["project"] = display_project_name(selected["project"])

    if selected["q"]:
        term = selected["q"]
        links = links.filter(
            Q(source_number__icontains=term)
            | Q(source_config_item_id__icontains=term)
            | Q(designation__icontains=term)
            | Q(name__icontains=term)
            | Q(contracting_client__icontains=term)
            | Q(business_project__icontains=term)
            | Q(legacy_project__icontains=term)
        )
    for key, field in (("client", "contracting_client"), ("uf", "state")):
        if selected[key]:
            links = links.filter(**{field: selected[key]})
    if project_aliases:
        links = links.filter(Q(business_project__in=project_aliases) | Q(legacy_project__in=project_aliases))
    if selected["isp"]:
        links = provider_filter_queryset(links, selected["isp"])
    if selected["level"] == "UNCLASSIFIED":
        links = links.filter(Q(active_level="") | Q(active_level__isnull=True))
    elif selected["level"]:
        links = links.filter(active_level=selected["level"])
    if selected["priority"] == "UNCLASSIFIED":
        links = links.filter(Q(active_priority="") | Q(active_priority__isnull=True))
    elif selected["priority"]:
        links = links.filter(active_priority=selected["priority"])
    if selected["oq"] == "WITH":
        links = links.filter(has_active_order=True)
    elif selected["oq"] == "WITHOUT":
        links = links.filter(has_active_order=False)
    if selected["status"]:
        links = links.filter(active_order_status=selected["status"])
    if selected["reason"]:
        order_reason = scoped_orders.filter(
            current_reason__icontains=selected["reason"],
        )
        order_reason = order_reason.filter(
            Q(link_id=OuterRef("source_config_item_id"))
            | Q(ci_snapshot=OuterRef("source_config_item_id")),
        )
        occurrence_reason = QualityOccurrence.objects.filter(
            quality_order__in=scoped_orders,
            excluded=False,
            reason__icontains=selected["reason"],
        ).filter(
            Q(quality_order__link_id=OuterRef("source_config_item_id"))
            | Q(quality_order__ci_snapshot=OuterRef("source_config_item_id")),
        )
        ticket_reason = OTRSTicketConfigItem.objects.filter(
            cmdb_link_id=OuterRef("pk"),
            is_present_in_source=True,
            ticket_id__in=valid_tickets.filter(
                closing_code_raw__icontains=selected["reason"],
            ).values("pk"),
        )
        links = links.filter(Exists(order_reason) | Exists(occurrence_reason) | Exists(ticket_reason))

    rows = list(links)
    provider_semantics = provider_values_for_links(rows)
    recurrence_by_link = analysis["recurrence"]["links"]
    for row in rows:
        semantics = provider_semantics[row.pk]
        row.provider_id = semantics.provider_id
        row.provider_name = semantics.provider_name
        row.isp_display = row.provider_name
        row.project_display = display_project_name(row.business_project or row.legacy_project)
        summary = recurrence_by_link.get(row.source_config_item_id, {})
        occurrences = summary.get("occurrences", ())
        in_period = [item for item in occurrences if analysis["cutoff"] <= item["occurred_at"] <= now]
        row.is_active_recurrence = bool(summary.get("is_active_recurrence"))
        row.is_stabilized = bool(summary.get("is_stabilized"))
        row.operational_state = summary.get("operational_state", "NO_VALID_OCCURRENCES")
        row.operational_state_label = summary.get("operational_state_label", "Sem ocorrência válida")
        row.current_episode_occurrence_count = summary.get("current_episode_occurrence_count", 0)
        row.current_episode_recurrence_count = summary.get("current_episode_recurrence_count", 0)
        row.current_episode_number = summary.get("current_episode_number")
        row.operational_recurrence_level = summary.get("operational_recurrence_level", "Inicial")
        row.stabilized_at = summary.get("current_episode_stabilized_at") if row.is_stabilized else None
        row.reopened_after_stability = bool(summary.get("reopened_after_stability"))
        row.valid_occurrence_count = len(in_period)
        row.period_occurrence_count = len(in_period)
        row.period_recurrence_count = sum(bool(item.get("is_recurrence")) for item in in_period)
        row.recurrence_count = summary.get("current_episode_recurrence_count", 0)
        row.last_occurrence = summary.get("last_occurrence")
        row.same_cause_recurrences = analysis["same_cause_by_link"].get(row.source_config_item_id, 0)
        row.business_priority = business_priority(row.provider_name, row.project_display, row.contracting_client)
        row.portfolio = "claro" if row.business_priority in {10, 20} else "vivo" if row.business_priority in {30, 40} else "other"
        row.formal_priority_order = {
            QualityOrder.Priority.CRITICA: 0,
            QualityOrder.Priority.ALTA: 1,
            QualityOrder.Priority.MEDIA: 2,
            QualityOrder.Priority.BAIXA: 3,
        }.get(row.active_priority, 4)
        row.days_since_last = max((now - row.last_occurrence).days, 0) if row.last_occurrence else None
        row.priority_label = dict(QualityOrder.Priority.choices).get(row.active_priority, "Sem classificação")
        row.level_label = row.active_level or "A classificar"
        row.status_label = {
            QualityOrder.Status.NOVA: "Nova",
            QualityOrder.Status.TRIAGEM: "Triagem",
            QualityOrder.Status.TRATAMENTO: "Em tratamento",
            QualityOrder.Status.AGUARDANDO_TERCEIRO: "Aguardando terceiro",
            QualityOrder.Status.MONITORAMENTO: "Monitoramento",
        }.get(row.active_order_status, "")
        from aplicativos.integracoes.services.semantics import link_designation
        row.designation_display = link_designation(row)

    if selected["portfolio"]:
        rows = [row for row in rows if row.portfolio == selected["portfolio"]]
    timestamp = lambda row: row.last_occurrence.timestamp() if row.last_occurrence else 0
    if ordering == "recent":
        rows.sort(key=lambda row: (row.last_occurrence is None, -timestamp(row), -row.recurrence_count, -row.period_occurrence_count, row.source_config_item_id))
    elif ordering == "occurrences":
        rows.sort(key=lambda row: (-row.period_occurrence_count, -row.period_recurrence_count, -timestamp(row), row.source_config_item_id))
    elif ordering == "recurrences":
        rows.sort(key=lambda row: (-row.recurrence_count, -timestamp(row), -row.period_occurrence_count, row.source_config_item_id))
    else:
        rows.sort(key=lambda row: (
            not row.is_active_recurrence,
            row.operational_state != ACTIVE_RECURRENCE,
            row.business_priority,
            -row.recurrence_count,
            -timestamp(row),
            row.formal_priority_order,
            row.source_config_item_id,
        ))
    page = Paginator(rows, page_size).get_page(params.get("page"))
    rows = list(page.object_list)
    for index, row in enumerate(rows, start=(page.number - 1) * page_size + 1):
        row.rank = index
        row.provider_id = provider_semantics[row.pk].provider_id
        row.provider_name = provider_semantics[row.pk].provider_name
        row.isp_display = row.provider_name
        row.priority_label = dict(QualityOrder.Priority.choices).get(row.active_priority, "Sem classificação")
        row.level_label = row.active_level or "A classificar"
        row.status_label = {
            QualityOrder.Status.NOVA: "Nova",
            QualityOrder.Status.TRIAGEM: "Triagem",
            QualityOrder.Status.TRATAMENTO: "Em tratamento",
            QualityOrder.Status.AGUARDANDO_TERCEIRO: "Aguardando terceiro",
            QualityOrder.Status.MONITORAMENTO: "Monitoramento",
        }.get(row.active_order_status, "")
        row.days_since_last = max((now - row.last_occurrence).days, 0) if row.last_occurrence else None
        from aplicativos.integracoes.services.semantics import link_designation
        row.designation_display = link_designation(row)

    scoped_ticket_relations = OTRSTicketConfigItem.objects.filter(
        cmdb_link__in=scope_links,
        cmdb_link__is_present_in_source=True,
        is_present_in_source=True,
    )
    options = {
        "clients": list(scope_links.exclude(contracting_client="").order_by("contracting_client").values_list("contracting_client", flat=True).distinct()),
        "projects": sorted({display_project_name(name) for name in raw_projects if name}, key=str.casefold),
        "ufs": list(scope_links.exclude(state="").order_by("state").values_list("state", flat=True).distinct()),
        "isps": provider_name_options(scope_links),
    }
    return {
        "page_obj": page,
        "rows": rows,
        "period": period,
        "period_options": QUALITY_PRIORITY_PERIOD_OPTIONS,
        "state_options": (("active", "Ativos"), ("stabilized", "Estabilizados"), ("all", "Todos")),
        "page_size": page_size,
        "page_sizes": PAGE_SIZES,
        "sort": ordering,
        "selected": selected,
        "filter_options": options,
        "portfolio_options": (("claro", "Claro prioritárias"), ("vivo", "Vivo prioritárias"), ("other", "Outras")),
        "level_options": QualityOrder.RecurrenceLevel.choices,
        "priority_options": QualityOrder.Priority.choices,
        "active_status_options": [(value, dict(QualityOrder.Status.choices)[value]) for value in ACTIVE_ORDER_STATUSES],
        "candidate_count": page.paginator.count,
        "has_any_source_data": OTRSTicket.objects.filter(
            pk__in=scoped_ticket_relations.values("ticket_id"),
        ).exists(),
    }
