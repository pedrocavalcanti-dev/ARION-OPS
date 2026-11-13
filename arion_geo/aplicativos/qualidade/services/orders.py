import logging
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.models import CMDBLinkMirror

from ..models import (
    QualityAction,
    QualityAuditEvent,
    QualityDeletedOrder,
    QualityEvidence,
    QualityMonitoringCycle,
    QualityOccurrence,
    QualityOrder,
    QualityTicket,
    QualityTreatment,
    QualityTreatmentIdentity,
    QualityTreatmentSequence,
)
from .data_scope import QualityDataScopeService


logger = logging.getLogger(__name__)


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
    # Status transitions are owned by treatment/monitoring services, never by the generic edit form.
    allowed_fields = {"recurrence_level", "owner", "priority", "treatment_deadline", "internal_notes"}
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


def _exclusive_audit_event_queryset(order, treatment_ids=(), treatment_codes=()):
    query = Q(category="QUALITY", object_type="QualityOrder", object_id=str(order.pk))
    if treatment_ids:
        query |= Q(
            category="QUALITY",
            object_type="QualityTreatment",
            object_id__in=[str(pk) for pk in treatment_ids],
        )
    if order.code:
        query |= Q(category="QUALITY", metadata__code=order.code)
    for code in treatment_codes:
        if code:
            query |= Q(category="QUALITY", metadata__treatment_code=code)
    return AuditEvent.objects.filter(query)


def _private_media_dirs(order, treatment_codes):
    media_root = Path(settings.MEDIA_ROOT).resolve()
    quality_root = media_root / "qualidade"
    candidates = [quality_root / "orders" / f"order-{order.pk}"]
    if order.code:
        candidates.append(quality_root / "orders" / order.code)
    for code in treatment_codes:
        if code:
            candidates.append(quality_root / "treatments" / code)
    safe_dirs = []
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(quality_root)
        except ValueError:
            continue
        if resolved not in safe_dirs:
            safe_dirs.append(resolved)
    return media_root, safe_dirs


def _collect_exclusive_files_and_dirs(order, treatment_codes, evidences):
    """Collect private files and directories that belong exclusively to this OQ."""
    media_root, exclusive_dirs = _private_media_dirs(order, treatment_codes)
    file_targets = []
    seen_names = set()
    for evidence in evidences:
        field_file = getattr(evidence, "file", None)
        name = str(getattr(field_file, "name", "") or "").strip()
        if not name or name in seen_names:
            continue
        if QualityEvidence.objects.exclude(quality_order=order).filter(file=name).exists():
            continue
        seen_names.add(name)
        file_targets.append(("storage", field_file.storage, name))

    remaining_names = set(
        QualityEvidence.objects.exclude(quality_order=order)
        .exclude(file="")
        .values_list("file", flat=True),
    )
    for directory in exclusive_dirs:
        if not directory.is_dir():
            continue
        for file_path in sorted(directory.rglob("*")):
            if not file_path.is_file():
                continue
            resolved_file = file_path.resolve()
            try:
                rel_name = resolved_file.relative_to(media_root).as_posix()
            except ValueError:
                continue
            if rel_name in seen_names or rel_name in remaining_names:
                continue
            seen_names.add(rel_name)
            file_targets.append(("path", resolved_file, rel_name))
    return file_targets, exclusive_dirs


def _delete_with_retry(action_fn):
    last_exc = None
    for _attempt in range(2):
        try:
            action_fn()
            return True
        except Exception as exc:
            last_exc = exc
    logger.exception("Falha ao remover arquivo exclusivo após exclusão da OQ.", exc_info=last_exc)
    return False


