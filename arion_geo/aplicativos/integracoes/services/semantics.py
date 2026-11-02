"""Business labels for CMDB link attributes; unresolved concepts stay explicit."""
from dataclasses import dataclass
import re

from django.db.models import Q


UNCLASSIFIED = "Não classificado"


@dataclass(frozen=True)
class LinkSemantics:
    project_name: str | None
    project_is_official: bool
    contract_operator_name: str | None
    provider_id: int | None
    provider_name: str
    provider_config_item_id: str
    isp_raw: str

    @classmethod
    def from_link(cls, link, *, provider_lookup=None):
        provider = getattr(link, "operator", None)
        raw = (link.isp_raw or "").strip()
        resolved_from_raw = False
        if not provider and raw:
            if provider_lookup is not None:
                provider = provider_lookup.get(raw)
                if provider is None and raw.isdigit():
                    provider = provider_lookup.get(f"pk:{int(raw)}")
            else:
                from aplicativos.integracoes.models import CMDBOperatorMirror

                provider = CMDBOperatorMirror.objects.filter(
                    source_config_item_id=raw, is_present_in_source=True,
                ).first()
                if provider is None and raw.isdigit():
                    provider = CMDBOperatorMirror.objects.filter(
                        pk=int(raw), is_present_in_source=True,
                    ).first()
            resolved_from_raw = provider is not None
        provider_name = provider.name.strip() if provider and provider.name.strip() else ""
        if not provider_name and raw and not raw.isdigit():
            provider_name = raw
        return cls(
            project_name=(link.business_project.strip() or None),
            project_is_official=bool(link.business_project and link.business_identity_confidence in {"HIGH", "MEDIUM"}),
            contract_operator_name=(link.contracting_client.strip() or None),
            provider_id=(provider.pk if provider else (int(raw) if raw.isdigit() else None)),
            provider_name=provider_name or UNCLASSIFIED,
            provider_config_item_id=(
                provider.source_config_item_id if provider
                else (raw if resolved_from_raw or raw.isdigit() else "")
            ),
            isp_raw=raw,
        )


def provider_label(link):
    """Return a human provider name, never a bare ConfigItemID."""
    return LinkSemantics.from_link(link).provider_name


def provider_values_for_links(links):
    """Resolve provider IDs in one CMDB lookup and keep IDs separate from labels."""
    links = list(links)
    raw_values = {str(link.isp_raw or "").strip() for link in links if str(link.isp_raw or "").strip()}
    numeric_ids = {int(value) for value in raw_values if value.isdigit()}
    from aplicativos.integracoes.models import CMDBOperatorMirror

    providers = CMDBOperatorMirror.objects.filter(is_present_in_source=True).filter(
        Q(source_config_item_id__in=raw_values) | Q(pk__in=numeric_ids),
    )
    lookup = {}
    for provider in providers:
        lookup[provider.source_config_item_id] = provider
        lookup[f"pk:{provider.pk}"] = provider
    return {
        link.pk: LinkSemantics.from_link(link, provider_lookup=lookup)
        for link in links
    }


def provider_name_options(queryset):
    """Build clean provider filter options from in-scope links."""
    rows = list(queryset.values_list("isp_raw", "operator__name").distinct())
    raw_values = {str(raw or "").strip() for raw, _name in rows if str(raw or "").strip()}
    raw_names = provider_names_for_values(raw_values)
    names = set()
    for raw, name in rows:
        operator_name = str(name or "").strip()
        if operator_name:
            names.add(operator_name)
        else:
            names.add(raw_names.get(str(raw or "").strip(), UNCLASSIFIED))
    return sorted(names, key=str.casefold)


def provider_names_for_values(values):
    """Resolve CMDB provider IDs in batches while preserving literal source names."""
    raw_values = {str(value or "").strip() for value in values if str(value or "").strip()}
    if not raw_values:
        return {}
    numeric_ids = {int(value) for value in raw_values if value.isdigit()}
    from aplicativos.integracoes.models import CMDBOperatorMirror

    providers = CMDBOperatorMirror.objects.filter(is_present_in_source=True).filter(
        Q(source_config_item_id__in=raw_values) | Q(pk__in=numeric_ids),
    )
    by_config_id = {item.source_config_item_id: item.name.strip() for item in providers if item.name.strip()}
    by_pk = {str(item.pk): item.name.strip() for item in providers if item.name.strip()}
    return {
        raw: by_config_id.get(raw) or by_pk.get(raw) or (raw if not raw.isdigit() else UNCLASSIFIED)
        for raw in raw_values
    }


