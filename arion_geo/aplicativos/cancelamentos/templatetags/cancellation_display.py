from django import template

register = template.Library()


@register.filter
def brl(value):
    if value is None:
        return "—"
    return "R$ " + format(value, ",.2f").translate(str.maketrans({",": ".", ".": ","}))


@register.filter
def person_name(user):
    if not user:
        return "Não atribuído"
    return user.get_full_name() or user.get_username()