def _cleanup_exclusive_files(file_targets, dir_targets):
    for kind, target, name in file_targets:
        if kind == "storage":
            _delete_with_retry(lambda storage=target, file_name=name: storage.delete(file_name))
        else:
            _delete_with_retry(lambda path=target: path.unlink(missing_ok=True))

    for directory in sorted(dir_targets, key=lambda item: len(item.parts), reverse=True):
        if not directory.is_dir():
            continue
        for sub_dir in sorted(
            (path for path in directory.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                if not any(sub_dir.iterdir()):
                    sub_dir.rmdir()
            except OSError:
                pass
        try:
            if not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass


def get_quality_order_delete_impact(order):
    """Return exact counts of exclusive dependent records removed alongside the OQ."""
    treatments = list(order.treatments.values_list("pk", "code", "status", "send_status"))
    treatment_ids = [item[0] for item in treatments]
    treatment_codes = [item[1] for item in treatments if item[1]]
    monitoring_cycles = order.monitoring_cycles.count()
    quality_tickets = order.quality_tickets.count()
    occurrences = order.occurrences.count()
    actions = order.actions.count()
    evidences = order.evidences.count()
    quality_audit_events = order.audit_events.count()
    central_audit_events = _exclusive_audit_event_queryset(order, treatment_ids, treatment_codes).count()
    audit_events = quality_audit_events + central_audit_events
    pending_workflows = sum(
        1 for _pk, _code, status, send_status in treatments
        if status in QualityTreatment.ACTIVE_STATUSES
        or send_status in (QualityTreatment.SendStatus.IN_PROGRESS, QualityTreatment.SendStatus.UNCERTAIN)
    ) + order.monitoring_cycles.filter(result=QualityMonitoringCycle.Result.IN_PROGRESS).count()
    total_dependents = (
        len(treatments)
        + monitoring_cycles
        + quality_tickets
        + occurrences
        + actions
        + evidences
        + audit_events
    )
    return {
        "treatments": len(treatments),
        "monitoring_cycles": monitoring_cycles,
        "quality_tickets": quality_tickets,
        "occurrences": occurrences,
        "actions": actions,
        "evidences": evidences,
        "quality_audit_events": quality_audit_events,
        "central_audit_events": central_audit_events,
        "audit_events": audit_events,
        "pending_workflows": pending_workflows,
        "total_dependents": total_dependents,
    }


def _detach_cross_order_references(order, treatment_ids, ticket_ids, occurrence_ids):
    """Preserve records belonging to other OQs by detaching only links to the deleted OQ."""
    if treatment_ids:
        QualityMonitoringCycle.objects.exclude(quality_order=order).filter(
            quality_treatment_id__in=treatment_ids,
        ).update(quality_treatment=None)
        QualityEvidence.objects.exclude(quality_order=order).filter(
            quality_treatment_id__in=treatment_ids,
        ).update(quality_treatment=None)
    if ticket_ids:
        QualityMonitoringCycle.objects.exclude(quality_order=order).filter(
            quality_ticket_id__in=ticket_ids,
        ).update(quality_ticket=None)
    if occurrence_ids:
        QualityMonitoringCycle.objects.exclude(quality_order=order).filter(
            trigger_occurrence_id__in=occurrence_ids,
        ).update(trigger_occurrence=None)


@transaction.atomic
def delete_quality_order(*, order_id, confirm_code, actor, reason="", request=None):
    """Permanently delete an OQ and all exclusive dependent records in one transaction."""
    if not (actor and getattr(actor, "is_authenticated", False) and (actor.is_superuser or actor.has_perm("qualidade.delete_qualityorder"))):
        raise PermissionDenied("Você não tem permissão para excluir Ordens de Qualidade.")
    if not QualityDataScopeService.orders_for(actor, include_archived=True).filter(pk=order_id).exists():
        raise QualityOrder.DoesNotExist("Ordem de Qualidade fora do escopo autorizado.")

    order = QualityOrder.objects.select_for_update().get(pk=order_id)
    expected_code = str(order.code or "").strip()
    if not expected_code or str(confirm_code or "").strip() != expected_code:
        raise ValidationError("O código informado não corresponde à OQ. Nenhuma alteração foi feita.")

    clean_reason = str(reason or "").strip()[:1000]
    treatments = list(QualityTreatment.objects.select_for_update().filter(quality_order=order))
    treatment_ids = [item.pk for item in treatments]
    treatment_codes = [item.code for item in treatments if item.code]
    correlation_ids = [item.correlation_id for item in treatments if item.correlation_id]

    cycles = list(QualityMonitoringCycle.objects.select_for_update().filter(quality_order=order))
    tickets = list(QualityTicket.objects.select_for_update().filter(quality_order=order))
    occurrences = list(QualityOccurrence.objects.select_for_update().filter(quality_order=order))
    actions = list(QualityAction.objects.select_for_update().filter(quality_order=order))
    evidences = list(QualityEvidence.objects.select_for_update().filter(quality_order=order))
    order_audit_events = list(QualityAuditEvent.objects.select_for_update().filter(quality_order=order))
    central_audit_qs = _exclusive_audit_event_queryset(order, treatment_ids, treatment_codes)
    central_audit_count = central_audit_qs.count()

    exclusive_files, exclusive_dirs = _collect_exclusive_files_and_dirs(order, treatment_codes, evidences)
    _detach_cross_order_references(
        order,
        treatment_ids,
        [item.pk for item in tickets],
        [item.pk for item in occurrences],
    )

    now = timezone.now()
    if correlation_ids or treatment_codes:
        identity_filter = Q()
        if correlation_ids:
            identity_filter |= Q(correlation_id__in=correlation_ids)
        if treatment_codes:
            identity_filter |= Q(code__in=treatment_codes)
        QualityTreatmentIdentity.objects.select_for_update().filter(identity_filter).update(deleted_at=now)

    QualityDeletedOrder.objects.update_or_create(
        order_code=expected_code,
        defaults={"original_order_id": order.pk, "deleted_at": now},
    )
    seq_row, _ = QualityTreatmentSequence.objects.select_for_update().get_or_create(
        key="oq", defaults={"last_value": order.pk},
    )
    if seq_row.last_value < order.pk:
        seq_row.last_value = order.pk
        seq_row.save(update_fields=("last_value",))

    # Delete strictly in dependency order: cycles (PROTECT->treatment) before treatments (PROTECT->order).
    QualityMonitoringCycle.objects.filter(quality_order=order).delete()
    QualityEvidence.objects.filter(quality_order=order).delete()
    QualityTreatment.objects.filter(quality_order=order).delete()
    QualityTicket.objects.filter(quality_order=order).delete()
    QualityOccurrence.objects.filter(quality_order=order).delete()
    QualityAction.objects.filter(quality_order=order).delete()
    QualityAuditEvent.objects.filter(quality_order=order).delete()
    central_audit_qs.delete()
    QualityOrder.objects.filter(pk=order.pk).delete()

    removed_counts = {
        "treatments": len(treatments),
        "monitoring_cycles": len(cycles),
        "quality_tickets": len(tickets),
        "occurrences": len(occurrences),
        "actions": len(actions),
        "evidences": len(evidences),
        "quality_audit_events": len(order_audit_events),
        "central_audit_events": central_audit_count,
    }
    total_dependents = sum(removed_counts.values())
    AuditService.record(
        request=request,
        user=actor,
        category="ADMINISTRATION",
        action="OQ_DELETED",
        module="Qualidade",
        description=f"Ordem de Qualidade {expected_code} excluída definitivamente.",
        object_label=expected_code,
        metadata={
            "order_code": expected_code,
            "deleted_dependents": removed_counts,
            **({"reason": clean_reason} if clean_reason else {}),
        },
    )
    if exclusive_files or exclusive_dirs:
        transaction.on_commit(lambda files=exclusive_files, dirs=exclusive_dirs: _cleanup_exclusive_files(files, dirs))
    return {
        "order_code": expected_code,
        "counts": removed_counts,
        "total_dependents": total_dependents,
        "deleted_files": len(exclusive_files),
    }


def _user_label(user):
    if user is None:
        return "Não atribuído"
    return user.get_full_name() or user.get_username()
