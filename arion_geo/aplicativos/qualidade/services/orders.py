from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.models import CMDBLinkMirror

from ..models import QualityAuditEvent, QualityMonitoringCycle, QualityOrder, QualityTreatment


class ActiveQualityOrderExists(Exception):
    def __init__(self, order):
        self.order = order
        super().__init__(f"Este link já possui a OQ ativa {order.code}.")


ACTIVE_ORDER_STATUSES = (
    QualityOrder.Status.NOVA,
    QualityOrder.Status.TRIAGEM,
    QualityOrder.Status.TRATAMENTO,
    QualityOrder.Status.AGUARDANDO_TERCEIRO,
    QualityOrder.Status.MONITORAMENTO,
)


@transaction.atomic
def create_quality_order(*, actor=None, request=None, **fields):
    """Create a local OQ and its initial event without writing to OTRS."""
    link = fields.get("link")
    if link:
        # Serializes creation attempts for the same source link on databases that
        # support row locks; no OQ is created if another active order exists.
        CMDBLinkMirror.objects.select_for_update().get(
            source_config_item_id=link.source_config_item_id,
        )
        existing = QualityOrder.objects.filter(
            Q(link_id=link.source_config_item_id) | Q(ci_snapshot=link.source_config_item_id),
            status__in=ACTIVE_ORDER_STATUSES,
            archived_at__isnull=True,
        ).order_by("-updated_at", "-pk").first()
        if existing:
            raise ActiveQualityOrderExists(existing)
    order = QualityOrder.objects.create(**fields)
    from .treatments import create_default_quality_actions

    create_default_quality_actions(order)
    QualityAuditEvent.objects.create(
        quality_order=order,
        event_type=QualityAuditEvent.EventType.OQ_CREATED,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        description="Ordem de Qualidade criada.",
    )
    AuditService.record(
        request=request,
        user=actor,
        category="QUALITY",
        action="OQ_CREATED",
        module="Qualidade",
        description="Ordem de Qualidade criada.",
        obj=order,
        metadata={"code": order.code},
    )
    return order


@transaction.atomic
def update_quality_order_field(*, order, field_name, value, actor=None, request=None):
    """Change one editable OQ field and record both Quality and central audit events."""
    allowed_fields = {"status", "recurrence_level", "owner", "priority", "treatment_deadline", "internal_notes"}
    if field_name not in allowed_fields:
        raise ValueError("Campo de atualização não permitido.")
    locked = QualityOrder.objects.select_for_update().select_related("owner").get(pk=order.pk)
    old_value = getattr(locked, f"{field_name}_id" if field_name == "owner" else field_name)
    old_owner = locked.owner if field_name == "owner" and locked.owner_id else None
    new_value = getattr(value, "pk", value)
    if old_value == new_value:
        return locked, False
    setattr(locked, field_name, value)
    locked.save(update_fields=(field_name, "updated_at"))

    if field_name == "owner":
        event_type = QualityAuditEvent.EventType.OWNER_CHANGED
        old_label = _user_label(old_owner)
        new_label = _user_label(value)
        action = "OWNER_CHANGED"
        description = f"Responsável alterado de {old_label} para {new_label}."
        metadata = {"previous_owner_id": old_value, "owner_id": new_value}
    elif field_name == "priority":
        event_type = QualityAuditEvent.EventType.PRIORITY_CHANGED
        choices = dict(QualityOrder.Priority.choices)
        old_label = choices.get(old_value, old_value or "—")
        new_label = choices.get(new_value, new_value)
        action = "PRIORITY_CHANGED"
        description = f"Prioridade alterada de {old_label} para {new_label}."
        metadata = {"previous_priority": old_value, "priority": new_value}
    elif field_name == "status":
        event_type = QualityAuditEvent.EventType.STATUS_CHANGED
        choices = dict(QualityOrder.Status.choices)
        old_label = choices.get(old_value, old_value or "—")
        new_label = choices.get(new_value, new_value)
        action = "STATUS_CHANGED"
        description = f"Status alterado de {old_label} para {new_label}."
        metadata = {"previous_status": old_value, "status": new_value}
    elif field_name == "recurrence_level":
        event_type = "LEVEL_CHANGED"
        choices = dict(QualityOrder.RecurrenceLevel.choices)
        old_label = choices.get(old_value, old_value or "A classificar")
        new_label = choices.get(new_value, new_value or "A classificar")
        action = "LEVEL_CHANGED"
        description = f"Nível alterado de {old_label} para {new_label}."
        metadata = {"previous_level": old_value, "level": new_value}
    elif field_name == "treatment_deadline":
        event_type = "DEADLINE_CHANGED"
        old_label = old_value.strftime("%d/%m/%Y") if old_value else "Sem prazo"
        new_label = new_value.strftime("%d/%m/%Y") if new_value else "Sem prazo"
        action = "DEADLINE_CHANGED"
        description = f"Prazo alterado de {old_label} para {new_label}."
        metadata = {
            "previous_deadline": old_value.isoformat() if old_value else "",
            "deadline": new_value.isoformat() if new_value else "",
        }
    else:
        event_type = "NOTES_CHANGED"
        action = "NOTES_CHANGED"
        description = "Observação interna atualizada."
        metadata = {"observation_updated": True}

    QualityAuditEvent.objects.create(
        quality_order=locked,
        event_type=event_type,
        actor=actor,
        description=description,
        metadata=metadata,
    )
    AuditService.record(
        request=request,
        user=actor,
        category="QUALITY",
        action=action,
        module="Qualidade",
        description=description,
        obj=locked,
        metadata={"code": locked.code, **metadata},
    )
    return locked, True


