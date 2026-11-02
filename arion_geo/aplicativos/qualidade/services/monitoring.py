from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.chamados.services import otrs_ticket_url
from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import OTRSTicket
from aplicativos.integracoes.services.project_names import canonical_project_name, display_project_name, project_alias_groups
from aplicativos.integracoes.services.semantics import (
    clean_link_designation,
    link_designation,
    provider_filter_order_queryset,
    provider_name_options_for_orders,
    provider_names_for_values,
    provider_values_for_links,
)

from .constants import QUALITY_RREIP_DAYS
from .data_scope import QualityDataScopeService
from ..models import QualityAuditEvent, QualityMonitoringCycle, QualityOccurrence, QualityOrder, QualityTicket
from .recurrence import build_quality_recurrence_analysis


MONITORING_TABS = (("active", "Ativos"), ("ending", "Terminando"), ("recurrence", "Reincidiram"), ("completed", "Concluídos"), ("all", "Todos"))
PAGE_SIZES = (25, 50, 100)


@transaction.atomic
def start_monitoring_cycle(
    *, quality_order, quality_ticket=None, started_at=None, duration_days=QUALITY_RREIP_DAYS,
    actor=None, request=None, financial_regularization_confirmed=False, financial_evidence="",
):
    """Start a local RREIP cycle; this service never changes a remote OTRS ticket."""
    if duration_days < 1:
        raise ValueError("A duração do ciclo RREIP deve ser positiva.")
    if not actor or not getattr(actor, "is_authenticated", False):
        raise ValidationError("Usuário autenticado obrigatório.")
    visible_order_ids = QualityDataScopeService.orders_for(actor).filter(pk=quality_order.pk).values("pk")
    try:
        order = QualityOrder.objects.select_for_update().get(pk__in=visible_order_ids)
    except QualityOrder.DoesNotExist:
        raise ValidationError("A OQ não está disponível no escopo do usuário.") from None
    if order.status in {QualityOrder.Status.CONCLUIDA, QualityOrder.Status.CANCELADA}:
        raise ValidationError("Uma OQ concluída ou cancelada não pode iniciar novo monitoramento.")
    if quality_ticket is not None:
        if not isinstance(quality_ticket, QualityTicket) or quality_ticket.quality_order_id != order.pk or quality_ticket.ticket_type != QualityTicket.TicketType.TREATMENT:
            raise ValidationError("O TQ de tratamento deve pertencer à mesma OQ.")
    if order.monitoring_cycles.filter(result=QualityMonitoringCycle.Result.IN_PROGRESS).exists():
        raise ValidationError("Esta OQ já possui um ciclo RREIP em andamento.")
    financial = is_financial_reason(order.current_reason)
    financial_evidence = str(financial_evidence or "").strip()[:2000]
    if financial and not (financial_regularization_confirmed and financial_evidence):
        raise ValidationError("A pendência financeira exige confirmação e evidência de regularização antes do RREIP.")

    started_at = started_at or timezone.now()
    last_cycle = order.monitoring_cycles.order_by("-cycle_number").first()
    cycle = QualityMonitoringCycle.objects.create(
        quality_order=order,
        quality_ticket=quality_ticket,
        cycle_number=(last_cycle.cycle_number + 1) if last_cycle else 1,
        started_at=started_at,
        expected_end_at=started_at + timedelta(days=duration_days),
        financial_regularization_confirmed=bool(financial and financial_regularization_confirmed),
        financial_evidence=financial_evidence if financial else "",
    )
    if order.status != QualityOrder.Status.MONITORAMENTO:
        order.status = QualityOrder.Status.MONITORAMENTO
        order.save(update_fields=("status", "updated_at"))
    QualityAuditEvent.objects.create(
        quality_order=order,
        event_type=QualityAuditEvent.EventType.MONITORING_STARTED,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        description=f"Ciclo RREIP {cycle.cycle_number} iniciado.",
        metadata={"cycle_number": cycle.cycle_number, "duration_days": duration_days},
    )
    AuditService.record(
        request=request,
        user=actor,
        category="QUALITY",
        action="MONITORING_STARTED",
        module="Qualidade",
        description="Ciclo RREIP iniciado.",
        obj=order,
        metadata={"code": order.code, "cycle_number": cycle.cycle_number, "financial_clearance_confirmed": cycle.financial_regularization_confirmed},
    )
    return cycle


