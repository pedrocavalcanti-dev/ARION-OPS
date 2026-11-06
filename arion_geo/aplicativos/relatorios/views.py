import logging
from pathlib import Path
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponseForbidden, JsonResponse
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from aplicativos.auditoria.models import AuditEvent
from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.models import OTRSTicket
from aplicativos.integracoes.services.otrs_status import business_status_label, normalize_business_status

from .forms import CompetenceCreateForm, TreatmentEditForm, TreatmentOverrideForm, TreatmentPauseForm, WorkbookSelectionForm
from .models import PenaltyCompetence, PenaltyFinancialChange, PenaltySourceRow, PenaltyTreatmentOverride, PenaltyTreatmentPause, PenaltyTreatmentRow, PenaltyTreatmentTicket
from .services.competencies import CompetenceAlreadyExists, competence_counts, create_draft_competence
from .services.imports import ImportAlreadyCompleted, confirm_competence_import
from .services.workbooks import FIELD_LABELS, WorkbookImportError, canonical_ticket_number, inspect_workbook, prepare_uploaded_workbook
from .services.calculations import calculate_treatment, effective_values
from .services.treatment import (
    TERMINAL_DECISIONS,
    claro_treatment_candidates,
    cmdb_isp_identity,
    competence_financial_summary,
    enrich_competence,
    treatment_export_dataset,
    treatment_queryset,
    record_financial_change,
    validate_financial_decision,
)
from .services.exports import EXPORT_SCOPES, build_treatment_workbook


REPORTS_PERMISSION = "relatorios.view_reports"
WORKSPACE_TABS = {"original", "tratativa", "resumo", "historico"}
PAGE_SIZE_CHOICES = (25, 50, 100)
logger = logging.getLogger(__name__)
TREATMENT_COLUMNS = (
    ("rec", "REC"), ("designation", "Designação"), ("client", "Cliente"), ("uf", "UF"), ("lp", "LP"),
    ("ticket", "Ticket OTRS"), ("isp", "ISP"), ("provider", "Fornecedor"), ("contract", "Contrato"), ("bandwidth", "Banda"),
    ("claro-opening", "Abertura Claro"), ("claro-fetec", "FETEC Claro"), ("claro-duration", "Tempo Claro"), ("claro-ev", "EV Claro"),
    ("monthly", "Valor mensal"), ("claro-inter", "Inter Claro"), ("claro-penalty", "Penal Claro"), ("claro-indisp", "Indisp Claro"), ("hours", "Qtd. horas"),
    ("arion-opening", "Abertura ARION"), ("arion-fetec", "FETEC ARION"), ("pauses", "Pausas"), ("tmp", "TMP ARION"),
    ("ev", "EV ARION"), ("inter", "Inter ARION"), ("indisp", "Indisp ARION"), ("cause", "Causa raiz"),
    ("procedence", "Procedência"), ("observation", "Observação"),
    ("total-claro", "Total Claro"), ("total-arion", "Total ARION"), ("contested", "Contestado"), ("reduction", "Redução"),
    ("decision", "Decisão"), ("status", "Status"),
)


def _competences(*, include_archived=False):
    queryset = PenaltyCompetence.objects.filter(report_type=PenaltyCompetence.ReportType.CLARO_PENALTIES)
    if not include_archived:
        queryset = queryset.filter(deleted_at__isnull=True)
    return queryset


