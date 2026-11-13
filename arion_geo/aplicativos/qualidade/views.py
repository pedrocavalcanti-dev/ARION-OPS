from datetime import datetime, time, timedelta
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.utils import OperationalError
from django.db.models import Count, Q
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.chamados.services import otrs_ticket_url
from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.clients.brevo import BrevoResult, BrevoSendingDisabled, BrevoTransactionalClient
from aplicativos.integracoes.clients.brevo_smtp import SMTPResult, send_brevo_smtp
from aplicativos.integracoes.models import CMDBLinkMirror, ExternalAPIIntegration, OTRSStateMapping
from aplicativos.integracoes.services.external_apis import decrypt_external_api_secret, decrypt_external_api_smtp_secret
from aplicativos.integracoes.services.analytics import TicketQueryService

from .forms import (
    QualityActionForm,
    QualityContactForm,
    QualityMonitoringFinishForm,
    QualityMonitoringStartForm,
    QualityOccurrenceReviewForm,
    QualityOrderCreateForm,
    QualityOrderDetailForm,
    QualityOrderOwnerForm,
    QualityOrderPriorityForm,
    QualityTicketLinkForm,
    QualityTreatmentCreateForm,
    QualityTreatmentSummaryForm,
    QualityTreatmentCompleteForm,
    QualityTreatmentCancelForm,
)
from .models import QualityAction, QualityAuditEvent, QualityContact, QualityMonitoringCycle, QualityOrder, QualityTicket, QualityTreatment
from .services.dashboard import get_dashboard_data
from .services.constants import QUALITY_PRIORITY_DEFAULT_PERIOD, QUALITY_RREIP_DAYS
from .services.data_scope import QualityDataScopeService
from .services.orders import (
    ActiveQualityOrderExists,
    QualityOrderArchiveBlocked,
    archive_quality_order,
    create_quality_order,
    update_quality_order_field,
)
from .services.orders_queue import get_quality_orders_queue
from .services.order_detail import get_quality_order_detail
from .services.occurrences import review_quality_occurrence
from .services.contacts import (
    contacts_for_user,
    get_quality_contacts,
    recommended_contacts_for_order,
    save_quality_contact,
    set_quality_contact_active,
)
from .services.history import get_global_quality_history, get_order_history
from .services.monitoring import finish_monitoring_cycle, get_monitoring_queue, is_financial_reason, start_monitoring_cycle
from .services.priority_link_detail import get_priority_link_detail
from .services.priority_links import get_priority_links
from .services.treatments import (
    add_quality_action,
    get_monitoring_tickets,
    get_treatment_context,
    create_quality_treatment,
    quality_treatment_creation_blocker,
    link_quality_ticket,
    support_tickets_for_order,
    toggle_quality_action,
    update_quality_treatment_summary,
    complete_quality_treatment,
    cancel_quality_treatment,
)
from .services.tq_delivery import claim_quality_treatment_send, record_tq_send_accepted, record_tq_send_failure
from .services.tq_email import brevo_send_readiness, build_treatment_email, treatment_send_block_message
from aplicativos.integracoes.services.semantics import link_designation, provider_values_for_links


QUALITY_PERMISSION = "qualidade.view_quality"
MANAGE_QUALITY_PERMISSION = "qualidade.manage_quality_orders"
MANAGE_CONTACTS_PERMISSION = "qualidade.manage_quality_contacts"
DELETE_QUALITY_ORDER_PERMISSION = "qualidade.delete_qualityorder"
logger = logging.getLogger(__name__)


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def dashboard(request):
    context = get_dashboard_data(request.user, request.GET)
    filters=context["filters"]
    context["primary_selected"]={**filters,"client":filters["cliente"],"project":filters["projeto"]}
    options=context["filter_options"]
    context["primary_options"]={"clients":options["cliente"],"projects":options["projeto"],"ufs":options["uf"],"regions":options["regions"]}
    context["filter_chips"]=_filter_chips(request, filters, filters["period"] if filters["period"]!=QUALITY_PRIORITY_DEFAULT_PERIOD else None)
    context["active_section"] = "overview"
    context["chart_data"] = {
        "evolution": context["evolution"],
        "status": context["status_distribution"],
        "links": context["recurring_links"],
    }
    return render(request, "qualidade/dashboard.html", context)


def _empty_page(request, *, title, subtitle, active_section):
    return render(
        request,
        "qualidade/empty_page.html",
        {
            "page_title": title,
            "page_subtitle": subtitle,
            "active_section": active_section,
        },
    )


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def priority_links(request):
    context = get_priority_links(request.user, request.GET)
    context.update({
        "active_section": "links",
        "page_title": "Links prioritários",
        "page_subtitle": "Identifique os circuitos que exigem maior atenção da Qualidade.",
        "page_query": _quality_filter_query(request, context),
        "filter_chips": _filter_chips(
            request,
            context["selected"],
            context["period"] if context["period"] != QUALITY_PRIORITY_DEFAULT_PERIOD else None,
        ),
        "can_manage_orders": request.user.has_perm(MANAGE_QUALITY_PERMISSION),
    })
    if context["can_manage_orders"]:
        scoped_links = DataScopeService.filter_queryset(
            request.user,
            CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
        )
        context["create_form"] = QualityOrderCreateForm(link_queryset=scoped_links)
    return render(request, "qualidade/priority_links.html", context)


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def priority_link_detail(request, identificador):
    context = get_priority_link_detail(request.user, identificador, request.GET)
    if context is None:
        raise Http404
    context.update({
        "active_section": "links",
        "page_title": "Histórico do link",
        "page_query": _page_query(request),
        "can_manage_orders": request.user.has_perm(MANAGE_QUALITY_PERMISSION),
    })
    for ticket in context["tickets"]:
        ticket.otrs_url = otrs_ticket_url(ticket.external_id)
    if context["can_manage_orders"]:
        scoped_links = DataScopeService.filter_queryset(
            request.user,
            CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
        )
        context["create_form"] = QualityOrderCreateForm(link_queryset=scoped_links)
    return render(request, "qualidade/priority_link_detail.html", context)


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
def link_options(request):
    term = str(request.GET.get("q", "")).strip()[:120]
    if len(term) < 2:
        return JsonResponse({"results": []})
    links = DataScopeService.filter_queryset(
        request.user,
        CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    ).filter(
        Q(source_number__icontains=term)
        | Q(source_config_item_id__icontains=term)
        | Q(designation__icontains=term)
        | Q(name__icontains=term)
        | Q(contracting_client__icontains=term)
        | Q(business_project__icontains=term)
        | Q(legacy_project__icontains=term)
        | Q(isp_raw__icontains=term)
        | Q(operator__name__icontains=term)
    ).order_by("source_number", "source_config_item_id")[:20]
    results = []
    semantics = provider_values_for_links(links)
    for link in links:
        provider = semantics[link.pk]
        label = link_designation(link)
        results.append({
            "id": link.pk,
            "label": f"{label} · CI {link.source_config_item_id}",
            "client": link.contracting_client,
            "project": link.business_project or link.legacy_project,
            "provider_id": provider.provider_id,
            "provider_name": provider.provider_name,
            "uf": link.state,
        })
    return JsonResponse({"results": results})


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def orders(request):
    context = get_quality_orders_queue(request.user, request.GET)
    page_query = _quality_filter_query(request, context)
    tab_links = []
    for tab in context["tabs"]:
        query = request.GET.copy()
        for key, value in context["selected"].items():
            if value:
                query[key] = value
            else:
                query.pop(key, None)
        query["tab"] = tab["key"]
        query.pop("page", None)
        tab_links.append({**tab, "url": f"{reverse('qualidade:ordens')}?{query.urlencode()}"})
    context["tabs"] = tab_links
    context.update({
        "active_section": "orders",
        "page_title": "Ordens de Qualidade",
        "page_subtitle": "Acompanhe os casos de reincidência, responsáveis, prazos, tratativas e monitoramentos.",
        "page_query": page_query,
        "filter_chips": _filter_chips(
            request,
            context["selected"],
            owner_labels={str(owner.pk): owner.get_full_name() or owner.get_username() for owner in context["filter_options"]["owners"]},
        ),
        "can_manage_orders": request.user.has_perm(MANAGE_QUALITY_PERMISSION),
    })
    if context["can_manage_orders"]:
        context["create_form"] = QualityOrderCreateForm(link_queryset=context["links_for_create"])
        context["owner_action_form"] = QualityOrderOwnerForm()
        context["priority_action_form"] = QualityOrderPriorityForm()
    return render(request, "qualidade/orders.html", context)


