"""Scoped context and occurrence history for a Quality Order detail page."""
from django.core.paginator import Paginator
from django.utils import timezone

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicketConfigItem
from aplicativos.integracoes.services.project_names import display_project_name
from aplicativos.integracoes.services.semantics import (
    clean_link_designation,
    link_designation,
    provider_names_for_values,
    provider_values_for_links,
)

from ..models import QualityMonitoringCycle, QualityOccurrence, QualityOrder, QualityTicket, QualityTreatment
from .data_scope import QualityDataScopeService
from .recurrence import build_quality_recurrence_analysis
from .ticket_validity import quality_ticket_sets


PAGE_SIZE = 25


def get_quality_order_detail(user, order, *, tab="resumo", history_filter="all", direction="recent", page=1, excluded_page=1):
    """Build summary data and paginated local OTRS mirror history for an in-scope OQ."""
    valid_tabs = {"resumo", "ocorrencias", "tratativas", "monitoramento", "historico"}
    tab = tab if tab in valid_tabs else "resumo"
    history_filter = history_filter if history_filter in {"all", "valid", "excluded", "pending"} else "all"
    direction = direction if direction in {"recent", "oldest"} else "recent"
    scoped_orders = QualityDataScopeService.orders_for(user)
    scope_links = DataScopeService.filter_queryset(
        user,
        CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    )
    link = scope_links.filter(source_config_item_id=order.link_id).first() if order.link_id else None
    valid_count = excluded_count = recurrence_count = same_cause_count = pending_count = 0
    all_ticket_count = 0
    valid_queryset = None
    occurrence_page = excluded_ticket_page = None
    review_index = {}
    recurrence_summary = {}
    current_episode = None
    occurrence_index = {}

    if link:
        relation_ids = OTRSTicketConfigItem.objects.filter(
            cmdb_link=link,
            cmdb_link__is_present_in_source=True,
            is_present_in_source=True,
        ).order_by().values("ticket_id")
        ticket_sets = quality_ticket_sets(user, scope_links=scope_links, scoped_orders=scoped_orders)
        recurrence_analysis = build_quality_recurrence_analysis(
            user, scope_links=scope_links, scoped_orders=scoped_orders,
        )
        recurrence_summary = recurrence_analysis["links"].get(link.source_config_item_id, {})
        current_episode = recurrence_summary.get("current_episode")
        occurrence_index = {
            item["external_id"]: item
            for item in recurrence_summary.get("occurrences", ())
        }
        all_tickets = ticket_sets["period_tickets"].filter(pk__in=relation_ids).distinct()
        valid_queryset = ticket_sets["valid_tickets"].filter(pk__in=relation_ids).distinct()
        all_ticket_count = all_tickets.count()
        valid_count = valid_queryset.count()
        excluded_count = max(all_ticket_count - valid_count, 0)
        recurrence_count = recurrence_summary.get("total_recurrence_count", 0)
        valid_ids = valid_queryset.order_by().values("external_id")
        operational_recurrence_ids = [
            external_id for external_id, item in occurrence_index.items()
            if item.get("is_recurrence")
        ]
        same_cause_count = QualityOccurrence.objects.filter(
            quality_order=order,
            excluded=False,
            same_root_cause=True,
            ticket_external_id_snapshot__in=operational_recurrence_ids,
        ).values("ticket_external_id_snapshot").distinct().count()
        excluded_ids = QualityOccurrence.objects.filter(
            quality_order=order, excluded=True, ticket_external_id_snapshot__in=valid_ids,
        ).order_by().values("ticket_external_id_snapshot")
        recurrence_ids = QualityOccurrence.objects.filter(
            quality_order=order, excluded=False, counts_as_recurrence=True,
            ticket_external_id_snapshot__in=valid_ids,
        ).order_by().values("ticket_external_id_snapshot")
        initial_ids = QualityOccurrence.objects.filter(
            quality_order=order, excluded=False, is_initial_occurrence=True,
            ticket_external_id_snapshot__in=valid_ids,
        ).order_by().values("ticket_external_id_snapshot")
        reviewed_ids = excluded_ids.union(recurrence_ids, initial_ids)
        pending_count = valid_queryset.exclude(external_id__in=reviewed_ids).count()
        review_index = {
            item.ticket_external_id_snapshot: item
            for item in QualityOccurrence.objects.filter(
                quality_order=order,
                ticket_external_id_snapshot__in=all_tickets.order_by().values("external_id"),
            ).order_by("ticket_external_id_snapshot", "-pk")
        }

        ordering = ("source_created_at", "external_id") if direction == "oldest" else ("-source_created_at", "-external_id")
        valid_history = valid_queryset
        excluded_history = all_tickets.exclude(pk__in=valid_queryset.values("pk"))
        if history_filter == "pending":
            valid_history = valid_history.exclude(external_id__in=reviewed_ids)
        if history_filter == "excluded":
            excluded_ticket_page = Paginator(excluded_history.order_by(*ordering), PAGE_SIZE).get_page(excluded_page)
        elif history_filter == "all":
            occurrence_page = Paginator(valid_history.order_by(*ordering), PAGE_SIZE).get_page(page)
            excluded_ticket_page = Paginator(excluded_history.order_by(*ordering), PAGE_SIZE).get_page(excluded_page)
        else:
            occurrence_page = Paginator(valid_history.order_by(*ordering), PAGE_SIZE).get_page(page)
        for ticket in occurrence_page.object_list if occurrence_page else ():
            occurrence = occurrence_index.get(ticket.external_id)
            if occurrence:
                ticket.sequence_label = (
                    f"Episódio {occurrence['episode_number']:02d} · Ocorrência inicial"
                    if not occurrence["is_recurrence"]
                    else f"Episódio {occurrence['episode_number']:02d} · Reincidência #{occurrence['recurrence_number']}"
                )

    semantics = provider_values_for_links([link])[link.pk] if link else None
    snapshot_provider = provider_names_for_values([order.isp_snapshot]).get(order.isp_snapshot, "") if order.isp_snapshot else ""
    provider_name = semantics.provider_name if semantics else (snapshot_provider or "Não classificado")
    designation = link_designation(link) if link else clean_link_designation(order.designation_snapshot)
    latest_review = QualityOccurrence.objects.filter(quality_order=order).order_by("-occurred_at", "-pk").first()
    previous_review = QualityOccurrence.objects.filter(quality_order=order).order_by("-occurred_at", "-pk")[1:2].first()
    latest_occurrence = valid_queryset.order_by("-source_created_at", "-external_id").first() if valid_queryset is not None else None
    first_occurrence = (
        valid_queryset.order_by("source_created_at", "external_id").first()
        if valid_queryset is not None and tab == "tratativas" else None
    )
    treatment_ticket = QualityTicket.objects.filter(
        quality_order=order, ticket_type=QualityTicket.TicketType.TREATMENT,
    ).select_related("otrs_ticket").order_by("-created_at", "-pk").first()
    local_treatment_count = QualityTreatment.objects.filter(quality_order=order).count()
    treatment_ticket_url = ""
    if treatment_ticket and treatment_ticket.ticket_external_id_snapshot:
        from aplicativos.chamados.services import otrs_ticket_url
        treatment_ticket_url = otrs_ticket_url(treatment_ticket.ticket_external_id_snapshot)
    active_cycle = QualityMonitoringCycle.objects.filter(
        quality_order=order, result=QualityMonitoringCycle.Result.IN_PROGRESS,
    ).select_related("quality_ticket").order_by("-cycle_number").first()
    latest_event = order.audit_events.select_related("actor").order_by("-created_at", "-pk").first()
    status_labels = {
        QualityOrder.Status.NOVA: "Nova",
        QualityOrder.Status.TRIAGEM: "Triagem",
        QualityOrder.Status.TRATAMENTO: "Em tratamento",
        QualityOrder.Status.AGUARDANDO_TERCEIRO: "Aguardando terceiro",
        QualityOrder.Status.MONITORAMENTO: "Monitoramento",
        QualityOrder.Status.CONCLUIDA: "Concluída",
        QualityOrder.Status.CANCELADA: "Cancelada",
    }
    lifecycle_stages = (
        ("Nova", QualityOrder.Status.NOVA),
        ("Triagem", QualityOrder.Status.TRIAGEM),
        ("Tratamento", QualityOrder.Status.TRATAMENTO),
        ("Monitoramento", QualityOrder.Status.MONITORAMENTO),
        ("Concluída", QualityOrder.Status.CONCLUIDA),
    )
    current_stage = {
        QualityOrder.Status.AGUARDANDO_TERCEIRO: QualityOrder.Status.TRATAMENTO,
    }.get(order.status, order.status)
    lifecycle_steps = [
        {"label": label, "current": current_stage == value}
        for label, value in lifecycle_stages
    ]
    event_labels = {
        "LEVEL_CHANGED": "Nível alterado",
        "DEADLINE_CHANGED": "Prazo alterado",
        "OCCURRENCE_REVIEWED": "Ocorrência revisada",
        "NOTES_CHANGED": "Observação atualizada",
    }

    return {
        "order": order,
        "link": link,
        "operator_name": semantics.contract_operator_name if semantics else "",
        "designation": designation,
        "ci_display": order.ci_snapshot or (link.source_config_item_id if link else "—"),
        "source_record": link.source_number if link else "",
        "provider_id": semantics.provider_id if semantics else None,
        "provider_name": provider_name,
        "client": (link.contracting_client if link else order.client_snapshot) or "—",
        "project": display_project_name((link.business_project or link.legacy_project) if link else order.project_snapshot) or "—",
        "city": (link.city if link else "") or "—",
        "uf": (link.state if link else order.uf_snapshot) or "—",
        "speed": (link.speed_raw if link else "") or "—",
        "circuit_status": ((link.deployment_state or link.incident_state) if link else "") or "—",
        "owner_label": (order.owner.get_full_name() or order.owner.get_username()) if order.owner_id else "Não atribuído",
        "status_label": status_labels.get(order.status, order.get_status_display()),
        "priority_label": order.get_priority_display() or "Sem classificação",
        "level_label": order.get_recurrence_level_display() if order.recurrence_level else "A classificar",
        "lifecycle_label": status_labels.get(order.status, order.get_status_display()),
        "lifecycle_steps": lifecycle_steps,
        "latest_event_label": event_labels.get(latest_event.event_type, latest_event.get_event_type_display()) if latest_event else "",
        "active_tab": tab,
        "history_filter": history_filter,
        "direction": direction,
        "valid_occurrence_count": valid_count,
        "recurrence_count": recurrence_count,
        "operational_state": recurrence_summary.get("operational_state", "SINGLE_OCCURRENCE"),
        "operational_state_label": recurrence_summary.get("operational_state_label", "Sem ocorrência válida"),
        "operational_is_active": bool(recurrence_summary.get("is_active_recurrence")),
        "operational_is_stabilized": bool(recurrence_summary.get("is_stabilized")),
        "current_episode_number": current_episode.number if current_episode else None,
        "current_episode_started_at": current_episode.started_at if current_episode else None,
        "current_episode_last_at": current_episode.last_at if current_episode else None,
        "current_episode_days_since_last": current_episode.days_since_last if current_episode else None,
        "current_episode_recurrence_count": current_episode.recurrence_count if current_episode else 0,
        "operational_recurrence_level": current_episode.operational_recurrence_level if current_episode else "Inicial",
        "same_cause_count": same_cause_count,
        "quality_ticket_count": local_treatment_count or QualityTicket.objects.filter(
            quality_order=order, ticket_type=QualityTicket.TicketType.TREATMENT,
        ).count(),
        "monitoring_cycle_count": order.monitoring_cycles.count(),
        "excluded_history_count": excluded_count,
        "pending_review_count": pending_count,
        "all_ticket_count": all_ticket_count,
        "latest_occurrence": latest_occurrence,
        "first_occurrence": first_occurrence,
        "current_reason": order.current_reason or (latest_review.reason if latest_review else ""),
        "current_root_cause": order.current_root_cause or (latest_review.root_cause if latest_review else ""),
        "previous_reason": previous_review.reason if previous_review else "",
        "previous_same_cause": previous_review.same_root_cause if previous_review else None,
        "treatment_ticket": treatment_ticket,
        "treatment_ticket_url": treatment_ticket_url,
        "active_cycle": active_cycle,
        "cycle_days_elapsed": max((timezone.localdate() - timezone.localdate(active_cycle.started_at)).days, 0) if active_cycle else 0,
        "latest_event": latest_event,
        "occurrence_page": occurrence_page,
        "excluded_ticket_page": excluded_ticket_page,
        "review_index": review_index,
        "has_link_history": bool(link),
    }