@transaction.atomic
def finish_monitoring_cycle(*, cycle, result, actor, request=None, trigger_ticket_identifier=""):
    if result not in {
        QualityMonitoringCycle.Result.SUCCESS,
        QualityMonitoringCycle.Result.RECURRENCE,
        QualityMonitoringCycle.Result.CANCELED,
    }:
        raise ValidationError("Resultado de ciclo inválido.")
    visible_orders = QualityDataScopeService.orders_for(actor).values("pk")
    try:
        locked = QualityMonitoringCycle.objects.select_for_update().select_related("quality_order").get(
            pk=cycle.pk,
            quality_order_id__in=visible_orders,
            result=QualityMonitoringCycle.Result.IN_PROGRESS,
        )
    except QualityMonitoringCycle.DoesNotExist:
        raise ValidationError("O ciclo não está ativo ou fora do seu escopo.") from None
    order = QualityOrder.objects.select_for_update().get(pk=locked.quality_order_id)
    locked.quality_order = order
    now = timezone.now()
    if result == QualityMonitoringCycle.Result.SUCCESS and locked.expected_end_at > now:
        raise ValidationError("O ciclo ainda não completou os 30 dias previstos.")
    if result == QualityMonitoringCycle.Result.SUCCESS:
        recurrence = build_quality_recurrence_analysis(actor, now=now)["links"].get(locked.quality_order.link_id, {})
        if any(
            locked.started_at < item["occurred_at"] <= locked.expected_end_at
            for item in recurrence.get("occurrences", ())
        ):
            raise ValidationError("Há uma ocorrência válida no espelho do OTRS durante o ciclo. Registre a reincidência antes de concluir.")

    trigger_occurrence = None
    if result == QualityMonitoringCycle.Result.RECURRENCE:
        trigger_occurrence = _resolve_cycle_trigger(
            actor=actor,
            order=locked.quality_order,
            cycle=locked,
            identifier=trigger_ticket_identifier,
        )
        locked.trigger_occurrence = trigger_occurrence
    locked.result = result
    locked.ended_at = trigger_occurrence.occurred_at if trigger_occurrence else now
    locked.save(update_fields=("result", "ended_at", "trigger_occurrence"))

    previous_status = order.status
    if previous_status == QualityOrder.Status.MONITORAMENTO:
        order.status = (
            QualityOrder.Status.CONCLUIDA
            if result == QualityMonitoringCycle.Result.SUCCESS
            else QualityOrder.Status.TRATAMENTO
        )
        order.completed_at = now if result == QualityMonitoringCycle.Result.SUCCESS else None
        order.save(update_fields=("status", "completed_at", "updated_at"))

    event_values = {
        QualityMonitoringCycle.Result.SUCCESS: (
            QualityAuditEvent.EventType.MONITORING_SUCCEEDED,
            "MONITORING_SUCCEEDED",
            f"Ciclo RREIP {locked.cycle_number} concluído após 30 dias sem reincidência registrada.",
        ),
        QualityMonitoringCycle.Result.CANCELED: (
            QualityAuditEvent.EventType.MONITORING_CANCELED,
            "MONITORING_CANCELED",
            f"Ciclo RREIP {locked.cycle_number} cancelado manualmente.",
        ),
    }
    if result == QualityMonitoringCycle.Result.RECURRENCE:
        failed_days = (timezone.localdate(trigger_occurrence.occurred_at) - timezone.localdate(locked.started_at)).days
        event_values[result] = (
            QualityAuditEvent.EventType.MONITORING_FAILED,
            "MONITORING_FAILED",
            f"Ciclo RREIP {locked.cycle_number} reincidiu após {failed_days} dias.",
        )
    event_type, action, description = event_values[result]
    metadata = {"cycle_number": locked.cycle_number, "result": result}
    if trigger_occurrence:
        metadata["ticket_number"] = trigger_occurrence.ticket_number
    QualityAuditEvent.objects.create(
        quality_order=locked.quality_order,
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
        obj=locked.quality_order,
        metadata={"code": locked.quality_order.code, **metadata},
    )
    if order.status != previous_status:
        status_description = f"Status da OQ alterado de {dict(QualityOrder.Status.choices).get(previous_status, previous_status)} para {order.get_status_display()} após o resultado do RREIP."
        QualityAuditEvent.objects.create(
            quality_order=order,
            event_type=QualityAuditEvent.EventType.STATUS_CHANGED,
            actor=actor,
            description=status_description,
            metadata={"previous_status": previous_status, "status": order.status, "cycle_number": locked.cycle_number},
        )
        AuditService.record(
            request=request, user=actor, category="QUALITY", action="STATUS_CHANGED", module="Qualidade",
            description=status_description, obj=order,
            metadata={"code": order.code, "previous_status": previous_status, "status": order.status, "cycle_number": locked.cycle_number},
        )
    return locked


