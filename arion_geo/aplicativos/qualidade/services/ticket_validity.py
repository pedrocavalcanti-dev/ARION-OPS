"""Shared policy for counting valid OTRS ticket occurrences in Quality."""
from django.db.models import Q

from aplicativos.integracoes.models import CMDBLinkMirror
from aplicativos.integracoes.services.analytics import TicketQueryService
from aplicativos.painel.services.analytics import AnalyticsQueryService

from ..models import QualityOccurrence
from .data_scope import QualityDataScopeService
from aplicativos.configuracoes.data_scope import DataScopeService


DUPLICATE_CLOSING_REASONS = ("Duplicado", "Chamado duplicado", "Ticket duplicado", "Duplicate")


def quality_ticket_sets(user, *, scope_links=None, scoped_orders=None, start=None, end=None):
    """Return mirrored tickets and the established operationally valid subset."""
    scope_links = scope_links if scope_links is not None else DataScopeService.filter_queryset(
        user,
        CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    )
    scoped_orders = scoped_orders if scoped_orders is not None else QualityDataScopeService.orders_for(user)
    tickets = TicketQueryService.base(user, links=scope_links)
    if start is not None:
        tickets = tickets.filter(source_created_at__gte=start)
    if end is not None:
        tickets = tickets.filter(source_created_at__lte=end)
    excluded_ids = QualityOccurrence.objects.filter(
        quality_order__in=scoped_orders,
        excluded=True,
        ticket_external_id_snapshot__isnull=False,
    ).values("ticket_external_id_snapshot")
    valid = tickets.filter(
        business_status__in=AnalyticsQueryService.OPERATIONAL_VALID_STATES,
    ).exclude(
        responsibility_normalized="IMPROCEDENTE",
    ).exclude(
        external_id__in=excluded_ids,
    )
    duplicate_reason = Q()
    for reason in DUPLICATE_CLOSING_REASONS:
        duplicate_reason |= Q(closing_code_raw__iexact=reason)
    valid = valid.exclude(duplicate_reason)
    return {"period_tickets": tickets, "valid_tickets": valid}
