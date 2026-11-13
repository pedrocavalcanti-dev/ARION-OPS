"""Scoped, batched list presentation; never changes historical facts."""
from datetime import date

from django.db.models import Count, Exists, OuterRef, Prefetch

from .financial_totals import money_columns
from ..forms import FINANCIAL_PENDING_KINDS
from ..models import CancellationPending, FinancialObligation, LegacyCancellationReference

LEGACY_STATUS_LABELS = {
    "PENDENCIA_IDENTIFICADA": "Pendência identificada",
    "CONCLUIDO_CANCELAMENTO": "Concluído",
    "CANCELAMENTO_CONFIRMADO": "Cancelamento confirmado",
    "AGUARDANDO_ISP": "Aguardando ISP",
    "EM_TRATATIVA": "Em tratativa",
}


def valid_date(value):
    return value is not None and 1990 <= value.year <= 2100


def historical_date(value):
    try:
        parsed = date.fromisoformat(str(value)[:10])
        return parsed if valid_date(parsed) else None
    except (TypeError, ValueError):
        return None


def _legacy_status_label(raw_status, stage_display):
    if not raw_status:
        return stage_display
    key = str(raw_status).strip().upper()
    return LEGACY_STATUS_LABELS.get(key) or str(raw_status).replace("_", " ").capitalize()


def prepare_rows(rows, user):
    """All related queries are constrained to the already scoped, paginated rows."""
    from django.db.models import prefetch_related_objects

    rows = list(rows)
    if not rows:
        return rows
    for row in rows:
        row.__dict__.pop("table_legacy", None)
        row.__dict__.pop("table_obligations", None)
    ids = [row.pk for row in rows]
    can_finance = user.has_perm("cancelamentos.view_cancellation_finance")
    prefetches = [Prefetch("legacy_references", queryset=LegacyCancellationReference.objects.filter(
        entity="cancelamento"), to_attr="table_legacy")]
    if can_finance:
        prefetches.append(Prefetch("obligations", queryset=FinancialObligation.objects.all(), to_attr="table_obligations"))
    prefetch_related_objects(rows, *prefetches)
    open_pendings = CancellationPending.objects.filter(process_id__in=ids, status="OPEN")
    if not can_finance:
        open_pendings = open_pendings.exclude(kind__in=FINANCIAL_PENDING_KINDS).filter(amount__isnull=True, legacy_reference__isnull=True)
    counts = {r["process_id"]: r["n"] for r in open_pendings.values("process_id").annotate(n=Count("pk"))}
    manageable = open_pendings
    if not (can_finance and user.has_perm("cancelamentos.manage_cancellation_finance")):
        manageable = manageable.exclude(kind__in=FINANCIAL_PENDING_KINDS).filter(amount__isnull=True, legacy_reference__isnull=True)
    manageable_ids = set(manageable.values_list("process_id", flat=True)) if user.has_perm("cancelamentos.manage_cancellation_pending") else set()
    financial_pending = {}
    if can_finance:
        legacy_ref = LegacyCancellationReference.objects.filter(pending_id=OuterRef("pk"))
        for item in (CancellationPending.objects.filter(process_id__in=ids)
                     .annotate(_has_legacy=Exists(legacy_ref))
                     .values("pk", "process_id", "kind", "status", "amount", "_has_legacy")):
            financial_pending.setdefault(item["process_id"], []).append(item)
    for row in rows:
        reference = row.table_legacy[0] if row.table_legacy else None
        raw = reference.source_data if reference else {}
        reconciliation = reference.reconciliation if reference else {}
        row.open_pending_count = counts.get(row.pk, 0)
        row.has_manageable_pending = row.pk in manageable_ids and row.stage != "COMPLETED"
        row.table_status = ("Concluído" if row.stage == "COMPLETED" else
                            row.get_operational_status_display() if row.operational_status else
                            _legacy_status_label(raw.get("status"), row.get_stage_display()))
        opened = row.ticket_opened_on or (row.intake.source_created_at if row.intake_id else None)
        row.table_opened = opened if valid_date(opened) and not reconciliation.get("opening_date_needs_review") else None
        row.opening_review = bool(opened or reference) and row.table_opened is None
        row.table_completed = row.completed_at if valid_date(row.completed_at) else None
        row.table_effective = row.treatment.effective_date if valid_date(row.treatment.effective_date) else None
        if row.table_effective is None:
            row.table_effective = historical_date(raw.get("data_cancelamento_confirmado"))
        row.effective_review = row.table_effective is None and bool(raw.get("data_cancelamento_confirmado"))
        row.finance = money_columns(row.table_obligations, financial_pending.get(row.pk, [])) if can_finance else None
    return rows