def provider_filter_queryset(queryset, value):
    """Filter by the displayed provider name while resolving legacy IDs in the mirror."""
    if not value:
        return queryset
    if value == UNCLASSIFIED:
        unresolved = [raw for raw, name in provider_names_for_values(
                queryset.filter(Q(operator__isnull=True) | Q(operator__name=""))
                .values_list("isp_raw", flat=True).distinct(),
            ).items()
            if name == UNCLASSIFIED
        ]
        return queryset.filter(
            (Q(operator__isnull=True) | Q(operator__name="")) & (Q(isp_raw="") | Q(isp_raw__in=unresolved)),
        )
    from aplicativos.integracoes.models import CMDBOperatorMirror

    providers = CMDBOperatorMirror.objects.filter(name=value, is_present_in_source=True)
    config_ids = providers.values("source_config_item_id")
    numeric_values = [str(item) for item in providers.values_list("pk", flat=True)]
    matches = Q(operator__name=value) | Q(isp_raw__in=config_ids)
    if numeric_values:
        matches |= Q(isp_raw__in=numeric_values)
    if not value.isdigit():
        matches |= Q(isp_raw=value)
    return queryset.filter(matches)


def provider_filter_order_queryset(queryset, value):
    """Filter OQs by the provider name shown in the UI, including legacy snapshots."""
    if not value:
        return queryset
    from aplicativos.integracoes.models import CMDBOperatorMirror

    if value == UNCLASSIFIED:
        unresolved = [
            raw for raw, name in provider_names_for_values(
                queryset.exclude(isp_snapshot="").values_list("isp_snapshot", flat=True).distinct(),
            ).items()
            if name == UNCLASSIFIED
        ]
        unresolved_link_raw = [
            raw for raw, name in provider_names_for_values(
                queryset.filter(Q(link__operator__isnull=True) | Q(link__operator__name=""))
                .values_list("link__isp_raw", flat=True).distinct(),
            ).items()
            if name == UNCLASSIFIED
        ]
        return queryset.filter(
            Q(isp_snapshot="") | Q(isp_snapshot__in=unresolved)
            | (Q(link__is_present_in_source=True)
               & (Q(link__operator__isnull=True) | Q(link__operator__name=""))
               & (Q(link__isp_raw="") | Q(link__isp_raw__in=unresolved_link_raw))),
        )

    providers = CMDBOperatorMirror.objects.filter(name=value, is_present_in_source=True)
    config_ids = providers.values("source_config_item_id")
    numeric_values = [str(item) for item in providers.values_list("pk", flat=True)]
    matches = Q(link__is_present_in_source=True, link__operator__name=value) | Q(link__is_present_in_source=True, link__isp_raw=value) | Q(isp_snapshot=value)
    matches |= Q(link__is_present_in_source=True, link__isp_raw__in=config_ids) | Q(isp_snapshot__in=config_ids)
    if numeric_values:
        matches |= Q(link__is_present_in_source=True, link__isp_raw__in=numeric_values) | Q(isp_snapshot__in=numeric_values)
    return queryset.filter(matches)


def provider_name_options_for_orders(queryset):
    """Return only displayable provider names for a scoped OQ queryset."""
    from aplicativos.integracoes.models import CMDBLinkMirror

    links = CMDBLinkMirror.objects.filter(
        source_config_item_id__in=queryset.exclude(link_id__isnull=True).values("link_id"),
        is_present_in_source=True,
    )
    names = set(provider_name_options(links))
    snapshots = list(queryset.exclude(isp_snapshot="").values_list("isp_snapshot", flat=True).distinct())
    names.update(provider_names_for_values(snapshots).values())
    if queryset.filter(isp_snapshot="").exists():
        names.add(UNCLASSIFIED)
    return sorted(names, key=str.casefold)


_INTERNAL_LINK_NUMBER = re.compile(r"^361310\d+$")


def link_designation(link):
    """Return the operational circuit name, never its internal CMDB record number."""
    candidates = (
        link.designation,
        link.circuit,
        link.operator_designation,
        link.identification,
        link.name,
        link.source_number,
    )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value and not _INTERNAL_LINK_NUMBER.fullmatch(value):
            return value
    return "Designação não informada"



def clean_link_designation(value):
    """Keep historical designations readable when a live CMDB link is unavailable."""
    display = str(value or "").strip()
    return display if display and not _INTERNAL_LINK_NUMBER.fullmatch(display) else "Designa\u00e7\u00e3o n\u00e3o informada"
