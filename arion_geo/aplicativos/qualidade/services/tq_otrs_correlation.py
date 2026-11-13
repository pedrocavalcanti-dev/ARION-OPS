"""Bounded, read-only incremental OTRS article correlation for Quality TQs."""
from types import SimpleNamespace
import os

from django.conf import settings
from django.utils import timezone

from aplicativos.auditoria.services import AuditService

from ..models import QualityCorrelationCheckpoint, QualityDeletedOrder, QualityOrder, QualityTreatment
from .tq_correlation import link_quality_ticket_from_article

ARTICLE_ID_OVERLAP = 40


def quality_queue_names():
    configured = getattr(settings, "QUALITY_OTRS_QUEUE_NAMES", None)
    if configured is None:
        configured = os.getenv("ARION_QUALITY_OTRS_QUEUE_NAMES", "NOC - QUALIDADE ATACADO")
    if isinstance(configured, str):
        configured = configured.split(",")
    return tuple(dict.fromkeys(str(name).strip() for name in configured if str(name).strip()))


def reconcile_quality_articles(client, *, limit=100):
    """Correlate a small article batch. Bodies remain transient and are never saved."""
    queues = quality_queue_names()
    if not queues:
        raise ValueError("Fila de Qualidade não configurada.")
    limit = max(1, min(int(limit), 200))
    result = {"articles": 0, "linked": 0, "already_linked": 0, "without_qid": 0, "conflicts": 0, "tickets_refreshed": 0}
    for queue_name in queues:
        checkpoint, _ = QualityCorrelationCheckpoint.objects.get_or_create(queue_name=queue_name)
        cursor = checkpoint.last_article_id
        rows = client.fetch_quality_articles(
            queue_name, after_article_id=max(0, cursor - ARTICLE_ID_OVERLAP), limit=limit,
        )
        next_cursor = cursor
        for row in rows:
            ticket_id = int(row.get("ticket_id") or 0)
            article_id = int(row.get("article_id") or 0)
            ticket = SimpleNamespace(
                external_id=ticket_id,
                ticket_number=str(row.get("ticket_number") or ""),
                queue_external_id=int(row.get("queue_id") or 0),
                queue_name=str(row.get("queue_name") or ""),
                external_state_id=int(row.get("state_id") or 0),
                external_state_name=str(row.get("state_name") or ""),
                is_present_in_queue=True,
            )
            linked = link_quality_ticket_from_article(
                ticket=ticket, article_body=row.get("article_body"),
                allowed_queue_names=(queue_name,), trusted_source=True,
            )
            status = linked["status"]
            result["articles"] += 1
            if status == "LINKED":
                result["linked"] += 1
            elif status == "ALREADY_LINKED":
                result["already_linked"] += 1
            elif status in {"TREATMENT_TICKET_CONFLICT", "TICKET_TREATMENT_CONFLICT", "MULTIPLE_CORRELATION_IDS"}:
                result["conflicts"] += 1
            elif status in {"MISSING_CORRELATION_ID", "TREATMENT_NOT_FOUND", "TREATMENT_DELETED", "ORDER_DELETED"}:
                result["without_qid"] += 1
            next_cursor = max(next_cursor, article_id)
        checkpoint.last_article_id = next_cursor
        checkpoint.last_success_at = timezone.now()
        checkpoint.last_error_at = None
        checkpoint.last_error = ""
        checkpoint.failure_count = 0
        checkpoint.records_processed = result["articles"]
        checkpoint.save(update_fields=(
            "last_article_id", "last_success_at", "last_error_at", "last_error",
            "failure_count", "records_processed", "updated_at",
        ))
    result["tickets_refreshed"] = refresh_linked_quality_ticket_states(
        client, queue_names=queues, limit=limit,
    )
    return result


def refresh_linked_quality_ticket_states(client, *, queue_names, limit=100):
    """Refresh linked ticket snapshots and retain a local audit if an ID disappears."""
    treatments = list(
        QualityTreatment.objects.filter(quality_ticket_external_id__isnull=False)
        .only("pk", "code", "quality_order_id", "quality_ticket_external_id", "quality_ticket_state_snapshot")
        .order_by("pk")[:max(1, min(int(limit), 200))]
    )
    if not treatments:
        return 0
    by_ticket_id = {item.quality_ticket_external_id: item for item in treatments}
    rows = client.fetch_quality_ticket_states(
        by_ticket_id.keys(), queue_names=queue_names, limit=limit,
    )
    rows_by_id = {int(row.get("ticket_id") or 0): row for row in rows}
    now = timezone.now()
    refreshed = 0
    unavailable_label = "Indisponivel no OTRS"
    for ticket_id, treatment in by_ticket_id.items():
        if QualityDeletedOrder.objects.filter(original_order_id=treatment.quality_order_id).exists():
            continue
        row = rows_by_id.get(ticket_id)
        if row is None:
            was_unavailable = treatment.quality_ticket_state_snapshot == unavailable_label
            updated = QualityTreatment.objects.filter(pk=treatment.pk).exclude(
                quality_ticket_state_snapshot=unavailable_label,
            ).update(
                quality_ticket_state_snapshot=unavailable_label,
                quality_ticket_last_read_at=now,
                updated_at=now,
            )
            if updated and not was_unavailable:
                order = QualityOrder.objects.filter(pk=treatment.quality_order_id).first()
                if order is not None and not QualityDeletedOrder.objects.filter(original_order_id=order.pk).exists():
                    AuditService.record(
                        user=None, category="QUALITY", action="QUALITY_TICKET_UNAVAILABLE",
                        module="Qualidade",
                        description=f"O vínculo OTRS da TQ {treatment.code} não foi localizado na consulta por ID.",
                        obj=order,
                        metadata={
                            "code": order.code,
                            "treatment_code": treatment.code,
                            "ticket_external_id": treatment.quality_ticket_external_id,
                            "issue": "linked_ticket_id_not_found",
                        },
                    )
            refreshed += updated
            continue
        updated = QualityTreatment.objects.filter(pk=treatment.pk).update(
            quality_ticket_number=str(row.get("ticket_number") or treatment.quality_ticket_number)[:80],
            quality_ticket_queue_snapshot=str(row.get("queue_name") or "")[:250],
            quality_ticket_state_snapshot=str(row.get("state_name") or "")[:250],
            quality_ticket_last_read_at=now,
            updated_at=now,
        )
        refreshed += updated
    return refreshed
