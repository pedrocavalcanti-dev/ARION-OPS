from django.db import transaction
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicket, OTRSTicketConfigItem

from ..models import QualityAuditEvent, QualityOccurrence
from .data_scope import QualityDataScopeService


@transaction.atomic
def link_occurrence(*, quality_order, actor=None, **fields):
    """Attach a manually reviewed local OTRS mirror record to an OQ."""
    occurrence = QualityOccurrence.objects.create(quality_order=quality_order, **fields)
    QualityAuditEvent.objects.create(
        quality_order=quality_order,
        event_type=QualityAuditEvent.EventType.OCCURRENCE_LINKED,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        description=f"Ocorrência {occurrence.ticket_number or occurrence.pk} vinculada à OQ.",
    )
    AuditService.record(
        user=actor,
        category="QUALITY",
        action="OCCURRENCE_LINKED",
        module="Qualidade",
        description="Ocorrência vinculada à Ordem de Qualidade.",
        obj=quality_order,
        metadata={"code": quality_order.code, "ticket_number": occurrence.ticket_number},
    )
    if occurrence.counts_as_recurrence:
        QualityAuditEvent.objects.create(
            quality_order=quality_order,
            event_type=QualityAuditEvent.EventType.RECURRENCE_CONFIRMED,
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            description="Reincidência confirmada manualmente.",
        )
    return occurrence


@transaction.atomic
def review_quality_occurrence(*, quality_order, ticket_external_id, classification, same_root_cause=False,
                              reason="", root_cause="", exclusion_reason="", actor=None, request=None):
    """Classify one linked mirrored ticket locally without writing to OTRS."""
    if classification not in {"INITIAL", "RECURRENCE", "EXCLUDED"}:
        raise ValueError("Classificação de ocorrência inválida.")
    if classification == "EXCLUDED" and not exclusion_reason:
        raise ValueError("Informe o motivo da desconsideração.")

    visible_order = QualityDataScopeService.orders_for(actor).filter(pk=quality_order.pk).first() if actor else None
    if visible_order is None:
        raise LookupError("Ordem fora do escopo autorizado.")
    scoped_links = DataScopeService.filter_queryset(
        actor,
        CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    )
    link = scoped_links.filter(source_config_item_id=visible_order.link_id).first() if visible_order.link_id else None
    if link is None:
        raise LookupError("Link fora do escopo autorizado.")
    linked = OTRSTicketConfigItem.objects.filter(
        cmdb_link=link,
        ticket__external_id=ticket_external_id,
        cmdb_link__is_present_in_source=True,
        is_present_in_source=True,
    ).exists()
    if not linked:
        raise LookupError("Ticket não pertence ao link desta OQ.")
    ticket = OTRSTicket.objects.filter(external_id=ticket_external_id, is_present_in_queue=True).first()
    if ticket is None:
        raise LookupError("Ticket não está disponível no espelho local.")

    QualityOccurrence.objects.select_for_update().filter(quality_order=visible_order).first()
    occurrence, created = QualityOccurrence.objects.get_or_create(
        quality_order=visible_order,
        ticket_external_id_snapshot=ticket.external_id,
        defaults={
            "otrs_ticket": ticket,
            "ticket_number": ticket.ticket_number,
            "occurred_at": ticket.source_created_at or timezone.now(),
            "ticket_status_snapshot": ticket.external_state_name,
            "queue_snapshot": ticket.queue_name,
        },
    )
    if not created:
        occurrence.otrs_ticket = ticket
        occurrence.ticket_number = ticket.ticket_number
        occurrence.occurred_at = ticket.source_created_at or occurrence.occurred_at
        occurrence.ticket_status_snapshot = ticket.external_state_name
        occurrence.queue_snapshot = ticket.queue_name

    previous = {
        "is_initial_occurrence": occurrence.is_initial_occurrence,
        "counts_as_recurrence": occurrence.counts_as_recurrence,
        "same_root_cause": occurrence.same_root_cause,
        "excluded": occurrence.excluded,
        "exclusion_reason": occurrence.exclusion_reason,
        "reason": occurrence.reason,
        "root_cause": occurrence.root_cause,
    }
    occurrence.is_initial_occurrence = classification == "INITIAL"
    occurrence.counts_as_recurrence = classification == "RECURRENCE"
    occurrence.same_root_cause = bool(same_root_cause and classification == "RECURRENCE")
    occurrence.excluded = classification == "EXCLUDED"
    occurrence.exclusion_reason = exclusion_reason if classification == "EXCLUDED" else ""
    occurrence.reason = reason.strip()
    occurrence.root_cause = root_cause.strip()
    current = {
        "is_initial_occurrence": occurrence.is_initial_occurrence,
        "counts_as_recurrence": occurrence.counts_as_recurrence,
        "same_root_cause": occurrence.same_root_cause,
        "excluded": occurrence.excluded,
        "exclusion_reason": occurrence.exclusion_reason,
    }
    previous_state = {key: previous[key] for key in current}
    review_values = {
        "reason": occurrence.reason,
        "root_cause": occurrence.root_cause,
    }
    old_review_values = {
        "reason": previous.get("reason", ""),
        "root_cause": previous.get("root_cause", ""),
    }
    if previous_state == current and old_review_values == review_values and not created:
        return occurrence, False
    occurrence.save()

    event_description = f"Revisão da ocorrência {occurrence.ticket_number or ticket.external_id} registrada."
    QualityAuditEvent.objects.create(
        quality_order=visible_order,
        event_type="OCCURRENCE_REVIEWED",
        actor=actor,
        description=event_description,
        metadata={
            "ticket_number": occurrence.ticket_number,
            "classification": classification,
            "previous": previous_state,
            "current": current,
        },
    )
    AuditService.record(
        request=request,
        user=actor,
        category="QUALITY",
        action="OCCURRENCE_REVIEWED",
        module="Qualidade",
        description=event_description,
        obj=visible_order,
        metadata={"code": visible_order.code, "ticket_number": occurrence.ticket_number, "classification": classification},
    )
    return occurrence, True
