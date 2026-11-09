from django import template

from ..services.financial_totals import format_brl

register = template.Library()


@register.filter
def brl(value):
    return format_brl(value)


@register.filter
def person_name(user):
    if not user:
        return "Não atribuído"
    return user.get_full_name() or user.get_username()
