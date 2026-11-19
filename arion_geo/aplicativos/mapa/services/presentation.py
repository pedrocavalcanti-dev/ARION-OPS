"""Sanitized client presentation; preserves exact valid incident totals without simulated projections."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from aplicativos.integracoes.services.geography import REGIONS, STATE_BY_UF, region_states
from aplicativos.integracoes.services.analytics import LinkQueryService
from aplicativos.integracoes.services.project_names import project_alias_groups
from aplicativos.integracoes.services.incidents import IncidentRules
from .analytics import MapAnalyticsService, _client_filter, STATE_NAMES, STATE_NAMES_ACCENTED

TECHNICAL = (
    "Massiva", "Rompimento", "Equipamento", "Energia", "Configuração", "Reset remoto",
    "Outros motivos", "Sem classificação",
)
DIRECT_TECHNICAL = ("Massiva", "Rompimento", "Equipamento", "Energia", "Configuração", "Reset remoto")
OTHER_GROUPED_SOURCES = {"Demais motivos técnicos", "Pendência Financeira", "Outros motivos"}
OTHER_MOTIVES_LABEL = "Outros motivos"


def presentation_ufs(user, params, *, extra_ufs=()):
    links = _client_filter(LinkQueryService.base(user), params["client"])
    if params.get("project"):
        aliases = project_alias_groups(links.values_list("business_project", flat=True).distinct())
        links = links.filter(business_project__in=aliases.get(params["project"], ()))
    if params.get("provider"):
        links = links.filter(operator_id=int(params["provider"]))
    names = {str(value).strip().casefold() for value in links.values_list("state", flat=True).distinct() if value}
    extra_set = {str(uf).strip().upper() for uf in extra_ufs if uf}
    return [
        uf for uf in region_states(params.get("region", ""))
        if uf in extra_set or names.intersection({
            uf.casefold(), STATE_NAMES[uf].casefold(), STATE_NAMES_ACCENTED.get(uf, STATE_NAMES[uf]).casefold(),
        })
    ]


def project_distribution(total, weights):
    """Retained only as a deterministic math helper for backward compatibility; never used to fabricate causes."""
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("Total de projeção inválido.")
    try:
        values = [(label, Decimal(str(weight))) for label, weight in weights.items()]
        if not values or any(label not in TECHNICAL or not weight.is_finite() or weight < 0 for label, weight in values):
            raise ValueError
        denominator = sum(weight for _, weight in values)
        if denominator <= 0:
            raise ValueError
    except (AttributeError, InvalidOperation, TypeError, ValueError):
        raise ValueError("Pesos de projeção inválidos.") from None
    exact = [(label, Decimal(total) * weight / denominator) for label, weight in values]
    output = {label: int(value.to_integral_value(rounding=ROUND_FLOOR)) for label, value in exact}
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i][1] - output[exact[i][0]]), i))
    for i in order[:total - sum(output.values())]:
        output[exact[i][0]] += 1
    return output


def sanitize_presentation_causes(category_counts):
    """Preserve true technical causes and group restricted/non-technical causes under 'Outros motivos'.

    Never exposes 'Pendência Financeira' and never fabricates technical causes from financial tickets.
    """
    source = category_counts or {}
    sanitized = {}
    for label in DIRECT_TECHNICAL:
        val = int(source.get(label, 0) or 0)
        if val > 0:
            sanitized[label] = val
    other_total = sum(int(source.get(key, 0) or 0) for key in OTHER_GROUPED_SOURCES)
    if other_total > 0:
        sanitized[OTHER_MOTIVES_LABEL] = other_total
    unclassified = int(source.get("Sem classificação", 0) or 0)
    if unclassified > 0:
        sanitized["Sem classificação"] = unclassified
    return sanitized


def _causes(row):
    return sanitize_presentation_causes(row.get("category_counts", {})), {}


def presentation_payload(user, params):
    source = MapAnalyticsService.query(user, params, fixed_client=True)
    incident_ufs = [uf for uf, row in source["states"].items() if row.get("ticket_count", 0) > 0]
    allowed_ufs = presentation_ufs(user, params, extra_ufs=incident_ufs)
    sanitized_causes, projection = _causes(source)
    state_fields = (
        "total_links", "ticket_count", "affected_links", "recurrence_links",
        "recurrence_tickets", "daily_counts", "technical_daily_counts",
        "project_counts", "provider_counts", "ticket_project_counts", "ticket_provider_counts",
    )
    states = {}
    region_links = {region["nome"]: 0 for region in REGIONS}
    for uf, row in source["states"].items():
        if uf not in allowed_ufs:
            continue
        region_name = next(r["nome"] for r in REGIONS if r["id"] == STATE_BY_UF[uf]["region_id"])
        region_links[region_name] += row["total_links"]
        state_causes, estimated = _causes(row)
        states[uf] = {key: row.get(key, 0) for key in state_fields}
        states[uf].update({
            "ticket_reason_counts": state_causes,
            "reason_counts": state_causes,
            "category_counts": state_causes,
            "distribution_projected": estimated,
            "client_counts": {params["client"]: row["total_links"]},
        })

    # Explicit allowlist: no original motives, titles, tickets, accounts or financial labels.
    safe_regions = {
        reg: sanitize_presentation_causes(cats)
        for reg, cats in (source.get("region_category_counts") or {}).items()
    }
    safe_regions = {reg: cats for reg, cats in safe_regions.items() if cats}
    prev = source.get("period_comparison") or {}
    safe_prev = {
        "date_from": prev.get("date_from", ""),
        "date_to": prev.get("date_to", ""),
        "current_from": prev.get("current_from", ""),
        "current_to": prev.get("current_to", ""),
        "current_days": prev.get("current_days", 0),
        "previous_days": prev.get("previous_days", 0),
        "criterion": prev.get("criterion", ""),
        "is_partial": bool(prev.get("is_partial")),
        "ticket_count": prev.get("ticket_count", 0),
        "delta": prev.get("delta", 0),
        "delta_percent": prev.get("delta_percent"),
        "category_counts": sanitize_presentation_causes(prev.get("category_counts") or {}),
    } if prev else {}
    return {
        "mode": params.get("mode", "calls"),
        "states": states,
        "period": source["period"],
        "region_link_counts": region_links,
        "correlation": source.get("correlation", {}),
        "operator_policy": source.get("operator_policy", {}),
        "region_category_counts": safe_regions,
        "period_comparison": safe_prev,
        "reconciliation": source.get("reconciliation", {}),
        "project_colors": source.get("project_colors", {}),
        "region": source["region"],
        "allowed_ufs": allowed_ufs,
        "projects": source["projects"],
        "selected_project": params.get("project", ""),
        "providers": source["providers"],
        "clients": [params["client"]],
        "priority_options": source.get("priority_options", []),
        "filtered_source_total": source["filtered_source_total"],
        "source_total": source["filtered_source_total"],
        "ticket_count": source["ticket_count"],
        "affected_link_count": source["affected_link_count"],
        "recurrence_link_count": source["recurrence_link_count"],
        "recurrence_ticket_count": source["recurrence_ticket_count"],
        "terminal_ticket_count": source["ticket_count"],
        "ticket_reason_counts": sanitized_causes,
        "category_counts": sanitized_causes,
        "distribution_projected": projection,
        "daily_counts": source["daily_counts"],
        "technical_daily_counts": source["technical_daily_counts"],
        "definitions": {
            "source_total": "Links CMDB no recorte autorizado; não representa tickets.",
            "filtered_source_total": "Links CMDB no recorte autorizado; não representa tickets.",
            "ticket_count": "Incidentes válidos distintos por TicketID, idêntico ao recorte operacional.",
            "category_counts": "Motivos técnicos reais preservados; ocorrências não técnicas ou restritas agrupadas em Outros motivos.",
            "distribution_projected": "Desativada: todos os incidentes válidos são contabilizados em suas categorias reais ou em Outros motivos.",
            "technical_daily_counts": "Tickets distintos com classificação técnica comprovada por dia de criação.",
        },
        "projection_label": "",
        "reasons": list(sanitized_causes),
        "reason_options": [
            {"value": o["value"], "label": o["label"]}
            for o in source["reason_options"]
            if IncidentRules.category(o["value"]) not in {"financeiro", "sem_classificacao"}
        ],
        "presentation": True,
    }
