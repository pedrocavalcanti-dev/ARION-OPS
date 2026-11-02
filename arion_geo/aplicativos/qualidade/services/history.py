"""Scoped OQ timeline and global Quality audit history."""
from django.core.paginator import Paginator
from django.db.models import CharField, F, Q
from django.db import connection
from django.db.models.functions import Cast, Collate
from django.utils.dateparse import parse_date

from aplicativos.auditoria.models import AuditEvent
from aplicativos.configuracoes.data_scope import DataScopeService
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

from ..models import QualityAuditEvent, QualityContact, QualityOrder
from .contacts import contacts_for_user
from .data_scope import QualityDataScopeService


PAGE_SIZES = (25, 50, 100)
ORDER_EVENT_TYPES = {
    "OQ_CREATED", "STATUS_CHANGED", "OWNER_CHANGED", "PRIORITY_CHANGED", "LEVEL_CHANGED",
    "DEADLINE_CHANGED", "NOTES_CHANGED", "ORDER_CLOSED", "CHECKLIST_ITEM_ADDED", "CHECKLIST_ITEM_CHANGED",
}
OCCURRENCE_EVENT_TYPES = {"OCCURRENCE_LINKED", "OCCURRENCE_REVIEWED", "RECURRENCE_CONFIRMED"}
TREATMENT_EVENT_TYPES = {"QUALITY_TICKET_LINKED"}
MONITORING_EVENT_TYPES = {"MONITORING_STARTED", "MONITORING_FAILED", "MONITORING_SUCCEEDED", "MONITORING_CANCELED"}
EVENT_LABELS = {
    **{value: label for value, label in QualityAuditEvent.EventType.choices},
    "OCCURRENCE_REVIEWED": "Ocorrência revisada",
    "LEVEL_CHANGED": "Nível alterado",
    "DEADLINE_CHANGED": "Prazo alterado",
    "NOTES_CHANGED": "Observação atualizada",
    "CHECKLIST_ITEM_ADDED": "Item de checklist adicionado",
    "CHECKLIST_ITEM_CHANGED": "Checklist atualizado",
    "MONITORING_CANCELED": "Monitoramento cancelado",
    "QUALITY_CONTACT_CREATED": "Contato criado",
    "QUALITY_CONTACT_UPDATED": "Contato atualizado",
    "QUALITY_CONTACT_ACTIVATED": "Contato reativado",
    "QUALITY_CONTACT_DEACTIVATED": "Contato desativado",
}
FILTERS = {
    "all": None,
    "order": ORDER_EVENT_TYPES,
    "occurrences": OCCURRENCE_EVENT_TYPES,
    "treatments": TREATMENT_EVENT_TYPES,
    "monitoring": MONITORING_EVENT_TYPES,
}


def get_order_history(user, order, *, category="all", page=1):
    category = category if category in FILTERS else "all"
    queryset = order.audit_events.select_related("actor").order_by("-created_at", "-pk")
    if FILTERS[category]:
        queryset = queryset.filter(event_type__in=FILTERS[category])
    page_obj = Paginator(queryset, 25).get_page(page)
    events = [_format_quality_event(event) for event in page_obj.object_list]
    return {"history_page": page_obj, "history_events": events, "history_category": category}


