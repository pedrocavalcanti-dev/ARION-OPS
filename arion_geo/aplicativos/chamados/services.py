from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.models import OTRSTicketConfigItem
from aplicativos.integracoes.services.analytics import (
    BUSINESS_OPEN_STATUSES, LinkQueryService, TicketQueryService,
    count_affected_links, ticket_link_relations,
)
from aplicativos.integracoes.services.closing_reason import ClosingReasonCanonicalizer
from aplicativos.integracoes.services.semantics import provider_label
from aplicativos.painel.services.analytics import AnalyticsQueryService

from .models import TicketTracking, TicketTrackingEvent


ACTIVE_STATES = BUSINESS_OPEN_STATUSES
VALID_STATES = AnalyticsQueryService.OPERATIONAL_VALID_STATES
STATUS_LABELS = {
    "OPEN": "Em atendimento",
    "PENDING": "Pendente",
    "SCHEDULED": "Agendado",
}
STATUS_CLASSES = {"OPEN": "open", "PENDING": "pending", "SCHEDULED": "scheduled"}
INTERNAL_STATUS_LABELS = dict(TicketTracking.InternalStatus.choices)
INTERNAL_PRIORITY_LABELS = dict(TicketTracking.InternalPriority.choices)
PAGE_SIZES = (25, 50, 100)
RECURRENCE_DAYS = 30
TAB_ALIASES = {
    "all": "all", "open": "OPEN", "pending": "PENDING", "scheduled": "SCHEDULED",
    "mine": "mine", "unassigned": "unassigned",
}
QUEUE_FILTER_KEYS = (
    "tab", "q", "otrs_status", "arion_status", "client", "project", "provider", "uf",
    "priority", "assignee", "reason", "aging", "recurrent", "sort", "per_page",
)
QUEUE_REQUEST_ALIASES = ("over_24h", "page_size")
QUEUE_SORTS = {"aging", "oldest", "newest", "provider", "project", "uf"}


def sanitize_queue_filters(payload):
    """Copy only supported filter fields into a bounded, serializable mapping."""
    if not isinstance(payload, dict) and not hasattr(payload, "get"):
        return {}
    clean = {}
    for key in (*QUEUE_FILTER_KEYS, *QUEUE_REQUEST_ALIASES):
        if key not in payload:
            continue
        raw = payload.get(key)
        if isinstance(raw, (dict, list, tuple, set)):
            continue
        if key in {"aging", "over_24h", "recurrent"}:
            target_key = "over_24h" if key in {"aging", "over_24h"} else "recurrent"
            clean[target_key] = str(raw).strip().lower() in {"1", "true", "yes", "on"}
            continue
        value = str(raw or "").strip()
        if key == "tab":
            clean[key] = TAB_ALIASES.get(value.lower(), "all")
        elif key == "otrs_status":
            value = value.upper()
            if value in ACTIVE_STATES:
                clean[key] = value
        elif key == "arion_status":
            if value in INTERNAL_STATUS_LABELS:
                clean[key] = value
        elif key == "assignee":
            if value == "unassigned" or (value.isdigit() and len(value) <= 32):
                clean[key] = value
        elif key == "sort":
            clean[key] = value if value in QUEUE_SORTS else "aging"
        elif key in {"per_page", "page_size"}:
            try:
                size = int(value)
            except (TypeError, ValueError):
                continue
            if size in PAGE_SIZES:
                clean["page_size"] = size
        else:
            limit = {"q": 160, "client": 300, "project": 300, "provider": 80, "uf": 100,
                     "priority": 160, "reason": 250}.get(key, 300)
            if value:
                clean[key] = value[:limit]
    return clean


def otrs_ticket_url(source_ticket_id):
    """Build the official AgentTicketZoom URL from the mirrored OTRS ticket.id."""
    try:
        ticket_id = int(source_ticket_id)
    except (TypeError, ValueError):
        return ""
    if ticket_id <= 0:
        return ""
    base = str(getattr(settings, "OTRS_WEB_BASE_URL", "") or "").strip().rstrip("?")
    try:
        parsed = urlsplit(base)
    except ValueError:
        return ""
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        return ""
    return f"{base}?Action=AgentTicketZoom;TicketID={ticket_id}"