class QualityOrderArchiveBlocked(Exception):
    """Raised when an OQ still has an operational process that needs follow-up."""


@transaction.atomic
def archive_quality_order(*, order_id, actor, reason, request=None):
    reason = str(reason or "").strip()[:1000]
    if not reason:
        raise ValueError("Informe o motivo do arquivamento.")

    order = QualityOrder.objects.select_for_update().get(pk=order_id)
    if order.archived_at:
        return order, False

    treatments = QualityTreatment.objects.filter(quality_order=order)
    unresolved_send = treatments.filter(
        Q(send_status__in=(QualityTreatment.SendStatus.IN_PROGRESS, QualityTreatment.SendStatus.UNCERTAIN))
        | Q(status__in=(
            QualityTreatment.Status.PENDING_SEND,
            QualityTreatment.Status.WAITING_OTRS,
            QualityTreatment.Status.IN_PROGRESS,
            QualityTreatment.Status.MONITORING,
            QualityTreatment.Status.SEND_FAILED,
        ))
    ).exists()
    unresolved_quality_ticket = order.quality_tickets.filter(closed_at__isnull=True).exists()
    linked_local_ticket_needs_followup = any(
        treatment.quality_ticket_external_id
        and not _otrs_state_is_terminal(treatment.quality_ticket_state_snapshot)
        for treatment in treatments.exclude(quality_ticket_external_id__isnull=True)
    )
    active_monitoring = QualityMonitoringCycle.objects.filter(
        quality_order=order, result=QualityMonitoringCycle.Result.IN_PROGRESS,
    ).exists()
    if unresolved_send or unresolved_quality_ticket or linked_local_ticket_needs_followup or active_monitoring:
        blockers = []
        if unresolved_send:
            blockers.append("há uma TQ pendente, em acompanhamento ou com envio não reconciliado")
        if unresolved_quality_ticket or linked_local_ticket_needs_followup:
            blockers.append("há ticket OTRS de Qualidade sem encerramento confirmado")
        if active_monitoring:
            blockers.append("há um ciclo RREIP em andamento")
        raise QualityOrderArchiveBlocked("O arquivamento foi bloqueado: " + "; ".join(blockers) + ".")

    snapshot = {
        "code": order.code,
        "designation": order.designation_snapshot,
        "client": order.client_snapshot,
        "project": order.project_snapshot,
        "occurrences": order.occurrences.count(),
        "treatments": treatments.count(),
        "quality_tickets": order.quality_tickets.count(),
        "monitoring_cycles": order.monitoring_cycles.count(),
        "has_linked_otrs_ticket": treatments.exclude(quality_ticket_external_id__isnull=True).exists()
        or order.quality_tickets.exclude(ticket_external_id_snapshot__isnull=True).exists(),
    }
    order.archived_at = timezone.now()
    order.archived_by = actor
    order.archive_reason = reason
    order.save(update_fields=("archived_at", "archived_by", "archive_reason", "updated_at"))
    QualityAuditEvent.objects.create(
        quality_order=order,
        event_type=QualityAuditEvent.EventType.ORDER_ARCHIVED,
        actor=actor,
        description=f"OQ arquivada administrativamente. Motivo: {reason}",
        metadata=snapshot,
    )
    AuditService.record(
        request=request,
        user=actor,
        category="QUALITY",
        action="OQ_ARCHIVED",
        module="Qualidade",
        description=f"Ordem de Qualidade {order.code} arquivada.",
        obj=order,
        metadata={**snapshot, "reason": reason},
    )
    return order, True


def _otrs_state_is_terminal(state):
    normalized = str(state or "").strip().casefold()
    terminal_fragments = (
        "resolved", "closed", "merged", "removed", "resolvido", "resolvida",
        "encerrado", "encerrada", "cancelado", "cancelada",
    )
    return bool(normalized) and any(fragment in normalized for fragment in terminal_fragments)


def _user_label(user):
    if user is None:
        return "Não atribuído"
    return user.get_full_name() or user.get_username()
