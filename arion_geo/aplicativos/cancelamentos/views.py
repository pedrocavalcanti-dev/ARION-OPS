import csv
from itertools import batched

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse, QueryDict
from django.template.loader import render_to_string
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_headers

from aplicativos.auditoria.services import AuditService
from aplicativos.chamados.services import otrs_ticket_url

from . import forms
from .services.data_scope import CancellationScope
from .services.otrs_intake import intake_settings, source_status, synchronization_indicator, current_intakes
from .models import CancellationIntake, CancellationPending, CancellationProcess
from .services.queries import query_module, TABS
from .services.legacy_history import legacy_context, present_events
from .services.legacy_people import names_for
from .services.presentation import prepare_rows
from .services.financial_totals import format_brl
from .services.workflow import (Conflict, require, decide_intake, act_on_process,
                                financial_summary, completion_blockers, pending_is_financial)


def module_access(view):
    return login_required(permission_required("cancelamentos.view_cancellations", raise_exception=True)(view))


@module_access
@never_cache
def dashboard(request):
    from .services.dashboard import build_dashboard
    return render(request, "cancelamentos/dashboard.html", build_dashboard(request.user, request.GET))


def _url_params(params, **changes):
    query = params.copy()
    query.pop("page", None)
    for key, value in changes.items():
        query[key] = str(value)
    return "?" + query.urlencode()


def _tab_url(params, tab):
    query = params.copy()
    for key in (("pending_kind", "sent", "stage", "with_open_pending") if tab == "intake" else ("situation", "presence")):
        query.pop(key, None)
    if tab != "pending":
        query.pop("stage", None)
    return _url_params(query, tab=tab)


def _metric_url(params, tab):
    query = params.copy()
    query.pop("with_open_pending", None)
    query.pop("stage", None)
    if tab == "intake":
        query.pop("presence", None)
        query.pop("situation", None)
    return _tab_url(query, tab)


def _return_url(request, default):
    if "return_query" not in request.POST:
        return default
    query = QueryDict(request.POST.get("return_query", ""))
    safe = QueryDict(mutable=True)
    for key in (*forms.QueryForm.base_fields, "page"):
        if key in query:
            safe[key] = query[key]
    return reverse("cancelamentos:index") + "?" + safe.urlencode()


@module_access
@vary_on_headers("X-Cancellation-Fragment")
def index(request):
    data = query_module(request.user, request.GET)
    page = Paginator(data.pop("queryset"), 40).get_page(request.GET.get("page"))
    if data["tab"] != "intake":
        page.object_list = prepare_rows(page.object_list, request.user)
    current_ids = set(current_intakes(CancellationIntake.objects.filter(pk__in=[row.pk for row in page])).values_list("pk", flat=True)) if data["tab"] == "intake" else set()
    for row in page:
        intake = data["tab"] == "intake"
        row.responsible = row.assigned_to if intake else row.owner
        row.current_entry = row.pk in current_ids if intake else False
        row.has_official = row._has_official if intake else False
        row.can_assign = request.user.has_perm("cancelamentos.assign_cancellations") and (
            (row.status == "WAITING" and row.current_entry and not row._has_official) if intake else True)
        row.assign_url = reverse("cancelamentos:intake_action" if intake else "cancelamentos:process_action", args=(row.pk, "assign"))
        row.assume_url = reverse("cancelamentos:intake_action" if intake else "cancelamentos:process_action", args=(row.pk, "assume"))
    assignment_form = forms.AssignmentForm()
    assignment_form.fields.pop("revision")
    assignment_form.auto_id = "id_list_%s"
    context = {
        **data, "page": page,
        "tabs": [{"key": key, "label": label, "url": _tab_url(request.GET, key), "metric_url": _metric_url(request.GET, key), "count": data["metrics"][key]} for key, label in TABS],
        "quick_filters": [{"label": label, "value": value, "url": _url_params(request.GET, owner=value)} for value, label in (("", "Todos"), ("me", "Meus" if data["tab"] == "intake" else "Meus cancelamentos"), ("none", "Sem responsável"))],
        "sync_indicator": synchronization_indicator(), "assignment_form": assignment_form,
        "list_query": request.GET.urlencode(),
        "open_pending_url": _url_params(request.GET, tab="pending", with_open_pending="on", stage=""),
        "export_query": request.GET.urlencode(),
        "previous_url": _url_params(request.GET, page=page.previous_page_number()) if page.has_previous() else "",
        "next_url": _url_params(request.GET, page=page.next_page_number()) if page.has_next() else "",
    }
    if request.headers.get("X-Cancellation-Fragment") == "1":
        return JsonResponse({"html": render_to_string("cancelamentos/_workspace.html", context, request=request)})
    return render(request, "cancelamentos/index.html", context)