def get_global_quality_history(user, params):
    orders = QualityDataScopeService.orders_for(user).select_related("link", "link__operator")
    contacts = contacts_for_user(user)
    visible_order_ids = _object_id_subquery(orders)
    visible_contact_ids = _object_id_subquery(contacts)
    queryset = AuditEvent.objects.filter(category="QUALITY").filter(
        Q(object_type="QualityOrder", object_id__in=visible_order_ids)
        | Q(object_type="QualityContact", object_id__in=visible_contact_ids)
    ).select_related("user").order_by("-timestamp", "-pk")

    selected = {
        key: str(params.get(key, "")).strip()[:180]
        for key in ("q", "event", "user", "oq", "client", "project", "isp", "date_from", "date_to")
    }
    if selected["isp"].isdigit():
        selected["isp"] = ""
    start_date = parse_date(selected["date_from"])
    end_date = parse_date(selected["date_to"])
    if start_date:
        queryset = queryset.filter(timestamp__date__gte=start_date)
    if end_date:
        queryset = queryset.filter(timestamp__date__lte=end_date)
    if selected["event"]:
        queryset = queryset.annotate(_quality_action=Collate("action", "utf8mb4_unicode_ci") if connection.vendor == "mysql" else F("action"))
        queryset = queryset.filter(_quality_action=selected["event"])
    if selected["user"].isdigit():
        queryset = queryset.filter(user_id=int(selected["user"]))

    if selected["client"]:
        order_ids = _object_id_subquery(orders.filter(client_snapshot__iexact=selected["client"]))
        contact_ids = _object_id_subquery(contacts.filter(client__iexact=selected["client"]))
        queryset = queryset.filter(
            Q(object_type="QualityOrder", object_id__in=order_ids)
            | Q(object_type="QualityContact", object_id__in=contact_ids)
        )
    raw_project_values = list(orders.exclude(project_snapshot="").values_list("project_snapshot", flat=True))
    raw_project_values.extend(orders.exclude(link__business_project="").values_list("link__business_project", flat=True))
    raw_project_values.extend(orders.exclude(link__legacy_project="").values_list("link__legacy_project", flat=True))
    raw_project_values.extend(contacts.exclude(project="").values_list("project", flat=True))
    project_groups = project_alias_groups(raw_project_values)
    project_key = canonical_project_name(selected["project"])
    if project_key not in project_groups:
        selected["project"] = ""
        project_key = ""
    elif project_key:
        selected["project"] = display_project_name(project_key)
    if project_key:
        project_aliases = project_groups[project_key]
        order_ids = _object_id_subquery(orders.filter(
            Q(project_snapshot__in=project_aliases)
            | Q(link__business_project__in=project_aliases)
            | Q(link__legacy_project__in=project_aliases)
        ))
        contact_ids = _object_id_subquery(contacts.filter(project__in=project_aliases))
        queryset = queryset.filter(
            Q(object_type="QualityOrder", object_id__in=order_ids)
            | Q(object_type="QualityContact", object_id__in=contact_ids)
        )
    if selected["isp"]:
        order_ids = _object_id_subquery(provider_filter_order_queryset(orders, selected["isp"]))
        contact_company_names = provider_names_for_values(
            contacts.order_by().exclude(company="").values_list("company", flat=True).distinct(),
        )
        matching_contact_companies = [
            raw for raw, name in contact_company_names.items()
            if name.casefold() == selected["isp"].casefold()
        ]
        contact_ids = _object_id_subquery(contacts.filter(
            Q(company__iexact=selected["isp"]) | Q(company__in=matching_contact_companies),
        ))
        queryset = queryset.filter(
            Q(object_type="QualityOrder", object_id__in=order_ids)
            | Q(object_type="QualityContact", object_id__in=contact_ids)
        )
    if selected["oq"]:
        matching_orders = _object_id_subquery(orders.filter(code__icontains=selected["oq"]))
        queryset = queryset.filter(object_type="QualityOrder", object_id__in=matching_orders)

    if selected["q"]:
        term = selected["q"]
        matching_orders = orders.filter(
            Q(code__icontains=term) | Q(designation_snapshot__icontains=term) | Q(ci_snapshot__icontains=term)
            | Q(client_snapshot__icontains=term) | Q(project_snapshot__icontains=term) | Q(isp_snapshot__icontains=term)
            | Q(link__source_config_item_id__icontains=term) | Q(link__source_number__icontains=term)
        )
        matching_contacts = contacts.filter(
            Q(name__icontains=term) | Q(company__icontains=term) | Q(email__icontains=term)
            | Q(client__icontains=term) | Q(project__icontains=term)
        )
        queryset = queryset.filter(
            Q(description__icontains=term) | Q(object_label__icontains=term)
            | Q(user__username__icontains=term) | Q(user__first_name__icontains=term)
            | Q(user__last_name__icontains=term)
            | Q(object_type="QualityOrder", object_id__in=_object_id_subquery(matching_orders))
            | Q(object_type="QualityContact", object_id__in=_object_id_subquery(matching_contacts))
        )

    action_expression = Collate("action", "utf8mb4_unicode_ci") if connection.vendor == "mysql" else F("action")
    event_actions = list(
        queryset.order_by().annotate(_quality_action=action_expression)
        .values_list("_quality_action", flat=True).distinct()
    )
    actor_options = list(queryset.exclude(user__isnull=True).values_list("user_id", "user__first_name", "user__last_name", "user__username").distinct())
    try:
        page_size = int(params.get("per_page", 25))
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in PAGE_SIZES:
        page_size = 25
    page_obj = Paginator(queryset, page_size).get_page(params.get("page", 1))

    page_order_ids = [int(event.object_id) for event in page_obj.object_list if event.object_type == "QualityOrder" and event.object_id.isdigit()]
    page_contact_ids = [int(event.object_id) for event in page_obj.object_list if event.object_type == "QualityContact" and event.object_id.isdigit()]
    order_map = {
        order.pk: order for order in orders.filter(pk__in=page_order_ids).select_related("link", "link__operator", "owner")
    }
    contact_map = {contact.pk: contact for contact in contacts.filter(pk__in=page_contact_ids)}
    links = [order.link for order in order_map.values() if order.link_id]
    semantics = provider_values_for_links(links)
    snapshot_names = provider_names_for_values([order.isp_snapshot for order in order_map.values() if order.isp_snapshot])
    contact_company_names = provider_names_for_values([
        contact.company for contact in contact_map.values() if contact.company
    ])
    rows = []
    for event in page_obj.object_list:
        order = order_map.get(int(event.object_id)) if event.object_type == "QualityOrder" and event.object_id.isdigit() else None
        contact = contact_map.get(int(event.object_id)) if event.object_type == "QualityContact" and event.object_id.isdigit() else None
        link_semantics = semantics.get(order.link.pk) if order and order.link_id else None
        event.order = order
        event.contact = contact
        event.designation = (
            link_designation(order.link) if order and order.link_id
            else clean_link_designation(order.designation_snapshot) if order else "—"
        )
        event.provider_name = (
            link_semantics.provider_name if link_semantics
            else snapshot_names.get(order.isp_snapshot, UNCLASSIFIED) if order
            else contact_company_names.get(contact.company, "—") if contact and contact.company
            else "—"
        )
        event.project_display = display_project_name(
            (order.link.business_project or order.link.legacy_project) if order and order.link_id
            else order.project_snapshot if order
            else contact.project if contact
            else ""
        ) or "—"
        event.event_label = EVENT_LABELS.get(event.action, event.get_action_display() if hasattr(event, "get_action_display") else event.action.replace("_", " ").title())
        event.summary = _format_audit_summary(event)
        rows.append(event)

    client_options = set(orders.exclude(client_snapshot="").order_by().values_list("client_snapshot", flat=True).distinct())
    client_options.update(contacts.exclude(client="").order_by().values_list("client", flat=True).distinct())
    project_options = sorted(
        {display_project_name(value) for value in raw_project_values if value},
        key=str.casefold,
    )
    contact_provider_names = provider_names_for_values(
        contacts.exclude(company="").order_by().values_list("company", flat=True).distinct(),
    )
    provider_options = set(provider_name_options_for_orders(orders)) | set(contact_provider_names.values())
    return {
        "history_page": page_obj,
        "history_events": rows,
        "selected": selected,
        "page_size": page_size,
        "total_count": page_obj.paginator.count,
        "event_options": [(action, EVENT_LABELS.get(action, action.replace("_", " ").title())) for action in sorted(event_actions)],
        "actor_options": [(str(pk), " ".join(part for part in (first, last) if part).strip() or username) for pk, first, last, username in actor_options],
        "client_options": sorted(client_options, key=str.casefold),
        "project_options": sorted(project_options, key=str.casefold),
        "provider_options": sorted(provider_options, key=str.casefold),
        "date_from": start_date.isoformat() if start_date else "",
        "date_to": end_date.isoformat() if end_date else "",
    }


def _object_id_subquery(queryset):
    """Return string primary keys lazily for the central audit object_id column."""
    object_id = Cast("pk", output_field=CharField())
    if connection.vendor == "mysql":
        object_id = Collate(object_id, "utf8mb4_unicode_ci")
    return queryset.order_by().annotate(_quality_object_id=object_id).values("_quality_object_id")


def _format_quality_event(event):
    metadata = event.metadata or {}
    summary = event.description or _format_old_new(metadata)
    return {
        "event": event,
        "label": EVENT_LABELS.get(event.event_type, event.get_event_type_display()),
        "summary": summary or "Evento registrado.",
        "actor_label": (event.actor.get_full_name() or event.actor.get_username()) if event.actor_id else "Sistema",
    }


def _format_audit_summary(event):
    description = str(event.description or "").strip()
    if description:
        return description
    return _format_old_new(event.metadata or {}) or "Evento registrado."


def _format_old_new(metadata):
    previous = metadata.get("previous_status") or metadata.get("previous_level") or metadata.get("previous_priority") or metadata.get("old")
    current = metadata.get("status") or metadata.get("level") or metadata.get("priority") or metadata.get("new")
    if previous is None and current is None:
        return ""
    return f"{previous or '—'} → {current or '—'}"