def otrs_queue_url():
    value = str(getattr(settings, "OTRS_NOC_ATACADO_QUEUE_URL", "") or "").strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.username or parsed.password or parsed.fragment):
        return ""
    return value


class TrackingConflict(Exception):
    """Optimistic revision or an already assigned ticket prevented the update."""


class TicketTrackingService:
    FIELDS = ("internal_status", "assigned_to", "internal_priority", "next_action_at")

    @classmethod
    def update(cls, *, request, ticket, changes, revision, assume=False, unassign=False):
        if set(changes) - set(cls.FIELDS):
            raise ValueError("Campo de acompanhamento inválido.")
        if assume and unassign:
            raise ValueError("A ação não pode assumir e desassumir ao mesmo tempo.")
        if assume and changes != {"assigned_to": request.user}:
            raise ValueError("A ação de assumir só pode atribuir o chamado ao usuário atual.")
        if unassign and changes != {"assigned_to": None}:
            raise ValueError("A ação de desassumir só pode remover o responsável atual.")
        try:
            with transaction.atomic():
                tracking = TicketTracking.objects.select_for_update().filter(
                    ticket_external_id=ticket.external_id,
                ).first()
                current_revision = tracking.revision if tracking else 0
                if revision != current_revision:
                    raise TrackingConflict("O acompanhamento mudou. Recarregue o chamado.")
                old = {
                    "internal_status": tracking.internal_status if tracking else TicketTracking.InternalStatus.OPEN,
                    "assigned_to": tracking.assigned_to if tracking else None,
                    "internal_priority": tracking.internal_priority if tracking else "",
                    "next_action_at": tracking.next_action_at if tracking else None,
                }
                if assume and old["assigned_to"] and old["assigned_to"].pk != request.user.pk:
                    raise TrackingConflict("Outro analista já assumiu este chamado.")
                if unassign and (not old["assigned_to"] or old["assigned_to"].pk != request.user.pk):
                    raise TrackingConflict("Somente o responsável atual pode desassumir este chamado.")
                changed = [field for field in cls.FIELDS if field in changes and old[field] != changes[field]]
                if not changed:
                    return tracking, False
                if tracking is None:
                    tracking = TicketTracking(ticket_external_id=ticket.external_id)
                previous = {field: _display_value(field, old[field]) for field in changed}
                for field in changed:
                    setattr(tracking, field, changes[field])
                tracking.updated_by = request.user
                tracking.revision = current_revision + 1
                tracking.save()
                for field in changed:
                    TicketTrackingEvent.objects.create(
                        tracking=tracking,
                        event_type=TicketTrackingEvent.EventType.FIELD_CHANGED,
                        field_name=field,
                        old_value=previous[field],
                        new_value=(
                            "Responsável removido"
                            if unassign and field == "assigned_to"
                            else _display_value(field, changes[field])
                        ),
                        actor=request.user,
                    )
                AuditService.record(
                    request=request,
                    category="operational_ticket",
                    action=("tracking_assumed" if assume else "tracking_unassigned" if unassign else "tracking_updated"),
                    description=(
                        "Chamado assumido para acompanhamento local." if assume else
                        "Responsável removido do acompanhamento local." if unassign else
                        "Acompanhamento local de chamado atualizado."
                    ),
                    module="chamados",
                    metadata={"ticket_external_id": ticket.external_id, "fields": changed},
                )
                return tracking, True
        except IntegrityError as exc:
            raise TrackingConflict("Outro analista atualizou este chamado. Recarregue e tente novamente.") from exc


def _display_value(field, value):
    if field == "internal_status":
        return dict(TicketTracking.InternalStatus.choices).get(value, value or "")
    if field == "assigned_to":
        return (value.get_full_name() or value.get_username()) if value else "Não atribuído"
    if field == "internal_priority":
        return dict(TicketTracking.InternalPriority.choices).get(value, value or "Sem prioridade")
    if field == "next_action_at":
        return timezone.localtime(value).strftime("%d/%m/%Y %H:%M") if value else "Não definida"
    return str(value or "")


