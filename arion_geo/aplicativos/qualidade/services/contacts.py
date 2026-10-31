"""Scoped directory and escalation contact services for Quality."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from aplicativos.auditoria.services import AuditService
from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.services.project_names import canonical_project_name, display_project_name, project_alias_groups
from aplicativos.integracoes.services.semantics import UNCLASSIFIED, provider_names_for_values

from ..models import QualityContact


CONTACT_TYPES = {
    "QUALIDADE": "Qualidade",
    "NOC": "NOC",
    "ENGENHARIA": "Engenharia",
    "COORDENACAO": "Coordenação",
    "GERENCIA": "Gerência",
    "FINANCEIRO": "Financeiro",
    "OUTRO": "Outro",
}
ESCALATION_LEVELS = {1: "R1", 2: "R2", 3: "R3", 4: "R4", 5: "R5+"}
CONTACT_PAGE_SIZES = (25, 50, 100)


def contacts_for_user(user, queryset=None):
    queryset = queryset if queryset is not None else QualityContact.objects.all()
    if user.is_authenticated and user.is_superuser or DataScopeService.is_global(user):
        return queryset
    operator_ids = DataScopeService.allowed_operator_ids(user)
    project_ids = DataScopeService.allowed_project_ids(user)
    if not operator_ids or not project_ids:
        return queryset.none()
    operator_names = Operadora.objects.filter(pk__in=operator_ids).values_list("name", flat=True)
    allowed_project_names = list(Projeto.objects.filter(pk__in=project_ids).values_list("name", flat=True))
    visible_projects = set(allowed_project_names)
    project_groups = project_alias_groups([
        *allowed_project_names,
        *queryset.exclude(project="").values_list("project", flat=True),
    ])
    allowed_project_keys = {canonical_project_name(name) for name in allowed_project_names}
    for project_key in allowed_project_keys:
        visible_projects.update(project_groups.get(project_key, ()))
    # Custom scope is an intersection: contacts need both an allowed client and project.
    return queryset.filter(client__in=operator_names, project__in=visible_projects)


def get_quality_contacts(user, params):
    scoped = contacts_for_user(user)
    selected = {
        key: str(params.get(key, "")).strip()[:160]
        for key in ("q", "company", "region", "client", "project", "contact_type", "level", "status")
    }
    if selected["contact_type"] not in CONTACT_TYPES:
        selected["contact_type"] = ""
    if selected["company"].isdigit():
        selected["company"] = provider_names_for_values([selected["company"]]).get(selected["company"], UNCLASSIFIED)
    if selected["level"]:
        try:
            selected["level"] = str(int(selected["level"]))
        except (TypeError, ValueError):
            selected["level"] = ""
        if selected["level"] not in {str(value) for value in ESCALATION_LEVELS}:
            selected["level"] = ""
    if selected["status"] not in {"active", "inactive"}:
        selected["status"] = ""
    raw_projects = list(scoped.exclude(project="").values_list("project", flat=True))
    project_groups = project_alias_groups(raw_projects)
    project_options = sorted({display_project_name(value) for value in raw_projects}, key=str.casefold)
    selected_project_key = canonical_project_name(selected["project"])
    if selected_project_key not in {canonical_project_name(value) for value in project_options}:
        selected["project"] = ""
        selected_project_key = ""
    else:
        selected["project"] = display_project_name(selected_project_key)

    filtered = scoped
    if selected["q"]:
        term = selected["q"]
        filtered = filtered.filter(
            Q(name__icontains=term) | Q(role__icontains=term) | Q(company__icontains=term)
            | Q(region__icontains=term) | Q(client__icontains=term) | Q(project__icontains=term)
            | Q(email__icontains=term) | Q(phone__icontains=term) | Q(teams__icontains=term)
        )
    if selected["company"]:
        raw_companies = scoped.exclude(company="").values_list("company", flat=True).distinct()
        resolved_companies = provider_names_for_values(raw_companies)
        matching_raw_values = [raw for raw, label in resolved_companies.items() if label.casefold() == selected["company"].casefold()]
        filtered = filtered.filter(Q(company__iexact=selected["company"]) | Q(company__in=matching_raw_values))
    for field in ("region", "client", "contact_type"):
        if selected[field]:
            filtered = filtered.filter(**{f"{field}__iexact": selected[field]})
    if selected_project_key:
        filtered = filtered.filter(project__in=project_groups.get(selected_project_key, ()))
    if selected["level"]:
        filtered = filtered.filter(escalation_level=int(selected["level"]))
    if selected["status"]:
        filtered = filtered.filter(active=selected["status"] == "active")

    try:
        page_size = int(params.get("per_page", 25))
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in CONTACT_PAGE_SIZES:
        page_size = 25

    from django.core.paginator import Paginator

    page_obj = Paginator(filtered.select_related().order_by("company", "region", "client", "project", "escalation_level", "name"), page_size).get_page(params.get("page", 1))
    company_names = provider_names_for_values([item.company for item in page_obj.object_list if item.company])
    for contact in page_obj.object_list:
        contact.contact_type_label = CONTACT_TYPES.get(contact.contact_type, contact.contact_type or "Outro")
        contact.escalation_label = ESCALATION_LEVELS.get(contact.escalation_level, "R1")
        contact.company_display = company_names.get(contact.company, contact.company or "—")
        contact.project_display = display_project_name(contact.project) or "Todos os projetos"

    def options(field):
        return list(scoped.exclude(**{field: ""}).order_by(field).values_list(field, flat=True).distinct())

    company_values = options("company")
    company_names = provider_names_for_values(company_values)
    company_options = sorted({company_names.get(value, value) for value in company_values}, key=str.casefold)

    return {
        "contacts": page_obj.object_list,
        "page_obj": page_obj,
        "selected": selected,
        "page_size": page_size,
        "total_count": filtered.count(),
        "active_count": scoped.filter(active=True).count(),
        "inactive_count": scoped.filter(active=False).count(),
        "external_count": scoped.filter(active=True).exclude(company__iexact="ARION").count(),
        "internal_count": scoped.filter(active=True, company__iexact="ARION").count(),
        "filter_options": {
            "company": company_options,
            "region": options("region"),
            "client": options("client"),
            "project": project_options,
            "contact_type": [(value, label) for value, label in CONTACT_TYPES.items()],
            "level": [(str(value), label) for value, label in ESCALATION_LEVELS.items()],
        },
    }


def recommended_contacts_for_order(user, *, client, project, provider_name, region, financial=False):
    contacts = contacts_for_user(user).filter(active=True)
    context_match = Q(client="") | Q(client__iexact=client) if client else Q(client="")
    if project:
        project_values = list(contacts.exclude(project="").values_list("project", flat=True))
        project_values.append(project)
        project_aliases = project_alias_groups(project_values).get(canonical_project_name(project), (project,))
        context_match &= Q(project="") | Q(project__in=project_aliases)
    else:
        context_match &= Q(project="")
    context_match &= (Q(region="") | Q(region__iexact=region)) if region else Q(region="")
    company_match = Q(company="")
    if provider_name:
        company_match |= Q(company__iexact=provider_name)
        scoped_company_values = contacts.exclude(company="").values_list("company", flat=True).distinct()
        resolved_companies = provider_names_for_values(scoped_company_values)
        matching_company_ids = [raw for raw, name in resolved_companies.items() if name.casefold() == provider_name.casefold()]
        company_match |= Q(company__in=matching_company_ids)
    if financial:
        company_match |= Q(contact_type="FINANCEIRO", company__iexact="ARION")
    matches = list(contacts.filter(context_match, company_match).order_by("escalation_level", "name")[:100])

    def rank(contact):
        company_score = int(bool(provider_name and contact.company.casefold() == provider_name.casefold()))
        client_score = int(bool(client and contact.client.casefold() == client.casefold()))
        project_score = int(bool(project and canonical_project_name(contact.project) == canonical_project_name(project)))
        region_score = int(bool(region and contact.region.casefold() == region.casefold()))
        finance_score = int(bool(financial and contact.contact_type == "FINANCEIRO"))
        return (-finance_score, -company_score, -client_score, -project_score, -region_score, contact.escalation_level, contact.name.casefold())

    matches.sort(key=rank)
    names = provider_names_for_values([contact.company for contact in matches if contact.company])
    for contact in matches[:5]:
        contact.contact_type_label = CONTACT_TYPES.get(contact.contact_type, contact.contact_type or "Outro")
        contact.escalation_label = ESCALATION_LEVELS.get(contact.escalation_level, "R1")
        contact.company_display = names.get(contact.company, contact.company or "—")
    return matches[:5]


@transaction.atomic
def save_quality_contact(*, contact=None, actor, request=None, **fields):
    existing = contact is not None
    contact = contact or QualityContact()
    if not _contact_fields_in_scope(actor, fields.get("client", contact.client), fields.get("project", contact.project)):
        raise ValidationError("Informe cliente e projeto dentro do seu escopo de dados.")
    if fields.get("company", "").strip().isdigit():
        provider = provider_names_for_values([fields["company"].strip()]).get(fields["company"].strip(), UNCLASSIFIED)
        if provider == UNCLASSIFIED:
            raise ValidationError("Informe o nome operacional da empresa/provedor, não um ID numérico.")
        fields["company"] = provider
    for key, value in fields.items():
        setattr(contact, key, value)
    contact.save()
    action = "QUALITY_CONTACT_UPDATED" if existing else "QUALITY_CONTACT_CREATED"
    description = "Contato de Qualidade atualizado." if existing else "Contato de Qualidade criado."
    AuditService.record(
        request=request, user=actor, category="QUALITY", action=action, module="Qualidade",
        description=description, obj=contact,
        metadata={"contact_id": contact.pk, "contact_type": contact.contact_type, "active": contact.active},
    )
    return contact


@transaction.atomic
def set_quality_contact_active(*, contact, active, actor, request=None):
    if contact.active == bool(active):
        return contact, False
    contact.active = bool(active)
    contact.save(update_fields=("active", "updated_at"))
    action = "QUALITY_CONTACT_ACTIVATED" if contact.active else "QUALITY_CONTACT_DEACTIVATED"
    description = "Contato de Qualidade reativado." if contact.active else "Contato de Qualidade desativado."
    AuditService.record(
        request=request, user=actor, category="QUALITY", action=action, module="Qualidade",
        description=description, obj=contact,
        metadata={"contact_id": contact.pk, "contact_type": contact.contact_type, "active": contact.active},
    )
    return contact, True


def _contact_fields_in_scope(user, client, project):
    if user.is_authenticated and user.is_superuser or DataScopeService.is_global(user):
        return True
    if not client or not project:
        return False
    operator_ids = DataScopeService.allowed_operator_ids(user) or ()
    project_ids = DataScopeService.allowed_project_ids(user) or ()
    allowed_project_names = Projeto.objects.filter(pk__in=project_ids).values_list("name", flat=True)
    return (
        Operadora.objects.filter(pk__in=operator_ids, name__iexact=client).exists()
        and any(canonical_project_name(name) == canonical_project_name(project) for name in allowed_project_names)
    )
