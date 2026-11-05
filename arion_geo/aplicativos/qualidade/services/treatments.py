"""Local links to OTRS treatment tickets and the Quality checklist."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.chamados.services import otrs_ticket_url
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicket, OTRSTicketConfigItem, OTRSStateMapping
from aplicativos.integracoes.services.analytics import TicketQueryService

from ..models import QualityAction, QualityAuditEvent, QualityMonitoringCycle, QualityOccurrence, QualityOrder, QualityTicket, QualityTreatment
from .data_scope import QualityDataScopeService
from aplicativos.configuracoes.data_scope import DataScopeService


DEFAULT_ACTIONS = (
    "Revisar a reincidência e a prioridade da OQ",
    "Confirmar o vínculo da TQ ao ticket OTRS",
    "Acompanhar o retorno da operadora no OTRS",
    "Liberar para monitoramento RREIP",
    "Avaliar a estabilidade ao fim do período",
    "Concluir a avaliação da Qualidade",
)


def quality_treatment_creation_blocker(order):
    active = QualityTreatment.objects.filter(
        quality_order=order, status__in=QualityTreatment.ACTIVE_STATUSES,
    ).order_by("-created_at", "-pk").first()
    if active:
        return f"Esta OQ j\u00e1 possui uma tratativa ativa: {active.code}."
    previous = QualityTreatment.objects.filter(quality_order=order).order_by("-created_at", "-pk").first()
    if not previous:
        return ""
    if previous.status == QualityTreatment.Status.CANCELED and previous.completed_at:
        return ""
    if previous.status not in {QualityTreatment.Status.COMPLETED, QualityTreatment.Status.RECURRENCE} or not previous.completed_at:
        return f"A TQ {previous.code} ainda n\u00e3o foi conclu\u00edda formalmente."
    if not QualityOccurrence.objects.filter(
        quality_order=order, excluded=False, counts_as_recurrence=True,
        occurred_at__gte=previous.completed_at,
    ).exists():
        return f"A TQ {previous.code} foi conclu\u00edda. Registre uma nova reincid\u00eancia v\u00e1lida antes de abrir outro ciclo."
    return ""

OPEN_BUSINESS_STATES = {
    OTRSStateMapping.BusinessStatus.OPEN,
    OTRSStateMapping.BusinessStatus.PENDING,
    OTRSStateMapping.BusinessStatus.SCHEDULED,
}


def support_tickets_for_order(user, order):
    """Return only active, mirrored support tickets for this in-scope OQ circuit."""
    scoped_links = DataScopeService.filter_queryset(
        user, CMDBLinkMirror.objects.filter(is_present_in_source=True),
    )
    link = scoped_links.filter(source_config_item_id=order.link_id).first() if order.link_id else None
    if not link:
        return OTRSTicket.objects.none()
    relation_ids = OTRSTicketConfigItem.objects.filter(
        cmdb_link=link, config_item_id=order.ci_snapshot or order.link_id,
        is_present_in_source=True,
    ).order_by().values("ticket_id")
    return TicketQueryService.business_open(user, links=scoped_links).filter(
        pk__in=relation_ids,
    ).order_by("-source_created_at", "-external_id")


@transaction.atomic
def create_quality_treatment(*, quality_order, reason, description="", support_ticket=None, actor=None, request=None):
    order = _lock_visible_order(quality_order, actor)
    if order.status == QualityOrder.Status.CANCELADA:
        raise ValidationError("Não é possível gerar TQ para uma OQ cancelada.")
    existing = QualityTreatment.objects.filter(
        quality_order=order, status__in=QualityTreatment.ACTIVE_STATUSES,
    ).first()
    if existing:
        raise ValidationError(f"Esta Ordem de Qualidade já possui uma tratativa ativa: {existing.code}.")

    creation_blocker = quality_treatment_creation_blocker(order)
    if creation_blocker:
        raise ValidationError(creation_blocker)

    reason = " ".join(str(reason or "").split())[:4000]
    description = " ".join(str(description or "").split())[:4000]
    if not reason:
        raise ValidationError("Informe o motivo da tratativa.")
    if support_ticket is not None:
        authorized_ticket = support_tickets_for_order(actor, order).filter(pk=support_ticket.pk).first()
        if not authorized_ticket:
            raise ValidationError("O chamado selecionado não está ativo ou fora do escopo desta OQ.")
        support_ticket = authorized_ticket

    try:
        with transaction.atomic():
            treatment = QualityTreatment.objects.create(
                quality_order=order,
                support_ticket=support_ticket,
                support_ticket_external_id_snapshot=support_ticket.external_id if support_ticket else None,
                support_ticket_number=support_ticket.ticket_number if support_ticket else "",
                support_ticket_state_snapshot=support_ticket.external_state_name if support_ticket else "",
                support_ticket_opened_at=support_ticket.source_created_at if support_ticket else None,
                operational_state_snapshot="UNCONFIRMED",
                reason=reason,
                description=description,
                owner=order.owner,
                created_by=actor,
                status=QualityTreatment.Status.PENDING_SEND,
                send_status=QualityTreatment.SendStatus.NOT_SENT,
            )
    except IntegrityError:
        existing = QualityTreatment.objects.filter(
            quality_order=order, status__in=QualityTreatment.ACTIVE_STATUSES,
        ).first()
        if existing:
            raise ValidationError(f"Esta Ordem de Qualidade já possui uma tratativa ativa: {existing.code}.") from None
        raise

    if order.status in {QualityOrder.Status.NOVA, QualityOrder.Status.TRIAGEM, QualityOrder.Status.CONCLUIDA}:
        previous_status = order.status
        order.status = QualityOrder.Status.TRATAMENTO
        order.completed_at = None
        order.save(update_fields=("status", "completed_at", "updated_at"))
        previous_label = dict(QualityOrder.Status.choices).get(previous_status, previous_status)
        _record_order_event(
            order, QualityAuditEvent.EventType.STATUS_CHANGED,
            f"Fase da OQ alterada de {previous_label} para Tratamento após gerar {treatment.code}.",
            actor, request, {"previous_status": previous_status, "status": order.status, "treatment_code": treatment.code},
        )
    description_text = f"Tratativa {treatment.code} criada para {order.code}."
    if support_ticket:
        description_text += f" Chamado de suporte relacionado: {support_ticket.ticket_number}."
    QualityAuditEvent.objects.create(
        quality_order=order,
        event_type=QualityAuditEvent.EventType.TREATMENT_CREATED,
        actor=actor,
        description=description_text,
        metadata={"treatment_code": treatment.code, "support_ticket_number": treatment.support_ticket_number},
    )
    AuditService.record(
        request=request, user=actor, category="QUALITY", action="TREATMENT_CREATED",
        module="Qualidade", description=description_text, obj=order,
        metadata={"code": order.code, "treatment_code": treatment.code,
                  "support_ticket_number": treatment.support_ticket_number},
    )
    return treatment


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


@transaction.atomic
def update_quality_treatment_summary(*, treatment, actor, request=None, management_summary="",
                                     management_situation="", next_action="", next_action_due=None,
                                     update_note=""):
    order = _lock_visible_order(treatment.quality_order, actor)
    locked = QualityTreatment.objects.select_for_update().get(pk=treatment.pk, quality_order=order)
    if locked.status not in {QualityTreatment.Status.IN_PROGRESS, QualityTreatment.Status.MONITORING}:
        raise ValidationError("Esta TQ não aceita atualizações gerenciais neste estado.")
    summary = " ".join(str(management_summary or "").split())[:1600]
    situation = str(management_situation or "")
    if situation not in QualityTreatment.ManagementSituation.values:
        raise ValidationError("Selecione uma situação válida para a tratativa.")
    action = " ".join(str(next_action or "").split())[:500]
    note = " ".join(str(update_note or "").split())[:800]
    changed_fields = []
    for field, value in (
        ("management_summary", summary),
        ("management_situation", situation),
        ("next_action", action),
        ("next_action_due", next_action_due),
    ):
        if getattr(locked, field) != value:
            setattr(locked, field, value)
            changed_fields.append(field)
    if not changed_fields and not note:
        return locked, False
    if changed_fields:
        locked.save(update_fields=(*changed_fields, "updated_at"))
    description = note or f"Resumo gerencial da TQ {locked.code} atualizado."
    QualityAuditEvent.objects.create(
        quality_order=order,
        event_type=QualityAuditEvent.EventType.NOTES_CHANGED,
        actor=actor,
        description=description,
        metadata={
            "treatment_code": locked.code,
            "changed_fields": changed_fields,
            "management_situation": locked.management_situation,
            "update_note": note,
        },
    )
    AuditService.record(
        request=request, user=actor, category="QUALITY", action="TREATMENT_UPDATED",
        module="Qualidade", description=f"Atualização gerencial da TQ {locked.code}.",
        obj=order, metadata={"code": order.code, "treatment_code": locked.code,
                             "changed_fields": changed_fields, "situation": locked.management_situation},
    )
    return locked, True


@transaction.atomic
def complete_quality_treatment(*, treatment, actor, request=None, confirmed=False, observations=""):
    order = _lock_visible_order(treatment.quality_order, actor)
    locked = QualityTreatment.objects.select_for_update().get(pk=treatment.pk, quality_order=order)
    if locked.status != QualityTreatment.Status.MONITORING:
        raise ValidationError("A TQ só pode ser concluída após o monitoramento RREIP.")
    cycle = QualityMonitoringCycle.objects.select_for_update().filter(
        quality_order=order, quality_treatment=locked, result=QualityMonitoringCycle.Result.SUCCESS,
    ).order_by("-cycle_number").first()
    if not cycle or not cycle.stability_validated or not cycle.final_analysis or not cycle.occurrence_review:
        raise ValidationError("Conclua e valide o monitoramento antes de concluir a TQ.")
    if not confirmed:
        raise ValidationError("Confirme a conclusão local da TQ após avaliar o RREIP.")
    now = timezone.now()
    if observations:
        QualityAuditEvent.objects.create(
            quality_order=order, event_type=QualityAuditEvent.EventType.NOTES_CHANGED,
            actor=actor, description=" ".join(str(observations).split())[:800],
            metadata={"treatment_code": locked.code, "update_note": "conclusão da TQ"},
        )
    previous_treatment = locked.status
    locked.status = QualityTreatment.Status.COMPLETED
    locked.completed_at = now
    locked.save(update_fields=("status", "completed_at", "active_order_key", "updated_at"))
    previous_order = order.status
    if not QualityTreatment.objects.filter(quality_order=order, status__in=QualityTreatment.ACTIVE_STATUSES).exists():
        order.status = QualityOrder.Status.CONCLUIDA
        order.completed_at = now
        order.save(update_fields=("status", "completed_at", "updated_at"))
        if previous_order != order.status:
            _record_order_event(order, QualityAuditEvent.EventType.STATUS_CHANGED,
                "OQ concluída após o encerramento validado de todos os ciclos ativos.", actor, request,
                {"previous_status": previous_order, "status": order.status, "treatment_code": locked.code})
    _complete_treatment_checklist_action(order, "Concluir a avaliação da Qualidade", locked, actor, request)
    _record_order_event(order, QualityAuditEvent.EventType.TREATMENT_COMPLETED,
        f"{locked.code} concluída formalmente após validação do RREIP.", actor, request,
        {"treatment_code": locked.code, "previous_status": previous_treatment, "status": locked.status,
         "previous_order_status": previous_order, "order_status": order.status})
    return locked


@transaction.atomic
def cancel_quality_treatment(*, treatment, actor, request=None, reason):
    order = _lock_visible_order(treatment.quality_order, actor)
    locked = QualityTreatment.objects.select_for_update().get(pk=treatment.pk, quality_order=order)
    reason = " ".join(str(reason or "").split())[:2000]
    if not reason:
        raise ValidationError("Informe a justificativa do cancelamento.")
    if locked.status not in QualityTreatment.ACTIVE_STATUSES:
        raise ValidationError("Somente uma TQ ativa pode ser cancelada.")
    if locked.send_status in {QualityTreatment.SendStatus.IN_PROGRESS, QualityTreatment.SendStatus.UNCERTAIN, QualityTreatment.SendStatus.SENT} or locked.quality_ticket_external_id:
        raise ValidationError("A solicitação já foi aceita ou vinculada a ticket. A TQ não pode ser cancelada sem encerrar o ciclo por reconciliação.")
    if locked.status == QualityTreatment.Status.MONITORING or order.monitoring_cycles.filter(result=QualityMonitoringCycle.Result.IN_PROGRESS).exists():
        raise ValidationError("Encerre ou avalie o ciclo RREIP antes do cancelamento administrativo.")
    previous = locked.status
    locked.status = QualityTreatment.Status.CANCELED
    locked.completed_at = timezone.now()
    locked.save(update_fields=("status", "completed_at", "active_order_key", "updated_at"))
    if order.status in {QualityOrder.Status.TRATAMENTO, QualityOrder.Status.MONITORAMENTO}:
        previous_order_status = order.status
        order.status = QualityOrder.Status.TRIAGEM
        order.completed_at = None
        order.save(update_fields=("status", "completed_at", "updated_at"))
        _record_order_event(order, QualityAuditEvent.EventType.STATUS_CHANGED,
            "OQ retornou à triagem após cancelamento local justificado da TQ.", actor, request,
            {"previous_status": previous_order_status, "status": order.status, "treatment_code": locked.code})
    _record_order_event(order, QualityAuditEvent.EventType.TREATMENT_CANCELED,
        f"{locked.code} cancelada administrativamente. Ticket OTRS não foi alterado.", actor, request,
        {"treatment_code": locked.code, "previous_status": previous, "status": locked.status, "reason": reason})
    return locked


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


def _complete_treatment_checklist_action(order, title, treatment, actor, request):
    action = QualityAction.objects.select_for_update().filter(
        quality_order=order, title__iexact=title, completed=False,
    ).first()
    if not action:
        return
    action.completed = True
    action.completed_at = timezone.now()
    action.completed_by = actor
    action.save(update_fields=("completed", "completed_at", "completed_by"))
    description = f"Checklist atualizado: {title}."
    metadata = {"action_id": action.pk, "completed": True, "treatment_code": treatment.code}
    _record_order_event(
        order, QualityAuditEvent.EventType.CHECKLIST_ITEM_CHANGED,
        description, actor, request, metadata,
    )
    AuditService.record(
        request=request, user=actor, category="QUALITY", action="CHECKLIST_ITEM_CHANGED",
        module="Qualidade", description=description, obj=order,
        metadata={"code": order.code, **metadata},
    )