def _page_query(request):
    query = request.GET.copy()
    query.pop("page", None)
    return query.urlencode()


def _quality_filter_query(request, context, *, exclude_keys=()):
    query = request.GET.copy()
    query.pop("page", None)
    for key in exclude_keys:
        query.pop(key, None)
    values = dict(context.get("selected") or {})
    for key in ("period", "date_from", "date_to"):
        if key in context:
            values[key] = context[key]
    for key, value in values.items():
        if key in exclude_keys:
            continue
        if value:
            query[key] = value
        else:
            query.pop(key, None)
    return query.urlencode()


def _query_without(request, *keys):
    query = request.GET.copy()
    for key in keys:
        query.pop(key, None)
    return query.urlencode()


def _filter_chips(request, selected, period=None, owner_labels=None):
    labels = {
        "q": "Busca", "period": "Período", "client": "Operadora", "project": "Projeto", "isp": "ISP", "uf": "UF",
        "level": "Nível", "priority": "Prioridade", "oq": "OQ", "status": "Status", "reason": "Motivo",
        "owner": "Responsável", "due": "Prazo", "rreip": "RREIP", "portfolio": "Carteira",
        "state": "Estado operacional", "region":"Macrorregião", "cliente":"Operadora", "projeto":"Projeto",
    }
    chips = []
    values = dict(selected)
    if period:
        values["period"] = period
    for key, value in values.items():
        if key in {"date_from","date_to","concentration_by","group_by","tab"} or (key=="period" and value==QUALITY_PRIORITY_DEFAULT_PERIOD):
            continue
        if not value:
            continue
        if key == "state" and value == "active":
            continue
        display_value = value
        if key == "period":
            display_value = f"{request.GET.get('date_from','')} – {request.GET.get('date_to','')}" if value=="custom" else f"{value} dias"
        elif key == "region":
            from aplicativos.integracoes.services.geography import REGIONS
            display_value=next((r["nome"] for r in REGIONS if r["sigla"]==value),value)
        elif key == "status":
            display_value = dict(QualityOrder.Status.choices).get(value, value)
        elif key == "priority":
            display_value = dict(QualityOrder.Priority.choices).get(value, value)
        elif key == "oq":
            display_value = {"WITH": "Com OQ ativa", "WITHOUT": "Sem OQ ativa"}.get(value, value)
        elif key == "level" and value == "UNCLASSIFIED":
            display_value = "A classificar"
        elif key == "owner" and value == "unassigned":
            display_value = "Não atribuído"
        elif key == "owner" and owner_labels:
            display_value = owner_labels.get(value, value)
        elif key == "due":
            display_value = {"overdue": "Vencidas", "soon": "Vence em até 3 dias", "none": "Sem prazo"}.get(value, value)
        elif key == "rreip":
            display_value = {"active": "Com ciclo ativo", "none": "Sem ciclo ativo"}.get(value, value)
        elif key == "portfolio":
            display_value = {"claro": "Claro prioritárias", "vivo": "Vivo prioritárias", "other": "Outras"}.get(value, value)
        elif key == "state":
            display_value = {"active": "Ativos", "stabilized": "Estabilizados", "all": "Todos"}.get(value, value)
        query = request.GET.copy()
        for field,normalized in selected.items():
            if normalized:
                query[field]=normalized
            else:
                query.pop(field,None)
        query.pop(key, None)
        query.pop("page", None)
        if key=="period":
            query.pop("date_from",None)
            query.pop("date_to",None)
        if request.resolver_match.url_name in {"links_prioritarios","dashboard"}:
            query.setdefault("period",QUALITY_PRIORITY_DEFAULT_PERIOD)
        chips.append({
            "label": labels.get(key, key),
            "value": display_value,
            "remove_url": f"{request.path}?{query.urlencode()}" if query else request.path,
        })
    return chips


def _treatment_send_configuration(blockers):
    otrs_blocked = any("OTRS" in blocker or "fila Qualidade" in blocker or "worker" in blocker.casefold() for blocker in blockers)
    route = "configuracoes:integracoes:otrs" if otrs_blocked else "configuracoes:integracoes:brevo"
    label = "Ver integra" + chr(0xe7) + chr(0xe3) + "o OTRS" if otrs_blocked else "Configurar Brevo"
    return reverse(route), label


def _treatment_send_checks(integration, blockers):
    blockers = tuple(blockers)
    config = integration.config or {} if integration else {}
    transport = bool(integration and integration.enabled and integration.health == ExternalAPIIntegration.Health.AVAILABLE)
    otrs_configured = not any("Integração OTRS somente" in item for item in blockers)
    checkpoint_ready = not any("Leitura incremental" in item for item in blockers)
    transport_label = "Brevo SMTP" if config.get("send_method") == "smtp" else "Brevo API REST"
    return [
        {"label": transport_label, "state": "Autenticado" if transport else "Pendente"},
        {"label": "Autorização administrativa", "state": "Autorizada" if integration and integration.allow_real_sends else "Desabilitada"},
        {"label": "Roteamento de e-mail", "state": "Validado" if config.get("quality_recipient_validated") else "Pendente"},
        {"label": "Leitura OTRS", "state": "Configurada" if otrs_configured else "Pendente"},
        {"label": "Checkpoint e correlação", "state": "Validado" if checkpoint_ready else "Pendente"},
    ]


