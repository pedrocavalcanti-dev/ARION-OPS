from django.db import transaction
from django.db.models import Q

from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.models import CMDBLinkMirror

from ..models import QualityAuditEvent, QualityOrder


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


def _user_label(user):
    if user is None:
        return "Não atribuído"
    return user.get_full_name() or user.get_username()
