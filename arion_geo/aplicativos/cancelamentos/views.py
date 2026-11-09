import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from aplicativos.auditoria.services import AuditService
from aplicativos.chamados.services import otrs_ticket_url
from aplicativos.integracoes.models import IntegrationSyncState

from . import forms
from .services.data_scope import CancellationScope
from .services.otrs_intake import intake_settings, source_status
from .services.queries import query_module, TABS
from .services.workflow import (Conflict, require, decide_intake, act_on_process,
                                financial_summary, completion_blockers)


def module_access(view):
    return login_required(permission_required("cancelamentos.view_cancellations", raise_exception=True)(view))


def _url_params(params, **changes):
    query = params.copy()
    query.pop("page", None)
    for key, value in changes.items():
        query[key] = str(value)
    return "?" + query.urlencode()


@module_access
def index(request):
    data = query_module(request.user, request.GET)
    page = Paginator(data.pop("queryset"), 40).get_page(request.GET.get("page"))
    ok, notice, state = source_status()
    return render(request, "cancelamentos/index.html", {
        **data, "page": page, "source_ok": ok, "source_notice": notice, "source_state": state,
        "projection_state": IntegrationSyncState.objects.filter(key="cancellation_intake").first(),
        "tabs": [{"key": key, "label": label, "url": _url_params(request.GET, tab=key)} for key, label in TABS],
        "export_query": request.GET.urlencode(),
        "previous_url": _url_params(request.GET, page=page.previous_page_number()) if page.has_previous() else "",
        "next_url": _url_params(request.GET, page=page.next_page_number()) if page.has_next() else "",
    })


@module_access
def intake_detail(request, pk):
    entry = get_object_or_404(CancellationScope.intakes(request.user).select_related("ticket", "assigned_to", "decided_by"), pk=pk)
    form = forms.TriageForm(user=request.user, intake=entry, initial={"revision": entry.revision, "link": entry.link_id})
    candidates = list(form.fields["link"].queryset.select_related("operator"))
    can_finance = request.user.has_perm("cancelamentos.view_cancellation_finance")
    history = entry.events.filter(process__isnull=True) | entry.events.filter(action="triage_approve")
    if not can_finance:
        history = history.filter(financial=False)
    process = CancellationScope.processes(request.user).filter(intake=entry).first()
    source_ok, source_notice, _ = source_status()
    return render(request, "cancelamentos/intake_detail.html", {
        "entry": entry, "triage_form": form, "candidates": candidates, "process": process,
        "history": history.select_related("actor"), "otrs_url": otrs_ticket_url(entry.ticket_external_id),
        "source_notice": source_notice, "source_ok": source_ok,
    })


def _action_response(request, url, error=None, status=200):
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"ok": error is None, "url": url, "errors": error or []}, status=status)
    if error:
        for message in error:
            messages.error(request, message)
    else:
        messages.success(request, "Registro salvo com histórico.")
    return redirect(url)


@module_access
@require_POST
def intake_action(request, pk, action):
    url = reverse("cancelamentos:intake_detail", args=(pk,))
    try:
        entry = decide_intake(request=request, pk=pk, action=action, data=request.POST)
        if action == "approve":
            url = reverse("cancelamentos:detail", args=(entry.process.pk,))
    except (Conflict, IntegrityError, OperationalError) as exc:
        error = exc.messages if isinstance(exc, Conflict) else ["Conflito de atualização. Recarregue a entrada antes de tentar novamente."]
        return _action_response(request, url, error, 409)
    except ValidationError as exc:
        return _action_response(request, url, exc.messages, 400)
    return _action_response(request, url)


def _dialog(action, title, form, *, key=None, child=None, button="Salvar"):
    form.fields.pop("revision", None)
    form.auto_id = f"id_{key or action}_%s"
    return {"action": action, "title": title, "form": form, "key": key or action,
            "child": child, "button": button}


