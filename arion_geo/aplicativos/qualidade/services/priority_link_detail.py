"""Read-only investigation history for a scoped priority link."""
from django.core.paginator import Paginator
from django.db.models import Min, Q

from aplicativos.integracoes.models import OTRSStateMapping, OTRSTicket, OTRSTicketConfigItem
from aplicativos.integracoes.services.project_names import display_project_name
from aplicativos.integracoes.services.semantics import link_designation, provider_values_for_links

from ..models import QualityOccurrence, QualityOrder
from .constants import QUALITY_PRIORITY_PERIOD_OPTIONS
from .priority_links import build_priority_link_analysis


PAGE_SIZE = 25
EXCLUSION_LABELS = {
    "CANCELADO": "Cancelado",
    "IMPROCEDENTE": "Improcedente",
    "DUPLICADO": "Duplicado",
    "FORA_ESCOPO": "Fora do escopo",
    "OUTRO": "Outro motivo",
}


def _review_index(link, external_ids, scoped_orders):
    if not external_ids:
        return {}
    reviews = QualityOccurrence.objects.filter(
        quality_order__in=scoped_orders,
        ticket_external_id_snapshot__in=external_ids,
    ).filter(
        Q(quality_order__link_id=link.source_config_item_id)
        | Q(quality_order__ci_snapshot=link.source_config_item_id)
    ).select_related("quality_order").order_by(
        "ticket_external_id_snapshot", "-quality_order__updated_at", "-pk",
    )
    indexed = {}
    for review in reviews:
        indexed.setdefault(review.ticket_external_id_snapshot, review)
    return indexed