def _safe_quality_return(request):
    candidate = str(request.POST.get("next", ""))
    if (candidate.startswith("/qualidade/") and not candidate.startswith("//")
            and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure())):
        return candidate
    return reverse("qualidade:ordens")


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def order_create(request):
    scoped_links = DataScopeService.filter_queryset(
        request.user,
        CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    )
    form = QualityOrderCreateForm(request.POST, link_queryset=scoped_links)
    if not form.is_valid():
        messages.error(request, "Revise os campos da nova OQ e tente novamente.")
        return redirect(_safe_quality_return(request))

    deadline = form.cleaned_data["treatment_deadline"]
    deadline_at = timezone.make_aware(datetime.combine(deadline, time.max)) if deadline else None
    try:
        order = create_quality_order(
            actor=request.user,
            request=request,
            link=form.cleaned_data["link"],
            priority=form.cleaned_data["priority"],
            owner=form.cleaned_data["owner"],
            treatment_deadline=deadline_at,
            internal_notes=form.cleaned_data["initial_note"],
            status=QualityOrder.Status.NOVA,
        )
    except ActiveQualityOrderExists as exc:
        visible_order = QualityDataScopeService.orders_for(request.user).filter(pk=exc.order.pk).first()
        if visible_order:
            messages.info(request, f"Este link já possui uma OQ ativa: {exc.order.code}. A ordem existente foi aberta.")
            return redirect("qualidade:ordem_detail", codigo=exc.order.code)
        messages.error(request, "Este link já possui uma OQ ativa. Ela não pode ser exibida no seu escopo de dados.")
        return redirect(_safe_quality_return(request))
    messages.success(request, f"{order.code} criada como Nova.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
@transaction.atomic
def order_update(request, codigo):
    order = get_object_or_404(
        QualityDataScopeService.orders_for(request.user).select_related("owner"), code=codigo,
    )
    field_name = request.POST.get("field")
    if field_name == "detail":
        form = QualityOrderDetailForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Revise os campos da Ordem de Qualidade e tente novamente.")
            return redirect(_safe_quality_return(request))
        deadline = form.cleaned_data["treatment_deadline"]
        deadline_at = timezone.make_aware(datetime.combine(deadline, time.max)) if deadline else None
        values = form.cleaned_data.copy()
        values["treatment_deadline"] = deadline_at
        changed = False
        for field, value in values.items():
            _updated, field_changed = update_quality_order_field(
                order=order, field_name=field, value=value, actor=request.user, request=request,
            )
            changed = changed or field_changed
        messages.success(request, "OQ atualizada e auditada." if changed else "A OQ já estava com esses valores.")
        return redirect(_safe_quality_return(request))
    form_class = QualityOrderOwnerForm if field_name == "owner" else QualityOrderPriorityForm if field_name == "priority" else None
    if form_class is None:
        messages.error(request, "Ação de atualização inválida.")
        return redirect(_safe_quality_return(request))
    form = form_class(request.POST)
    if not form.is_valid():
        messages.error(request, "Revise o valor selecionado e tente novamente.")
        return redirect(_safe_quality_return(request))
    _updated, changed = update_quality_order_field(
        order=order,
        field_name=field_name,
        value=form.cleaned_data[field_name],
        actor=request.user,
        request=request,
    )
    messages.success(request, "Alteração registrada." if changed else "A OQ já estava com esse valor.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def order_detail(request, codigo):
    can_archive_order = request.user.is_superuser or request.user.has_perm(DELETE_QUALITY_ORDER_PERMISSION)
    order = get_object_or_404(
        QualityDataScopeService.orders_for(
            request.user, include_archived=can_archive_order,
        ).select_related("owner", "link", "link__operator"),
        code=codigo,
    )
    if order.archived_at and not can_archive_order:
        raise Http404
    tab = request.GET.get("tab", "resumo")
    context = get_quality_order_detail(
        request.user,
        order,
        tab=tab,
        history_filter=request.GET.get("history", "all"),
        direction=request.GET.get("direction", "recent"),
        page=request.GET.get("page", 1),
        excluded_page=request.GET.get("excluded_page", 1),
    )
    context.update({
        "active_section": "orders",
        "can_manage_orders": request.user.has_perm(MANAGE_QUALITY_PERMISSION) and not order.archived_at,
        "can_archive_order": can_archive_order and not order.archived_at,
        "is_archived": bool(order.archived_at),
        "open_edit_dialog": request.GET.get("edit") == "1" and request.user.has_perm(MANAGE_QUALITY_PERMISSION),
        "rreip_days": QUALITY_RREIP_DAYS,
        "page_query": _query_without(request, "page"),
        "detail_form": QualityOrderDetailForm(initial={
            "recurrence_level": order.recurrence_level,
            "priority": order.priority,
            "owner": order.owner_id,
            "treatment_deadline": timezone.localtime(order.treatment_deadline).date() if order.treatment_deadline else None,
            "internal_notes": order.internal_notes,
        }),
        "review_form": QualityOccurrenceReviewForm(),
        "action_form": QualityActionForm(),
        "ticket_form": QualityTicketLinkForm(),
    })
    context.update(get_treatment_context(order))
    active_local_treatment = QualityTreatment.objects.filter(
        quality_order=order, status__in=QualityTreatment.ACTIVE_STATUSES,
    ).select_related("support_ticket", "created_by", "owner").first()
    latest_order_treatment = order.treatments.order_by("-created_at", "-pk").first()
    if active_local_treatment and active_local_treatment.quality_ticket_external_id:
        active_local_treatment.otrs_url = otrs_ticket_url(active_local_treatment.quality_ticket_external_id)
    support_tickets = support_tickets_for_order(request.user, order)
    support_ticket_rows = list(support_tickets)
    for support_ticket in support_ticket_rows:
        support_ticket.otrs_url = otrs_ticket_url(support_ticket.external_id)
    context.update({
        "local_treatment": active_local_treatment,
        "latest_order_treatment": latest_order_treatment,
        "treatment_creation_blocker": quality_treatment_creation_blocker(order),
        "treatment_history_local": order.treatments.select_related(
            "created_by", "owner", "support_ticket",
        ).exclude(pk=active_local_treatment.pk if active_local_treatment else None).order_by("-created_at", "-pk"),
        "support_tickets": support_ticket_rows,
        "operational_state_label": (
            "Incidente de suporte ativo; estado do serviço não confirmado"
            if support_tickets.exists() else "Situação não confirmada"
        ),
        "treatment_create_form": QualityTreatmentCreateForm(
            support_tickets=support_tickets,
            initial={"reason": order.current_reason or context.get("current_reason") or "Reincidência confirmada; investigar causa raiz e definir ações corretivas e preventivas."},
        ),
        "treatment_summary_form": QualityTreatmentSummaryForm(initial={
            "management_summary": (active_local_treatment.management_summary or active_local_treatment.description or active_local_treatment.reason) if active_local_treatment else "",
            "management_situation": active_local_treatment.management_situation if active_local_treatment else QualityTreatment.ManagementSituation.WAITING_RESPONSE,
            "next_action": active_local_treatment.next_action if active_local_treatment else "",
            "next_action_due": active_local_treatment.next_action_due if active_local_treatment else None,
        }),
        "treatment_complete_form": QualityTreatmentCompleteForm(),
        "treatment_cancel_form": QualityTreatmentCancelForm(),
    })
    context["treatment_updates"] = [
        event for event in order.audit_events.select_related("actor").filter(
            event_type=QualityAuditEvent.EventType.NOTES_CHANGED,
        ).order_by("-created_at", "-pk")[:50]
        if active_local_treatment and (event.metadata or {}).get("treatment_code") == active_local_treatment.code
    ][:5]
    context["treatment_can_start_monitoring"] = bool(
        active_local_treatment
        and active_local_treatment.status == QualityTreatment.Status.IN_PROGRESS
        and active_local_treatment.quality_ticket_external_id
    )
    brevo_integration = ExternalAPIIntegration.objects.filter(
        provider=ExternalAPIIntegration.Provider.BREVO,
    ).first()
    send_readiness = brevo_send_readiness(active_local_treatment, brevo_integration)
    send_configuration_url, send_configuration_label = _treatment_send_configuration(send_readiness["blockers"])
    context.update({
        "treatment_send_ready": send_readiness["ready"],
        "treatment_send_blockers": send_readiness["blockers"],
        "treatment_send_block_message": treatment_send_block_message(send_readiness["blockers"]),
        "treatment_send_configuration_url": send_configuration_url,
        "treatment_send_configuration_label": send_configuration_label,
        "treatment_send_checks": _treatment_send_checks(brevo_integration, send_readiness["blockers"]),
    })
    context["quality_actions"] = order.actions.select_related("completed_by").all()
    context["checklist_total"] = len(context["quality_actions"])
    context["checklist_completed"] = sum(action.completed for action in context["quality_actions"])
    context["checklist_progress"] = round(context["checklist_completed"] / context["checklist_total"] * 100) if context["checklist_total"] else 0
    context["archive_ticket_count"] = order.quality_tickets.exclude(ticket_external_id_snapshot__isnull=True).count() + order.treatments.exclude(quality_ticket_external_id__isnull=True).count()
    context["can_manage_contacts"] = request.user.has_perm(MANAGE_CONTACTS_PERMISSION)
    context["recommended_contacts"] = recommended_contacts_for_order(
        request.user,
        client=context["client"],
        project=context["project"],
        provider_name=context["provider_name"],
        region=context["uf"],
        financial=is_financial_reason(order.current_reason or context["current_reason"]),
    )
    cycles = list(order.monitoring_cycles.select_related(
        "quality_ticket", "quality_ticket__otrs_ticket", "quality_treatment", "trigger_occurrence", "trigger_occurrence__otrs_ticket",
    ).order_by("-cycle_number"))
    today = timezone.localdate()
    for cycle in cycles:
        total_days = max((cycle.expected_end_at.date() - cycle.started_at.date()).days, 1)
        elapsed = max((today - cycle.started_at.date()).days, 0)
        cycle.total_days = total_days
        cycle.days_elapsed = min(elapsed, total_days)
        cycle.progress_width = min(100, round(cycle.days_elapsed / total_days * 100))
        cycle.days_remaining = max((cycle.expected_end_at.date() - today).days, 0)
        cycle.failed_after_days = max(((cycle.ended_at or cycle.expected_end_at).date() - cycle.started_at.date()).days, 0)
        cycle.trigger_ticket_number = cycle.trigger_occurrence.ticket_number if cycle.trigger_occurrence_id else ""
        cycle.otrs_url = otrs_ticket_url(cycle.trigger_occurrence.ticket_external_id_snapshot) if cycle.trigger_occurrence_id and cycle.trigger_occurrence.ticket_external_id_snapshot else ""
        cycle.treatment_code = cycle.quality_treatment.code if cycle.quality_treatment_id else ""
        cycle.quality_ticket_number = cycle.quality_treatment.quality_ticket_number if cycle.quality_treatment_id else ""
        cycle.occurrences_in_window = order.occurrences.filter(
            excluded=False, occurred_at__gt=cycle.started_at,
            occurred_at__lte=min(cycle.expected_end_at, timezone.now()),
        ).count()
        cycle.is_ending = cycle.result == QualityMonitoringCycle.Result.IN_PROGRESS and 0 <= (cycle.expected_end_at.date() - today).days <= 3
        cycle.is_overdue = cycle.result == QualityMonitoringCycle.Result.IN_PROGRESS and cycle.expected_end_at.date() < today
    context["monitoring_cycles"] = cycles
    context["active_cycle"] = next(
        (cycle for cycle in cycles if cycle.result == QualityMonitoringCycle.Result.IN_PROGRESS), None,
    )
    context["latest_successful_cycle"] = next((cycle for cycle in cycles if (
        cycle.result == QualityMonitoringCycle.Result.SUCCESS
        and latest_order_treatment
        and cycle.quality_treatment_id == latest_order_treatment.pk
    )), None)
    context["can_complete_treatment"] = bool(
        active_local_treatment
        and active_local_treatment.status == QualityTreatment.Status.MONITORING
        and context["latest_successful_cycle"]
        and context["latest_successful_cycle"].stability_validated
    )
    context["monitoring_start_form"] = QualityMonitoringStartForm()
    context["monitoring_finish_form"] = QualityMonitoringFinishForm()
    context["has_financial_reason"] = is_financial_reason(order.current_reason or context["current_reason"])
    context["monitoring_start_preview_end"] = timezone.localtime(timezone.now() + timedelta(days=QUALITY_RREIP_DAYS))
    context["monitoring_start_preview_date"] = timezone.localtime(timezone.now())
    context["monitoring_tickets"] = get_monitoring_tickets(order)
    if context["active_tab"] == "historico":
        context.update(get_order_history(
            request.user, order, category=request.GET.get("history_type", "all"), page=request.GET.get("page", 1),
        ))
        context["page_query"] = _query_without(request, "page")
    for ticket in context["occurrence_page"].object_list if context["occurrence_page"] else ():
        _decorate_occurrence_ticket(ticket, context["review_index"])
        ticket.otrs_url = otrs_ticket_url(ticket.external_id)
    for ticket in context["excluded_ticket_page"].object_list if context["excluded_ticket_page"] else ():
        _decorate_occurrence_ticket(ticket, context["review_index"], is_valid=False)
        ticket.exclusion_label = _occurrence_exclusion_label(ticket, ticket.quality_review)
        ticket.otrs_url = otrs_ticket_url(ticket.external_id)
    return render(
        request,
        "qualidade/order_detail.html",
        context,
    )


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(DELETE_QUALITY_ORDER_PERMISSION, raise_exception=True)
@require_POST
def quality_order_archive(request, codigo):
    order = get_object_or_404(
        QualityDataScopeService.orders_for(request.user).select_related("link"), code=codigo,
    )
    if request.POST.get("confirm_code", "").strip() != order.code:
        messages.error(request, "O código informado não corresponde à OQ. Nenhuma alteração foi feita.")
        return redirect(reverse("qualidade:ordem_detail", kwargs={"codigo": codigo}))
    try:
        _order, archived = archive_quality_order(
            order_id=order.pk,
            actor=request.user,
            request=request,
            reason=request.POST.get("reason", ""),
        )
    except QualityOrderArchiveBlocked as exc:
        messages.error(request, str(exc))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        if archived:
            messages.success(request, f"{order.code} foi arquivada. O histórico e os registros relacionados foram preservados.")
    return redirect(reverse("qualidade:ordem_detail", kwargs={"codigo": codigo}))


def _scoped_treatment(request, codigo, treatment_id):
    order = get_object_or_404(
        QualityDataScopeService.orders_for(request.user).select_related("owner", "link", "link__operator"),
        code=codigo,
    )
    treatment = get_object_or_404(
        QualityTreatment.objects.select_related(
            "quality_order", "quality_order__link", "quality_order__link__operator",
            "support_ticket", "owner", "created_by",
        ),
        pk=treatment_id, quality_order=order,
    )
    return order, treatment


def _tq_email_detail(request, order, treatment):
    detail = get_quality_order_detail(request.user, order, tab="tratativas")
    if treatment.support_ticket_external_id_snapshot and detail.get("link"):
        scoped_links = DataScopeService.filter_queryset(
            request.user,
            CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
        )
        detail["support_ticket"] = TicketQueryService.base(
            request.user, links=scoped_links, all_queues=True,
        ).filter(external_id=treatment.support_ticket_external_id_snapshot).first()
    return detail


def _tq_email_page_context(request, order, treatment):
    detail = _tq_email_detail(request, order, treatment)
    email = build_treatment_email(treatment, detail=detail, request=request)
    integration = ExternalAPIIntegration.objects.filter(
        provider=ExternalAPIIntegration.Provider.BREVO,
    ).first()
    readiness = brevo_send_readiness(treatment, integration)
    config = readiness["config"]
    sender = "Não configurado"
    if config.get("sender_email"):
        sender = f"{config.get('sender_name') or 'ARION OPS'} <{config['sender_email']}>"
    send_configuration_url, send_configuration_label = _treatment_send_configuration(readiness["blockers"])
    return {
        "send_configuration_url": send_configuration_url,
        "send_configuration_label": send_configuration_label,
        "send_checks": _treatment_send_checks(integration, readiness["blockers"]),
        "active_section": "orders",
        "page_title": "Pré-visualização da solicitação",
        "page_subtitle": f"Confira o conteúdo final da {treatment.code} antes de qualquer envio.",
        "order": order,
        "treatment": treatment,
        "email_subject": email["subject"],
        "email_html": email["html_content"],
        "email_text": email["text_content"],
        "sender_label": sender,
        "recipient_label": config.get("quality_recipient") or "Não configurado",
        "cc_label": config.get("quality_cc") or "Não configurado",
        "reply_to_label": config.get("reply_to") or "Não configurado",
        "project_label": email["context"]["project"],
        "designation_label": email["context"]["designation"],
        "send_ready": readiness["ready"],
        "send_blockers": readiness["blockers"],
        "send_block_message": treatment_send_block_message(readiness["blockers"]),
    }


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def quality_treatment_email_preview(request, codigo, treatment_id):
    order, treatment = _scoped_treatment(request, codigo, treatment_id)
    context = _tq_email_page_context(request, order, treatment)
    context["preview_only"] = True
    return render(request, "qualidade/treatment_email_preview.html", context)


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_http_methods(["GET", "POST"])
def quality_treatment_send(request, codigo, treatment_id):
    order, treatment = _scoped_treatment(request, codigo, treatment_id)
    context = _tq_email_page_context(request, order, treatment)
    context["preview_only"] = False
    if request.method == "GET":
        return render(request, "qualidade/treatment_email_preview.html", context)

    if request.POST.get("confirm_send") != "yes":
        messages.error(request, "Confirme explicitamente o envio na tela de conferência.")
        return render(request, "qualidade/treatment_email_preview.html", context, status=400)

    integration = ExternalAPIIntegration.objects.filter(
        provider=ExternalAPIIntegration.Provider.BREVO,
    ).first()
    readiness = brevo_send_readiness(treatment, integration)
    if not readiness["ready"]:
        messages.error(request, "Envio bloqueado: " + "; ".join(readiness["blockers"]) + ".")
        context["send_ready"] = False
        context["send_blockers"] = readiness["blockers"]
        return render(request, "qualidade/treatment_email_preview.html", context, status=400)

    send_method = readiness["method"]
    try:
        if send_method == "smtp":
            smtp_password = str(decrypt_external_api_smtp_secret(integration) or "")
            smtp_login = str(readiness["config"].get("smtp_login") or "").strip()
            if not smtp_password or not smtp_login:
                raise ValueError("Credencial SMTP vazia")
            brevo_client = None
        else:
            api_key = str(decrypt_external_api_secret(integration) or "").strip()
            if not api_key:
                raise ValueError("Credencial vazia")
            brevo_client = BrevoTransactionalClient(api_key)
    except Exception:
        context["send_ready"] = False
        context["send_blockers"] = ["API Key protegida indisponível para este processo"]
        messages.error(request, "Envio bloqueado: não foi possível recuperar a credencial protegida da Brevo.")
        return render(request, "qualidade/treatment_email_preview.html", context, status=400)

    detail = _tq_email_detail(request, order, treatment)
    email = build_treatment_email(treatment, detail=detail, request=request)
    if len(email["html_content"].encode("utf-8")) + len(email["text_content"].encode("utf-8")) > 300_000:
        messages.error(request, "Envio bloqueado: o conteúdo excede o limite operacional de tamanho.")
        context["send_ready"] = False
        context["send_blockers"] = ["Conteúdo acima de 300 KB"]
        return render(request, "qualidade/treatment_email_preview.html", context, status=400)
    payload = None
    if send_method == "api_rest":
        try:
            payload = BrevoTransactionalClient.build_transactional_payload(
                sender_email=readiness["config"].get("sender_email"),
                sender_name=readiness["config"].get("sender_name"),
                to_email=readiness["config"].get("quality_recipient"),
                cc_email=readiness["config"].get("quality_cc", ""),
                reply_to=readiness["config"].get("reply_to", ""),
                subject=email["subject"], html_content=email["html_content"],
                text_content=email["text_content"], tags=["arion-ops", "quality-treatment"],
            )
        except (TypeError, ValueError):
            messages.error(request, "Envio bloqueado: revise os dados do remetente, destinatário e conteúdo.")
            context["send_ready"] = False
            context["send_blockers"] = ["Configuração de e-mail inválida"]
            return render(request, "qualidade/treatment_email_preview.html", context, status=400)

    retry = treatment.send_status == QualityTreatment.SendStatus.FAILED
    claim = claim_quality_treatment_send(treatment.pk, allow_retry=retry, method=send_method)
    if claim["status"] != "CLAIMED":
        messages.warning(request, "A TQ já teve uma tentativa iniciada ou mudou de estado. Atualize a pré-visualização antes de continuar.")
        return redirect("qualidade:tq_email_preview", codigo=order.code, treatment_id=treatment.pk)

    AuditService.record(
        request=request, user=request.user, category="QUALITY", action="TREATMENT_SEND_STARTED",
        module="Qualidade", description=f"Envio explícito iniciado para a {treatment.code}.", obj=treatment,
        metadata={"treatment_code": treatment.code, "attempt": claim["treatment"].send_attempt_count,
                  "attempt_id": str(claim["treatment"].send_attempt_id), "method": send_method},
    )
    try:
        if not integration.allow_real_sends:
            raise BrevoSendingDisabled()
        if send_method == "smtp":
            result = send_brevo_smtp(
                username=smtp_login, password=smtp_password, config=readiness["config"],
                subject=email["subject"], text_content=email["text_content"],
                html_content=email["html_content"],
            )
        else:
            result = brevo_client.send_transactional(payload, allow_real_sends=integration.allow_real_sends)
    except BrevoSendingDisabled:
        result = BrevoResult(False, "Envios reais foram desabilitados antes do envio.")
    except Exception as exc:
        logger.warning("Quality treatment SMTP dispatch failed stage=smtp_dispatch exception=%s", type(exc).__name__)
        result = SMTPResult(False, "Falha de comunicação; resultado do envio pode ser incerto.", uncertain=True,
                            exception_class=type(exc).__name__, stage="smtp_dispatch")

    accepted = result.ok and (send_method == "smtp" or bool(result.message_id))
    if accepted:
        message_id = result.message_id if send_method == "api_rest" else ""
        recorded = record_tq_send_accepted(treatment.pk, message_id)
        action = "TREATMENT_SEND_ACCEPTED"
        description = f"Brevo aceitou a solicitação da {treatment.code} por {send_method.upper()}."
        outcome = "SUCCESS"
        metadata = {"treatment_code": treatment.code, "method": send_method,
                    "attempt_id": str(claim["treatment"].send_attempt_id),
                    **({"message_id": result.message_id} if send_method == "api_rest" else {})}
        success_message = f"Solicitação enviada à Brevo. A {treatment.code} aguarda a vinculação real do ticket OTRS."
    else:
        recorded = record_tq_send_failure(treatment.pk, result.message, uncertain=result.uncertain)
        action = "TREATMENT_SEND_UNCERTAIN" if result.uncertain else "TREATMENT_SEND_FAILED"
        description = f"Resultado do envio da {treatment.code}: {result.message}"
        outcome = "FAILURE"
        metadata = {"treatment_code": treatment.code, "method": send_method,
                    "attempt_id": str(claim["treatment"].send_attempt_id),
                  "status_code": getattr(result, "status_code", getattr(result, "error_code", None)),
                  "uncertain": result.uncertain,
                  "exception_type": getattr(result, "exception_class", ""),
                  "stage": getattr(result, "stage", ""),
                  "sanitized_message": str(result.message or "")[:240]}
        success_message = (
            "Resultado incerto. O sistema não repetirá o envio automaticamente; faça a reconciliação antes de qualquer nova ação."
            if result.uncertain else "A Brevo recusou o envio. A TQ ficou disponível para nova tentativa controlada."
        )
    if recorded:
        AuditService.record(
            request=request, user=request.user, category="QUALITY", action=action,
            module="Qualidade", description=description, obj=treatment, result=outcome, metadata=metadata,
        )
        (messages.success if accepted else messages.error)(request, success_message)
    else:
        messages.error(request, "O estado da tentativa mudou antes de o resultado ser registrado. Consulte o histórico da TQ.")
    return redirect("qualidade:ordem_detail", codigo=order.code)


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def quality_treatment_reconcile_send(request, codigo, treatment_id):
    """Manually reconcile an ambiguous send; this endpoint never sends email."""
    import re
    from types import SimpleNamespace
    from django.db import transaction
    from django.utils import timezone
    from aplicativos.integracoes.clients.factory import configured_otrs_client
    from aplicativos.integracoes.models import Integration
    from aplicativos.qualidade.services.tq_correlation import extract_correlation_ids, link_quality_ticket_from_article
    from aplicativos.qualidade.services.tq_otrs_correlation import quality_queue_names

    order, treatment = _scoped_treatment(request, codigo, treatment_id)
    if treatment.send_status != QualityTreatment.SendStatus.UNCERTAIN:
        messages.error(request, "A reconcilia??o s? est? dispon?vel para uma tentativa em resultado incerto.")
        return redirect("qualidade:ordem_detail", codigo=order.code)
    checks_confirmed = all(request.POST.get(name) == "yes" for name in (
        "confirm_brevo", "confirm_mailbox", "confirm_otrs",
    ))
    evidence_reference = str(request.POST.get("evidence_reference") or "").strip()
    if (not checks_confirmed or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/#-]{5,99}", evidence_reference)):
        messages.error(request, "Confirme as tr?s verifica??es e informe a refer?ncia da evid?ncia consultada.")
        return redirect("qualidade:ordem_detail", codigo=order.code)

    decision = request.POST.get("decision")
    if decision == "not_delivered":
        if treatment.quality_ticket_external_id or treatment.send_attempt_count != 1:
            messages.error(request, "A tentativa n?o pode ser liberada: h? v?nculo existente ou o hist?rico n?o permite uma libera??o ?nica.")
            return redirect("qualidade:ordem_detail", codigo=order.code)
        with transaction.atomic():
            updated = QualityTreatment.objects.filter(
                pk=treatment.pk, send_status=QualityTreatment.SendStatus.UNCERTAIN,
                status=QualityTreatment.Status.PENDING_SEND,
                send_attempt_count=1, quality_ticket_external_id__isnull=True,
            ).update(
                send_status=QualityTreatment.SendStatus.FAILED,
                status=QualityTreatment.Status.SEND_FAILED,
                send_error="Reconciliada manualmente como n?o entregue; uma tentativa manual liberada.",
                updated_at=timezone.now(),
            )
            if updated:
                AuditService.record(
                    request=request, user=request.user, category="QUALITY",
                    action="TREATMENT_UNCERTAIN_RECONCILED_NOT_DELIVERED",
                    module="Qualidade", description=f"Tentativa incerta da {treatment.code} conciliada como n?o entregue; uma nova tentativa manual foi liberada.",
                    obj=treatment, result="SUCCESS",
                    metadata={"treatment_code": treatment.code,
                              "attempt_id": str(treatment.send_attempt_id),
                              "checks": {"brevo": True, "mailbox": True, "otrs": True},
                              "evidence_reference": evidence_reference,
                              "manual_retry_limit": 1},
                )
        if updated:
            messages.success(request, "Reconcilia??o registrada. Uma tentativa manual foi liberada para esta TQ; nenhum envio foi executado.")
        else:
            messages.error(request, "O estado mudou durante a reconcilia??o; nenhuma tentativa foi liberada.")
        return redirect("qualidade:ordem_detail", codigo=order.code)

    if decision != "delivered":
        messages.error(request, "Selecione uma decis?o de reconcilia??o v?lida.")
        return redirect("qualidade:ordem_detail", codigo=order.code)

    ticket_number = str(request.POST.get("ticket_number") or "").strip()
    if not ticket_number or len(ticket_number) > 80:
        messages.error(request, "Para confirmar entrega, informe o n?mero do ticket OTRS.")
        return redirect("qualidade:ordem_detail", codigo=order.code)
    integration = Integration.objects.filter(key=Integration.Key.OTRS, enabled=True).first()
    try:
        if integration is None:
            raise ValueError("OTRS n?o configurado")
        client = configured_otrs_client(integration)
        rows = client.find_quality_ticket_article_by_qid(ticket_number, treatment.correlation_id, limit=20)
    except Exception as exc:
        logger.warning("Manual TQ reconciliation OTRS lookup failed treatment_id=%s exception=%s",
                       treatment.pk, type(exc).__name__)
        rows = []
    exact_rows = [row for row in rows if treatment.correlation_id in extract_correlation_ids(row.get("article_body"))]
    queue_names = set(quality_queue_names())
    ticket_ids = {int(row.get("ticket_id") or 0) for row in exact_rows}
    if not exact_rows or len(ticket_ids) != 1 or any(row.get("queue_name") not in queue_names for row in exact_rows):
        AuditService.record(
            request=request, user=request.user, category="QUALITY",
            action="TREATMENT_UNCERTAIN_RECONCILIATION_REVIEWED", module="Qualidade",
            description=f"A verifica??o de entrega da {treatment.code} n?o encontrou uma correspond?ncia ?nica na fila autorizada; o estado incerto foi preservado.",
            obj=treatment, result="FAILURE",
            metadata={"treatment_code": treatment.code, "decision": "delivered",
                      "ticket_number": ticket_number, "evidence_reference": evidence_reference,
                      "matching_articles": len(exact_rows), "queue_valid": bool(exact_rows) and all(row.get("queue_name") in queue_names for row in exact_rows)},
        )
        messages.error(request, "N?o foi comprovado um artigo ?nico com o QID na fila autorizada. A TQ permanece incerta e bloqueada para reenvio.")
        return redirect("qualidade:ordem_detail", codigo=order.code)
    row = max(exact_rows, key=lambda item: int(item.get("article_id") or 0))
    ticket = SimpleNamespace(
        external_id=int(row.get("ticket_id") or 0), ticket_number=str(row.get("ticket_number") or ""),
        queue_external_id=int(row.get("queue_id") or 0), queue_name=str(row.get("queue_name") or ""),
        external_state_id=int(row.get("state_id") or 0), external_state_name=str(row.get("state_name") or ""),
        is_present_in_queue=True,
    )
    linked = link_quality_ticket_from_article(
        ticket=ticket, article_body=row.get("article_body"), allowed_queue_names=queue_names,
        actor=request.user, request=request, trusted_source=True,
    )
    if linked.get("status") not in {"LINKED", "ALREADY_LINKED"} or linked.get("treatment", treatment).pk != treatment.pk:
        messages.error(request, "O ticket foi localizado, mas houve conflito de v?nculo. A TQ n?o foi liberada para reenvio.")
        return redirect("qualidade:ordem_detail", codigo=order.code)
    article_time = row.get("article_time") or timezone.now()
    if not hasattr(article_time, "tzinfo"):
        article_time = timezone.now()
    QualityTreatment.objects.filter(pk=treatment.pk).update(
        send_status=QualityTreatment.SendStatus.SENT,
        sent_at=treatment.sent_at or article_time,
        status=QualityTreatment.Status.IN_PROGRESS,
        send_error="",
        updated_at=timezone.now(),
    )
    AuditService.record(
        request=request, user=request.user, category="QUALITY",
        action="TREATMENT_UNCERTAIN_RECONCILED_DELIVERED", module="Qualidade",
        description=f"Entrega da {treatment.code} comprovada por artigo OTRS; v?nculo priorizado e reenvio bloqueado.",
        obj=treatment, result="SUCCESS",
        metadata={"treatment_code": treatment.code, "ticket_number": ticket.ticket_number,
                  "ticket_external_id": ticket.external_id, "evidence_reference": evidence_reference,
                  "correlation_source": "article_body"},
    )
    messages.success(request, "Entrega comprovada pelo QID no artigo OTRS. O ticket foi vinculado e nenhum reenvio foi realizado.")
    return redirect("qualidade:ordem_detail", codigo=order.code)


def _decorate_occurrence_ticket(ticket, review_index, *, is_valid=True):
    review = review_index.get(ticket.external_id)
    ticket.quality_review = review
    ticket.is_valid_occurrence = is_valid
    position = getattr(ticket, "sequence_number", None)
    ticket.sequence_label = getattr(ticket, "sequence_label", "") or (
        "Ocorrência inicial" if position == 1 else f"Reincidência #{position - 1}" if position else ""
    )
    ticket.exclusion_label = "Válida" if is_valid else "Não contabilizada pelas regras operacionais"
    ticket.is_pending_review = is_valid and not bool(
        review and (review.excluded or review.is_initial_occurrence or review.counts_as_recurrence)
    )
    ticket.reason_display = review.reason if review and review.reason else "Não classificado"
    ticket.closing_code_display = ticket.closing_code_raw
    ticket.root_cause_display = review.root_cause if review and review.root_cause else "Não classificada"
    ticket.same_cause_label = "Mesma causa confirmada" if review and review.counts_as_recurrence and review.same_root_cause and not review.excluded else ""
    if review and review.excluded:
        classification = "EXCLUDED"
    elif review and review.is_initial_occurrence:
        classification = "INITIAL"
    elif review and review.counts_as_recurrence:
        classification = "RECURRENCE"
    else:
        classification = "INITIAL" if position == 1 else "RECURRENCE"
    ticket.review_classification = classification
    ticket.review_same_cause = bool(review and review.same_root_cause)
    ticket.review_reason = review.reason if review else ""
    ticket.review_root_cause = review.root_cause if review else ""
    ticket.review_exclusion_reason = review.exclusion_reason if review else ""


def _occurrence_exclusion_label(ticket, review):
    if review and review.excluded:
        labels = {"CANCELADO": "Cancelado", "IMPROCEDENTE": "Improcedente", "DUPLICADO": "Duplicado", "FORA_ESCOPO": "Fora do escopo", "OUTRO": "Outro motivo"}
        return f"{labels.get(review.exclusion_reason, 'Desconsiderada')} · não contabilizada"
    if ticket.business_status == OTRSStateMapping.BusinessStatus.CANCELED:
        return "Cancelado · não contabilizado"
    if ticket.responsibility_normalized == "IMPROCEDENTE":
        return "Improcedente · não contabilizada"
    if ticket.business_status == OTRSStateMapping.BusinessStatus.UNKNOWN:
        return "Estado não mapeado · não contabilizado"
    return "Não contabilizada pelas regras operacionais"


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def occurrence_review(request, codigo, ticket_external_id):
    order = get_object_or_404(
        QualityDataScopeService.orders_for(request.user).select_related("link"), code=codigo,
    )
    form = QualityOccurrenceReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Exclusão exige um motivo. Revise os campos da ocorrência.")
        return redirect(_safe_quality_return(request))
    try:
        _occurrence, changed = review_quality_occurrence(
            quality_order=order,
            ticket_external_id=ticket_external_id,
            actor=request.user,
            request=request,
            **form.cleaned_data,
        )
    except LookupError:
        raise Http404 from None
    messages.success(request, "Revisão registrada e métricas atualizadas." if changed else "A ocorrência já estava com essa classificação.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def quality_ticket_link(request, codigo):
    order = get_object_or_404(QualityDataScopeService.orders_for(request.user), code=codigo)
    form = QualityTicketLinkForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe o ticket e o tipo de vínculo.")
        return redirect(_safe_quality_return(request))
    try:
        _ticket, created = link_quality_ticket(
            quality_order=order, actor=request.user, request=request, **form.cleaned_data,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "TQ vinculado à OQ." if created else "Este TQ já está vinculado à OQ.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def quality_treatment_create(request, codigo):
    order = get_object_or_404(QualityDataScopeService.orders_for(request.user), code=codigo)
    scoped_tickets = support_tickets_for_order(request.user, order)
    form = QualityTreatmentCreateForm(request.POST, support_tickets=scoped_tickets)
    if not form.is_valid():
        messages.error(request, "Informe um motivo válido e selecione somente um chamado de suporte ativo relacionado.")
        return redirect(reverse("qualidade:ordem_detail", kwargs={"codigo": codigo}) + "?tab=tratativas")
    try:
        treatment = create_quality_treatment(
            quality_order=order, actor=request.user, request=request, **form.cleaned_data,
        )
    except ValidationError as exc:
        messages.warning(request, " ".join(exc.messages))
        return redirect(reverse("qualidade:ordem_detail", kwargs={"codigo": codigo}) + "?tab=tratativas")
    messages.success(
        request,
        f"{treatment.code} criada com sucesso. Estado: Pendente de envio. Tratativa registrada no ARION OPS; o envio da solicitação será disponibilizado posteriormente.",
    )
    return redirect(reverse("qualidade:ordem_detail", kwargs={"codigo": codigo}) + "?tab=tratativas")


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def quality_action_add(request, codigo):
    order = get_object_or_404(QualityDataScopeService.orders_for(request.user), code=codigo)
    form = QualityActionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe uma ação para o checklist.")
        return redirect(_safe_quality_return(request))
    try:
        add_quality_action(quality_order=order, actor=request.user, request=request, **form.cleaned_data)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Ação adicionada ao checklist.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def quality_treatment_summary_update(request, codigo, treatment_id):
    order, treatment = _scoped_treatment(request, codigo, treatment_id)
    form = QualityTreatmentSummaryForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revise o resumo gerencial informado.")
        return redirect(_safe_quality_return(request))
    try:
        _treatment, changed = update_quality_treatment_summary(
            treatment=treatment, actor=request.user, request=request, **form.cleaned_data,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Resumo da tratativa atualizado." if changed else "Nenhuma alteração informada.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def quality_treatment_complete(request, codigo, treatment_id):
    order, treatment = _scoped_treatment(request, codigo, treatment_id)
    form = QualityTreatmentCompleteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Confirme a conclusão da TQ após a avaliação do RREIP.")
        return redirect(_safe_quality_return(request))
    try:
        complete_quality_treatment(treatment=treatment, actor=request.user, request=request, **form.cleaned_data)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"{treatment.code} concluída no ARION OPS. O ticket OTRS não foi alterado.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def quality_treatment_cancel(request, codigo, treatment_id):
    order, treatment = _scoped_treatment(request, codigo, treatment_id)
    form = QualityTreatmentCancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe a justificativa para o cancelamento administrativo.")
        return redirect(_safe_quality_return(request))
    try:
        cancel_quality_treatment(treatment=treatment, actor=request.user, request=request, **form.cleaned_data)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"{treatment.code} cancelada localmente; nenhum ticket OTRS foi alterado.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def quality_action_toggle(request, codigo, action_id):
    is_async = request.headers.get("x-requested-with") == "XMLHttpRequest"
    completed_value = request.POST.get("completed")
    if is_async and completed_value not in {"true", "false"}:
        return JsonResponse({"ok": False, "message": "Estado do checklist inválido."}, status=400)
    completed = completed_value == "true"

    try:
        # Keep the write, audit event, and response aggregates in one transaction.
        # If SQLite is busy, the UI must not receive a false success or stale write.
        with transaction.atomic():
            order = get_object_or_404(QualityDataScopeService.orders_for(request.user), code=codigo)
            action = get_object_or_404(QualityAction, pk=action_id, quality_order=order)
            action, changed = toggle_quality_action(
                quality_order=order, action_id=action.pk, completed=completed,
                actor=request.user, request=request,
            )
            if is_async:
                progress = order.actions.aggregate(
                    total=Count("pk"), completed=Count("pk", filter=Q(completed=True)),
                )
                payload = {
                    "ok": True,
                    "changed": changed,
                    "completed": action.completed,
                    "completed_at": timezone.localtime(action.completed_at).strftime("%d/%m/%Y %H:%M") if action.completed_at else "",
                    "completed_by": (action.completed_by.get_full_name() or action.completed_by.get_username()) if action.completed_by_id else "",
                    "total": progress["total"],
                    "completed_count": progress["completed"],
                }
    except Http404:
        if is_async:
            return JsonResponse({"ok": False, "message": "Ordem ou item de checklist não encontrado."}, status=404)
        raise
    except (ValidationError, QualityAction.DoesNotExist) as exc:
        error = " ".join(exc.messages) if isinstance(exc, ValidationError) else "Ação indisponível."
        if is_async:
            return JsonResponse({"ok": False, "message": error}, status=404 if isinstance(exc, QualityAction.DoesNotExist) else 400)
        messages.error(request, error)
    except OperationalError:
        if not is_async:
            raise
        logger.exception("Falha de persistência ao atualizar checklist; ordem=%s action_id=%s", codigo, action_id)
        return JsonResponse({
            "ok": False,
            "message": "Banco temporariamente indisponível. A alteração não foi salva; tente novamente.",
        }, status=503)
    else:
        if is_async:
            return JsonResponse(payload)
        if changed:
            messages.success(request, "Checklist atualizado.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def monitoring_cycle_start(request, codigo):
    order = get_object_or_404(QualityDataScopeService.orders_for(request.user), code=codigo)
    form = QualityMonitoringStartForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revise os dados do início do ciclo RREIP.")
        return redirect(_safe_quality_return(request))
    try:
        start_monitoring_cycle(
            quality_order=order, actor=request.user, request=request, **form.cleaned_data,
        )
    except (ValidationError, ValueError) as exc:
        messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
    else:
        messages.success(request, "Ciclo RREIP iniciado no ARION OPS.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def monitoring_cycle_finish(request, codigo, cycle_id):
    order = get_object_or_404(QualityDataScopeService.orders_for(request.user), code=codigo)
    cycle = get_object_or_404(order.monitoring_cycles.all(), pk=cycle_id)
    form = QualityMonitoringFinishForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe um ticket válido para registrar reincidência.")
        return redirect(_safe_quality_return(request))
    try:
        finish_monitoring_cycle(
            cycle=cycle, actor=request.user, request=request, **form.cleaned_data,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        result = form.cleaned_data.get("result")
        messages.success(request, "Monitoramento validado. Conclua a TQ na aba Tratativas." if result == QualityMonitoringCycle.Result.SUCCESS else "Resultado do ciclo registrado localmente.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_CONTACTS_PERMISSION, raise_exception=True)
@require_POST
def contact_save(request):
    contact_id = str(request.POST.get("contact_id", "")).strip()
    contact = None
    if contact_id:
        if not contact_id.isdigit():
            raise Http404
        contact = get_object_or_404(contacts_for_user(request.user), pk=int(contact_id))
    form = QualityContactForm(request.POST, instance=contact)
    if not form.is_valid():
        messages.error(request, "Revise os campos do contato: " + " ".join(str(error) for errors in form.errors.values() for error in errors))
        return redirect(_safe_quality_return(request))
    try:
        saved = save_quality_contact(
            contact=contact, actor=request.user, request=request, **form.cleaned_data,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(_safe_quality_return(request))
    messages.success(request, f"Contato {saved.name} salvo.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_CONTACTS_PERMISSION, raise_exception=True)
@require_POST
def contact_toggle(request, contact_id):
    contact = get_object_or_404(contacts_for_user(request.user), pk=contact_id)
    active = request.POST.get("active") == "true"
    _contact, changed = set_quality_contact_active(
        contact=contact, active=active, actor=request.user, request=request,
    )
    if changed:
        messages.success(request, "Contato reativado." if active else "Contato desativado; ele permanece no histórico.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def monitoring(request):
    context = get_monitoring_queue(request.user, request.GET)
    context["filter_chips"]=_filter_chips(request,context["selected"])
    context["clear_filters_url"]=reverse("qualidade:monitoramento")+"?period=30"
    options=context["filter_options"]
    context["primary_options"]={"clients":options["client"],"projects":options["project"],"regions":options["regions"],"ufs":options["uf"]}
    context["tab_query"] = _quality_filter_query(request, context, exclude_keys=("tab",))
    context.update({
        "active_section": "monitoring",
        "page_title": "Monitoramento RREIP",
        "page_subtitle": "Acompanhe os ciclos locais de verificação e identifique os próximos vencimentos.",
        "page_query": _quality_filter_query(request, context),
    })
    return render(request, "qualidade/monitoring.html", context)


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def contacts(request):
    context = get_quality_contacts(request.user, request.GET)
    context.update({
        "active_section": "contacts",
        "page_title": "Contatos e escalonamentos",
        "page_subtitle": "Consulte a matriz operacional usada pela Qualidade para acionar cada equipe.",
        "can_manage_contacts": request.user.has_perm(MANAGE_CONTACTS_PERMISSION),
        "contact_form": QualityContactForm(),
        "page_query": _query_without(request, "page"),
    })
    return render(request, "qualidade/contacts.html", context)


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def history(request):
    context = get_global_quality_history(request.user, request.GET)
    context["filter_chips"]=_filter_chips(request,context["selected"])
    context["clear_filters_url"]=reverse("qualidade:historico")+"?period=30"
    context.update({
        "active_section": "history",
        "page_title": "Histórico da Qualidade",
        "page_subtitle": "Consulte os eventos auditados das OQs e do diretório de contatos.",
        "page_query": _quality_filter_query(request, context),
    })
    return render(request, "qualidade/history.html", context)
