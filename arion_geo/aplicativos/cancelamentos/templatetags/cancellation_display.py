from django import template

from ..services.financial_totals import format_brl
from aplicativos.chamados.services import otrs_ticket_url

register = template.Library()


@register.filter
def cancellation_ticket_url(obj):
    """Use only a source TicketID; the displayed number is never an identifier."""
    return otrs_ticket_url(getattr(obj,"ticket_external_id",None) or getattr(obj,"external_ticket_id",None))


@register.filter
def brl(value):
    return format_brl(value)


@register.filter
def person_name(user):
    if not user:
        return "Não atribuído"
    return user.get_full_name() or user.get_username()
