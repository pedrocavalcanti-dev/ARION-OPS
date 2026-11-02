from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.chamados.services import otrs_ticket_url
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSStateMapping

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
)
from .models import QualityAction, QualityContact, QualityMonitoringCycle, QualityOrder, QualityTicket
from .services.dashboard import get_dashboard_data
from .services.constants import QUALITY_PRIORITY_DEFAULT_PERIOD, QUALITY_RREIP_DAYS
from .services.data_scope import QualityDataScopeService
from .services.orders import ActiveQualityOrderExists, create_quality_order, update_quality_order_field
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
    link_quality_ticket,
    toggle_quality_action,
)
from aplicativos.integracoes.services.semantics import link_designation, provider_values_for_links


QUALITY_PERMISSION = "qualidade.view_quality"
MANAGE_QUALITY_PERMISSION = "qualidade.manage_quality_orders"
MANAGE_CONTACTS_PERMISSION = "qualidade.manage_quality_contacts"


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
def dashboard(request):
    context = get_dashboard_data(request.user, request.GET)
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
        "page_query": _page_query(request),
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
    tab_links = []
    for tab in context["tabs"]:
        query = request.GET.copy()
        query["tab"] = tab["key"]
        query.pop("page", None)
        tab_links.append({**tab, "url": f"{reverse('qualidade:ordens')}?{query.urlencode()}"})
    context["tabs"] = tab_links
    context.update({
        "active_section": "orders",
        "page_title": "Ordens de Qualidade",
        "page_subtitle": "Acompanhe os casos de reincidência, responsáveis, prazos, tratativas e monitoramentos.",
        "page_query": _page_query(request),
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


def _query_without(request, *keys):
    query = request.GET.copy()
    for key in keys:
        query.pop(key, None)
    return query.urlencode()


def _filter_chips(request, selected, period=None, owner_labels=None):
    labels = {
        "q": "Busca", "period": "Período", "client": "Cliente", "project": "Projeto", "isp": "ISP", "uf": "UF",
        "level": "Nível", "priority": "Prioridade", "oq": "OQ", "status": "Status", "reason": "Motivo",
        "owner": "Responsável", "due": "Prazo", "rreip": "RREIP", "portfolio": "Carteira",
        "state": "Estado operacional",
    }
    chips = []
    values = dict(selected)
    if period:
        values["period"] = period
    for key, value in values.items():
        if not value:
            continue
        if key == "state" and value == "active":
            continue
        display_value = value
        if key == "period":
            display_value = f"{value} dias"
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
        query.pop(key, None)
        query.pop("page", None)
        chips.append({
            "label": labels.get(key, key),
            "value": display_value,
            "remove_url": f"{request.path}?{query.urlencode()}" if query else request.path,
        })
    return chips


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
    order = get_object_or_404(
        QualityDataScopeService.orders_for(request.user).select_related("owner", "link", "link__operator"),
        code=codigo,
    )
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
        "can_manage_orders": request.user.has_perm(MANAGE_QUALITY_PERMISSION),
        "open_edit_dialog": request.GET.get("edit") == "1" and request.user.has_perm(MANAGE_QUALITY_PERMISSION),
        "rreip_days": QUALITY_RREIP_DAYS,
        "page_query": _query_without(request, "page"),
        "detail_form": QualityOrderDetailForm(initial={
            "status": order.status,
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
    context["quality_actions"] = order.actions.select_related("completed_by").all()
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
        "quality_ticket", "quality_ticket__otrs_ticket", "trigger_occurrence", "trigger_occurrence__otrs_ticket",
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
        cycle.is_ending = cycle.result == QualityMonitoringCycle.Result.IN_PROGRESS and 0 <= (cycle.expected_end_at.date() - today).days <= 3
        cycle.is_overdue = cycle.result == QualityMonitoringCycle.Result.IN_PROGRESS and cycle.expected_end_at.date() < today
    context["monitoring_cycles"] = cycles
    context["active_cycle"] = next(
        (cycle for cycle in cycles if cycle.result == QualityMonitoringCycle.Result.IN_PROGRESS), None,
    )
    context["monitoring_start_form"] = QualityMonitoringStartForm(order=order)
    context["monitoring_finish_form"] = QualityMonitoringFinishForm()
    context["has_financial_reason"] = is_financial_reason(order.current_reason or context["current_reason"])
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
def quality_action_toggle(request, codigo, action_id):
    order = get_object_or_404(QualityDataScopeService.orders_for(request.user), code=codigo)
    action = get_object_or_404(QualityAction, pk=action_id, quality_order=order)
    completed = request.POST.get("completed") == "true"
    try:
        _action, changed = toggle_quality_action(
            quality_order=order, action_id=action.pk, completed=completed,
            actor=request.user, request=request,
        )
    except (ValidationError, QualityAction.DoesNotExist) as exc:
        messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else "Ação indisponível.")
    else:
        if changed:
            messages.success(request, "Checklist atualizado.")
    return redirect(_safe_quality_return(request))


@login_required
@permission_required(QUALITY_PERMISSION, raise_exception=True)
@permission_required(MANAGE_QUALITY_PERMISSION, raise_exception=True)
@require_POST
def monitoring_cycle_start(request, codigo):
    order = get_object_or_404(QualityDataScopeService.orders_for(request.user), code=codigo)
    form = QualityMonitoringStartForm(request.POST, order=order)
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
        messages.success(request, "Resultado do ciclo registrado localmente.")
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
    context.update({
        "active_section": "monitoring",
        "page_title": "Monitoramento RREIP",
        "page_subtitle": "Acompanhe os ciclos locais de verificação e identifique os próximos vencimentos.",
        "page_query": _query_without(request, "page"),
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
    context.update({
        "active_section": "history",
        "page_title": "Histórico da Qualidade",
        "page_subtitle": "Consulte os eventos auditados das OQs e do diretório de contatos.",
        "page_query": _query_without(request, "page"),
    })
    return render(request, "qualidade/history.html", context)