@module_access
def detail(request, pk):
    process = get_object_or_404(CancellationScope.processes(request.user).select_related("intake", "owner", "treatment", "link__operator"), pk=pk)
    can_finance = request.user.has_perm("cancelamentos.view_cancellation_finance")
    manage_finance = can_finance and request.user.has_perm("cancelamentos.manage_cancellation_finance")
    can_change = request.user.has_perm("cancelamentos.change_cancellation_stage")
    can_pending = request.user.has_perm("cancelamentos.manage_cancellation_pending")
    dialogs = []
    if request.user.has_perm("cancelamentos.assign_cancellations"):
        dialogs.append(_dialog("assign", "Atribuir responsável", forms.AssignmentForm(initial={"owner": process.owner_id})))
    if can_change:
        evidence_form = forms.EvidenceForm()
        if not manage_finance:
            evidence_form.fields.pop("financial")
        dialogs += [
            _dialog("update", "Completar informações", forms.ProcessForm(instance=process)),
            _dialog("note", "Registrar observação", forms.NoteForm()),
            _dialog("treatment", "Registrar retorno e confirmação ISP", forms.TreatmentForm(instance=process.treatment)),
            _dialog("evidence", "Registrar referência de evidência", evidence_form),
        ]
        if request.user.has_perm("cancelamentos.triage_cancellations"):
            dialogs.append(_dialog("link", "Validar circuito principal", forms.TriageForm(user=request.user, intake=process.intake, initial={"link": process.link_id})))
    if can_pending:
        dialogs += [_dialog("pending_add", "Nova pendência", forms.PendingForm(financial=manage_finance)),
                    _dialog("equipment_add", "Adicionar equipamento", forms.EquipmentForm())]
    pendings = process.pendings.select_related("owner")
    if not can_finance:
        pendings = pendings.exclude(kind__in=forms.FINANCIAL_PENDING_KINDS).filter(amount__isnull=True)
    pendings = list(pendings)
    for pending in pendings:
        pending.can_resolve = False
        if can_pending and pending.status == "OPEN" and (manage_finance or (pending.kind not in forms.FINANCIAL_PENDING_KINDS and pending.amount is None)):
            pending.can_resolve = True
            dialogs.append(_dialog("pending_resolve", f"Resolver pendência #{pending.pk}", forms.ResolvePendingForm(),
                                   key=f"pending-{pending.pk}", child=pending.pk))
    equipment = list(process.equipment.all())
    if can_pending:
        for item in equipment:
            dialogs.append(_dialog("equipment_update", f"Atualizar {item.description}", forms.EquipmentForm(instance=item), key=f"equipment-{item.pk}", child=item.pk))
    obligations = list(process.obligations.all()) if can_finance else []
    if manage_finance:
        dialogs += [_dialog("obligation_add", "Registrar obrigação financeira", forms.ObligationForm()),
                    _dialog("handoff", "Registrar encaminhamento já realizado", forms.HandoffForm(), button="Registrar envio manual")]
        for item in obligations:
            dialogs.append(_dialog("obligation_update", f"Atualizar obrigação #{item.pk}", forms.ObligationForm(instance=item), key=f"obligation-{item.pk}", child=item.pk))
    if request.user.has_perm("cancelamentos.complete_cancellations"):
        dialogs.append(_dialog("complete", "Concluir responsabilidade operacional NOC", forms.CompletionForm(), button="Concluir operacionalmente"))
    events = process.events.select_related("actor")
    evidence = process.evidence.all()
    if not can_finance:
        events, evidence = events.filter(financial=False), evidence.filter(financial=False)
    history = Paginator(events, 40).get_page(request.GET.get("history_page"))
    return render(request, "cancelamentos/detail.html", {
        "process": process, "dialogs": dialogs, "pendings": pendings, "equipment": equipment,
        "obligations": obligations, "evidence": evidence, "history": history,
        "handoffs": process.handoffs.select_related("actor") if can_finance else [],
        "summary": financial_summary(process) if can_finance else "",
        "blockers": completion_blockers(process) if process.stage != "COMPLETED" else [],
        "otrs_url": otrs_ticket_url(process.intake.ticket_external_id),
    })


@module_access
@require_POST
def process_action(request, pk, action):
    url = reverse("cancelamentos:detail", args=(pk,))
    try:
        act_on_process(request=request, pk=pk, action=action, data=request.POST)
    except (Conflict, IntegrityError, OperationalError) as exc:
        error = exc.messages if isinstance(exc, Conflict) else ["Conflito de atualização. Recarregue o processo antes de tentar novamente."]
        return _action_response(request, url, error, 409)
    except ValidationError as exc:
        return _action_response(request, url, exc.messages, 400)
    return _action_response(request, url)


@module_access
def configuration(request):
    require(request.user, "configure_cancellations")
    config = intake_settings()
    form = forms.SettingsForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            config = form.save()
            AuditService.record(request=request, category="cancellation", module="cancelamentos", action="configure_intake",
                                obj=config, description="Configuração da entrada OTRS atualizada.",
                                metadata={"enabled": config.enabled, "max_age_minutes": config.max_age_minutes})
        messages.success(request, "Configuração salva. Será considerada pelo worker existente.")
        return redirect("cancelamentos:configuration")
    return render(request, "cancelamentos/configuration.html", {"form": form, "source_notice": source_status()[1]})


def _csv_value(value):
    value = "" if value is None else str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value


@module_access
def export(request):
    require(request.user, "export_cancellations")
    data = query_module(request.user, request.GET)
    if not data["form"].is_valid():
        return HttpResponse("Filtros inválidos.", status=400)
    rows = data["queryset"]
    count = rows.count()
    if count > 10000:
        return HttpResponse("Refine os filtros para exportar até 10.000 registros.", status=400)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="cancelamentos.csv"'
    response["Cache-Control"] = "no-store"
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(("Origem", "TicketID", "Ticket#", "Pedido", "Situação", "Cliente", "Projeto", "Designação", "ISP", "Responsável", "Conclusão operacional"))
    for obj in rows.iterator(chunk_size=200):
        if data["tab"] == "intake":
            values = ("Espelho OTRS / triagem", obj.ticket_external_id, obj.ticket_number, "", obj.get_status_display(),
                      obj.client_snapshot, obj.project_snapshot, obj.designation_snapshot,
                      obj.link.operator.name if obj.link_id and obj.link.operator else "", obj.assigned_to or "", "")
        else:
            values = ("Processo ARION OPS", obj.intake.ticket_external_id, obj.intake.ticket_number, obj.code,
                      obj.get_stage_display(), obj.contracting_client, obj.project, obj.designation, obj.isp, obj.owner or "", obj.completed_at)
        writer.writerow([_csv_value(value) for value in values])
    AuditService.record(request=request, category="cancellation", module="cancelamentos", action="export",
                        description="Exportação operacional de Cancelamentos.", metadata={"count": count, "tab": data["tab"]})
    return response
