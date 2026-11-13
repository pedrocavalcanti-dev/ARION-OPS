"""Idempotent TQ delivery state transitions; this module never performs network calls."""
import uuid

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from ..models import QualityDeletedOrder, QualityOrder, QualityTreatment, QualityTreatmentIdentity


@transaction.atomic
def claim_quality_treatment_send(treatment_id, *, allow_retry=False, method="api_rest"):
    """Atomically claim a new send or an explicitly requested definitive retry."""
    now = timezone.now()
    eligible = Q(status=QualityTreatment.Status.PENDING_SEND, send_status=QualityTreatment.SendStatus.NOT_SENT)
    if allow_retry:
        eligible |= Q(status=QualityTreatment.Status.SEND_FAILED, send_status=QualityTreatment.SendStatus.FAILED)
    treatment = (
        QualityTreatment.objects.select_for_update()
        .filter(pk=treatment_id)
        .only("pk", "code", "correlation_id", "quality_order_id", "send_attempt_count")
        .first()
    )
    if treatment is None:
        return {"status": "NOT_CLAIMED", "treatment": None}
    if (
        QualityTreatmentIdentity.objects.filter(
            Q(correlation_id=treatment.correlation_id) | Q(code=treatment.code),
            deleted_at__isnull=False,
        ).exists()
        or QualityDeletedOrder.objects.filter(original_order_id=treatment.quality_order_id).exists()
        or not QualityOrder.objects.filter(pk=treatment.quality_order_id).exists()
    ):
        return {"status": "NOT_CLAIMED", "treatment": None}
    reconciled_uncertain = AuditEvent.objects.filter(
        category="QUALITY", action="TREATMENT_UNCERTAIN_RECONCILED_NOT_DELIVERED",
        object_type="QualityTreatment", object_id=str(treatment_id),
    ).exists()
    claim_query = QualityTreatment.objects.filter(pk=treatment_id).filter(eligible)
    if reconciled_uncertain:
        # One manually authorized retry after an UNCERTAIN reconciliation, ever.
        claim_query = claim_query.filter(send_attempt_count=1)
    claimed = claim_query.filter(
        quality_ticket_external_id__isnull=True,
    ).update(
        send_status=QualityTreatment.SendStatus.IN_PROGRESS,
        status=QualityTreatment.Status.PENDING_SEND,
        send_started_at=now,
        send_attempt_count=F("send_attempt_count") + 1,
        send_error="",
        send_method=str(method)[:16],
        send_attempt_id=uuid.uuid4(),
        updated_at=now,
    )
    if not claimed:
        return {"status": "NOT_CLAIMED", "treatment": QualityTreatment.objects.filter(pk=treatment_id).first()}
    return {"status": "CLAIMED", "treatment": QualityTreatment.objects.get(pk=treatment_id)}


@transaction.atomic
def record_tq_send_accepted(treatment_id, message_id=""):
    """Record Brevo acceptance only; this is not proof that an OTRS ticket exists."""
    now = timezone.now()
    treatment = (
        QualityTreatment.objects.select_for_update()
        .filter(pk=treatment_id)
        .only("pk", "quality_order_id", "send_status")
        .first()
    )
    if treatment is None or QualityDeletedOrder.objects.filter(original_order_id=treatment.quality_order_id).exists():
        return False
    updated = QualityTreatment.objects.filter(
        pk=treatment_id, send_status=QualityTreatment.SendStatus.IN_PROGRESS,
    ).update(
        send_status=QualityTreatment.SendStatus.SENT,
        sent_at=now,
        brevo_message_id=str(message_id or "")[:250],
        status=QualityTreatment.Status.WAITING_OTRS,
        send_error="",
        updated_at=now,
    )
    return updated == 1


@transaction.atomic
def record_tq_send_failure(treatment_id, message, *, uncertain=False):
    """Persist safe known/ambiguous failures; ambiguous outcomes remain non-retryable."""
    treatment = (
        QualityTreatment.objects.select_for_update()
        .filter(pk=treatment_id)
        .only("pk", "quality_order_id", "send_status")
        .first()
    )
    if treatment is None or QualityDeletedOrder.objects.filter(original_order_id=treatment.quality_order_id).exists():
        return False
    status = QualityTreatment.SendStatus.UNCERTAIN if uncertain else QualityTreatment.SendStatus.FAILED
    treatment_status = QualityTreatment.Status.PENDING_SEND if uncertain else QualityTreatment.Status.SEND_FAILED
    updated = QualityTreatment.objects.filter(
        pk=treatment_id, send_status=QualityTreatment.SendStatus.IN_PROGRESS,
    ).update(
        send_status=status,
        status=treatment_status,
        send_error=str(message or "Falha segura no envio.")[:240],
        updated_at=timezone.now(),
    )
    return updated == 1