def _display_city(value):
    """Normalize whitespace for display without changing the CMDB mirror value."""
    return " ".join(str(value or "").split())


def _ticket_tracking(tracking=None):
    return {
        "record": tracking,
        "internal_status": tracking.internal_status if tracking else TicketTracking.InternalStatus.OPEN,
        "internal_status_label": INTERNAL_STATUS_LABELS[
            tracking.internal_status if tracking else TicketTracking.InternalStatus.OPEN
        ],
        "assigned_to": tracking.assigned_to if tracking else None,
        "assigned_to_id": tracking.assigned_to_id if tracking else None,
        "internal_priority": tracking.internal_priority if tracking else "",
        "internal_priority_label": INTERNAL_PRIORITY_LABELS.get(tracking.internal_priority, "") if tracking else "",
        "next_action_at": tracking.next_action_at if tracking else None,
        "revision": tracking.revision if tracking else 0,
    }


def _links_for_rows(relations, recurrent_link_ids):
    by_ticket = {}
    seen = set()
    for relation in relations:
        link = relation.cmdb_link
        if not link or (relation.ticket_id, relation.config_item_id) in seen:
            continue
        seen.add((relation.ticket_id, relation.config_item_id))
        operator = link.operator
        item = {
            "id": link.pk,
            "config_item_id": link.source_config_item_id,
            "number": link.source_number or "",
            "designation": link.designation or link.name or link.source_number or link.source_config_item_id,
            "client": link.contracting_client or "",
            "project": link.business_project or "",
            "provider": provider_label(link),
            "provider_config_item_id": operator.source_config_item_id if operator else "",
            "city": _display_city(link.city),
            "state": str(link.state or "").strip(),
            "deployment_state": link.deployment_state or "",
            "incident_state": link.incident_state or "",
            "recurrent": link.pk in recurrent_link_ids,
        }
        by_ticket.setdefault(relation.ticket_id, []).append(item)

    for rows in by_ticket.values():
        rows.sort(key=lambda row: (row["designation"].casefold(), row["config_item_id"]))
    return by_ticket


def recurrent_link_ids_for_user(user, scoped_links, now):
    recurrence_tickets = TicketQueryService.base(user, links=scoped_links).filter(
        business_status__in=VALID_STATES,
        is_present_in_queue=True,
        source_created_at__gte=now - timedelta(days=RECURRENCE_DAYS),
        source_created_at__lte=now,
    )
    recurrence_rows = OTRSTicketConfigItem.objects.filter(
        is_present_in_source=True,
        cmdb_link__in=scoped_links,
        ticket_id__in=recurrence_tickets.values("pk"),
    ).values("cmdb_link_id").annotate(total=Count("ticket_id", distinct=True)).filter(total__gte=2)
    return set(recurrence_rows.values_list("cmdb_link_id", flat=True))


