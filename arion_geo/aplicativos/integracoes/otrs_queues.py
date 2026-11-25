"""OTRS queues that back the operational ticket scopes in the Cockpit."""

NOC_QUEUE_NAME = "NOC - ATACADO"
TRIAGE_QUEUE_NAME = "NOC - TRIAGEM"
NOC_QUEUE_NAMES = (NOC_QUEUE_NAME, TRIAGE_QUEUE_NAME)
# Separate coverage key: an older N2-only history must never imply N1 coverage.
NOC_HISTORY_SCOPE = "|".join(NOC_QUEUE_NAMES)
ATTENDANCE_LEVELS = {TRIAGE_QUEUE_NAME: "N1", NOC_QUEUE_NAME: "N2"}
CANCELLATION_REQUEST_QUEUE_NAME = "NOC - CANCELAMENTOS NGO"
EQUIPMENT_REMOVAL_QUEUE_NAME = "NOC - RETIRA EQUIPAMENTO"
# The source KPI profile stores the real queue name with an en dash.
PAYMENT_PENDING_QUEUE_NAME = "NOC \u2013 PENDENTE PAGAMENTO"

CANCELLATION_QUEUE_NAMES = (
    CANCELLATION_REQUEST_QUEUE_NAME,
    EQUIPMENT_REMOVAL_QUEUE_NAME,
    PAYMENT_PENDING_QUEUE_NAME,
)

COCKPIT_QUEUE_NAMES = (*NOC_QUEUE_NAMES, *CANCELLATION_QUEUE_NAMES)

# These criteria were read from the OTRS KPI definitions (kpi.search), not
# inferred from ticket titles. Treatment shares the request queue and is
# separated by the saved profile's StateIDs.
CANCELLATION_REQUEST_STATES = ("em atendimento",)
CANCELLATION_TREATMENT_STATES = (
    "agendado",
    "pendente aprova\u00e7\u00e3o",
    "pendente cliente endere\u00e7o n\u00e3o localizado",
    "pendente cliente local fechado",
    "Pendente",
    "pendente operadora",
    "pendente outros",
)
CANCELLATION_PROFILE_ACTIVE_STATES = (
    "agendado",
    "merged",
    "aguardando aprova\u00e7\u00e3o do gestor",
    "aguardando aprova\u00e7\u00e3o do solicitante",
    "em atendimento",
    "new",
    "pendente aprova\u00e7\u00e3o",
    "pendente cliente endere\u00e7o n\u00e3o localizado",
    "pendente cliente local fechado",
    "Pendente",
    "pendente operadora",
    "pendente outros",
    "Projeto Especial",
)

# Explicit old spelling retained for mirrors populated before the real source
# queue's punctuation was verified. New syncs always resolve the exact source
# queue name above.
PAYMENT_PENDING_QUEUE_ALIASES = (PAYMENT_PENDING_QUEUE_NAME, "NOC - PENDENTE PAGAMENTO")
ALL_CANCELLATION_QUEUE_ALIASES = (
    CANCELLATION_REQUEST_QUEUE_NAME,
    EQUIPMENT_REMOVAL_QUEUE_NAME,
    *PAYMENT_PENDING_QUEUE_ALIASES,
)


def queue_signature(queue_names=COCKPIT_QUEUE_NAMES):
    return "|".join(queue_names)