def get_monitoring_queue(user, params):
    today = timezone.localdate()
    orders = QualityDataScopeService.orders_for(user)
    queryset = QualityMonitoringCycle.objects.filter(quality_order_id__in=orders.values("pk")).select_related(
        "quality_order", "quality_order__link", "quality_order__link__operator", "quality_order__owner",
        "quality_ticket", "quality_ticket__otrs_ticket", "trigger_occurrence", "trigger_occurrence__otrs_ticket",
    )
    selected = {
        key: str(params.get(key, "")).strip()[:160]
        for key in ("q", "tab", "client", "project", "isp", "uf", "owner", "result", "date_from", "date_to")
    }
    if selected["tab"] not in {key for key, _label in MONITORING_TABS}:
        selected["tab"] = "active"
    if selected["result"] not in QualityMonitoringCycle.Result.values:
        selected["result"] = ""
    if selected["isp"].isdigit():
        selected["isp"] = ""
    raw_projects = list(orders.values_list("project_snapshot", flat=True))
    raw_projects.extend(orders.values_list("link__business_project", flat=True))
    raw_projects.extend(orders.values_list("link__legacy_project", flat=True))
    project_groups = project_alias_groups(raw_projects)
    project_options = sorted({display_project_name(value) for value in raw_projects if value}, key=str.casefold)
    project_key = canonical_project_name(selected["project"])
    if project_key not in {canonical_project_name(value) for value in project_options}:
        selected["project"] = ""
        project_key = ""
    else:
        selected["project"] = display_project_name(project_key)
    if selected["q"]:
        term = selected["q"]
        queryset = queryset.filter(
            Q(quality_order__code__icontains=term) | Q(quality_order__designation_snapshot__icontains=term)
            | Q(quality_order__ci_snapshot__icontains=term) | Q(quality_order__client_snapshot__icontains=term)
            | Q(quality_order__project_snapshot__icontains=term) | Q(quality_order__isp_snapshot__icontains=term)
            | Q(quality_order__link__source_config_item_id__icontains=term)
            | Q(quality_order__link__source_number__icontains=term)
            | Q(quality_order__link__operator__name__icontains=term)
            | Q(quality_order__owner__username__icontains=term)
            | Q(quality_order__owner__first_name__icontains=term)
            | Q(quality_order__owner__last_name__icontains=term)
            | Q(trigger_occurrence__ticket_number__icontains=term)
        )
    for key, field in (("client", "quality_order__client_snapshot"), ("uf", "quality_order__uf_snapshot")):
        if selected[key]:
            queryset = queryset.filter(**{f"{field}__iexact": selected[key]})
    if project_key:
        aliases = project_groups.get(project_key, ())
        queryset = queryset.filter(
            Q(quality_order__project_snapshot__in=aliases)
            | Q(quality_order__link__business_project__in=aliases)
            | Q(quality_order__link__legacy_project__in=aliases)
        )
    if selected["isp"]:
        matching_orders = provider_filter_order_queryset(orders, selected["isp"]).order_by().values("pk")
        queryset = queryset.filter(quality_order_id__in=matching_orders)
    if selected["owner"] == "unassigned":
        queryset = queryset.filter(quality_order__owner__isnull=True)
    elif selected["owner"].isdigit():
        queryset = queryset.filter(quality_order__owner_id=int(selected["owner"]))
    else:
        selected["owner"] = ""
    if selected["result"]:
        queryset = queryset.filter(result=selected["result"])
    start_date = _date_param(selected["date_from"])
    end_date = _date_param(selected["date_to"])
    selected["date_from"] = start_date.isoformat() if start_date else ""
    selected["date_to"] = end_date.isoformat() if end_date else ""
    if start_date:
        start_at = timezone.make_aware(datetime.combine(start_date, time.min))
        queryset = queryset.filter(started_at__gte=start_at)
    if end_date:
        end_before = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
        queryset = queryset.filter(expected_end_at__lt=end_before)

    # Counters are based on the Data Scope and current search/filter context, before the selected quick tab.
    scoped_cycles = queryset.order_by()
    active_count = scoped_cycles.filter(result=QualityMonitoringCycle.Result.IN_PROGRESS).count()
    ending_start = timezone.make_aware(datetime.combine(today, time.min))
    ending_before = ending_start + timedelta(days=4)
    ending_filter = {
        "result": QualityMonitoringCycle.Result.IN_PROGRESS,
        "expected_end_at__gte": ending_start,
        "expected_end_at__lt": ending_before,
    }
    ending_count = scoped_cycles.filter(**ending_filter).count()
    recurrence_count = scoped_cycles.filter(result=QualityMonitoringCycle.Result.RECURRENCE).count()
    completed_count = scoped_cycles.filter(result=QualityMonitoringCycle.Result.SUCCESS).count()
    tabs = [
        {"key": "active", "label": "Ativos", "count": active_count},
        {"key": "ending", "label": "Terminando", "count": ending_count},
        {"key": "recurrence", "label": "Reincidiram", "count": recurrence_count},
        {"key": "completed", "label": "Concluídos", "count": completed_count},
        {"key": "all", "label": "Todos", "count": scoped_cycles.count()},
    ]
    active_tab = selected["tab"]
    if active_tab == "active":
        queryset = queryset.filter(result=QualityMonitoringCycle.Result.IN_PROGRESS)
    elif active_tab == "ending":
        queryset = queryset.filter(**ending_filter)
    elif active_tab == "recurrence":
        queryset = queryset.filter(result=QualityMonitoringCycle.Result.RECURRENCE)
    elif active_tab == "completed":
        queryset = queryset.filter(result=QualityMonitoringCycle.Result.SUCCESS)

    try:
        page_size = int(params.get("per_page", 25))
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in PAGE_SIZES:
        page_size = 25
    ordering = ("-expected_end_at", "-started_at", "-pk") if active_tab != "recurrence" else ("-ended_at", "-pk")
    page_obj = Paginator(queryset.order_by(*ordering), page_size).get_page(params.get("page", 1))
    cycles = list(page_obj.object_list)
    links = [cycle.quality_order.link for cycle in cycles if cycle.quality_order.link_id]
    semantics = provider_values_for_links(links)
    snapshot_names = provider_names_for_values([cycle.quality_order.isp_snapshot for cycle in cycles if cycle.quality_order.isp_snapshot])
    for cycle in cycles:
        order = cycle.quality_order
        link = order.link if order.link_id else None
        provider = semantics.get(link.pk) if link else None
        cycle.designation_display = link_designation(link) if link else clean_link_designation(order.designation_snapshot)
        cycle.ci_display = order.ci_snapshot or (link.source_config_item_id if link else "—")
        cycle.provider_name = provider.provider_name if provider else snapshot_names.get(order.isp_snapshot, "Não classificado")
        cycle.client_display = order.client_snapshot or (link.contracting_client if link else "—")
        cycle.project_display = display_project_name(
            (link.business_project or link.legacy_project) if link else order.project_snapshot,
        ) or "—"
        cycle.uf_display = order.uf_snapshot or (link.state if link else "—")
        cycle.owner_label = (order.owner.get_full_name() or order.owner.get_username()) if order.owner_id else "Não atribuído"
        cycle.status_label = cycle.get_result_display()
        total_days = max((cycle.expected_end_at.date() - cycle.started_at.date()).days, 1)
        days_elapsed = max((today - cycle.started_at.date()).days, 0)
        cycle.total_days = total_days
        cycle.days_elapsed = min(days_elapsed, total_days)
        cycle.progress_width = min(100, round(cycle.days_elapsed / total_days * 100))
        cycle.days_remaining = max((cycle.expected_end_at.date() - today).days, 0)
        cycle.failed_after_days = max(((cycle.ended_at or cycle.expected_end_at).date() - cycle.started_at.date()).days, 0)
        trigger = cycle.trigger_occurrence
        cycle.trigger_ticket_number = (trigger.ticket_number if trigger else "")
        cycle.otrs_url = otrs_ticket_url(trigger.ticket_external_id_snapshot) if trigger and trigger.ticket_external_id_snapshot else ""
        cycle.is_ending = cycle.result == QualityMonitoringCycle.Result.IN_PROGRESS and 0 <= (cycle.expected_end_at.date() - today).days <= 3
        cycle.is_overdue = cycle.result == QualityMonitoringCycle.Result.IN_PROGRESS and cycle.expected_end_at.date() < today

    filter_orders = orders
    owner_options = list(filter_orders.filter(monitoring_cycles__isnull=False).values_list("owner_id", "owner__first_name", "owner__last_name", "owner__username").distinct())
    return {
        "cycles": cycles,
        "page_obj": page_obj,
        "tabs": tabs,
        "selected": selected,
        "page_size": page_size,
        "active_count": active_count,
        "ending_count": ending_count,
        "recurrence_count": recurrence_count,
        "completed_count": completed_count,
        "date_from": start_date.isoformat() if start_date else "",
        "date_to": end_date.isoformat() if end_date else "",
        "filter_options": {
            "client": _options(orders, "client_snapshot"),
            "project": project_options,
            "isp": provider_name_options_for_orders(orders),
            "uf": _options(orders, "uf_snapshot"),
            "owner": [(str(pk) if pk else "unassigned", " ".join(part for part in (first, last) if part).strip() or username or "Não atribuído") for pk, first, last, username in owner_options],
            "result": list(QualityMonitoringCycle.Result.choices),
        },
    }


