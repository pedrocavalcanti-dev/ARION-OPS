"""Rules for classifying the live cancellation process from OTRS fields."""
import re
import unicodedata

from django.db.models import Case, CharField, Q, Value, When

from aplicativos.integracoes.otrs_queues import (
    ALL_CANCELLATION_QUEUE_ALIASES,
    CANCELLATION_PROFILE_ACTIVE_STATES,
    CANCELLATION_REQUEST_QUEUE_NAME,
    CANCELLATION_REQUEST_STATES,
    CANCELLATION_TREATMENT_STATES,
    EQUIPMENT_REMOVAL_QUEUE_NAME,
    PAYMENT_PENDING_QUEUE_ALIASES,
)
from aplicativos.integracoes.services.otrs_status import (
    TERMINAL_QUERY_STATUSES, TERMINAL_STATUSES, normalize_business_status,
)


def normalize_operational_name(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).strip()


ACTIVE_CANCELLATION_STAGES = (
    ("REQUEST", "Solicita\u00e7\u00f5es", CANCELLATION_REQUEST_QUEUE_NAME, CANCELLATION_REQUEST_STATES),
    ("TREATMENT", "Em tratamento", CANCELLATION_REQUEST_QUEUE_NAME, CANCELLATION_TREATMENT_STATES),
    ("EQUIPMENT_REMOVAL", "Retirada de equipamento", EQUIPMENT_REMOVAL_QUEUE_NAME, CANCELLATION_PROFILE_ACTIVE_STATES),
    ("PAYMENT_PENDING", "Pendente pagamento", PAYMENT_PENDING_QUEUE_ALIASES[0], CANCELLATION_PROFILE_ACTIVE_STATES),
)

CANCELLATION_STAGE_LABELS = {
    "REQUEST": "Solicita\u00e7\u00f5es",
    "TREATMENT": "Em tratamento",
    "EQUIPMENT_REMOVAL": "Retirada de equipamento",
    "PAYMENT_PENDING": "Pendente pagamento",
    "CLOSED": "Encerrado",
    "UNKNOWN": "N\u00e3o classificado",
}
TERMINAL_BUSINESS_STATUSES = TERMINAL_STATUSES
_QUEUE_STAGE_BY_KEY = {}
for _stage, _label, _queue, _states in ACTIVE_CANCELLATION_STAGES:
    for _queue_alias in (
        PAYMENT_PENDING_QUEUE_ALIASES if _stage == "PAYMENT_PENDING" else (_queue,)
    ):
        _QUEUE_STAGE_BY_KEY.setdefault(normalize_operational_name(_queue_alias), _stage)
def cancellation_queue_filter():
    return Q(queue_name__in=ALL_CANCELLATION_QUEUE_ALIASES)


def _text_matches(field, values):
    conditions = [Q(**{f"{field}__iexact": value}) for value in values]
    result = conditions[0] if conditions else Q(pk__in=[])
    for condition in conditions[1:]:
        result |= condition
    return result


def cancellation_stage_expression():
    cancellation_queue = cancellation_queue_filter()
    request_queue = Q(queue_name__iexact=CANCELLATION_REQUEST_QUEUE_NAME)
    equipment_queue = Q(queue_name__iexact=EQUIPMENT_REMOVAL_QUEUE_NAME)
    payment_queue = Q(queue_name__in=PAYMENT_PENDING_QUEUE_ALIASES)
    request_state = _text_matches("external_state_name", CANCELLATION_REQUEST_STATES)
    treatment_state = _text_matches("external_state_name", CANCELLATION_TREATMENT_STATES)
    profile_active_state = _text_matches("external_state_name", CANCELLATION_PROFILE_ACTIVE_STATES)
    terminal = Q(business_status__in=TERMINAL_QUERY_STATUSES) | (
        Q(external_state_type__icontains="closed")
        | Q(external_state_type__iexact="merged")
    )
    return Case(
        When(cancellation_queue & terminal, then=Value("CLOSED")),
        When(request_queue & request_state, then=Value("REQUEST")),
        When(request_queue & treatment_state, then=Value("TREATMENT")),
        When(equipment_queue & profile_active_state, then=Value("EQUIPMENT_REMOVAL")),
        When(payment_queue & profile_active_state, then=Value("PAYMENT_PENDING")),
        When(cancellation_queue, then=Value("UNKNOWN")),
        default=Value(""),
        output_field=CharField(max_length=24),
    )


def classify_cancellation_stage(ticket):
    """Return the ticket's current process stage using the OTRS profile rules."""
    stage = _QUEUE_STAGE_BY_KEY.get(normalize_operational_name(getattr(ticket, "queue_name", "")))
    if not stage:
        return ""

    status = normalize_business_status(getattr(ticket, "business_status", ""))
    state_type = normalize_operational_name(getattr(ticket, "external_state_type", ""))
    if status in TERMINAL_BUSINESS_STATUSES or "closed" in state_type or state_type == "merged":
        return "CLOSED"

    state_name = normalize_operational_name(getattr(ticket, "external_state_name", ""))
    if stage == "REQUEST":
        if state_name in {normalize_operational_name(value) for value in CANCELLATION_REQUEST_STATES}:
            return "REQUEST"
        if state_name in {normalize_operational_name(value) for value in CANCELLATION_TREATMENT_STATES}:
            return "TREATMENT"
        return "UNKNOWN"
    if state_name in {normalize_operational_name(value) for value in CANCELLATION_PROFILE_ACTIVE_STATES}:
        return stage
    return "UNKNOWN"