def _wants_json(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _form_feedback(form):
    errors = [str(error) for error in form.non_field_errors()]
    errors.extend(str(error) for field in form for error in field.errors)
    return errors or ["Revise os dados e tente novamente."]


def _duplicate_competence_url(request, competence):
    if competence is None:
        return ""
    if competence.deleted_at and not request.user.has_perm("relatorios.delete_finalized_penaltycompetence"):
        return ""
    return reverse("relatorios:workspace", args=(competence.pk,))


def _next_pending_treatment(queryset, source_row_number, *, exclude_pk=None):
    pending = queryset.filter(decision=PenaltyTreatmentRow.Decision.PENDING)
    if exclude_pk is not None:
        pending = pending.exclude(pk=exclude_pk)
    return (
        pending.filter(source_row__source_row_number__gt=source_row_number)
        .select_related("source_row")
        .order_by("source_row__source_row_number")
        .first()
        or pending.select_related("source_row").order_by("source_row__source_row_number").first()
    )


def _treatment_counters(competence):
    summary = competence_financial_summary(competence)
    return {
        "with_penalty": summary["with_penalty"],
        "treated": summary["due_count"] + summary["reduced_count"] + summary["invalid_count"],
        "reduced": summary["reduced_count"],
        "invalid": summary["invalid_count"],
        "pending": summary["pending"],
    }


def _delete_private_original(storage, name):
    try:
        storage.delete(name)
    except Exception:
        logger.exception("Failed to remove private original file after competence deletion.")


def _audit(request, *, action, description, competence, metadata=None):
    return AuditService.record(
        request=request,
        category="REPORTS",
        action=action,
        description=description,
        obj=competence,
        module="relatorios",
        metadata=metadata or {},
    )


@transaction.atomic
def _attach_original(competence, prepared, user):
    competence = PenaltyCompetence.objects.select_for_update().get(pk=competence.pk)
    if competence.original_file or competence.imported_at or competence.source_rows.exists():
        raise WorkbookImportError("Esta competência já possui um arquivo Original associado.")
    stored_name = ""
    original_field_file = competence.original_file
    try:
        original_field_file.save(prepared["filename"], ContentFile(prepared["data"]), save=False)
        stored_name = original_field_file.name
        competence.original_filename = prepared["filename"]
        competence.original_file_size = prepared["file_size"]
        competence.original_file_sha256 = prepared["sha256"]
        competence.original_file_extension = prepared["extension"]
        competence.original_uploaded_at = timezone.now()
        competence.original_uploaded_by = user
        competence.workbook_sheets = prepared["inspection"]["sheets"]
        competence.status = PenaltyCompetence.Status.DRAFT
        competence.import_error = ""
        competence.updated_by = user
        competence.save(update_fields=(
            "original_file", "original_filename", "original_file_size", "original_file_sha256",
            "original_file_extension", "original_uploaded_at", "original_uploaded_by", "workbook_sheets",
            "status", "import_error", "updated_by", "updated_at",
        ))
    except Exception:
        if stored_name:
            original_field_file.storage.delete(stored_name)
        raise
    return competence


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
def dashboard(request):
    competencies = _competences()
    counts = competence_counts(competencies)
    recent = competencies.select_related("created_by").order_by("-created_at", "-pk")[:5]
    return render(request, "relatorios/dashboard.html", {
        "active_section": "overview",
        "counts": counts,
        "recent_competencies": recent,
        "report_events": AuditEvent.objects.filter(module="relatorios").select_related("user")[:5],
    })


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
def claro_penalidades(request):
    queryset = _competences().select_related("created_by", "updated_by")
    year = request.GET.get("ano", "").strip()
    status = request.GET.get("status", "").strip()
    query = request.GET.get("q", "").strip()[:100]
    if year.isdigit() and 2000 <= int(year) <= 2100:
        queryset = queryset.filter(competence_year=int(year))
    else:
        year = ""
    valid_statuses = {value for value, _label in PenaltyCompetence.Status.choices}
    if status in valid_statuses:
        queryset = queryset.filter(status=status)
    else:
        status = ""
    if query:
        matching = Q(original_filename__icontains=query)
        if query.isdigit():
            matching |= Q(competence_year=int(query))
            if 1 <= int(query) <= 12:
                matching |= Q(competence_month=int(query))
        queryset = queryset.filter(matching)
    years = _competences().order_by().values_list("competence_year", flat=True).distinct()
    return render(request, "relatorios/claro/index.html", {
        "active_section": "claro",
        "competencies": Paginator(queryset, 20).get_page(request.GET.get("page")),
        "years": years,
        "selected_year": year,
        "selected_status": status,
        "query": query,
        "status_choices": PenaltyCompetence.Status.choices,
        "counts": competence_counts(_competences()),
    })


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
@require_http_methods(["GET"])
def competence_period_exists(request):
    try:
        month = int(request.GET.get("month", ""))
        year = int(request.GET.get("year", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Informe mês e ano válidos."}, status=400)
    if month not in range(1, 13) or year not in range(2000, 2101):
        return JsonResponse({"error": "Informe mês e ano válidos."}, status=400)
    existing = _competences(include_archived=True).filter(
        competence_month=month, competence_year=year,
    ).first()
    if existing is None:
        return JsonResponse({"exists": False})
    return JsonResponse({
        "exists": True,
        "archived": bool(existing.deleted_at),
        "period": existing.period_label,
        "url": _duplicate_competence_url(request, existing) or None,
    })


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
@require_http_methods(["GET", "POST"])
def competence_create(request):
    form = CompetenceCreateForm(request.POST or None, request.FILES or None)
    prepared = None
    if request.method == "POST" and form.is_valid():
        uploaded_file = form.cleaned_data.get("original_file")
        if uploaded_file:
            try:
                prepared = prepare_uploaded_workbook(uploaded_file)
            except WorkbookImportError as exc:
                if _wants_json(request):
                    return JsonResponse({"ok": False, "messages": [str(exc)]}, status=400)
                form.add_error("original_file", str(exc))

        if not form.errors:
            try:
                with transaction.atomic():
                    competence = create_draft_competence(
                        user=request.user,
                        month=form.cleaned_data["competence_month"],
                        year=form.cleaned_data["competence_year"],
                    )
                    if prepared:
                        competence = _attach_original(competence, prepared, request.user)
            except CompetenceAlreadyExists:
                existing = _competences(include_archived=True).filter(
                    competence_month=form.cleaned_data["competence_month"],
                    competence_year=form.cleaned_data["competence_year"],
                ).first()
                form.duplicate_competence = existing
                period = existing.period_label if existing else f"{form.cleaned_data['competence_month']}/{form.cleaned_data['competence_year']}"
                form.add_error(None, f"Já existe uma competência Claro Multas para {period}.")
            except Exception:
                logger.exception("Failed to create a Claro penalty competence.")
                form.add_error(None, "Não foi possível salvar a competência ou o arquivo. Revise os dados e tente novamente.")
            else:
                _audit(
                    request,
                    action="COMPETENCE_CREATED",
                    description="Competência Claro Multas criada com arquivo Original." if prepared else "Competência Claro Multas criada sem arquivo Original.",
                    competence=competence,
                    metadata={"report_type": competence.report_type, "year": competence.competence_year,
                              "month": competence.competence_month, "status": competence.status},
                )
                if prepared:
                    _audit(
                        request,
                        action="ORIGINAL_UPLOADED_AND_VALIDATED",
                        description="Arquivo Original recebido e validado.",
                        competence=competence,
                        metadata={"filename": competence.original_filename, "size": competence.original_file_size,
                                  "extension": competence.original_file_extension,
                                  "sha256": competence.original_file_sha256,
                                  "sheets": [sheet["name"] for sheet in competence.workbook_sheets]},
                    )
                    messages.success(request, "Arquivo validado. Confira a aba, o cabeçalho e o mapeamento antes de importar.")
                    if _wants_json(request):
                        return JsonResponse({"ok": True, "redirect_url": reverse("relatorios:import-review", args=(competence.pk,))})
                    return redirect("relatorios:import-review", pk=competence.pk)
                messages.success(request, "Competência criada como rascunho. Você pode anexar o arquivo Original no workspace.")
                if _wants_json(request):
                    return JsonResponse({"ok": True, "redirect_url": reverse("relatorios:workspace", args=(competence.pk,))})
                return redirect("relatorios:workspace", pk=competence.pk)
    if request.method == "POST" and _wants_json(request):
        duplicate = getattr(form, "duplicate_competence", None)
        duplicate_url = _duplicate_competence_url(request, duplicate)
        return JsonResponse({
            "ok": False,
            "messages": _form_feedback(form),
            "duplicate_url": duplicate_url or None,
            "duplicate_period": duplicate.period_label if duplicate else "",
            "duplicate_archived": bool(duplicate and duplicate.deleted_at),
        }, status=200 if duplicate else 400)
    return render(request, "relatorios/claro/competence_create.html", {
        "active_section": "claro",
        "form": form,
        "duplicate_competence": getattr(form, "duplicate_competence", None),
        "duplicate_competence_url": _duplicate_competence_url(request, getattr(form, "duplicate_competence", None)),
        "allowed_extensions": ".xls, .xlsx",
        "max_file_size_mb": 25,
    })


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
@require_http_methods(["POST"])
def competence_upload(request, pk):
    competence = get_object_or_404(_competences(), pk=pk)
    if competence.original_file or competence.imported_at or competence.source_rows.exists():
        message = "Esta competência já possui um arquivo Original associado. O arquivo não pode ser substituído."
        if _wants_json(request):
            return JsonResponse({"ok": False, "messages": [message]}, status=409)
        messages.error(request, message)
        return redirect("relatorios:workspace", pk=competence.pk)
    uploaded_file = request.FILES.get("original_file")
    if not uploaded_file:
        message = "Selecione um arquivo XLS ou XLSX."
        if _wants_json(request):
            return JsonResponse({"ok": False, "messages": [message]}, status=400)
        messages.error(request, message)
        return redirect("relatorios:workspace", pk=competence.pk)
    try:
        prepared = prepare_uploaded_workbook(uploaded_file)
        competence = _attach_original(competence, prepared, request.user)
    except WorkbookImportError as exc:
        if _wants_json(request):
            return JsonResponse({"ok": False, "messages": [str(exc)]}, status=400)
        messages.error(request, str(exc))
        return redirect("relatorios:workspace", pk=competence.pk)
    except Exception:
        logger.exception("Failed to store an uploaded Claro penalty workbook.")
        if _wants_json(request):
            return JsonResponse({"ok": False, "messages": ["Não foi possível processar o arquivo. Nenhum dado foi importado."]}, status=500)
        messages.error(request, "Não foi possível armazenar o arquivo enviado. Tente novamente.")
        return redirect("relatorios:workspace", pk=competence.pk)
    _audit(
        request,
        action="ORIGINAL_UPLOADED_AND_VALIDATED",
        description="Arquivo Original recebido e validado.",
        competence=competence,
        metadata={"filename": competence.original_filename, "size": competence.original_file_size,
                  "extension": competence.original_file_extension, "sha256": competence.original_file_sha256,
                  "sheets": [sheet["name"] for sheet in competence.workbook_sheets]},
    )
    messages.success(request, "Arquivo validado. Confira a aba, o cabeçalho e o mapeamento antes de importar.")
    if _wants_json(request):
        return JsonResponse({"ok": True, "redirect_url": reverse("relatorios:import-review", args=(competence.pk,))})
    return redirect("relatorios:import-review", pk=competence.pk)


def _saved_workbook(competence):
    if not competence.original_file or not competence.original_file_sha256:
        raise Http404
    try:
        with competence.original_file.open("rb") as original:
            data = original.read()
    except (OSError, ValueError):
        raise Http404 from None
    if not data:
        raise Http404
    return data


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
@require_http_methods(["GET", "POST"])
def competence_import_review(request, pk):
    competence = get_object_or_404(_competences(), pk=pk)
    if competence.imported_at or competence.source_rows.exists():
        messages.info(request, "Este arquivo já foi importado para esta competência.")
        return redirect("relatorios:workspace", pk=competence.pk)
    data = _saved_workbook(competence)
    extension = competence.original_file_extension

    if request.method == "POST":
        selected_sheet = request.POST.get("sheet_name", "")
        header_row_value = request.POST.get("header_row", "")
        try:
            inspection = inspect_workbook(data, extension, selected_sheet=selected_sheet, header_row=header_row_value)
        except WorkbookImportError as exc:
            messages.error(request, str(exc))
            return redirect("relatorios:import-review", pk=competence.pk)
    else:
        selected_sheet = request.GET.get("sheet_name") or None
        header_row_value = request.GET.get("header_row") or None
        try:
            inspection = inspect_workbook(data, extension, selected_sheet=selected_sheet, header_row=header_row_value)
        except WorkbookImportError as exc:
            messages.error(request, str(exc))
            return redirect("relatorios:workspace", pk=competence.pk)

    selected_sheet = inspection["selected_sheet"] or ""
    selected_header = inspection["header_row"] or 1
    form = WorkbookSelectionForm(
        request.POST or None,
        sheets=inspection["sheets"],
        headers=inspection["headers"],
        selected_sheet=selected_sheet,
        header_row=selected_header,
        initial_mapping=inspection["mapping"],
    )
    if request.method == "POST" and form.is_valid():
        if request.POST.get("intent") == "preview":
            return render(request, "relatorios/claro/import_review.html", _import_review_context(competence, inspection, form))
        try:
            result = confirm_competence_import(
                competence_id=competence.pk,
                data=data,
                extension=extension,
                sheet_name=form.cleaned_data["sheet_name"],
                header_row=form.cleaned_data["header_row"],
                mapping=form.cleaned_data["column_mapping"],
                physical_row_count=inspection["source_row_count"],
                user=request.user,
            )
        except ImportAlreadyCompleted:
            messages.info(request, "Este arquivo já foi importado para esta competência.")
            return redirect("relatorios:workspace", pk=competence.pk)
        except WorkbookImportError as exc:
            competence.refresh_from_db()
            competence.import_error = str(exc)[:500]
            competence.save(update_fields=("import_error", "updated_at"))
            _audit(request, action="IMPORT_VALIDATION_FAILED", description="A confirmação da importação foi recusada pela validação.", competence=competence,
                   metadata={"error": str(exc)[:250]})
            messages.error(request, str(exc))
        except Exception:
            competence.refresh_from_db()
            competence.status = PenaltyCompetence.Status.ERROR
            competence.import_error = "Falha segura durante a importação. Nenhuma linha parcial foi mantida."
            competence.save(update_fields=("status", "import_error", "updated_at"))
            _audit(request, action="IMPORT_FAILED", description="A importação falhou e a transação foi revertida.", competence=competence,
                   metadata={"status": "ERROR"})
            messages.error(request, "A importação não foi concluída. Nenhuma linha parcial foi mantida; tente novamente ou revise o arquivo.")
        else:
            competence.refresh_from_db()
            _audit(
                request,
                action="SOURCE_TAB_SELECTED",
                description="Aba e cabeçalho do arquivo Original confirmados.",
                competence=competence,
                metadata={"sheet": result["sheet_name"], "header_row": result["header_row"]},
            )
            _audit(
                request,
                action="IMPORT_CONFIRMED",
                description=f"Importação confirmada: {result['imported_rows']} registros persistidos.",
                competence=competence,
                metadata={"imported_rows": result["imported_rows"], "physical_rows": result["source_total_rows"],
                          "sha256": competence.original_file_sha256},
            )
            try:
                enrichment = enrich_competence(competence.pk, user=request.user, eligible_only=False)
            except Exception:
                logger.exception("Local OTRS/CMDB enrichment failed after importing competence %s.", competence.pk)
                messages.warning(request, "Arquivo importado. Não foi possível consultar os mirrors locais agora; atualize as evidências pela planilha.")
            else:
                _audit(
                    request,
                    action="TREATMENT_AUTO_ENRICHED",
                    description="Tickets principais e evidências foram associados a partir dos mirrors locais após a importação.",
                    competence=competence,
                    metadata={key: enrichment[key] for key in ("processed", "found", "not_found", "ambiguous", "ticket_ambiguous", "isp_found")},
                )
            messages.success(request, f"Importação concluída: {result['imported_rows']} registros disponíveis no Original.")
            return redirect("relatorios:workspace", pk=competence.pk)

    selected_info = next((sheet for sheet in inspection["sheets"] if sheet["name"] == inspection["selected_sheet"]), None)
    context = _import_review_context(competence, inspection, form)
    context["header_row_choices"] = range(1, min(max(selected_info["row_count"] if selected_info else 1, 1), 30) + 1)
    return render(request, "relatorios/claro/import_review.html", context)


def _import_review_context(competence, inspection, form):
    selected_info = next((sheet for sheet in inspection["sheets"] if sheet["name"] == inspection["selected_sheet"]), None)
    header_limit = min(max(selected_info["row_count"] if selected_info else 1, 1), 30)
    return {
        "active_section": "claro",
        "competence": competence,
        "inspection": inspection,
        "form": form,
        "field_labels": FIELD_LABELS,
        "selected_sheet_info": selected_info,
        "header_row_choices": range(1, header_limit + 1),
    }


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
def original_download(request, pk):
    competence = get_object_or_404(_competences(include_archived=True), pk=pk)
    if competence.deleted_at and not request.user.has_perm("relatorios.delete_finalized_penaltycompetence"):
        raise Http404
    if not competence.original_file:
        raise Http404
    try:
        original = competence.original_file.open("rb")
    except (OSError, ValueError):
        raise Http404 from None
    response = FileResponse(original, as_attachment=True, filename=Path(competence.original_filename or "original.xlsx").name,
                            content_type="application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
def competence_workspace(request, pk):
    competence = get_object_or_404(
        _competences(include_archived=True).select_related("created_by", "updated_by", "finalized_by", "deleted_by"),
        pk=pk,
    )
    if competence.deleted_at and not request.user.has_perm("relatorios.delete_finalized_penaltycompetence"):
        raise Http404
    tab = request.GET.get("aba", "original")
    if tab not in WORKSPACE_TABS:
        tab = "original"
    history_queryset = AuditEvent.objects.filter(
        module="relatorios", object_type=competence.__class__.__name__, object_id=str(competence.pk),
    ).select_related("user").order_by("-timestamp")
    history_user = request.GET.get("history_user", "").strip()[:80]
    history_type = request.GET.get("history_type", "").strip()[:64]
    history_from = request.GET.get("history_from", "").strip()[:10]
    history_to = request.GET.get("history_to", "").strip()[:10]
    history_rec = request.GET.get("history_rec", "").strip()[:80]
    history_from = parse_date(history_from).isoformat() if parse_date(history_from) else ""
    history_to = parse_date(history_to).isoformat() if parse_date(history_to) else ""
    if history_user.isdigit():
        history_queryset = history_queryset.filter(user_id=int(history_user))
    if history_type:
        history_queryset = history_queryset.filter(action=history_type)
    if history_from:
        history_queryset = history_queryset.filter(timestamp__date__gte=history_from)
    if history_to:
        history_queryset = history_queryset.filter(timestamp__date__lte=history_to)
    audit_events = list(history_queryset[:500])
    audit_row_ids = {
        str((event.metadata or {}).get("treatment_row_id"))
        for event in audit_events if (event.metadata or {}).get("treatment_row_id")
    }
    row_rec_map = dict(PenaltyTreatmentRow.objects.filter(pk__in=audit_row_ids).values_list("pk", "source_row__canonical_data__rec")) if audit_row_ids else {}
    financial_changes = PenaltyFinancialChange.objects.filter(treatment__source_row__competence=competence).select_related(
        "actor", "treatment__source_row",
    ).order_by("-created_at")
    if history_user.isdigit():
        financial_changes = financial_changes.filter(actor_id=int(history_user))
    if history_type:
        financial_changes = financial_changes.filter(source=history_type)
    if history_from:
        financial_changes = financial_changes.filter(created_at__date__gte=history_from)
    if history_to:
        financial_changes = financial_changes.filter(created_at__date__lte=history_to)
    if history_rec:
        financial_changes = financial_changes.filter(treatment__source_row__canonical_data__rec__icontains=history_rec)
        audit_events = [event for event in audit_events if history_rec.casefold() in str(row_rec_map.get((event.metadata or {}).get("treatment_row_id"), "")).casefold()]
    if history_type:
        audit_events = [event for event in audit_events if event.action == history_type]
    history = [
        {"action": event.action, "description": event.description or event.action, "user": event.user,
         "timestamp": event.timestamp, "result": event.result,
         "rec": row_rec_map.get((event.metadata or {}).get("treatment_row_id"), "")}
        for event in audit_events
    ]
    for change in financial_changes[:500]:
        rec = (change.treatment.source_row.canonical_data or {}).get("rec", "")
        history.append({
            "action": change.source, "description": "Valor efetivo ARION alterado na tratativa",
            "user": change.actor, "timestamp": change.created_at,
            "result": f"{change.previous_total if change.previous_total is not None else '—'} → {change.new_total if change.new_total is not None else '—'}",
            "rec": rec,
        })
    history.sort(key=lambda item: item["timestamp"], reverse=True)
    history = history[:250]

    original_rows = PenaltySourceRow.objects.filter(competence=competence)
    query = request.GET.get("q", "").strip()[:100]
    if query:
        match = Q()
        for field in FIELD_LABELS:
            match |= Q(**{f"canonical_data__{field}__icontains": query})
        original_rows = original_rows.filter(match)
    page_size_text = request.GET.get("page_size", "25")
    page_size = int(page_size_text) if page_size_text.isdigit() and int(page_size_text) in PAGE_SIZE_CHOICES else 25
    original_page = Paginator(original_rows.select_related("competence"), page_size).get_page(request.GET.get("page"))
    all_treatment_rows = PenaltyTreatmentRow.objects.filter(source_row__competence=competence).select_related("source_row", "ticket")
    treatment_rows = treatment_queryset(competence, request.GET)
    treatment_page = Paginator(treatment_rows, page_size).get_page(request.GET.get("treatment_page"))
    from aplicativos.chamados.services import otrs_ticket_url
    for treatment in treatment_page.object_list:
        snapshot = (treatment.arion_values or {}).get("ticket_snapshot") or {}
        treatment.grid_ticket_number = treatment.ticket.ticket_number if treatment.ticket_id else snapshot.get("number", "")
        external_id = treatment.ticket.external_id if treatment.ticket_id else snapshot.get("external_id")
        treatment.otrs_url = otrs_ticket_url(external_id) if external_id else ""
    eligible_rows = claro_treatment_candidates(all_treatment_rows)
    totals = eligible_rows.aggregate(
        total_claro=Sum("total_claro"), total_arion=Sum("total_arion"),
        amount_due=Sum("amount_due"), amount_charged=Sum("amount_charged"),
        difference=Sum("difference"), not_charged=Sum("amount_not_charged"), contested=Sum("amount_contested"),
    )
    summary = competence_financial_summary(competence)
    pending_count = summary["pending"]
    calculated_count = eligible_rows.filter(calculation_state="CALCULATED").count()
    claro_total_count = eligible_rows.filter(total_claro__isnull=False).count()
    decision_breakdown = [
        {"value": value, "label": label, "count": eligible_rows.filter(decision=value).count()}
        for value, label in PenaltyTreatmentRow.Decision.choices
    ]
    raw_isp_breakdown = list(
        eligible_rows.exclude(isp="").values("isp").annotate(count=Count("pk"), contested=Sum("amount_contested"))
        .order_by("-count", "isp")
    )
    isp_groups = {}
    for item in raw_isp_breakdown:
        label = "ISP não identificado" if item["isp"].isdecimal() else item["isp"]
        group = isp_groups.setdefault(label, {"isp": label, "count": 0, "contested": None})
        group["count"] += item["count"]
        if item["contested"] is not None:
            group["contested"] = (group["contested"] or Decimal("0")) + item["contested"]
    isp_breakdown = sorted(isp_groups.values(), key=lambda item: (-item["count"], item["isp"]))[:8]
    root_cause_breakdown = list(
        eligible_rows.exclude(root_cause="").values("root_cause").annotate(count=Count("pk"))
        .order_by("-count", "root_cause")[:8]
    )
    state_breakdown = list(
        eligible_rows.exclude(source_row__canonical_data__uf="").values("source_row__canonical_data__uf")
        .annotate(count=Count("pk")).order_by("-count")[:8]
    )
    top_contested = list(
        eligible_rows.filter(amount_contested__gt=0).select_related("source_row")
        .order_by("-amount_contested", "source_row__source_row_number")[:10]
    )
    history_users = list(AuditEvent.objects.filter(
        module="relatorios", object_type=competence.__class__.__name__, object_id=str(competence.pk), user__isnull=False,
    ).values_list("user_id", "user__username").distinct().order_by("user__username"))
    history_users.extend(PenaltyFinancialChange.objects.filter(treatment__source_row__competence=competence, actor__isnull=False)
                         .values_list("actor_id", "actor__username").distinct().order_by("actor__username"))
    history_users = sorted(set(history_users), key=lambda item: item[1].casefold())
    history_actions = list(AuditEvent.objects.filter(
        module="relatorios", object_type=competence.__class__.__name__, object_id=str(competence.pk),
    ).values_list("action", flat=True).distinct().order_by("action"))
    history_actions.extend(PenaltyFinancialChange.objects.filter(treatment__source_row__competence=competence)
                           .values_list("source", flat=True).distinct().order_by("source"))
    history_actions = sorted(set(history_actions))
    treatment_query_params = request.GET.copy()
    for key in ("aba", "page", "treatment_page", "page_size"):
        treatment_query_params.pop(key, None)
    return render(request, "relatorios/claro/workspace.html", {
        "active_section": "claro",
        "competence": competence,
        "active_tab": tab,
        "history": history,
        "original_rows": original_page,
        "original_search": query,
        "page_size": page_size,
        "page_size_choices": PAGE_SIZE_CHOICES,
        "treatment_rows": treatment_page,
        "source_headers": competence.source_headers,
        "source_rec_column": next((int(index) for index, field in (competence.column_mapping or {}).items() if str(index).isdigit() and field == "rec"), None),
        "source_designation_column": next((int(index) for index, field in (competence.column_mapping or {}).items() if str(index).isdigit() and field == "designation"), None),
        "treatment_totals": totals,
        "treatment_pending_count": pending_count,
        "treatment_calculated_count": calculated_count,
        "claro_total_count": claro_total_count,
        "financial_summary": summary,
        "treatment_filters": request.GET,
        "treatment_decisions": PenaltyTreatmentRow.Decision.choices,
        "treatment_statuses": PenaltyTreatmentRow.Status.choices,
        "treatment_query": treatment_query_params.urlencode(),
        "decision_breakdown": decision_breakdown,
        "isp_breakdown": isp_breakdown,
        "root_cause_breakdown": root_cause_breakdown,
        "state_breakdown": state_breakdown,
        "top_contested": top_contested,
        "history_users": history_users,
        "history_actions": history_actions,
        "history_filters": {"user": history_user, "type": history_type, "from": history_from, "to": history_to, "rec": history_rec},
        "export_scopes": EXPORT_SCOPES,
        "treatment_columns": TREATMENT_COLUMNS,
        "can_edit_treatments": request.user.has_perm("relatorios.edit_treatments") and competence.status != PenaltyCompetence.Status.FINALIZED,
        "can_export_treatments": request.user.has_perm("relatorios.export_treatments"),
        "competence_archived": bool(competence.deleted_at),
        "can_delete_competence": (
            request.user.has_perm("relatorios.delete_finalized_penaltycompetence")
            if competence.status == PenaltyCompetence.Status.FINALIZED
            else request.user.has_perm("relatorios.delete_penaltycompetence")
        ) and not competence.deleted_at,
        "delete_confirmation_text": f"EXCLUIR {competence.period_label.upper()}" if competence.status == PenaltyCompetence.Status.FINALIZED else "EXCLUIR",
    })


def _treatment(pk, *, user=None):
    treatment = get_object_or_404(PenaltyTreatmentRow.objects.select_related("source_row__competence"), pk=pk)
    competence = treatment.source_row.competence
    if competence.deleted_at and (user is None or not user.has_perm("relatorios.delete_finalized_penaltycompetence")):
        raise Http404
    return treatment


def _display_currency(value):
    if value is None:
        return "—"
    return "R$ " + f"{Decimal(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _display_decimal(value):
    if value is None:
        return "—"
    rounded = Decimal(value).quantize(Decimal("0.01"))
    return format(rounded.normalize(), "f").replace(".", ",")


def _display_datetime(value):
    if isinstance(value, str):
        value = parse_datetime(value)
    if value and timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_default_timezone())
    return timezone.localtime(value).strftime("%d/%m/%Y %H:%M") if value else "—"


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
@require_http_methods(["GET"])
def treatment_detail_data(request, pk):
    treatment = get_object_or_404(
        PenaltyTreatmentRow.objects.select_related("source_row__competence", "ticket")
        .prefetch_related(
            "pauses__created_by", "overrides__created_by", "financial_changes__actor",
            "ticket_relations__otrs_ticket__config_items__cmdb_link__operator",
        ), pk=pk,
    )
    competence = treatment.source_row.competence
    if competence.deleted_at and not request.user.has_perm("relatorios.delete_finalized_penaltycompetence"):
        raise Http404
    source = treatment.source_row.canonical_data or {}
    isp_sources_saved = (treatment.arion_values or {}).get("cmdb_isp_sources", [])
    isp_label = treatment.isp if treatment.isp and not treatment.isp.isdecimal() else "ISP não identificado"
    resolved_isp_source = next((item for item in isp_sources_saved if item.get("name") == treatment.isp), {})
    isp_internal_id = (
        resolved_isp_source.get("id")
        or (treatment.arion_values or {}).get("cmdb_isp_unresolved_id", "")
        or (treatment.isp if treatment.isp.isdecimal() else "")
    )
    from aplicativos.chamados.services import otrs_ticket_url

    ticket_relations = list(treatment.ticket_relations.all())
    if not ticket_relations and treatment.ticket_id:
        ticket_relations = [None]
    tickets = []
    for relation in ticket_relations:
        ticket = relation.otrs_ticket if relation else treatment.ticket
        if not ticket:
            continue
        config_items = []
        isps = set()
        for linked_config in ticket.config_items.all():
            link = linked_config.cmdb_link
            if linked_config.is_present_in_source and link and link.is_present_in_source:
                isp_identity = cmdb_isp_identity(link)
                if isp_identity["name"]:
                    isps.add(isp_identity["name"])
                config_items.append({
                    "id": link.source_config_item_id,
                    "name": link.name or link.source_number or link.source_config_item_id,
                    "isp": isp_identity["name"] or "ISP não identificado",
                    "isp_internal_id": isp_identity["id"],
                    "isp_full_name": isp_identity["full_name"],
                    "isp_provider_number": isp_identity["number"],
                    "isp_confidence": isp_identity["confidence"],
                    "relation_type": linked_config.relation_type,
                    "state": linked_config.relation_state,
                })
        tickets.append({
            "relation_id": relation.pk if relation else None,
            "id": ticket.pk, "number": ticket.ticket_number,
            "url": otrs_ticket_url(ticket.external_id),
            "title": ticket.title, "queue": ticket.queue_name,
            "status": ticket.operational_status_label,
            "source_state": ticket.external_state_name,
            "responsible": ticket.responsible_user_id,
            "opened_at": _display_datetime(ticket.source_created_at),
            "resolved_at": _display_datetime(ticket.resolved_at),
            "closing_code": ticket.closing_code_raw,
            "responsibility": ticket.responsibility_normalized,
            "is_primary": relation.is_primary if relation else True,
            "source": relation.get_source_display() if relation else "Vínculo anterior",
            "source_code": relation.source if relation else PenaltyTreatmentTicket.Source.LEGACY,
            "config_items": config_items, "isps": sorted(isps),
        })
    ticket_snapshot = (treatment.arion_values or {}).get("ticket_snapshot") or {}
    if ticket_snapshot.get("number") and not any(item["number"] == ticket_snapshot["number"] for item in tickets):
        tickets.insert(0, {
            "relation_id": None, "id": ticket_snapshot.get("external_id"),
            "number": ticket_snapshot["number"],
            "url": otrs_ticket_url(ticket_snapshot.get("external_id")) if ticket_snapshot.get("external_id") else "",
            "title": ticket_snapshot.get("title", ""), "queue": ticket_snapshot.get("queue", ""),
            "status": business_status_label(ticket_snapshot.get("business_status") or ticket_snapshot.get("state", "")),
            "source_state": ticket_snapshot.get("source_state") or ticket_snapshot.get("state", ""),
            "responsible": ticket_snapshot.get("responsible"),
            "opened_at": _display_datetime(ticket_snapshot.get("created_at")),
            "resolved_at": _display_datetime(ticket_snapshot.get("resolved_at")),
            "closing_code": ticket_snapshot.get("closing_code", ""),
            "responsibility": "INDETERMINADO", "is_primary": True,
            "source": "OTRS geral" if ticket_snapshot.get("source") == "OTRS_GENERAL" else "Mirror local",
            "source_code": ticket_snapshot.get("source", "OTRS_GENERAL"),
            "config_items": [{"id": value, "name": value, "isp": "", "relation_type": "", "state": ""}
                             for value in ticket_snapshot.get("ci", [])],
            "isps": ticket_snapshot.get("isps", []),
        })
    match_info = (treatment.arion_values or {}).get("ticket_match", {})
    stored_candidates = (treatment.arion_values or {}).get("ticket_candidates", [])
    candidate_numbers = [item.get("number") for item in stored_candidates if item.get("number")]
    candidate_tickets = {
        ticket.ticket_number: ticket
        for ticket in OTRSTicket.objects.filter(ticket_number__in=candidate_numbers)
    }
    ticket_candidates = []
    for candidate in stored_candidates:
        candidate_ticket = candidate_tickets.get(candidate.get("number"))
        if candidate_ticket:
            ticket_candidates.append({
                **candidate,
                "status_label": candidate_ticket.operational_status_label,
                "queue": candidate_ticket.queue_name,
                "url": otrs_ticket_url(candidate_ticket.external_id),
            })
        elif candidate.get("source") == "OTRS_GENERAL":
            ticket_candidates.append({
                **candidate,
                "status_label": business_status_label(candidate.get("status", "Estado não informado")),
                "source_state": candidate.get("source_state", ""),
                "url": otrs_ticket_url(candidate.get("external_id")) if candidate.get("external_id") else "",
            })
    overrides = list(treatment.overrides.filter(removed_at__isnull=True))
    override_by_field = {item.field: item for item in overrides}
    isp_origin_code = (treatment.field_origins or {}).get("isp", "")
    isp_origin_label = dict(PenaltyTreatmentRow.Origin.choices).get(isp_origin_code, "Manual / não enriquecido")
    effective = effective_values(treatment)
    calculation_data = {}
    formula_labels = {
        "tmp": "Base calculada: FETEC OTRS − Abertura OTRS. Efetivo: intervalo das datas ARION efetivas menos pausas.",
        "ev": "EV calculado sobre o TMP bruto da base; EV efetivo usa o TMP efetivo conforme a regra da competência.",
        "inter": "Inter ARION = (3 × Valor mensal Claro × EV efetivo) ÷ 1440",
        "unavailability": "Indisp calculada: faixa SLA aplicada ao TMP bruto. Indisp efetiva: faixa SLA aplicada ao TMP efetivo.",
        "total": "Total ARION = Inter efetivo + Indisp efetivo + Penal efetivo + valor não cobrado",
    }
    calc_fields = {
        "tmp": (treatment.tmp_gross_minutes, effective.get("tmp"), PenaltyTreatmentOverride.Field.TMP),
        "ev": (treatment.ev_calculated, effective.get("ev"), PenaltyTreatmentOverride.Field.EV),
        "inter": (treatment.inter_calculated, treatment.inter_effective, PenaltyTreatmentOverride.Field.INTER),
        "unavailability": (treatment.unavailability_calculated, treatment.unavailability_effective, PenaltyTreatmentOverride.Field.UNAVAILABILITY),
        "total": (treatment.total_calculated_arion, treatment.total_arion, None),
    }
    for name, (calculated, effective_value, override_field) in calc_fields.items():
        override = override_by_field.get(override_field) if override_field else None
        calculation_data[name] = {
            "formula": formula_labels[name],
            "calculated": str(calculated) if calculated is not None else None,
            "effective": str(effective_value) if effective_value is not None else None,
            "origin": "Override manual" if override else ("Calculado" if calculated is not None else "Não calculado"),
            "reason": override.justification if override else "",
        }

    filtered_rows = treatment_queryset(competence, request.GET)
    source_number = treatment.source_row.source_row_number
    filtered_count = filtered_rows.count()
    position = filtered_rows.filter(source_row__source_row_number__lt=source_number).count() + 1
    previous_id = filtered_rows.filter(source_row__source_row_number__lt=source_number).order_by("-source_row__source_row_number").values_list("pk", flat=True).first()
    next_id = filtered_rows.filter(source_row__source_row_number__gt=source_number).values_list("pk", flat=True).first()
    next_pending = _next_pending_treatment(filtered_rows, source_number, exclude_pk=treatment.pk)
    next_pending_id = next_pending.pk if next_pending else None
    claro_fields = (
        "rec", "designation", "client", "uf", "lp", "provider", "contract", "bandwidth",
        "opening", "fetec", "duration", "events", "monthly_value", "interconnection_fine",
        "penalty_fine", "unavailability_fine", "hours_quantity",
    )
    if treatment.ticket_id:
        grid_ticket_text, grid_ticket_state = treatment.ticket.ticket_number, "found"
    elif ticket_snapshot.get("number"):
        grid_ticket_text, grid_ticket_state = ticket_snapshot["number"], "found"
    elif ticket_candidates:
        grid_ticket_text, grid_ticket_state = f"{len(ticket_candidates)} candidatos", "ambiguous"
    elif treatment.evidence_status == PenaltyTreatmentRow.EvidenceStatus.NOT_CHECKED:
        grid_ticket_text, grid_ticket_state = "Não verificado", "not_checked"
    else:
        grid_ticket_text, grid_ticket_state = "Sem evidência", "not_found"
    response = JsonResponse({
        "id": treatment.pk,
        "version": treatment.version,
        "rec": source.get("rec", "—"),
        "designation": source.get("designation", "—"),
        "client": source.get("client", "—"),
        "claro": {key: source.get(key) for key in claro_fields},
        "arion": {
            "opening": _display_datetime(treatment.arion_opening_at),
            "opening_input": timezone.localtime(treatment.arion_opening_at).strftime("%Y-%m-%dT%H:%M") if treatment.arion_opening_at else "",
            "opening_auto": _display_datetime(treatment.arion_opening_auto_at),
            "fetec": _display_datetime(treatment.arion_fetec_at),
            "fetec_input": timezone.localtime(treatment.arion_fetec_at).strftime("%Y-%m-%dT%H:%M") if treatment.arion_fetec_at else "",
            "fetec_auto": _display_datetime(treatment.arion_fetec_auto_at),
            "isp": isp_label,
            "isp_internal_id": isp_internal_id,
            "isp_config_item_id": resolved_isp_source.get("id", ""),
            "isp_provider_number": resolved_isp_source.get("number", ""),
            "isp_full_name": resolved_isp_source.get("full_name", ""),
            "isp_confidence": resolved_isp_source.get("confidence", ""),
            "root_cause": treatment.root_cause or "—",
            "observation": treatment.observation or "—", "decision": treatment.get_decision_display(),
            "decision_code": treatment.decision, "decision_reason": treatment.decision_reason,
            "status_code": treatment.status, "status": treatment.get_status_display(),
            "treatment_justification": treatment.treatment_justification,
            "amount_not_charged": str(treatment.amount_not_charged or 0),
            "amount_not_charged_justification": treatment.amount_not_charged_justification,
            "procedence": treatment.get_procedence_display(),
            "procedence_code": treatment.procedence,
            "procedence_origin": treatment.get_procedence_origin_display() or "Não informada",
            "inter": _display_currency(treatment.inter_effective),
            "unavailability": _display_currency(treatment.unavailability_effective),
            "total": _display_currency(treatment.total_arion), "contested": _display_currency(treatment.amount_contested),
        },
        "origins": {
            "opening": treatment.get_arion_opening_origin_display(),
            "fetec": treatment.get_arion_fetec_origin_display(),
            "isp": isp_origin_label,
            "root_cause": dict(PenaltyTreatmentRow.Origin.choices).get(
                (treatment.field_origins or {}).get("root_cause", ""), "Manual",
            ),
            "procedence": treatment.get_procedence_origin_display() or "Não informada",
        },
        "evidence": {
            "status": treatment.get_evidence_status_display(), "found": bool(tickets),
            "status_code": treatment.evidence_status,
            "ticket_identifier": canonical_ticket_number(
                source.get("pu_fornecedor_ticket") or source.get("provider_unit_price")
            ) or treatment.ticket_identifier or "",
            "pu_received": str(source.get("pu_fornecedor_ticket") or source.get("provider_unit_price") or ""),
            "lookup_error": (treatment.arion_values or {}).get("ticket_lookup_error", ""),
            "ticket": treatment.ticket.ticket_number if treatment.ticket else ticket_snapshot.get("number") or treatment.ticket_identifier or "—",
            "title": treatment.ticket.title if treatment.ticket else ticket_snapshot.get("title", ""),
            "queue": treatment.ticket.queue_name if treatment.ticket else ticket_snapshot.get("queue", ""),
            "state": treatment.ticket.operational_status_label if treatment.ticket else business_status_label(ticket_snapshot.get("business_status") or ticket_snapshot.get("state", "")),
            "source_state": treatment.ticket.external_state_name if treatment.ticket else ticket_snapshot.get("source_state") or ticket_snapshot.get("state", ""),
            "responsible": treatment.ticket.responsible_user_id if treatment.ticket else ticket_snapshot.get("responsible"),
            "tickets": tickets,
            "ticket_match": match_info,
            "ticket_candidates": ticket_candidates,
            "rec": source.get("rec", ""),
            "lp": source.get("lp", ""),
            "isp_candidates": (treatment.arion_values or {}).get("cmdb_isp_candidates", []),
            "isp_sources": (treatment.arion_values or {}).get("cmdb_isp_sources", []),
            "procedence_candidates": (treatment.arion_values or {}).get("otrs_procedence_candidates", []),
        },
        "calculations": calculation_data,
        "totals": {
            "claro": _display_currency(treatment.total_claro),
            "arion": _display_currency(treatment.total_arion),
            "contested": _display_currency(treatment.amount_contested),
            "reduction": f"{treatment.reduction_percent}%" if treatment.reduction_percent is not None else "—",
            "tmp_gross": treatment.tmp_gross_minutes,
            "pause_minutes": treatment.pause_minutes,
            "tmp_net": effective.get("tmp"),
            "tmp_effective": effective.get("tmp"),
        },
        "grid": {
            "isp": isp_label,
            "isp_title": ("Nome completo CMDB: " + resolved_isp_source.get("full_name", "")) if resolved_isp_source.get("full_name") else ("ID interno CMDB: " + isp_internal_id) if isp_internal_id else "",
            "opening": _display_datetime(treatment.arion_opening_at),
            "fetec": _display_datetime(treatment.arion_fetec_at),
            "pauses": f"{treatment.pause_minutes} min",
            "tmp": f"{effective.get('tmp')} min" if effective.get("tmp") is not None else "—",
            "tmp_title": f"Bruto: {treatment.tmp_gross_minutes} min · Pausas: {treatment.pause_minutes} min · Efetivo: {effective.get('tmp')} min" if treatment.tmp_gross_minutes is not None and effective.get("tmp") is not None else "",
            "ev": str(effective.get("ev")) if effective.get("ev") is not None else "—",
            "ev_title": "EV efetivo calculado a partir do TMP efetivo segundo a regra da competência.",
            "inter": _display_currency(treatment.inter_effective),
            "inter_title": "Valor calculado pelo ARION com base no EV efetivo e no valor mensal.",
            "indisp": _display_currency(treatment.unavailability_effective),
            "indisp_title": "Indisp. efetiva calculada pelo TMP efetivo segundo o perfil SLA da competência.",
            "cause": treatment.root_cause or "—",
            "observation": treatment.observation or "—",
            "procedence": treatment.get_procedence_display(),
            "total_claro": _display_currency(treatment.total_claro),
            "total_arion": _display_currency(treatment.total_arion),
            "contested": _display_currency(treatment.amount_contested),
            "reduction": f"{treatment.reduction_percent}%" if treatment.reduction_percent is not None else "—",
            "decision": treatment.get_decision_display(),
            "decision_code": treatment.decision,
            "status": treatment.get_status_display(),
            "ticket": grid_ticket_text,
            "ticket_url": otrs_ticket_url(treatment.ticket.external_id) if treatment.ticket_id else (otrs_ticket_url(ticket_snapshot.get("external_id")) if ticket_snapshot.get("external_id") else ""),
            "ticket_state": grid_ticket_state,
        },
        "form": {
            "version": treatment.version,
            "edit_url": reverse("relatorios:treatment-edit", args=(treatment.pk,)),
            "pause_url": reverse("relatorios:treatment-pause-add", args=(treatment.pk,)),
            "override_url": reverse("relatorios:treatment-override-add", args=(treatment.pk,)),
        },
        "validation_codes": {
            "first_configured": bool(treatment.validation_code_ciphertext),
            "second_configured": bool(treatment.validation_code_2_ciphertext),
        },
        "pauses": [{"duration": pause.duration_minutes, "kind": pause.get_kind_display(),
                    "reason": pause.reason, "evidence": pause.evidence,
                    "created_at": timezone.localtime(pause.created_at).strftime("%d/%m/%Y %H:%M"),
                    "user": pause.created_by.get_username() if pause.created_by else "Sistema"}
                   for pause in treatment.pauses.all()],
        "overrides": [{"field": override.get_field_display(), "calculated": str(override.calculated_value) if override.calculated_value is not None else None,
                       "effective": str(override.override_value),
                       "justification": override.justification,
                       "created_at": timezone.localtime(override.created_at).strftime("%d/%m/%Y %H:%M"),
                       "user": override.created_by.get_username() if override.created_by else "Sistema"}
                      for override in treatment.overrides.filter(removed_at__isnull=True)],
        "history": [{"source": change.source, "previous": str(change.previous_total) if change.previous_total is not None else None,
                     "new": str(change.new_total) if change.new_total is not None else None,
                     "created_at": timezone.localtime(change.created_at).strftime("%d/%m/%Y %H:%M"),
                     "user": change.actor.get_username() if change.actor else "Sistema"}
                    for change in treatment.financial_changes.all()[:20]],
        "navigation": {"position": position, "count": filtered_count, "previous_id": previous_id,
                       "next_id": next_id, "next_pending_id": next_pending_id},
        "permissions": {
            "can_edit": request.user.has_perm("relatorios.edit_treatments") and _mutable_treatment(treatment),
            "can_override": request.user.has_perm("relatorios.override_treatments") and _mutable_treatment(treatment),
        },
        "edit_url": reverse("relatorios:treatment-edit", args=(treatment.pk,)),
        "ticket_relation_url": reverse("relatorios:treatment-tickets", args=(treatment.pk,)),
        "enrich_url": reverse("relatorios:treatment-enrich", args=(treatment.pk,)),
    })
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@permission_required("relatorios.edit_treatments", raise_exception=True)
@require_POST
def treatment_ticket_relation(request, pk):
    treatment = _treatment(pk, user=request.user)
    competence = treatment.source_row.competence
    if not _mutable_treatment(treatment):
        return JsonResponse({"ok": False, "error": "A competência está finalizada."}, status=409)
    action = request.POST.get("action", "link")
    ticket = None
    relation = None
    with transaction.atomic():
        PenaltyCompetence.objects.select_for_update().get(pk=competence.pk)
        treatment = PenaltyTreatmentRow.objects.select_for_update().select_related("source_row__competence").get(pk=pk)
        if not _mutable_treatment(treatment):
            return JsonResponse({"ok": False, "error": "A competência está finalizada."}, status=409)
        if action == "link":
            ticket_number = request.POST.get("ticket_number", "").strip()[:80]
            if not ticket_number:
                return JsonResponse({"ok": False, "error": "Informe o número exato do ticket OTRS."}, status=400)
            ticket = OTRSTicket.objects.filter(ticket_number=ticket_number).first()
            if not ticket:
                return JsonResponse({"ok": False, "error": "Nenhum ticket com esse número exato está disponível no mirror local."}, status=404)
            is_pu_ticket = canonical_ticket_number(
                (treatment.source_row.canonical_data or {}).get("pu_fornecedor_ticket")
                or (treatment.source_row.canonical_data or {}).get("provider_unit_price")
            ) == ticket.ticket_number
            relation, created = PenaltyTreatmentTicket.objects.get_or_create(
                treatment=treatment, otrs_ticket=ticket,
                defaults={
                    "source": PenaltyTreatmentTicket.Source.MANUAL,
                    "created_by": request.user,
                    "is_primary": is_pu_ticket or not treatment.ticket_relations.filter(is_primary=True).exists(),
                },
            )
            if not created:
                return JsonResponse({"ok": False, "error": "Este ticket já está relacionado a esta linha."}, status=409)
            if is_pu_ticket:
                PenaltyTreatmentTicket.objects.filter(treatment=treatment).exclude(pk=relation.pk).update(is_primary=False)
            treatment.arion_values = dict(treatment.arion_values or {})
            treatment.arion_values.pop("ticket_snapshot", None)
            match_info = dict(treatment.arion_values.get("ticket_match", {}))
            match_info.update({"method": "MANUAL", "confidence": "USER_CONFIRMED",
                              "selected_ticket_number": ticket.ticket_number, "selection_source": "MANUAL",
                              "source": "LOCAL_MIRROR"})
            treatment.arion_values["ticket_match"] = match_info
            event = "TREATMENT_TICKET_LINKED"
            description = "Ticket OTRS do mirror local relacionado manualmente à linha."
        elif action == "candidate":
            ticket_number = request.POST.get("ticket_number", "").strip()[:80]
            treatment.arion_values = dict(treatment.arion_values or {})
            candidate_data = next((item for item in treatment.arion_values.get("ticket_candidates", [])
                                   if item.get("number") == ticket_number), None)
            if not ticket_number or not candidate_data:
                return JsonResponse({"ok": False, "error": "Selecione um candidato exibido para esta linha."}, status=400)
            ticket = OTRSTicket.objects.filter(ticket_number=ticket_number).first()
            if ticket:
                relation, _created = PenaltyTreatmentTicket.objects.get_or_create(
                    treatment=treatment, otrs_ticket=ticket,
                    defaults={"source": PenaltyTreatmentTicket.Source.MANUAL, "created_by": request.user},
                )
                PenaltyTreatmentTicket.objects.filter(treatment=treatment).exclude(pk=relation.pk).update(is_primary=False)
                relation.is_primary = True
                relation.source = PenaltyTreatmentTicket.Source.MANUAL
                relation.created_by = request.user
                relation.save(update_fields=("is_primary", "source", "created_by"))
                treatment.arion_values.pop("ticket_snapshot", None)
                selected_source = "LOCAL_MIRROR"
            elif candidate_data.get("source") == "OTRS_GENERAL":
                PenaltyTreatmentTicket.objects.filter(treatment=treatment, is_primary=True).update(is_primary=False)
                treatment.ticket = None
                treatment.arion_values["ticket_snapshot"] = {
                    "external_id": candidate_data.get("external_id"), "number": candidate_data["number"],
                    "title": candidate_data.get("title", ""), "queue": candidate_data.get("queue", ""),
                    "state": candidate_data.get("source_state") or candidate_data.get("status", ""),
                    "source_state": candidate_data.get("source_state") or candidate_data.get("status", ""),
                    "business_status": normalize_business_status(candidate_data.get("status", "")),
                    "created_at": candidate_data.get("created_at"), "resolved_at": candidate_data.get("resolved_at"),
                    "closed_at": candidate_data.get("closed_at"), "responsible": candidate_data.get("responsible"),
                    "ci": candidate_data.get("ci", []), "lp": candidate_data.get("lp", []),
                    "isps": candidate_data.get("isps", []), "source": "OTRS_GENERAL",
                    "retrieved_at": candidate_data.get("retrieved_at") or timezone.now().isoformat(),
                }
                selected_source = "OTRS_GENERAL"
            else:
                return JsonResponse({"ok": False, "error": "O candidato n?o est? mais dispon?vel na evid?ncia salva."}, status=404)
            selected_number = ticket.ticket_number if ticket else candidate_data["number"]
            match_info = dict(treatment.arion_values.get("ticket_match", {}))
            match_info.update({"method": "MANUAL", "selected_ticket_number": selected_number,
                              "selection_source": "MANUAL", "confidence": "USER_CONFIRMED",
                              "source": selected_source})
            treatment.arion_values["ticket_match"] = match_info
            event = "TREATMENT_TICKET_CANDIDATE_SELECTED"
            description = "Candidato OTRS do mirror local selecionado manualmente."
        elif action in {"primary", "unlink"}:
            try:
                relation = PenaltyTreatmentTicket.objects.select_for_update().select_related("otrs_ticket").get(
                    pk=int(request.POST.get("relation_id", "")), treatment=treatment,
                )
            except (ValueError, PenaltyTreatmentTicket.DoesNotExist):
                return JsonResponse({"ok": False, "error": "Vínculo de ticket inválido."}, status=404)
            ticket = relation.otrs_ticket
            if action == "primary":
                pu_ticket = canonical_ticket_number(
                    (treatment.source_row.canonical_data or {}).get("pu_fornecedor_ticket")
                    or (treatment.source_row.canonical_data or {}).get("provider_unit_price")
                )
                if pu_ticket and PenaltyTreatmentTicket.objects.filter(
                    treatment=treatment, source=PenaltyTreatmentTicket.Source.AUTOMATIC,
                    otrs_ticket__ticket_number=pu_ticket,
                ).exists():
                    return JsonResponse({"ok": False, "error": "O ticket de PU Fornecedor permanece como principal; vincule este ticket como relacionado."}, status=409)
                PenaltyTreatmentTicket.objects.filter(treatment=treatment, is_primary=True).update(is_primary=False)
                relation.is_primary = True
                relation.save(update_fields=("is_primary",))
                event = "TREATMENT_TICKET_PRIMARY_CHANGED"
                description = "Ticket principal da linha alterado."
            else:
                if relation.source == PenaltyTreatmentTicket.Source.AUTOMATIC:
                    return JsonResponse({"ok": False, "error": "O ticket identificado por PU Fornecedor é a referência principal e não pode ser removido."}, status=409)
                relation.delete()
                next_primary = treatment.ticket_relations.order_by("created_at", "pk").first()
                if next_primary:
                    next_primary.is_primary = True
                    next_primary.save(update_fields=("is_primary",))
                    ticket = next_primary.otrs_ticket
                else:
                    ticket = None
                event = "TREATMENT_TICKET_UNLINKED"
                description = "Vínculo de ticket removido da linha."
        else:
            return JsonResponse({"ok": False, "error": "Ação de ticket inválida."}, status=400)

        primary_relation = treatment.ticket_relations.filter(is_primary=True).select_related("otrs_ticket").first()
        if action != "candidate" or ticket:
            treatment.ticket = primary_relation.otrs_ticket if primary_relation else None
        if action == "candidate":
            treatment.evidence_status = PenaltyTreatmentRow.EvidenceStatus.FOUND
        treatment.version += 1
        treatment.save(update_fields=("ticket", "evidence_status", "arion_values", "version", "updated_at"))
        enrich_competence(
            competence.pk, user=request.user, treatment_ids=[treatment.pk],
            include_direct=False, eligible_only=False,
        )
        _audit(request, action=event, description=description, competence=competence,
               metadata={"treatment_row_id": treatment.pk, "ticket_id": ticket.pk if ticket else None,
                         "ticket_number": ticket.ticket_number if ticket else (selected_number if action == "candidate" else None),
                         "relation_source": "MANUAL"})
    return JsonResponse({"ok": True, "action": action})


@login_required
@permission_required("relatorios.edit_treatments", raise_exception=True)
@require_POST
def treatment_enrich(request, pk):
    treatment = _treatment(pk, user=request.user)
    competence = treatment.source_row.competence
    if not _mutable_treatment(treatment):
        return JsonResponse({"ok": False, "error": "A competência está finalizada."}, status=409)
    try:
        with transaction.atomic():
            result = enrich_competence(
                competence.pk, user=request.user, treatment_ids=[treatment.pk],
                include_direct=True, eligible_only=False, remote_lookup=True,
            )
            _audit(request, action="TREATMENT_LINE_ENRICHED", description="Evidências da linha atualizadas a partir do mirror local e, quando necessário, do OTRS geral somente leitura.", competence=competence,
                   metadata={"treatment_row_id": treatment.pk, **result})
    except Exception:
        logger.exception("Local OTRS/CMDB enrichment failed for treatment row %s.", treatment.pk)
        return JsonResponse({"ok": False, "error": "Não foi possível atualizar as evidências da linha. A Tratativa continua disponível manualmente."}, status=500)
    return JsonResponse({"ok": True, **result, "error": result.get("remote_message", "")})


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
@permission_required("relatorios.edit_treatments", raise_exception=True)
@require_POST
def treatment_inline_edit(request, pk):
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return JsonResponse({"ok": False, "error": "Requisição inválida."}, status=400)
    allowed_fields = {"arion_opening_at", "arion_fetec_at", "isp", "root_cause", "observation", "decision", "procedence"}
    field = request.POST.get("field", "")
    if field not in allowed_fields:
        return JsonResponse({"ok": False, "error": "Campo não editável."}, status=400)

    with transaction.atomic():
        treatment = _treatment(pk, user=request.user)
        competence = treatment.source_row.competence
        PenaltyCompetence.objects.select_for_update().get(pk=competence.pk)
        treatment = PenaltyTreatmentRow.objects.select_for_update().select_related("source_row__competence").get(pk=pk)
        if not _mutable_treatment(treatment):
            return JsonResponse({"ok": False, "error": "A competência está finalizada."}, status=409)
        try:
            expected_version = int(request.POST.get("version", ""))
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Versão da linha inválida."}, status=400)
        if treatment.version != expected_version:
            return JsonResponse({"ok": False, "error": "Esta linha mudou. Atualize a planilha antes de salvar."}, status=409)

        def local_value(value):
            return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M") if value else ""

        form_data = {
            "version": str(treatment.version),
            "arion_opening_at": local_value(treatment.arion_opening_at),
            "arion_fetec_at": local_value(treatment.arion_fetec_at),
            "isp": treatment.isp, "root_cause": treatment.root_cause, "observation": treatment.observation,
            "amount_not_charged": str(treatment.amount_not_charged or 0),
            "amount_not_charged_justification": treatment.amount_not_charged_justification,
            "status": treatment.status, "treatment_justification": treatment.treatment_justification,
            "decision": treatment.decision, "decision_reason": treatment.decision_reason, "procedence": treatment.procedence,
        }
        original_values = {
            name: getattr(treatment, name)
            for name in allowed_fields | {"decision_reason", "treatment_justification"}
        }
        form_data[field] = request.POST.get("value", "")
        reason = request.POST.get("reason", "").strip()[:500]
        if field == "decision" and reason:
            form_data["decision_reason"] = reason
        elif field in {"arion_opening_at", "arion_fetec_at"} and reason:
            form_data["treatment_justification"] = reason
        form = TreatmentEditForm(form_data, instance=treatment)
        if not form.is_valid():
            errors = {key: [str(error) for error in values] for key, values in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errors, "error": next(iter(form.errors.values()))[0]}, status=422)

        previous_total = treatment.total_arion
        cleaned = form.cleaned_data
        changed_fields = []
        for name in allowed_fields | {"decision_reason", "treatment_justification"}:
            value = cleaned.get(name)
            if original_values[name] != value:
                setattr(treatment, name, value)
                changed_fields.append(name)
        if "arion_opening_at" in changed_fields:
            treatment.arion_opening_origin = PenaltyTreatmentRow.Origin.MANUAL
        if "arion_fetec_at" in changed_fields:
            treatment.arion_fetec_origin = PenaltyTreatmentRow.Origin.MANUAL
        if "isp" in changed_fields or "root_cause" in changed_fields:
            treatment.field_origins = dict(treatment.field_origins or {})
            if "isp" in changed_fields:
                treatment.field_origins["isp"] = PenaltyTreatmentRow.Origin.MANUAL
            if "root_cause" in changed_fields:
                treatment.field_origins["root_cause"] = PenaltyTreatmentRow.Origin.MANUAL
        if "procedence" in changed_fields:
            treatment.procedence_origin = PenaltyTreatmentRow.Origin.MANUAL
        treatment.version += 1
        treatment.save()
        calculate_treatment(treatment)
        try:
            validate_financial_decision(treatment)
        except ValidationError as exc:
            transaction.set_rollback(True)
            return JsonResponse({"ok": False, "error": exc.messages[0]}, status=422)
        record_financial_change(
            treatment=treatment, actor=request.user, previous_total=previous_total, new_total=treatment.total_arion,
            reason=reason or treatment.decision_reason or treatment.treatment_justification,
            source="INLINE_EDIT",
        )

    date_adjustments = {}
    for field, key, automatic_field, origin_field in (
        ("arion_opening_at", "opening", "arion_opening_auto_at", "arion_opening_origin"),
        ("arion_fetec_at", "fetec", "arion_fetec_auto_at", "arion_fetec_origin"),
    ):
        if field in changed_fields:
            date_adjustments[key] = {
                "automatic": _display_datetime(getattr(treatment, automatic_field)),
                "effective": _display_datetime(getattr(treatment, field)),
                "origin": getattr(treatment, origin_field),
            }
    _audit(request, action="TREATMENT_FIELDS_CHANGED", description="Campo da tratativa atualizado pela planilha.", competence=competence,
           metadata={"treatment_row_id": treatment.pk, "fields": changed_fields, "version": treatment.version,
                     "reason": reason if date_adjustments else "", "date_adjustments": date_adjustments})
    if "decision" in changed_fields:
        _audit(request, action="FINANCIAL_DECISION_CHANGED", description="Decisão financeira alterada pela planilha.", competence=competence,
               metadata={"treatment_row_id": treatment.pk, "decision": treatment.decision})
    treatment.refresh_from_db()
    return JsonResponse({
        "ok": True, "version": treatment.version, "field": field,
        "display": {
            "arion_opening_at": _display_datetime(treatment.arion_opening_at),
            "arion_fetec_at": _display_datetime(treatment.arion_fetec_at),
            "isp": treatment.isp if treatment.isp and not treatment.isp.isdecimal() else "ISP não identificado", "root_cause": treatment.root_cause or "—",
            "observation": treatment.observation or "—", "decision": treatment.get_decision_display(),
            "procedence": treatment.get_procedence_display(),
            "pause_minutes": f"{treatment.pause_minutes} min",
            "tmp": f"{treatment.tmp_effective_minutes} min" if treatment.tmp_effective_minutes is not None else "—",
            "ev": _display_decimal(treatment.ev_effective),
            "inter": _display_currency(treatment.inter_effective),
            "unavailability": _display_currency(treatment.unavailability_effective),
            "claro": _display_currency(treatment.total_claro), "arion": _display_currency(treatment.total_arion),
            "contested": _display_currency(treatment.amount_contested),
            "reduction": f"{treatment.reduction_percent}%" if treatment.reduction_percent is not None else "—",
            "origin": {
                "arion_opening_at": treatment.get_arion_opening_origin_display(),
                "arion_fetec_at": treatment.get_arion_fetec_origin_display(),
                "isp": (treatment.field_origins or {}).get("isp", "Manual"),
                "root_cause": (treatment.field_origins or {}).get("root_cause", "Manual"),
                "procedence": treatment.get_procedence_origin_display() or "Não informada",
            }.get(field, ""),
        },
    })


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
@permission_required("relatorios.export_treatments", raise_exception=True)
@require_http_methods(["GET"])
def treatment_export(request, pk):
    competence = get_object_or_404(_competences(), pk=pk)
    scope = request.GET.get("scope", "eligible")
    if scope not in EXPORT_SCOPES:
        raise Http404
    workbook = build_treatment_workbook(competence, scope=scope)
    month = slugify(competence.get_competence_month_display()).replace("-", "_")
    filename = f"Claro_Multas_{month}_{competence.competence_year}_ARION.xlsx"
    response = FileResponse(
        workbook, as_attachment=True, filename=filename,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _mutable_treatment(treatment):
    if treatment.source_row.competence.status == PenaltyCompetence.Status.FINALIZED or treatment.source_row.competence.deleted_at:
        return False
    return True


@login_required
@permission_required("relatorios.edit_treatments", raise_exception=True)
@require_http_methods(["GET", "POST"])
def treatment_edit(request, pk):
    treatment = _treatment(pk, user=request.user)
    competence = treatment.source_row.competence
    ajax = _wants_json(request)
    form = TreatmentEditForm(request.POST or None, instance=treatment)
    if request.method == "POST" and form.is_valid():
        from django.db import transaction
        try:
            with transaction.atomic():
                PenaltyCompetence.objects.select_for_update().get(pk=competence.pk)
                locked = PenaltyTreatmentRow.objects.select_for_update().select_related("source_row__competence").get(pk=pk)
                if not _mutable_treatment(locked):
                    messages.error(request, "A competência está finalizada e bloqueia alterações.")
                    return redirect("relatorios:workspace", pk=competence.pk)
                expected = form.cleaned_data["version"]
                if locked.version != expected:
                    form.add_error(None, "Esta linha foi alterada por outra pessoa. Recarregue antes de salvar.")
                    raise ValueError("stale")
                previous_total_arion = locked.total_arion
                changed = []
                for field in ("arion_opening_at", "arion_fetec_at", "isp", "root_cause", "observation", "amount_not_charged", "amount_not_charged_justification", "status", "treatment_justification", "decision", "decision_reason", "procedence"):
                    value = form.cleaned_data.get(field)
                    if getattr(locked, field) != value:
                        setattr(locked, field, value)
                        changed.append(field)
                if "arion_opening_at" in changed:
                    locked.arion_opening_origin = PenaltyTreatmentRow.Origin.MANUAL
                if "arion_fetec_at" in changed:
                    locked.arion_fetec_origin = PenaltyTreatmentRow.Origin.MANUAL
                if "isp" in changed:
                    locked.field_origins = dict(locked.field_origins or {})
                    locked.field_origins["isp"] = PenaltyTreatmentRow.Origin.MANUAL
                if "root_cause" in changed:
                    locked.field_origins = dict(locked.field_origins or {})
                    locked.field_origins["root_cause"] = PenaltyTreatmentRow.Origin.MANUAL
                if "procedence" in changed:
                    locked.procedence_origin = PenaltyTreatmentRow.Origin.MANUAL
                if "amount_not_charged" in changed:
                    locked.amount_not_charged_by = request.user
                    locked.amount_not_charged_at = timezone.now()
                if form.cleaned_data.get("validation_code"):
                    from aplicativos.integracoes.services.secrets import get_secret_protector
                    locked.validation_code_ciphertext = get_secret_protector().protect(form.cleaned_data["validation_code"])
                    changed.append("validation_secret_updated")
                if form.cleaned_data.get("validation_code_2"):
                    from aplicativos.integracoes.services.secrets import get_secret_protector
                    locked.validation_code_2_ciphertext = get_secret_protector().protect(form.cleaned_data["validation_code_2"])
                    changed.append("validation_secret_2_updated")
                if form.cleaned_data.get("clear_validation_code"):
                    locked.validation_code_ciphertext = ""
                    changed.append("validation_secret_removed")
                if form.cleaned_data.get("clear_validation_code_2"):
                    locked.validation_code_2_ciphertext = ""
                    changed.append("validation_secret_2_removed")
                locked.version += 1
                locked.save()
                calculate_treatment(locked)
                try:
                    validate_financial_decision(locked)
                except ValidationError as exc:
                    form.add_error("decision", exc.messages[0])
                    raise ValueError("invalid_decision") from exc
                record_financial_change(
                    treatment=locked, actor=request.user, previous_total=previous_total_arion,
                    new_total=locked.total_arion,
                    reason=locked.decision_reason or locked.treatment_justification,
                    source="MANUAL_EDIT",
                )
                treatment = locked
        except ValueError as exc:
            if str(exc) not in ("stale", "invalid_decision"):
                raise
        except Exception:
            form.add_error(None, "Não foi possível proteger os campos sensíveis. Verifique o contexto seguro da aplicação e tente novamente.")
        else:
            if treatment.status == PenaltyTreatmentRow.Status.PENDING and changed:
                _audit(request, action="TREATMENT_STARTED", description="Tratativa iniciada na linha.", competence=competence,
                       metadata={"treatment_row_id": treatment.pk})
            date_adjustments = {}
            for field, key, automatic_field, origin_field in (
                ("arion_opening_at", "opening", "arion_opening_auto_at", "arion_opening_origin"),
                ("arion_fetec_at", "fetec", "arion_fetec_auto_at", "arion_fetec_origin"),
            ):
                if field in changed:
                    date_adjustments[key] = {
                        "automatic": _display_datetime(getattr(treatment, automatic_field)),
                        "effective": _display_datetime(getattr(treatment, field)),
                        "origin": getattr(treatment, origin_field),
                    }
            _audit(request, action="TREATMENT_FIELDS_CHANGED", description="Campos da tratativa atualizados.", competence=competence,
                   metadata={"treatment_row_id": treatment.pk, "fields": changed, "version": treatment.version + 1,
                             "reason": form.cleaned_data.get("treatment_justification", "") if date_adjustments else "",
                             "date_adjustments": date_adjustments})
            if "status" in changed:
                _audit(request, action="TREATMENT_STATUS_CHANGED", description="Status da linha atualizado.", competence=competence,
                       metadata={"treatment_row_id": treatment.pk, "status": form.cleaned_data["status"]})
                if form.cleaned_data["status"] == PenaltyTreatmentRow.Status.APPROVED:
                    _audit(request, action="TREATMENT_LINE_FINALIZED", description="Linha aprovada na tratativa.", competence=competence,
                           metadata={"treatment_row_id": treatment.pk})
            if "decision" in changed:
                _audit(request, action="FINANCIAL_DECISION_CHANGED", description="Decisão financeira da cobrança alterada.", competence=competence,
                       metadata={"treatment_row_id": treatment.pk, "decision": treatment.decision,
                                 "total_claro": str(treatment.total_claro) if treatment.total_claro is not None else None,
                                 "total_arion": str(treatment.total_arion) if treatment.total_arion is not None else None,
                                 "reason": treatment.decision_reason[:500]})
            if previous_total_arion != treatment.total_arion:
                _audit(request, action="FINANCIAL_TOTAL_CHANGED", description="Valor efetivo ARION alterado na tratativa.", competence=competence,
                       metadata={"treatment_row_id": treatment.pk,
                                 "previous_total_arion": str(previous_total_arion) if previous_total_arion is not None else None,
                                 "new_total_arion": str(treatment.total_arion) if treatment.total_arion is not None else None,
                                 "reason": treatment.decision_reason or treatment.treatment_justification})
            if ajax:
                next_treatment = None
                if "save_and_next" in request.POST:
                    filtered_rows = treatment_queryset(competence, request.GET)
                    next_treatment = _next_pending_treatment(
                        filtered_rows, treatment.source_row.source_row_number, exclude_pk=treatment.pk,
                    )
                return JsonResponse({
                    "ok": True,
                    "id": treatment.pk,
                    "version": treatment.version,
                    "next_id": next_treatment.pk if next_treatment else None,
                    "message": "Tratativa salva." if next_treatment else "Tratativa salva. Não há outro caso pendente neste filtro.",
                    "counters": _treatment_counters(competence),
                })
            messages.success(request, "Tratativa salva.")
            if "save_and_next" in request.POST:
                if treatment.decision not in TERMINAL_DECISIONS:
                    messages.info(request, "Registre uma decisão financeira válida para avançar ao próximo caso pendente.")
                    return redirect("relatorios:treatment-edit", pk=treatment.pk)
                try:
                    next_id = int(request.POST.get("next_id", ""))
                except (TypeError, ValueError):
                    next_id = None
                filtered_rows = treatment_queryset(competence, request.GET)
                next_treatment = filtered_rows.filter(
                    pk=next_id, decision=PenaltyTreatmentRow.Decision.PENDING,
                ).select_related("source_row").first() if next_id else None
                if next_treatment:
                    next_pending = _next_pending_treatment(
                        filtered_rows, next_treatment.source_row.source_row_number,
                        exclude_pk=next_treatment.pk,
                    )
                    redirect_query = request.GET.copy()
                    for key in ("aba", "page", "treatment_page", "next_id"):
                        redirect_query.pop(key, None)
                    if next_pending:
                        redirect_query["next_id"] = str(next_pending.pk)
                    target_url = reverse("relatorios:treatment-edit", args=(next_treatment.pk,))
                    query_string = redirect_query.urlencode()
                    return redirect(f"{target_url}?{query_string}" if query_string else target_url)
            return redirect("relatorios:treatment-edit", pk=treatment.pk)
    if ajax and request.method == "POST" and form.errors:
        errors = {name: [str(error) for error in values] for name, values in form.errors.items()}
        return JsonResponse({"ok": False, "errors": errors, "error": _form_feedback(form)[0]}, status=422)
    treatment.refresh_from_db()
    calculate_treatment(treatment, save=False)
    from .services.treatment import date_divergences
    filtered_rows = treatment_queryset(competence, request.GET)
    next_treatment = None
    try:
        next_id = int(request.GET.get("next_id", ""))
    except (TypeError, ValueError):
        next_id = None
    if next_id:
        next_treatment = filtered_rows.filter(
            pk=next_id,
            decision=PenaltyTreatmentRow.Decision.PENDING,
        ).select_related("source_row").first()
    if not next_treatment:
        next_treatment = _next_pending_treatment(
            filtered_rows, treatment.source_row.source_row_number, exclude_pk=treatment.pk,
        )
    return render(request, "relatorios/claro/treatment_edit.html", {
        "active_section": "claro", "competence": competence, "treatment": treatment,
        "next_treatment": next_treatment,
        "effective": effective_values(treatment), "form": form,
        "pause_form": TreatmentPauseForm(initial={"version": treatment.version}), "pause_rows": treatment.pauses.all(),
        "overrides": treatment.overrides.filter(removed_at__isnull=True),
        "override_form": TreatmentOverrideForm(initial={"version": treatment.version}),
        "source": treatment.source_row.canonical_data,
        "validation_configured": bool(treatment.validation_code_ciphertext),
        "validation_2_configured": bool(treatment.validation_code_2_ciphertext),
        "date_divergences": date_divergences(treatment),
    })


@login_required
@permission_required("relatorios.edit_treatments", raise_exception=True)
@require_http_methods(["POST"])
def treatment_pause_add(request, pk):
    treatment = _treatment(pk, user=request.user)
    if not _mutable_treatment(treatment):
        raise Http404
    form = TreatmentPauseForm(request.POST)
    if form.is_valid():
        from django.db import transaction
        try:
            with transaction.atomic():
                PenaltyCompetence.objects.select_for_update().get(pk=treatment.source_row.competence_id)
                treatment = PenaltyTreatmentRow.objects.select_for_update().select_related("source_row__competence").get(pk=pk)
                if treatment.version != form.cleaned_data["version"] or not _mutable_treatment(treatment):
                    if _wants_json(request):
                        return JsonResponse({"ok": False, "error": "A linha mudou ou foi finalizada. Atualize os detalhes antes de continuar."}, status=409)
                    messages.error(request, "A linha mudou ou foi finalizada. Recarregue antes de continuar.")
                    return redirect("relatorios:treatment-edit", pk=pk)
                previous_total_arion = treatment.total_arion
                pause = PenaltyTreatmentPause(treatment=treatment, created_by=request.user, **{key: value for key, value in form.cleaned_data.items() if key != "version"})
                pause.full_clean()
                pause.save()
                treatment.version += 1
                treatment.save(update_fields=("version", "updated_at"))
                calculate_treatment(treatment)
                record_financial_change(
                    treatment=treatment, actor=request.user, previous_total=previous_total_arion,
                    new_total=treatment.total_arion,
                    reason=f"{pause.reason} · Evidência: {pause.evidence}", source="PAUSE_ADDED",
                )
        except Exception:
            if _wants_json(request):
                return JsonResponse({"ok": False, "error": "Não foi possível registrar essa pausa."}, status=422)
            messages.error(request, "Não foi possível registrar essa pausa.")
        else:
            _audit(request, action="TREATMENT_PAUSE_ADDED", description="Pausa adicionada à tratativa.", competence=treatment.source_row.competence,
                   metadata={"treatment_row_id": treatment.pk, "pause_id": pause.pk, "kind": pause.kind,
                             "minutes": pause.duration_minutes, "reason": pause.reason,
                             "evidence": pause.evidence, "created_at": pause.created_at})
            if _wants_json(request):
                return JsonResponse({"ok": True, "version": treatment.version})
            messages.success(request, "Pausa adicionada.")
    else:
        if _wants_json(request):
            return JsonResponse({"ok": False, "errors": form.errors.get_json_data(), "error": _form_feedback(form)[0]}, status=422)
        messages.error(request, "Confira os dados da pausa.")
    return redirect("relatorios:treatment-edit", pk=pk)


@login_required
@permission_required("relatorios.edit_treatments", raise_exception=True)
@require_http_methods(["POST"])
def treatment_pause_remove(request, pk, pause_pk):
    treatment = _treatment(pk, user=request.user)
    from django.db import transaction
    with transaction.atomic():
        PenaltyCompetence.objects.select_for_update().get(pk=treatment.source_row.competence_id)
        treatment = PenaltyTreatmentRow.objects.select_for_update().select_related("source_row__competence").get(pk=pk)
        if not _mutable_treatment(treatment):
            raise Http404
        if str(request.POST.get("version", "")) != str(treatment.version):
            messages.error(request, "A linha mudou. Recarregue antes de remover a pausa.")
            return redirect("relatorios:treatment-edit", pk=pk)
        previous_total_arion = treatment.total_arion
        pause = get_object_or_404(PenaltyTreatmentPause, pk=pause_pk, treatment=treatment)
        minutes, pause_id = pause.duration_minutes, pause.pk
        reason, evidence = pause.reason, pause.evidence
        pause.delete()
        treatment.version += 1
        treatment.save(update_fields=("version", "updated_at"))
        calculate_treatment(treatment)
        record_financial_change(
            treatment=treatment, actor=request.user, previous_total=previous_total_arion,
            new_total=treatment.total_arion,
            reason=f"Remoção da pausa {pause_id} ({minutes} min): {reason} · Evidência: {evidence}", source="PAUSE_REMOVED",
        )
    _audit(request, action="TREATMENT_PAUSE_REMOVED", description="Pausa removida da tratativa.", competence=treatment.source_row.competence,
           metadata={"treatment_row_id": treatment.pk, "pause_id": pause_id, "minutes": minutes,
                     "reason": reason, "evidence": evidence})
    return redirect("relatorios:treatment-edit", pk=pk)


@login_required
@permission_required("relatorios.override_treatments", raise_exception=True)
@require_http_methods(["POST"])
def treatment_override_add(request, pk):
    treatment = _treatment(pk, user=request.user)
    form = TreatmentOverrideForm(request.POST)
    if _mutable_treatment(treatment) and form.is_valid():
        from django.db import transaction
        field, value = form.cleaned_data["field"], form.cleaned_data["value"]
        with transaction.atomic():
            PenaltyCompetence.objects.select_for_update().get(pk=treatment.source_row.competence_id)
            treatment = PenaltyTreatmentRow.objects.select_for_update().select_related("source_row__competence").get(pk=pk)
            if treatment.version != form.cleaned_data["version"] or not _mutable_treatment(treatment):
                if _wants_json(request):
                    return JsonResponse({"ok": False, "error": "A linha mudou ou foi finalizada. Atualize os detalhes antes de continuar."}, status=409)
                messages.error(request, "A linha mudou ou foi finalizada. Recarregue antes de continuar.")
                return redirect("relatorios:treatment-edit", pk=pk)
            previous_total_arion = treatment.total_arion
            calculated = {"tmp": treatment.tmp_gross_minutes, "ev": treatment.ev_calculated,
                          "inter": treatment.inter_calculated, "unavailability": treatment.unavailability_calculated,
                          "penalty": treatment.penalty_arion}.get(field)
            previous = list(PenaltyTreatmentOverride.objects.filter(treatment=treatment, field=field, removed_at__isnull=True))
            removed_at = timezone.now()
            for prior in previous:
                prior.removed_at, prior.removed_by = removed_at, request.user
                prior.save(update_fields=("removed_at", "removed_by"))
            override = PenaltyTreatmentOverride.objects.create(treatment=treatment, field=field, calculated_value=calculated,
                override_value=value, justification=form.cleaned_data["justification"], created_by=request.user)
            treatment.version += 1
            treatment.save(update_fields=("version", "updated_at"))
            calculate_treatment(treatment)
            record_financial_change(
                treatment=treatment, actor=request.user, previous_total=previous_total_arion,
                new_total=treatment.total_arion, reason=form.cleaned_data["justification"], source="OVERRIDE_APPLIED",
            )
        for prior in previous:
            _audit(request, action="TREATMENT_OVERRIDE_REMOVED", description="Override substituído por um novo ajuste.", competence=treatment.source_row.competence,
                   metadata={"treatment_row_id": treatment.pk, "override_id": prior.pk, "field": prior.field})
        _audit(request, action="TREATMENT_OVERRIDE_APPLIED", description="Override aplicado à tratativa.", competence=treatment.source_row.competence,
               metadata={"treatment_row_id": treatment.pk, "override_id": override.pk, "field": field, "calculated": str(calculated) if calculated is not None else None, "effective": str(value)})
        if previous_total_arion != treatment.total_arion:
            _audit(request, action="FINANCIAL_TOTAL_CHANGED", description="Override alterou o valor efetivo ARION.", competence=treatment.source_row.competence,
                   metadata={"treatment_row_id": treatment.pk, "previous_total_arion": str(previous_total_arion) if previous_total_arion is not None else None,
                             "new_total_arion": str(treatment.total_arion) if treatment.total_arion is not None else None,
                             "reason": form.cleaned_data["justification"]})
        if _wants_json(request):
            return JsonResponse({"ok": True, "version": treatment.version})
        messages.success(request, "Override aplicado.")
    else:
        if _wants_json(request):
            return JsonResponse({"ok": False, "errors": form.errors.get_json_data(), "error": _form_feedback(form)[0]}, status=422)
        messages.error(request, "Revise o valor e a justificativa do override.")
    return redirect("relatorios:treatment-edit", pk=pk)


@login_required
@permission_required("relatorios.override_treatments", raise_exception=True)
@require_http_methods(["POST"])
def treatment_override_remove(request, pk, override_pk):
    treatment = _treatment(pk, user=request.user)
    from django.db import transaction
    with transaction.atomic():
        PenaltyCompetence.objects.select_for_update().get(pk=treatment.source_row.competence_id)
        treatment = PenaltyTreatmentRow.objects.select_for_update().select_related("source_row__competence").get(pk=pk)
        if not _mutable_treatment(treatment):
            raise Http404
        if str(request.POST.get("version", "")) != str(treatment.version):
            messages.error(request, "A linha mudou. Recarregue antes de remover o override.")
            return redirect("relatorios:treatment-edit", pk=pk)
        previous_total_arion = treatment.total_arion
        override = get_object_or_404(PenaltyTreatmentOverride, pk=override_pk, treatment=treatment, removed_at__isnull=True)
        override.removed_at, override.removed_by = timezone.now(), request.user
        override.save(update_fields=("removed_at", "removed_by"))
        treatment.version += 1
        treatment.save(update_fields=("version", "updated_at"))
        calculate_treatment(treatment)
        record_financial_change(
            treatment=treatment, actor=request.user, previous_total=previous_total_arion,
            new_total=treatment.total_arion, reason=override.justification, source="OVERRIDE_REMOVED",
        )
    _audit(request, action="TREATMENT_OVERRIDE_REMOVED", description="Override removido da tratativa.", competence=treatment.source_row.competence,
           metadata={"treatment_row_id": treatment.pk, "override_id": override.pk, "field": override.field})
    if previous_total_arion != treatment.total_arion:
        _audit(request, action="FINANCIAL_TOTAL_CHANGED", description="Remoção de override alterou o valor efetivo ARION.", competence=treatment.source_row.competence,
               metadata={"treatment_row_id": treatment.pk, "previous_total_arion": str(previous_total_arion) if previous_total_arion is not None else None,
                         "new_total_arion": str(treatment.total_arion) if treatment.total_arion is not None else None,
                         "reason": override.justification})
    return redirect("relatorios:treatment-edit", pk=pk)


@login_required
@permission_required("relatorios.finalize_treatments", raise_exception=True)
@require_http_methods(["POST"])
def competence_finalize(request, pk):
    with transaction.atomic():
        competence = get_object_or_404(_competences().select_for_update(), pk=pk)
        if not competence.imported_at:
            messages.error(request, "Importe o Original antes de finalizar a competência.")
            return redirect("relatorios:workspace", pk=pk)
        eligible = claro_treatment_candidates(PenaltyTreatmentRow.objects.filter(source_row__competence=competence))
        pending = eligible.exclude(decision__in=TERMINAL_DECISIONS).count()
        if pending:
            messages.error(request, f"{pending} linha(s) do Recorte Claro (Indisp. > 0) ainda não possuem decisão financeira terminal.")
            return redirect("relatorios:workspace", pk=pk)
        for row in eligible.select_for_update().select_related("source_row__competence").prefetch_related("overrides", "pauses").iterator(chunk_size=500):
            previous_total_arion = row.total_arion
            try:
                calculate_treatment(row)
                validate_financial_decision(row)
            except ValidationError as exc:
                messages.error(request, f"A linha {row.source_row.source_row_number} não pode ser finalizada: {exc.messages[0]}")
                return redirect("relatorios:workspace", pk=pk)
            record_financial_change(
                treatment=row, actor=request.user, previous_total=previous_total_arion,
                new_total=row.total_arion, reason="Conferência e recálculo durante finalização.", source="FINALIZATION",
            )
        competence.status = PenaltyCompetence.Status.FINALIZED
        competence.finalized_at, competence.finalized_by = timezone.now(), request.user
        competence.updated_by = request.user
        competence.save(update_fields=("status", "finalized_at", "finalized_by", "updated_by", "updated_at"))
    _audit(request, action="COMPETENCE_FINALIZED", description="Competência finalizada.", competence=competence,
           metadata={"rows": competence.imported_row_count, "eligible_rows": eligible.count(), "pending_rows": 0})
    return redirect("relatorios:workspace", pk=pk)


@login_required
@permission_required("relatorios.reopen_treatments", raise_exception=True)
@require_http_methods(["POST"])
def competence_reopen(request, pk):
    reason = (request.POST.get("reason") or "").strip()[:500]
    with transaction.atomic():
        competence = get_object_or_404(_competences().select_for_update(), pk=pk)
        if competence.status == PenaltyCompetence.Status.FINALIZED and not reason:
            messages.error(request, "Informe o motivo da reabertura.")
        elif competence.status == PenaltyCompetence.Status.FINALIZED:
            competence.status = PenaltyCompetence.Status.REVIEW
            competence.finalized_at, competence.finalized_by = None, None
            competence.updated_by = request.user
            competence.save(update_fields=("status", "finalized_at", "finalized_by", "updated_by", "updated_at"))
            _audit(request, action="COMPETENCE_REOPENED", description="Competência reaberta para revisão.", competence=competence,
                   metadata={"reason": reason})
    return redirect("relatorios:workspace", pk=pk)


@login_required
@permission_required(REPORTS_PERMISSION, raise_exception=True)
@require_POST
def competence_delete(request, pk):
    with transaction.atomic():
        competence = get_object_or_404(_competences().select_for_update(), pk=pk)
        finalized = competence.status == PenaltyCompetence.Status.FINALIZED
        permission = "relatorios.delete_finalized_penaltycompetence" if finalized else "relatorios.delete_penaltycompetence"
        if not request.user.has_perm(permission):
            return HttpResponseForbidden("Acesso negado.")

        expected_confirmation = f"EXCLUIR {competence.period_label.upper()}" if finalized else "EXCLUIR"
        confirmation = request.POST.get("confirmation", "").strip().upper()
        reason = request.POST.get("reason", "").strip()[:500]
        if confirmation != expected_confirmation or not reason:
            messages.error(request, "Confirme a exclusão com o texto solicitado e informe o motivo.")
            return redirect("relatorios:workspace", pk=pk)

        deleted_at = timezone.now()
        source_rows = competence.source_rows.count()
        file_name = competence.original_file.name if competence.original_file else ""
        file_storage = competence.original_file.storage if competence.original_file else None
        action = "COMPETENCE_ARCHIVED" if finalized else "COMPETENCE_DELETED"
        _audit(
            request,
            action=action,
            description="Competência finalizada arquivada." if finalized else "Competência não finalizada excluída.",
            competence=competence,
            metadata={
                "report_type": competence.report_type,
                "competence_month": competence.competence_month,
                "competence_year": competence.competence_year,
                "period": competence.period_label,
                "status": competence.status,
                "source_rows": source_rows,
                "original_file_present": bool(file_name),
                "user_id": request.user.pk,
                "deleted_at": deleted_at,
                "reason": reason,
                "retention": "ARCHIVED" if finalized else "PHYSICAL_DELETE",
            },
        )
        if finalized:
            competence.deleted_at = deleted_at
            competence.deleted_by = request.user
            competence.delete_reason = reason
            competence.updated_by = request.user
            competence.save(update_fields=("deleted_at", "deleted_by", "delete_reason", "updated_by", "updated_at"))
            messages.success(request, "Competência finalizada arquivada. Seu histórico financeiro foi preservado.")
        else:
            competence.delete()
            if file_name and file_storage:
                transaction.on_commit(lambda storage=file_storage, name=file_name: _delete_private_original(storage, name))
            messages.success(request, "Competência excluída. O Original privado foi removido junto com os registros associados.")
    return redirect("relatorios:claro-penalidades")


@login_required
@permission_required("relatorios.edit_treatments", raise_exception=True)
@require_http_methods(["POST"])
def competence_enrich(request, pk):
    competence = get_object_or_404(_competences(), pk=pk)
    if not competence.imported_at:
        messages.error(request, "Importe as linhas antes de solicitar o enriquecimento.")
        return redirect("relatorios:workspace", pk=pk)
    if not _mutable_competence(competence):
        messages.error(request, "Reabra a competência antes de atualizar evidências.")
        return redirect("relatorios:workspace", pk=pk)
    try:
        with transaction.atomic():
            result = enrich_competence(competence.pk, user=request.user, remote_lookup=True)
            _audit(request, action="TREATMENT_ENRICHED", description="Linhas enriquecidas com os espelhos locais disponíveis.", competence=competence,
                   metadata=result)
    except Exception:
        logger.exception("Local OTRS/CMDB enrichment failed for penalty competence %s.", competence.pk)
        messages.error(request, "O enriquecimento local não foi concluído. Nenhuma consulta foi enviada para OTRS remoto.")
        return redirect("relatorios:workspace", pk=pk)
    result_message = (
        f"Enriquecimento concluído: {result['processed']} linhas analisadas; "
        f"{result['found']} com ticket encontrado ({result.get('remote_found', 0)} no OTRS geral); "
        f"{result['not_found']} sem associação automática; "
        f"{result['ticket_ambiguous']} com candidatos OTRS para escolha; "
        f"{result['isp_found']} ISPs identificados; {result['ambiguous']} com múltiplos candidatos de ISP."
    )
    if result.get("remote_unavailable"):
        messages.warning(request, f"{result_message} {result['remote_message']} A Tratativa continua disponível manualmente.")
    else:
        messages.success(request, result_message)
    return redirect("relatorios:workspace", pk=pk)


def _mutable_competence(competence):
    return competence.status != PenaltyCompetence.Status.FINALIZED and competence.deleted_at is None