class TicketQueueService:
    """Build the operational queue from scope-filtered local mirrors."""

    @classmethod
    def build(cls, user, params):
        now = timezone.now()
        aging_cutoff = now - timedelta(hours=24)
        filters = sanitize_queue_filters(params)
        filters = {
            "q": "", "client": "", "project": "", "provider": "", "uf": "",
            "otrs_status": "", "arion_status": "", "assignee": "", "priority": "", "reason": "",
            "recurrent": False, "over_24h": False, "tab": "all", "sort": "aging", **filters,
        }
        page_size = filters.get("page_size", 50)

        scoped_links = LinkQueryService.base(user)
        scoped_tickets = TicketQueryService.business_open(user, links=scoped_links)
        related_links = ticket_link_relations(scoped_tickets, scoped_links)

        recurrent_link_ids = recurrent_link_ids_for_user(user, scoped_links, now)

        matched_links = scoped_links
        if filters["client"]:
            matched_links = matched_links.filter(contracting_client=filters["client"])
        if filters["project"]:
            matched_links = matched_links.filter(business_project=filters["project"])
        if filters["provider"]:
            matched_links = matched_links.filter(operator__source_config_item_id=filters["provider"])
        if filters["uf"]:
            matched_links = matched_links.filter(state__iexact=filters["uf"])

        ticket_query = scoped_tickets.annotate(
            _tracking_status=Subquery(
                TicketTracking.objects.filter(ticket_external_id=OuterRef("external_id"))
                .values("internal_status")[:1]
            ),
            _tracking_assignee_id=Subquery(
                TicketTracking.objects.filter(ticket_external_id=OuterRef("external_id"))
                .values("assigned_to_id")[:1]
            ),
        )
        if any((filters["client"], filters["project"], filters["provider"], filters["uf"])):
            matching_ticket_ids = related_links.filter(cmdb_link__in=matched_links).values("ticket_id")
            ticket_query = ticket_query.filter(pk__in=matching_ticket_ids)

        if filters["q"]:
            query = filters["q"]
            ticket_match = (
                Q(ticket_number__icontains=query)
                | Q(title__icontains=query)
                | Q(external_state_name__icontains=query)
                | Q(priority_name__icontains=query)
                | Q(closing_code_raw__icontains=query)
            )
            if query.isdigit():
                ticket_match |= Q(external_id=int(query))
            link_match = related_links.filter(
                Q(cmdb_link__designation__icontains=query)
                | Q(cmdb_link__name__icontains=query)
                | Q(cmdb_link__source_config_item_id__icontains=query)
                | Q(cmdb_link__source_number__icontains=query)
                | Q(cmdb_link__business_id__icontains=query)
                | Q(cmdb_link__operator__name__icontains=query)
                | Q(cmdb_link__contracting_client__icontains=query)
                | Q(cmdb_link__contracting_client_raw__icontains=query)
                | Q(cmdb_link__business_project__icontains=query)
                | Q(cmdb_link__city__icontains=query)
                | Q(cmdb_link__state__icontains=query)
            ).values("ticket_id")
            ticket_query = ticket_query.filter(ticket_match | Q(pk__in=link_match))

        if filters["arion_status"] == TicketTracking.InternalStatus.OPEN:
            ticket_query = ticket_query.filter(
                Q(_tracking_status__isnull=True)
                | Q(_tracking_status=TicketTracking.InternalStatus.OPEN)
            )
        elif filters["arion_status"]:
            ticket_query = ticket_query.filter(_tracking_status=filters["arion_status"])

        if filters["assignee"] == "unassigned":
            ticket_query = ticket_query.filter(_tracking_assignee_id__isnull=True)
        elif filters["assignee"].isdigit():
            ticket_query = ticket_query.filter(_tracking_assignee_id=int(filters["assignee"]))
        if filters["priority"]:
            ticket_query = ticket_query.filter(priority_name=filters["priority"])
        if filters["reason"]:
            ticket_query = ticket_query.filter(
                closing_code_raw__in=ClosingReasonCanonicalizer.raw_values(filters["reason"])
            )
        if filters["over_24h"]:
            ticket_query = ticket_query.filter(source_created_at__lte=aging_cutoff)
        if filters["recurrent"]:
            recurrent_tickets = related_links.filter(
                cmdb_link_id__in=recurrent_link_ids,
            ).values("ticket_id")
            ticket_query = ticket_query.filter(pk__in=recurrent_tickets)

        # Counters share the active backend queryset and are not derived from the current page.
        counts_qs = ticket_query
        count_rows = counts_qs.values(
            "business_status", "_tracking_assignee_id",
        ).annotate(total=Count("pk"))
        status_counts = {status: 0 for status in ACTIVE_STATES}
        mine_count = 0
        unassigned_count = 0
        active_count = 0
        for item in count_rows:
            status = item["business_status"]
            total = item["total"]
            status_counts[status] += total
            active_count += total
            if item["_tracking_assignee_id"] == user.pk:
                mine_count += total
            if item["_tracking_assignee_id"] is None:
                unassigned_count += total

        if filters["otrs_status"]:
            ticket_query = ticket_query.filter(business_status=filters["otrs_status"])

        if filters["tab"] == "mine":
            ticket_query = ticket_query.filter(_tracking_assignee_id=user.pk)
        elif filters["tab"] == "unassigned":
            ticket_query = ticket_query.filter(_tracking_assignee_id__isnull=True)
        elif filters["tab"] in ACTIVE_STATES:
            ticket_query = ticket_query.filter(business_status=filters["tab"])

        ticket_query = cls._sort(ticket_query, filters["sort"], scoped_links, related_links, aging_cutoff)
        paginator = Paginator(ticket_query, page_size)
        page = paginator.get_page(params.get("page", 1))
        page_tickets = list(page.object_list)
        page_ids = [ticket.pk for ticket in page_tickets]
        tracking_by_external_id = {
            row.ticket_external_id: row for row in TicketTracking.objects.filter(
                ticket_external_id__in=[ticket.external_id for ticket in page_tickets],
            ).select_related("assigned_to")
        }
        page_relations = list(
            related_links.filter(ticket_id__in=page_ids)
            .select_related("ticket", "cmdb_link", "cmdb_link__operator")
            .order_by("ticket_id", "cmdb_link__designation", "cmdb_link__source_config_item_id", "pk")
        ) if page_ids else []
        links_by_ticket = _links_for_rows(page_relations, recurrent_link_ids)

        rows = []
        for ticket in page_tickets:
            linked = links_by_ticket.get(ticket.pk, [])
            unique_clients = list(dict.fromkeys(item["client"] for item in linked if item["client"]))
            unique_projects = list(dict.fromkeys(item["project"] for item in linked if item["project"]))
            unique_providers = list(dict.fromkeys(item["provider"] for item in linked if item["provider"]))
            primary = linked[0] if linked else {}
            tracking = _ticket_tracking(tracking_by_external_id.get(ticket.external_id))
            created = ticket.source_created_at
            elapsed = max(timedelta(0), now - created) if created else None
            aged = bool(elapsed and elapsed > timedelta(hours=24))
            rows.append({
                "id": ticket.pk,
                "external_id": ticket.external_id,
                "ticket_number": ticket.ticket_number,
                "otrs_url": otrs_ticket_url(ticket.external_id),
                "title": ticket.title,
                "source_created_at": created,
                "age_label": format_age(elapsed),
                "over_24h": aged,
                "business_status": ticket.business_status,
                "status_label": STATUS_LABELS[ticket.business_status],
                "status_class": STATUS_CLASSES[ticket.business_status],
                "priority": ticket.priority_name or "Não informada",
                "client": unique_clients[0] if unique_clients else "Não identificado",
                "client_more": max(0, len(unique_clients) - 1),
                "project": unique_projects[0] if unique_projects else "Não identificado",
                "project_more": max(0, len(unique_projects) - 1),
                "designation": primary.get("designation", "Link não identificado"),
                "config_item_id": primary.get("config_item_id", ""),
                "links_count": len(linked),
                "provider": unique_providers[0] if unique_providers else "Não identificado",
                "provider_more": max(0, len(unique_providers) - 1),
                "city": primary.get("city", ""),
                "state": primary.get("state", ""),
                "recurrent": any(item["recurrent"] for item in linked),
                "tracking": tracking,
                "tracking_class": tracking["internal_status"].lower(),
                "assigned_to_self": tracking["assigned_to_id"] == user.pk,
            })

        affected_count = count_affected_links(counts_qs, scoped_links)
        aged_count = counts_qs.filter(source_created_at__lte=aging_cutoff).count()
        filter_options = cls._options(scoped_tickets, related_links)
        return {
            "rows": rows,
            "page": page,
            "paginator": paginator,
            "filters": filters,
            "page_size": page_size,
            "saved_filter_values": sanitize_queue_filters({**filters, "page_size": page_size}),
            "status_counts": status_counts,
            "tab_counts": {"all": active_count, **status_counts, "mine": mine_count, "unassigned": unassigned_count},
            "active_count": active_count,
            "mine_count": mine_count,
            "unassigned_count": unassigned_count,
            "affected_count": affected_count,
            "aged_count": aged_count,
            "recurrent_link_count": len(recurrent_link_ids),
            "recurrence_days": RECURRENCE_DAYS,
            "filter_options": filter_options,
            "available_sort": (
                ("aging", "Maior aging"), ("oldest", "Mais antigos"),
                ("newest", "Mais recentes"), ("provider", "Provedor"),
                ("project", "Projeto"), ("uf", "UF"),
            ),
            "internal_statuses": TicketTracking.InternalStatus.choices,
            "internal_status_labels": dict(TicketTracking.InternalStatus.choices),
            "internal_priorities": TicketTracking.InternalPriority.choices,
            "status_choices": tuple((value, STATUS_LABELS[value]) for value in ACTIVE_STATES),
            "now": now,
            "aging_cutoff": aging_cutoff,
        }

    @staticmethod
    def _sort(queryset, sort, scoped_links, related_links, aging_cutoff):
        if sort == "oldest":
            return queryset.order_by(F("source_created_at").asc(nulls_last=True), "ticket_number", "pk")
        if sort == "newest":
            return queryset.order_by(F("source_created_at").desc(nulls_last=True), "ticket_number", "pk")
        if sort in {"provider", "project", "uf"}:
            field = {
                "provider": "cmdb_link__operator__name",
                "project": "cmdb_link__business_project",
                "uf": "cmdb_link__state",
            }[sort]
            sort_value = related_links.filter(ticket_id=OuterRef("pk"), cmdb_link__in=scoped_links).order_by(
                field, "cmdb_link_id",
            ).values(field)[:1]
            return queryset.annotate(_scope_sort=Subquery(sort_value)).order_by(
                "_scope_sort", F("source_created_at").asc(nulls_last=True), "ticket_number", "pk",
            )
        return queryset.annotate(
            _aging_order=Case(
                When(source_created_at__lte=aging_cutoff, then=Value(0)),
                default=Value(1), output_field=IntegerField(),
            ),
        ).order_by(
            "_aging_order", F("source_created_at").asc(nulls_last=True), "ticket_number", "pk",
        )

    @staticmethod
    def _options(scoped_tickets, related_links):
        dimensions = list(related_links.values_list(
            "cmdb_link__contracting_client", "cmdb_link__business_project",
            "cmdb_link__operator__source_config_item_id", "cmdb_link__operator__name", "cmdb_link__state",
        ).distinct())
        clients = sorted({row[0] for row in dimensions if row[0]}, key=str.casefold)
        projects = sorted({row[1] for row in dimensions if row[1]}, key=str.casefold)
        providers = sorted(
            ({"value": row[2], "label": row[3]} for row in dimensions if row[2] and row[3]),
            key=lambda row: row["label"].casefold(),
        )
        ufs = sorted({row[4] for row in dimensions if row[4]}, key=str.casefold)
        ticket_options = list(scoped_tickets.values_list("priority_name", "closing_code_raw").distinct())
        priorities = sorted({row[0] for row in ticket_options if row[0]}, key=str.casefold)
        reasons = ClosingReasonCanonicalizer.options(row[1] for row in ticket_options if row[1])
        assignee_ids = TicketTracking.objects.filter(
            ticket_external_id__in=scoped_tickets.values("external_id"),
            assigned_to__isnull=False,
        ).order_by().values("assigned_to_id")
        assignees = list(get_user_model().objects.filter(pk__in=assignee_ids, is_active=True))
        assignees.sort(key=lambda user: _display_value("assigned_to", user).casefold())
        return {
            "clients": clients,
            "projects": projects,
            "providers": providers,
            "ufs": ufs,
            "priorities": priorities,
            "reasons": reasons,
            "assignees": [{
                "value": user.pk,
                "label": _display_value("assigned_to", user),
            } for user in assignees],
        }


def format_age(elapsed):
    if elapsed is None:
        return "Sem data de criação"
    total_minutes = int(elapsed.total_seconds() // 60)
    days, rem = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}min" if minutes else f"{hours}h"
    return f"{minutes}min"