def get_priority_link_detail(user, identifier, params):
    """Return the current candidate and a paginated, locally mirrored ticket history."""
    analysis = build_priority_link_analysis(user, params.get("period"), state="all", date_from=params.get("date_from",""), date_to=params.get("date_to",""))
    link = analysis["links"].filter(source_config_item_id=identifier).first()
    if link is None:
        return None

    direction = str(params.get("direction", "recent"))
    if direction not in {"recent", "oldest"}:
        direction = "recent"
    history_filter = str(params.get("history", "all"))
    if history_filter not in {"all", "valid", "excluded"}:
        history_filter = "all"
    state_filter = str(params.get("state", "all"))
    if state_filter not in {"active", "stabilized", "all"}:
        state_filter = "all"

    relations = OTRSTicketConfigItem.objects.filter(
        cmdb_link=link,
        cmdb_link__is_present_in_source=True,
        is_present_in_source=True,
    )
    linked_ticket_ids = relations.order_by().values("ticket_id")
    history_tickets = analysis["period_tickets"].filter(pk__in=linked_ticket_ids)
    valid_tickets = analysis["valid_tickets"].filter(pk__in=linked_ticket_ids)
    first_occurrence = valid_tickets.aggregate(first=Min("source_created_at"))["first"]
    recurrence_summary = analysis["recurrence"]["links"].get(link.source_config_item_id, {})
    current_episode = recurrence_summary.get("current_episode")
    occurrence_index = {
        item["external_id"]: item
        for item in recurrence_summary.get("occurrences", ())
    }
    sequence_by_external_id = {
        external_id: item
        for external_id, item in occurrence_index.items()
    }
    valid_external_ids = set(occurrence_index)
    period_occurrences = [
        item for item in recurrence_summary.get("occurrences", ())
        if analysis["cutoff"] <= item["occurred_at"] < analysis["end_exclusive"]
    ]
    period_recurrence_ids = {
        item["external_id"] for item in period_occurrences if item.get("is_recurrence")
    }
    same_cause_recurrences = QualityOccurrence.objects.filter(
        quality_order__in=analysis["scoped_orders"],
        excluded=False,
        same_root_cause=True,
        ticket_external_id_snapshot__in=period_recurrence_ids,
    ).filter(
        Q(quality_order__link_id=link.source_config_item_id)
        | Q(quality_order__ci_snapshot=link.source_config_item_id)
    ).values("ticket_external_id_snapshot").distinct().count()

    if history_filter == "valid":
        history_tickets = history_tickets.filter(pk__in=valid_tickets.values("pk"))
    elif history_filter == "excluded":
        history_tickets = history_tickets.exclude(pk__in=valid_tickets.values("pk"))

    order_by = ("source_created_at", "external_id")
    if direction == "recent":
        order_by = ("-source_created_at", "-external_id")
    paginator = Paginator(history_tickets.order_by(*order_by), PAGE_SIZE)
    page = paginator.get_page(params.get("page"))
    tickets = list(page.object_list)
    reviews = _review_index(
        link,
        [ticket.external_id for ticket in tickets],
        analysis["scoped_orders"],
    )

    for ticket in tickets:
        review = reviews.get(ticket.external_id)
        occurrence = sequence_by_external_id.get(ticket.external_id)
        position = occurrence.get("episode_position") if occurrence else None
        episode_number = occurrence.get("episode_number") if occurrence else None
        ticket.quality_review = review
        ticket.is_valid_occurrence = ticket.external_id in valid_external_ids
        ticket.exclusion_label = ""
        ticket.same_cause_label = ""
        ticket.reason_display = "Não classificado"
        ticket.root_cause_display = "Não classificada"
        ticket.sequence_label = ""

        if position == 1:
            ticket.sequence_label = f"Episódio {episode_number:02d} · Ocorrência inicial"
        elif position:
            ticket.sequence_label = f"Episódio {episode_number:02d} · Reincidência #{position - 1}"

        if review and review.reason.strip():
            ticket.reason_display = review.reason.strip()
        elif ticket.closing_code_raw.strip():
            ticket.reason_display = ticket.closing_code_raw.strip()
        if review and review.root_cause.strip():
            ticket.root_cause_display = review.root_cause.strip()

        if ticket.is_valid_occurrence:
            ticket.exclusion_label = "Válida"
            review_in_window = bool(
                review
                and analysis["cutoff"] <= review.occurred_at < analysis["end_exclusive"]
            )
            if (review_in_window and review.counts_as_recurrence and review.same_root_cause
                    and not review.excluded):
                ticket.same_cause_label = "Mesma causa confirmada"
            elif position and position > 1:
                ticket.same_cause_label = "Mesma causa não confirmada"
        else:
            if ticket.business_status == OTRSStateMapping.BusinessStatus.CANCELED:
                ticket.exclusion_label = "Cancelado · não contabilizado"
            elif ticket.responsibility_normalized == "IMPROCEDENTE":
                ticket.exclusion_label = "Improcedente · não contabilizado"
            elif review and review.excluded:
                label = EXCLUSION_LABELS.get(review.exclusion_reason, "Desconsiderado")
                ticket.exclusion_label = f"{label} · não contabilizado"
            elif ticket.business_status == OTRSStateMapping.BusinessStatus.UNKNOWN:
                ticket.exclusion_label = "Estado não mapeado · não contabilizado"
            else:
                ticket.exclusion_label = "Não contabilizado pelas regras da carteira"

    semantics = provider_values_for_links([link])[link.pk]
    link.provider_id = semantics.provider_id
    link.provider_name = semantics.provider_name
    link.isp_display = link.provider_name
    link.designation_display = link_designation(link)
    link.project_display = display_project_name(link.business_project or link.legacy_project)
    link.priority_label = dict(QualityOrder.Priority.choices).get(link.active_priority, "Sem classificação")
    link.level_label = link.active_level or "A classificar"
    link.status_label = {
        QualityOrder.Status.NOVA: "Nova",
        QualityOrder.Status.TRIAGEM: "Triagem",
        QualityOrder.Status.TRATAMENTO: "Em tratamento",
        QualityOrder.Status.AGUARDANDO_TERCEIRO: "Aguardando terceiro",
        QualityOrder.Status.MONITORAMENTO: "Monitoramento",
    }.get(link.active_order_status, "")
    excluded_history_count = analysis["period_tickets"].filter(
        pk__in=linked_ticket_ids,
    ).exclude(pk__in=valid_tickets.values("pk")).count()

    return {
        "link": link,
        "period": analysis["period"], "period_label":analysis["period_label"],
        "date_from":analysis["date_from"], "date_to":analysis["date_to"], "filter_notices":analysis["notices"],
        "operational_state_filter": state_filter,
        "period_options": analysis["period_options"],
        "first_occurrence": first_occurrence,
        "last_occurrence": recurrence_summary.get("last_occurrence"),
        "valid_occurrence_count": valid_tickets.count(),
        "recurrence_count": len(period_recurrence_ids),
        "current_episode_recurrence_count": recurrence_summary.get("recurrence_count", 0),
        "is_active_recurrence": bool(recurrence_summary.get("is_active_recurrence")),
        "is_stabilized": bool(recurrence_summary.get("is_stabilized")),
        "operational_state": recurrence_summary.get("operational_state", "SINGLE_OCCURRENCE"),
        "operational_state_label": recurrence_summary.get("operational_state_label", "Ocorrência isolada"),
        "current_episode_number": current_episode.number if current_episode else None,
        "current_episode_started_at": current_episode.started_at if current_episode else None,
        "current_episode_last_at": current_episode.last_at if current_episode else None,
        "current_episode_days_since_last": current_episode.days_since_last if current_episode else None,
        "operational_recurrence_level": current_episode.operational_recurrence_level if current_episode else "Inicial",
        "current_episode_stabilized_at": current_episode.stabilized_at if current_episode else None,
        "reopened_after_stability": bool(recurrence_summary.get("reopened_after_stability")),
        "same_cause_recurrences": same_cause_recurrences,
        "page_obj": page,
        "tickets": tickets,
        "history_filter": history_filter,
        "direction": direction,
        "history_count": paginator.count,
        "valid_history_count": len(valid_external_ids),
        "excluded_history_count": excluded_history_count,
    }
