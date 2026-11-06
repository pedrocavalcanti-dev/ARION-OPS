"""Shared local-only querysets for operational analytics consumers."""
from datetime import date

from django.db.models import Case, Count, DateTimeField, Exists, F, OuterRef, Q, Value, When
from django.db.models.functions import Coalesce

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicket, OTRSTicketConfigItem
from aplicativos.integracoes.otrs_queues import CANCELLATION_QUEUE_NAMES, NOC_QUEUE_NAME
from aplicativos.integracoes.services.otrs_status import (
    BACKLOG_STATUSES, BUSINESS_OPEN_STATUSES, OPERATIONAL_VALID_STATUSES,
    RESOLVED_STATUS, TERMINAL_STATUSES, normalize_business_status,
)
from .sync_common import normalized_name


OPERATIONAL_RESOLUTION_FIELD = "operational_resolution_at"
SOURCE_RESOLUTION_STATES = {"resolvido", "merged"}
SOURCE_CLOSED_STATES = {"closed successful", "encerrado com sucesso", "encerrado com viabilidade"}


def operational_resolution_expression(prefix=""):
    """Resolve an effective resolution without mistaking a later close for it.

    The source's explicit ``resolvido`` state can use the latest matching
    StateUpdate for a reopened/current resolution cycle. In close-success
    states, only the approved ``ticketResolvido`` date is safe: the terminal
    StateUpdate can describe administrative closure instead.
    """
    resolved = F(f"{prefix}resolved_at")
    terminal = F(f"{prefix}terminal_state_at")
    current_cycle = Case(
        When(**{f"{prefix}terminal_state_at__gt": resolved}, then=terminal),
        default=Coalesce(resolved, terminal),
        output_field=DateTimeField(),
    )
    resolved_status = Q(**{f"{prefix}business_status__in": (RESOLVED_STATUS, "CLOSED")})
    explicit_resolution_state = Q()
    explicit_closed_state = Q()
    for state_name in SOURCE_RESOLUTION_STATES:
        explicit_resolution_state |= Q(**{f"{prefix}external_state_name__iexact": state_name})
    for state_name in SOURCE_CLOSED_STATES:
        explicit_closed_state |= Q(**{f"{prefix}external_state_name__iexact": state_name})
    return Case(
        When(resolved_status & explicit_resolution_state, then=current_cycle),
        When(resolved_status & explicit_closed_state, then=resolved),
        When(resolved_status & Q(**{f"{prefix}resolved_at__isnull": False}), then=resolved),
        When(resolved_status, then=terminal),
        default=Value(None, output_field=DateTimeField()),
        output_field=DateTimeField(),
    )


def operational_resolution_at(ticket):
    """Python equivalent of operational_resolution_expression for loaded tickets."""
    status = normalize_business_status(getattr(ticket, "business_status", "UNKNOWN"))
    if status != RESOLVED_STATUS:
        return None
    terminal = getattr(ticket, "terminal_state_at", None)
    resolved = getattr(ticket, "resolved_at", None)
    state = normalized_name(getattr(ticket, "external_state_name", ""))
    if state in SOURCE_CLOSED_STATES:
        return resolved
    if state in SOURCE_RESOLUTION_STATES:
        dates = [value for value in (resolved, terminal) if value]
        return max(dates) if dates else None
    return resolved or terminal


def operational_resolution_source(ticket):
    """Describe which trustworthy OTRS source supplied the current resolution."""
    resolved = getattr(ticket, "resolved_at", None)
    terminal = getattr(ticket, "terminal_state_at", None)
    state = normalized_name(getattr(ticket, "external_state_name", ""))
    if normalize_business_status(getattr(ticket, "business_status", "UNKNOWN")) != RESOLVED_STATUS:
        return ""
    if state in SOURCE_CLOSED_STATES:
        return "Dynamic Field ticketResolvido" if resolved else ""
    if state in SOURCE_RESOLUTION_STATES and terminal and (not resolved or terminal > resolved):
        return "ticket_history StateUpdate do estado atual"
    if resolved:
        return "Dynamic Field ticketResolvido"
    return "ticket_history StateUpdate terminal" if terminal else ""


