"""Local links to OTRS treatment tickets and the Quality checklist."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.chamados.services import otrs_ticket_url
from aplicativos.integracoes.models import OTRSTicket, OTRSTicketConfigItem, OTRSStateMapping

from ..models import QualityAction, QualityAuditEvent, QualityOrder, QualityTicket
from .data_scope import QualityDataScopeService


DEFAULT_ACTIONS = (
    "Revisar o histórico do circuito",
    "Confirmar a reincidência",
    "Solicitar e registrar a causa raiz",
    "Validar a ação corretiva",
    "Validar a ação preventiva",
    "Liberar para monitoramento RREIP",
)

OPEN_BUSINESS_STATES = {
    OTRSStateMapping.BusinessStatus.OPEN,
    OTRSStateMapping.BusinessStatus.PENDING,
    OTRSStateMapping.BusinessStatus.SCHEDULED,
}


@transaction.atomic
def create_default_quality_actions(order):
    """Seed a small managerial checklist for newly created OQs."""
    if order.actions.exists():
        return
    QualityAction.objects.bulk_create([
        QualityAction(quality_order=order, title=title, order=index)
        for index, title in enumerate(DEFAULT_ACTIONS, 1)
    ])


@transaction.atomic
def link_quality_ticket(*, quality_order, ticket_identifier, ticket_type, actor=None, request=None):
    order = _lock_visible_order(quality_order, actor)
    identifier = str(ticket_identifier or "").strip()[:80]
    if not identifier:
        raise ValidationError("Informe o número do ticket OTRS.")
    ticket_query = OTRSTicket.objects.all()
    if identifier.isdigit():
        ticket_query = ticket_query.filter(ticket_number=identifier) | OTRSTicket.objects.filter(external_id=int(identifier))
    else:
        ticket_query = ticket_query.filter(ticket_number=identifier)
    ticket = ticket_query.order_by("pk").first()
    if ticket is None:
        raise ValidationError("O ticket não foi encontrado no espelho local do OTRS.")
    config_item_id = quality_order.ci_snapshot or quality_order.link_id
    if not config_item_id or not OTRSTicketConfigItem.objects.filter(
        ticket=ticket,
        config_item_id=config_item_id,
        is_present_in_source=True,
    ).exists():
        raise ValidationError("O ticket não possui vínculo espelhado com o circuito desta OQ.")

    existing = QualityTicket.objects.filter(
        quality_order=order, ticket_external_id_snapshot=ticket.external_id, ticket_type=ticket_type,
    ).first()
    if existing:
        return existing, False

    closed_at = ticket.resolved_at or ticket.terminal_state_at or ticket.canceled_at
    record = QualityTicket.objects.create(
        quality_order=order,
        otrs_ticket=ticket,
        ticket_external_id_snapshot=ticket.external_id,
        ticket_number=ticket.ticket_number,
        ticket_type=ticket_type,
        queue_snapshot=ticket.queue_name,
        state_snapshot=ticket.external_state_name,
        # The mirror has a responsible user ID but no official name lookup; never expose the ID as a name.
        owner_snapshot="",
        opened_at=ticket.source_created_at,
        closed_at=closed_at,
        result=ticket.closing_code_raw or ticket.get_business_status_display(),
    )
    description = f"Ticket {ticket.ticket_number} vinculado como {record.get_ticket_type_display().lower()}."
    QualityAuditEvent.objects.create(
        quality_order=order,
        event_type=QualityAuditEvent.EventType.QUALITY_TICKET_LINKED,
        actor=actor,
        description=description,
        metadata={"ticket_number": ticket.ticket_number, "ticket_type": ticket_type},
    )
    AuditService.record(
        request=request,
        user=actor,
        category="QUALITY",
        action="QUALITY_TICKET_LINKED",
        module="Qualidade",
        description=description,
        obj=order,
        metadata={"code": order.code, "ticket_number": ticket.ticket_number, "ticket_type": ticket_type},
    )
    return record, True


@transaction.atomic
def add_quality_action(*, quality_order, title, actor=None, request=None):
    order = _lock_visible_order(quality_order, actor)
    title = " ".join(str(title or "").split())[:240]
    if not title:
        raise ValidationError("Informe a ação do checklist.")
    last_order = order.actions.order_by("-order", "-pk").values_list("order", flat=True).first() or 0
    action = QualityAction.objects.create(quality_order=order, title=title, order=last_order + 1)
    description = f"Item de checklist adicionado: {action.title}."
    _record_order_event(order, QualityAuditEvent.EventType.CHECKLIST_ITEM_ADDED, description, actor, request, {"action_id": action.pk})
    return action


@transaction.atomic
def toggle_quality_action(*, quality_order, action_id, completed, actor=None, request=None):
    order = _lock_visible_order(quality_order, actor)
    action = QualityAction.objects.select_for_update().get(pk=action_id, quality_order=order)
    completed = bool(completed)
    if action.completed == completed:
        return action, False
    action.completed = completed
    action.completed_at = timezone.now() if completed else None
    action.completed_by = actor if completed and getattr(actor, "is_authenticated", False) else None
    action.save(update_fields=("completed", "completed_at", "completed_by"))
    description = f"Ação {'concluída' if completed else 'reaberta'}: {action.title}."
    _record_order_event(order, QualityAuditEvent.EventType.CHECKLIST_ITEM_CHANGED, description, actor, request, {
        "action_id": action.pk,
        "completed": completed,
    })
    return action, True


def get_treatment_context(order):
    records = list(
        QualityTicket.objects.filter(quality_order=order, ticket_type=QualityTicket.TicketType.TREATMENT)
        .select_related("otrs_ticket").order_by("-created_at", "-pk")
    )
    entries = []
    for record in records:
        ticket = record.otrs_ticket
        state = (ticket.external_state_name if ticket else record.state_snapshot) or "Estado não informado"
        active = bool(
            ticket and ticket.business_status in OPEN_BUSINESS_STATES
            or not ticket and record.closed_at is None
        )
        entries.append({
            "record": record,
            "ticket": ticket,
            "ticket_number": (ticket.ticket_number if ticket else record.ticket_number) or "Ticket sem número",
            "ticket_type_label": record.get_ticket_type_display(),
            "queue": (ticket.queue_name if ticket else record.queue_snapshot) or "Fila não informada",
            "state": state,
            "owner": record.owner_snapshot or "Responsável não informado",
            "opened_at": (ticket.source_created_at if ticket else record.opened_at),
            "closed_at": ((ticket.resolved_at or ticket.terminal_state_at or ticket.canceled_at) if ticket else record.closed_at),
            "last_movement_at": (ticket.source_changed_at if ticket else None),
            "result": ((ticket.closing_code_raw or ticket.get_business_status_display()) if ticket else record.result) or "Resultado não informado",
            "active": active,
            "otrs_url": otrs_ticket_url(ticket.external_id if ticket else record.ticket_external_id_snapshot),
        })
    active_ticket = next((entry for entry in entries if entry["active"]), None)
    history = [entry for entry in entries if entry is not active_ticket]
    return {"current_treatment": active_ticket, "treatment_history": history, "treatments": entries}


def get_monitoring_tickets(order):
    records = QualityTicket.objects.filter(
        quality_order=order,
        ticket_type=QualityTicket.TicketType.MONITORING,
    ).select_related("otrs_ticket").order_by("-created_at", "-pk")
    return [{
        "record": record,
        "ticket_number": (record.otrs_ticket.ticket_number if record.otrs_ticket_id else record.ticket_number) or "Ticket sem número",
        "otrs_url": otrs_ticket_url(record.ticket_external_id_snapshot),
    } for record in records]


def _lock_visible_order(order, actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise ValidationError("Usuário autenticado obrigatório.")
    visible = QualityDataScopeService.orders_for(actor).filter(pk=order.pk)
    try:
        return QualityOrder.objects.select_for_update().get(pk__in=visible)
    except QualityOrder.DoesNotExist:
        raise ValidationError("A OQ não está disponível no escopo do usuário.") from None


def _record_order_event(order, event_type, description, actor, request, metadata):
    QualityAuditEvent.objects.create(
        quality_order=order,
        event_type=event_type,
        actor=actor,
        description=description,
        metadata=metadata,
    )
    AuditService.record(
        request=request,
        user=actor,
        category="QUALITY",
        action=event_type,
        module="Qualidade",
        description=description,
        obj=order,
        metadata={"code": order.code, **metadata},
    )