@module_access
def intake_detail(request, pk):
    entry = get_object_or_404(CancellationScope.intakes(request.user).select_related("ticket", "assigned_to", "decided_by"), pk=pk)
    form = forms.TriageForm(user=request.user, intake=entry, initial={"revision": entry.revision, "link": entry.link_id})
    candidates = list(form.fields["link"].queryset.select_related("operator"))
    can_finance = request.user.has_perm("cancelamentos.view_cancellation_finance")
    history = entry.events.filter(process__isnull=True) | entry.events.filter(action="triage_approve")
    if not can_finance:
        history = history.filter(financial=False)
    official_identity = Q(intake=entry) | Q(ticket_number=entry.ticket_number)
    if entry.ticket_id:
        official_identity |= Q(source_ticket_id=entry.ticket_id)
    process = CancellationScope.processes(request.user).filter(official_identity).first()
    source_ok, source_notice, _ = source_status()
    return render(request, "cancelamentos/intake_detail.html", {
        "entry": entry, "triage_form": form, "candidates": candidates, "process": process,
        "is_current_entry": current_intakes(CancellationIntake.objects.filter(pk=entry.pk)).exists(),
        "has_official_process": CancellationProcess.objects.filter(official_identity).exists(),
        "history": present_events(history.select_related("actor")), "otrs_url": otrs_ticket_url(entry.ticket_external_id),
        "source_notice": source_notice, "source_ok": source_ok,
    })


def _action_response(request, url, error=None, status=200, revision=None):
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"ok": error is None, "url": url, "errors": error or [], "revision": revision}, status=status)
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
    if action in ("assign", "assume"):
        url = _return_url(request, url)
        if "return_query" in request.POST and "application/json" not in request.headers.get("Accept", ""):
            messages.success(request, "Responsável atualizado. O recorte da lista foi preservado.")
    return _action_response(request, _return_url(request, url), revision=entry.revision)


def _dialog(action, title, form, *, key=None, child=None, button="Salvar"):
    form.fields.pop("revision", None)
    form.auto_id = f"id_{key or action}_%s"
    return {"action": action, "title": title, "form": form, "key": key or action,
            "child": child, "button": button}