class LinkQueryService:
    @staticmethod
    def base(user=None):
        links = CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator")
        return DataScopeService.filter_queryset(user, links) if user is not None else links


class TicketQueryService:
    QUEUE_NAME = NOC_QUEUE_NAME
    CANCELLATION_QUEUE_NAMES = CANCELLATION_QUEUE_NAMES

    @classmethod
    def noc_tickets(cls):
        """Current operational ticket universe, resolved by the configured queue name."""
        return OTRSTicket.objects.filter(queue_name=cls.QUEUE_NAME, is_present_in_queue=True)

    @staticmethod
    def base(user=None, *, links=None, all_queues=False):
        # Keep the established NOC universe as the default for existing callers.
        # Cockpit contexts that explicitly combine operational processes can ask
        # for all mirrored queues while retaining the same Data Scope boundary.
        tickets = (OTRSTicket.objects.filter(is_present_in_queue=True)
                   if all_queues else TicketQueryService.noc_tickets())
        if user is None or DataScopeService.is_global(user):
            return tickets
        allowed = links if links is not None else LinkQueryService.base(user)
        related = OTRSTicketConfigItem.objects.filter(
            ticket_id=OuterRef("pk"), is_present_in_source=True, cmdb_link__in=allowed,
        )
        return tickets.annotate(_has_allowed_link=Exists(related)).filter(_has_allowed_link=True)

    @classmethod
    def business_open(cls, user=None, *, links=None, queryset=None):
        tickets = queryset if queryset is not None else cls.base(user, links=links)
        return tickets.filter(
            queue_name=cls.QUEUE_NAME,
            is_present_in_queue=True,
            business_status__in=BUSINESS_OPEN_STATUSES,
        )


class PeriodService:
    """Interpret explicit creation-date bounds without implying historical state snapshots."""
    @staticmethod
    def parse(params):
        def bound(name):
            value = (params.get(name) or "").strip()
            if not value:
                return None
            try:
                return date.fromisoformat(value)
            except ValueError:
                raise ValueError(f"Data inválida: {name}.") from None
        start, end = bound("date_from"), bound("date_to")
        if start and end and start > end:
            raise ValueError("A data inicial deve ser anterior à data final.")
        return start, end

    @staticmethod
    def parse_month(value):
        try:
            return date.fromisoformat(f"{value}-01")
        except (TypeError, ValueError):
            raise ValueError("Período mensal inválido.") from None

    @staticmethod
    def tickets(queryset, start=None, end=None):
        if start:
            queryset = queryset.filter(source_created_at__date__gte=start)
        if end:
            queryset = queryset.filter(source_created_at__date__lte=end)
        return queryset


def ticket_counts_by_link(user=None):
    links = LinkQueryService.base(user)
    open_tickets = TicketQueryService.business_open(user, links=links)
    relation = OTRSTicketConfigItem.objects.filter(
        cmdb_link_id=OuterRef("pk"), is_present_in_source=True,
        cmdb_link__is_present_in_source=True, ticket_id__in=open_tickets.values("pk"),
    )
    return links.annotate(has_open_ticket=Exists(relation)).annotate(
        open_ticket_count=Count("ticket_relations__ticket_id", filter=Q(
            ticket_relations__is_present_in_source=True,
            ticket_relations__cmdb_link__is_present_in_source=True,
            ticket_relations__ticket_id__in=open_tickets.values("pk"),
        ), distinct=True),
    )


def ticket_link_relations(tickets, links):
    return OTRSTicketConfigItem.objects.filter(
        ticket_id__in=tickets.order_by().values("pk"),
        is_present_in_source=True,
        cmdb_link__is_present_in_source=True,
        cmdb_link__in=links,
    )


def count_affected_links(tickets, links):
    return ticket_link_relations(tickets, links).aggregate(
        total=Count("cmdb_link_id", distinct=True),
    )["total"] or 0
