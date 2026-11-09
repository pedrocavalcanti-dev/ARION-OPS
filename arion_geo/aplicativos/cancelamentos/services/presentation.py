"""Scoped, batched list presentation; never changes historical facts."""
from decimal import Decimal

from django.db.models import Count, Prefetch

from ..forms import FINANCIAL_PENDING_KINDS
from ..models import CancellationPending, FinancialObligation, LegacyCancellationReference


def valid_date(value):
    return value is not None and 1990 <= value.year <= 2100


def money_columns(obligations, pending_amounts):
    """Use the obligation ledger once. Child amounts remain evidence, not additions."""
    amounts = {"CONTRACT": [], "EQUIPMENT": [], "OTHER": []}
    reliable = bool(obligations)
    for item in obligations:
        amount = item.charged_amount if item.charged_amount is not None else item.estimated_amount
        amounts[item.kind if item.kind in amounts else "OTHER"].append(amount)
        if amount is None or item.reconciliation_state != "NOT_REQUIRED":
            reliable = False
    # Without a reconciliation link, child charges cannot prove a consolidated total.
    if pending_amounts:
        reliable = False
    # A child-only category can be shown as an unconfirmed amount; never add it
    # to a category already represented in the obligation ledger (including zero).
    fallback = {"CONTRACT": [], "EQUIPMENT": [], "OTHER": []}
    for pending in pending_amounts:
        key = {"CONTRACT_FINE": "CONTRACT", "EQUIPMENT_FINE": "EQUIPMENT"}.get(pending["kind"], "OTHER")
        fallback[key].append(pending["amount"])
    for key in amounts:
        if not amounts[key]:
            amounts[key] = fallback[key]
    totals = {key: sum((v for v in values if v is not None), Decimal("0"))
              if values and all(v is not None for v in values) else None for key, values in amounts.items()}
    return {"fine": totals["CONTRACT"], "equipment": totals["EQUIPMENT"], "other": totals["OTHER"],
            "total": sum((v for v in totals.values() if v is not None), Decimal("0")) if reliable else None,
            "review": not reliable}


def prepare_rows(rows, user):
    """All related queries are constrained to the already scoped, paginated rows."""
    from django.db.models import prefetch_related_objects

    rows = list(rows)
    ids = [row.pk for row in rows]
    can_finance = user.has_perm("cancelamentos.view_cancellation_finance")
    prefetches = [Prefetch("legacy_references", queryset=LegacyCancellationReference.objects.filter(
        entity="cancelamento"), to_attr="table_legacy")]
    if can_finance:
        prefetches.append(Prefetch("obligations", queryset=FinancialObligation.objects.all(), to_attr="table_obligations"))
    prefetch_related_objects(rows, *prefetches)
    pendings = CancellationPending.objects.filter(process_id__in=ids, status="OPEN")
    if not can_finance:
        pendings = pendings.exclude(kind__in=FINANCIAL_PENDING_KINDS).filter(amount__isnull=True, legacy_reference__isnull=True)
    counts = {r["process_id"]: r["n"] for r in pendings.values("process_id").annotate(n=Count("pk"))}
    financial_pending = {}
    if can_finance:
        for item in pendings.filter(amount__isnull=False).values("process_id", "kind", "amount"):
            financial_pending.setdefault(item["process_id"], []).append(item)
    for row in rows:
        reference = row.table_legacy[0] if row.table_legacy else None
        raw = reference.source_data if reference else {}
        reconciliation = reference.reconciliation if reference else {}
        row.open_pending_count = counts.get(row.pk, 0)
        row.table_status = ("Concluído" if row.stage == "COMPLETED" else
                            row.get_operational_status_display() if row.operational_status else
                            str(raw.get("status") or row.get_stage_display()).replace("_", " ").capitalize())
        opened = row.ticket_opened_on or (row.intake.source_created_at if row.intake_id else None)
        row.table_opened = opened if valid_date(opened) and not reconciliation.get("opening_date_needs_review") else None
        row.opening_review = bool(opened or reference) and row.table_opened is None
        row.table_completed = row.completed_at if valid_date(row.completed_at) else None
        row.table_effective = row.treatment.effective_date if valid_date(row.treatment.effective_date) else None
        row.finance = money_columns(row.table_obligations, financial_pending.get(row.pk, [])) if can_finance else None
    return rows
