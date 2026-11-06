"""Canonical operational interpretation of OTRS ticket states.

The raw OTRS state remains in ``external_state_name``. This module only
normalizes the ARION operational category used by queries and indicators.
"""

from aplicativos.integracoes.services.sync_common import normalized_name
from django.db.models import Q


BUSINESS_OPEN_STATUSES = ("OPEN", "PENDING", "SCHEDULED")
BACKLOG_STATUSES = ("OPEN", "PENDING")
RESOLVED_STATUS = "RESOLVED"
TERMINAL_STATUSES = (RESOLVED_STATUS, "CANCELED")
OPERATIONAL_VALID_STATUSES = (*BUSINESS_OPEN_STATUSES, RESOLVED_STATUS)
TERMINAL_QUERY_STATUSES = (*TERMINAL_STATUSES, "CLOSED")
OPERATIONAL_VALID_QUERY_STATUSES = (*OPERATIONAL_VALID_STATUSES, "CLOSED")

BUSINESS_STATUS_LABELS = {
    "OPEN": "Em atendimento",
    "PENDING": "Pendente",
    "SCHEDULED": "Agendado",
    RESOLVED_STATUS: "Resolvido",
    "CANCELED": "Cancelado",
    "UNKNOWN": "N\u00e3o classificado",
}

_STATUS_ALIASES = {"CLOSED": RESOLVED_STATUS}

_EXTERNAL_STATE_STATUSES = {
    "new": "OPEN",
    "merged": RESOLVED_STATUS,
    "em atendimento": "OPEN",
    "pendente": "PENDING",
    "pendente aprovacao": "PENDING",
    "pendente cliente endereco nao localizado": "PENDING",
    "pendente cliente local fechado": "PENDING",
    "pendente operadora": "PENDING",
    "pendente outros": "PENDING",
    "aguardando aprovacao do gestor": "PENDING",
    "aguardando aprovacao do solicitante": "PENDING",
    "projeto especial": "OPEN",
    "agendado": "SCHEDULED",
    "resolvido": RESOLVED_STATUS,
    "closed successful": RESOLVED_STATUS,
    "encerrado com sucesso": RESOLVED_STATUS,
    "encerrado com viabilidade": RESOLVED_STATUS,
    "cancelado": "CANCELED",
}


def normalize_business_status(value):
    """Collapse legacy CLOSED rows and inputs into the canonical RESOLVED status."""
    status = str(value or "UNKNOWN").strip().upper()
    return _STATUS_ALIASES.get(status, status)


def business_status_label(value):
    """Return the shared operational label while preserving source state elsewhere."""
    status = normalize_business_status(value)
    if status not in BUSINESS_STATUS_LABELS:
        status = status_for_external_state(value)
    return BUSINESS_STATUS_LABELS.get(status, status)


def business_status_query(field, value):
    """Build a query matching a canonical category and its legacy stored alias."""
    status = normalize_business_status(value)
    if status == RESOLVED_STATUS:
        return Q(**{f"{field}__in": (RESOLVED_STATUS, "CLOSED")})
    return Q(**{field: status})


def business_statuses_query(field, values):
    """Build a query for canonical statuses while tolerating pre-migration CLOSED rows."""
    statuses = {normalize_business_status(value) for value in values}
    query_values = set(statuses)
    if RESOLVED_STATUS in statuses:
        query_values.add("CLOSED")
    return Q(**{f"{field}__in": tuple(sorted(query_values))})


def status_for_external_state(value):
    """Map a source state name to its ARION business category."""
    return _EXTERNAL_STATE_STATUSES.get(normalized_name(value), "UNKNOWN")


def normalized_status_counts(rows):
    """Fold grouped counts into canonical categories, including legacy CLOSED rows."""
    counts = {}
    for row in rows:
        status = normalize_business_status(row["business_status"])
        counts[status] = counts.get(status, 0) + row["total"]
    return counts