def _process_context(request, pk):
    process = get_object_or_404(CancellationScope.processes(request.user).select_related("intake", "source_ticket", "owner", "treatment", "link__operator"), pk=pk)
    process.responsible = process.owner
    process.can_assign = request.user.has_perm("cancelamentos.assign_cancellations")
    process.assume_url = reverse("cancelamentos:process_action", args=(pk, "assume"))
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
            _dialog("start", "Iniciar tratativa", forms.OperationalForm(), button="Iniciar tratativa"),
            _dialog("status", "Alterar status operacional", forms.StatusForm(initial={"status": process.operational_status})),
            _dialog("move", "Mover / retornar etapa", forms.StageForm(initial={"stage": process.stage})),
            _dialog("update", "Completar informações", forms.ProcessForm(instance=process)),
            _dialog("note", "Registrar observação", forms.NoteForm()),
            _dialog("treatment", "Registrar retorno e confirmação ISP", forms.TreatmentForm(instance=process.treatment)),
            _dialog("evidence", "Registrar referência de evidência", evidence_form),
        ]
        if process.stage == "COMPLETED" and request.user.has_perm("cancelamentos.reopen_cancellations"):
            dialogs.append(_dialog("reopen", "Reabrir com justificativa", forms.StageForm(), button="Confirmar reabertura"))
        if request.user.has_perm("cancelamentos.triage_cancellations") and (process.intake_id or process.source_ticket_id):
            dialogs.append(_dialog("link", "Validar circuito principal", forms.TriageForm(user=request.user, intake=process.intake, ticket=process.source_ticket, initial={"link": process.link_id})))
    if can_pending:
        dialogs += [_dialog("pending_add", "Nova pendência", forms.PendingForm(financial=manage_finance)),
                    _dialog("equipment_add", "Adicionar equipamento", forms.EquipmentForm())]
    pendings = process.pendings.select_related("owner", "legacy_reference")
    if not can_finance:
        pendings = pendings.exclude(kind__in=forms.FINANCIAL_PENDING_KINDS).filter(amount__isnull=True, legacy_reference__isnull=True)
    pendings = list(pendings)
    legacy = legacy_context(process, request.user)
    pending_names = names_for([p.legacy_reference.source_data.get("criada_por_id") for p in pendings
                              if getattr(p, "legacy_reference", None) and p.legacy_reference.source_data.get("criada_por_id") is not None])
    for pending in pendings:
        reference = getattr(pending, "legacy_reference", None)
        pending.historical_creator = pending_names.get(reference.source_data.get("criada_por_id"), "Nome não disponível") if reference else ""
        if reference and reference.entity == "workbook_pending":
            pending.historical_creator = reference.source_data.get("creator_name") or "Nome não disponível"
        pending.historical_responsible = legacy["responsible_name"] if reference and legacy else ""
        pending.can_resolve = False
        if can_pending and pending.status == "OPEN" and (manage_finance or not pending_is_financial(pending)):
            pending.can_resolve = True
            dialogs.append(_dialog("pending_resolve", "Resolver pendência", forms.ResolvePendingForm(),
                                   key=f"pending-{pending.pk}", child=pending.pk))
            dialogs.append(_dialog("pending_update", "Editar pendência", forms.PendingForm(instance=pending, financial=manage_finance), key=f"pending-edit-{pending.pk}", child=pending.pk))
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
    allowed_dialogs = [dialog for dialog in dialogs if process.stage != "COMPLETED" or dialog["action"] in ("assign", "reopen", "note")]
    requested_action = request.GET.get("action", "")
    auto_dialog = next((f"action-{dialog['key']}" for dialog in allowed_dialogs if dialog["action"] == requested_action), "")
    history.object_list = present_events(history.object_list)
    notes = present_events(events.filter(action__in=("note", "legacy_observacaocancelamento"))[:40])
    prepare_rows([process], request.user)
    assignment = process.events.filter(action__in=("assign", "assume")).first()
    last_treatment = process.events.filter(action="treatment").first()
    return {
        "process": process, "dialogs": allowed_dialogs, "auto_dialog": auto_dialog,
        "assigned_at": assignment.created_at if assignment else None, "notes": notes,
        "last_treatment_at": last_treatment.created_at if last_treatment else None,
        "show_technical": request.user.has_perm("configuracoes.inspect_integrations") or request.user.has_perm("configuracoes.view_audit_log"),
        "pendings": pendings, "equipment": equipment,
        "obligations": obligations, "evidence": evidence, "history": history,
        "handoffs": process.handoffs.select_related("actor") if can_finance else [],
        "summary": financial_summary(process) if can_finance else "",
        "blockers": completion_blockers(process) if process.stage != "COMPLETED" else [],
        "otrs_url": otrs_ticket_url(process.external_ticket_id) if process.external_ticket_id else "",
        "legacy": legacy,
    }


@module_access
def detail(request, pk):
    return render(request, "cancelamentos/detail.html", _process_context(request, pk))


@module_access
@never_cache
def process_dialog(request, pk, action):
    context = _process_context(request, pk)
    stage = context["process"].stage
    if ((action == "treatment" and stage not in ("REQUEST", "PENDING"))
            or (action == "complete" and stage not in ("REQUEST", "PENDING"))
            or (action == "start" and stage not in ("ORDER", "PENDING"))):
        raise PermissionDenied
    dialogs = [d for d in context["dialogs"] if d["action"] == action]
    child = request.GET.get("child_id", "")
    if action in ("pending_resolve", "pending_update") and not child:
        if not dialogs:
            raise PermissionDenied
        allowed = {d["child"] for d in dialogs}
        return render(request, "cancelamentos/_pending_picker.html", {
            "picker_url": reverse("cancelamentos:process_dialog", args=(pk, action)),
            "pendings": [p for p in context["pendings"] if p.pk in allowed],
            "title": "Resolver pendência" if action == "pending_resolve" else "Editar pendência",
        })
    dialog = next((d for d in dialogs if not d["child"] or str(d["child"]) == child), None)
    if dialog is None:
        raise PermissionDenied
    return render(request, "cancelamentos/_action_dialog.html", {
        "dialog": dialog, "process": context["process"], "revision": context["process"].revision,
        "action_url": reverse("cancelamentos:process_action", args=(pk, action)),
    })