def is_financial_reason(reason):
    return "finance" in str(reason or "").casefold()


def _resolve_cycle_trigger(*, actor, order, cycle, identifier):
    identifier = str(identifier or "").strip()[:80]
    if not identifier:
        raise ValidationError("Informe o ticket que causou a reincidência.")
    ticket_query = OTRSTicket.objects.all()
    if identifier.isdigit():
        ticket_query = ticket_query.filter(ticket_number=identifier) | OTRSTicket.objects.filter(external_id=int(identifier))
    else:
        ticket_query = ticket_query.filter(ticket_number=identifier)
    ticket = ticket_query.order_by("pk").first()
    if not ticket:
        raise ValidationError("O ticket não foi encontrado no espelho local do OTRS.")
    if not order.link_id:
        raise ValidationError("O ticket não está relacionado ao circuito desta OQ.")
    analysis = build_quality_recurrence_analysis(actor, now=timezone.now())
    occurrence = analysis["ticket_index"].get((order.link_id, ticket.external_id))
    if occurrence is None:
        raise ValidationError("O ticket não atende às regras de ocorrência válida da Qualidade.")
    if not cycle.started_at < occurrence["occurred_at"] <= cycle.expected_end_at:
        raise ValidationError("O ticket precisa ter ocorrido dentro dos 30 dias deste ciclo RREIP.")
    occurrence, created = QualityOccurrence.objects.get_or_create(
        quality_order=order,
        ticket_external_id_snapshot=ticket.external_id,
        defaults={
            "otrs_ticket": ticket,
            "ticket_number": ticket.ticket_number,
            "occurred_at": ticket.source_created_at,
            "counts_as_recurrence": True,
            "reason": "Reincidência registrada durante o ciclo RREIP.",
            "ticket_status_snapshot": ticket.external_state_name,
            "queue_snapshot": ticket.queue_name,
        },
    )
    if occurrence.excluded:
        raise ValidationError("A ocorrência está desconsiderada e não pode causar falha do RREIP.")
    if not created and not occurrence.counts_as_recurrence:
        occurrence.is_initial_occurrence = False
        occurrence.counts_as_recurrence = True
        occurrence.recurrence_number = max(order.occurrences.filter(excluded=False, counts_as_recurrence=True).count(), 0) + 1
        occurrence.reason = occurrence.reason or "Reincidência registrada durante o ciclo RREIP."
        occurrence.save(update_fields=("is_initial_occurrence", "counts_as_recurrence", "recurrence_number", "reason"))
    return occurrence


def _date_param(value):
    from django.utils.dateparse import parse_date

    return parse_date(value)


def _options(queryset, field):
    return list(queryset.exclude(**{field: ""}).order_by(field).values_list(field, flat=True).distinct())