@module_access
@never_cache
def intake_dialog(request, pk, action):
    require(request.user, "triage_cancellations")
    if action not in ("approve", "reject"):
        raise PermissionDenied
    entry = get_object_or_404(current_intakes(CancellationScope.intakes(request.user)).filter(status="WAITING"), pk=pk)
    identity = Q(ticket_number=entry.ticket_number) | Q(intake=entry)
    if entry.ticket_id:
        identity |= Q(source_ticket_id=entry.ticket_id)
    if CancellationProcess.objects.filter(identity).exists():
        raise PermissionDenied
    dialog = _dialog(action, "Aprovar pedido" if action == "approve" else "Rejeitar entrada",
        forms.TriageForm(user=request.user, intake=entry), button="Aprovar pedido" if action == "approve" else "Rejeitar entrada")
    return render(request, "cancelamentos/_action_dialog.html", {
        "dialog": dialog, "revision": entry.revision,
        "action_url": reverse("cancelamentos:intake_action", args=(pk, action)),
    })


@module_access
@require_POST
def process_action(request, pk, action):
    url = reverse("cancelamentos:detail", args=(pk,))
    try:
        process = act_on_process(request=request, pk=pk, action=action, data=request.POST)
    except (Conflict, IntegrityError, OperationalError) as exc:
        error = exc.messages if isinstance(exc, Conflict) else ["Conflito de atualização. Recarregue o processo antes de tentar novamente."]
        return _action_response(request, url, error, 409)
    except ValidationError as exc:
        return _action_response(request, url, exc.messages, 400)
    if action in ("assign", "assume"):
        url = _return_url(request, url)
        if "return_query" in request.POST and "application/json" not in request.headers.get("Accept", ""):
            messages.success(request, "Responsável atualizado. O recorte da lista foi preservado.")
    return _action_response(request, _return_url(request, url), revision=process.revision)


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
    financial = data["tab"] != "intake" and request.user.has_perm("cancelamentos.view_cancellation_finance")
    header = ["Origem", "TicketID", "Ticket#", "Pedido", "Situação", "Cliente", "Projeto", "Designação", "ISP", "Responsável", "Conclusão operacional"]
    if financial:
        header += ["Multa (R$)", "Equipamento (R$)", "Outros (R$)", "Total informado (R$)", "Total parcial", "Conciliação financeira"]
    writer.writerow(header)
    for batch in batched(rows.iterator(chunk_size=200), 200):
        for obj in prepare_rows(batch, request.user) if financial else batch:
            if data["tab"] == "intake":
                values = ("Espelho OTRS / triagem", obj.ticket_external_id, obj.ticket_number, "", obj.get_status_display(),
                          obj.client_snapshot, obj.project_snapshot, obj.designation_snapshot,
                          obj.link.operator.name if obj.link_id and obj.link.operator else "", obj.assigned_to or "", "")
            else:
                values = (obj.get_origin_display(), obj.external_ticket_id, obj.display_ticket_number, obj.code,
                          obj.get_stage_display(), obj.contracting_client, obj.project, obj.designation, obj.isp, obj.owner or "", obj.completed_at)
            if financial:
                values += tuple(format_brl(obj.finance[key]) for key in ("fine", "equipment", "other", "total")) + (
                    "Sim" if obj.finance["partial"] else "Não", "A conferir" if obj.finance["review"] else "Sem divergência indicada")
            writer.writerow([_csv_value(value) for value in values])
    AuditService.record(request=request, category="cancellation", module="cancelamentos", action="export",
                        description="Exportação operacional de Cancelamentos.", metadata={"count": count, "tab": data["tab"]})
    return response
