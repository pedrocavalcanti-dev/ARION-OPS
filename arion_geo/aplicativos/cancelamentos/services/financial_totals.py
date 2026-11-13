"""Centralized financial calculation and provenance deduplication for cancellations."""
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Case, CharField, DecimalField, Value, When
from django.db.models.functions import Coalesce

PENDING_PRINCIPAL_KIND = {
    "CONTRACT_FINE": "CONTRACT",
    "EQUIPMENT_FINE": "EQUIPMENT",
    "OVERDUE": "OTHER",
    "BILL": "OTHER",
    "OTHER": "OTHER",
}

LEGACY_FIELD_PENDING_KINDS = {
    "valor_multa_rescisao": frozenset({"CONTRACT_FINE"}),
    "valor_equipamento": frozenset({"EQUIPMENT_FINE"}),
    "valor_outros": frozenset({"OVERDUE", "OTHER"}),
}

ZERO = Decimal("0")


def principal_kind(kind):
    return kind if kind in {"CONTRACT", "EQUIPMENT"} else "OTHER"


def pending_principal_kind(kind):
    return PENDING_PRINCIPAL_KIND.get(kind, "OTHER")


def principal_amount_expression():
    """SQL equivalent of the ledger value used by table/detail/export."""
    return Coalesce("charged_amount", "estimated_amount", output_field=DecimalField(max_digits=30, decimal_places=2))


def principal_kind_expression():
    return Case(
        When(kind="CONTRACT", then=Value("CONTRACT")),
        When(kind="EQUIPMENT", then=Value("EQUIPMENT")),
        default=Value("OTHER"),
        output_field=CharField(),
    )


def format_brl(value):
    if value is None:
        return "—"
    return "R$ " + format(value, ",.2f").translate(str.maketrans({",": ".", ".": ","}))


def _attr(item, name, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _pending_pk(item):
    pk = _attr(item, "pk", None)
    return pk if pk is not None else _attr(item, "id", None)


def _pending_has_legacy(item):
    annotated = _attr(item, "_has_legacy", None)
    if annotated is not None:
        return bool(annotated)
    if _attr(item, "is_legacy", False):
        return True
    if _attr(item, "legacy_reference_id", None) is not None:
        return True
    try:
        return _attr(item, "legacy_reference", None) is not None
    except ObjectDoesNotExist:
        return False


def obligation_effective_amount(item):
    charged = _attr(item, "charged_amount", None)
    return charged if charged is not None else _attr(item, "estimated_amount", None)


def obligation_paid_amount(item, effective=None):
    paid = _attr(item, "paid_amount", None)
    if paid is not None:
        return paid
    if _attr(item, "financial_status", None) == "PAID":
        return obligation_effective_amount(item) if effective is None else effective
    return None


def pending_is_covered(pending, obligations):
    """Return True only when an obligation explicitly links or reliably represents a legacy pending."""
    amount = _attr(pending, "amount", None)
    if amount is None:
        return False
    pending_id = _pending_pk(pending)
    if pending_id is not None:
        for ob in obligations:
            if _attr(ob, "source_pending_id", None) == pending_id and obligation_effective_amount(ob) is not None:
                return True
    if _pending_has_legacy(pending):
        kind = _attr(pending, "kind", "OTHER")
        status = _attr(pending, "status", "OPEN")
        for ob in obligations:
            legacy_field = _attr(ob, "legacy_field", None)
            if not legacy_field:
                continue
            expected_kinds = LEGACY_FIELD_PENDING_KINDS.get(legacy_field, ())
            if kind not in expected_kinds:
                continue
            eff = obligation_effective_amount(ob)
            if eff is None:
                continue
            if eff > ZERO or (status == "DISCARDED" and _attr(ob, "reconciliation_state", "") == "POSSIBLE_DUPLICATE"):
                return True
    return False


def money_columns(obligations, pendings=()):
    """Compute consistent process financial totals across obligations and unlinked monetary pendings."""
    obligations = list(obligations)
    pendings = list(pendings)
    amounts = {"CONTRACT": [], "EQUIPMENT": [], "OTHER": []}
    review = False
    possible_duplicate = False

    estimated_values = []
    charged_values = []
    paid_values = []
    open_obligations_total = ZERO
    has_open_or_effective_obligation = False
    unlinked_obligation_kinds = set()

    for item in obligations:
        eff = obligation_effective_amount(item)
        kind_group = principal_kind(_attr(item, "kind", "OTHER"))
        amounts[kind_group].append(eff)
        estimated_val = _attr(item, "estimated_amount", None)
        if estimated_val is not None:
            estimated_values.append(estimated_val)
        charged_val = _attr(item, "charged_amount", None)
        if charged_val is not None:
            charged_values.append(charged_val)
        paid = obligation_paid_amount(item, eff)
        if paid is not None:
            paid_values.append(paid)
        if eff is not None:
            has_open_or_effective_obligation = True
            if _attr(item, "financial_status", None) != "PAID":
                remaining = eff - (paid or ZERO)
                if remaining > ZERO:
                    open_obligations_total += remaining
        reconcile = _attr(item, "reconciliation_state", "NOT_REQUIRED")
        if reconcile == "POSSIBLE_DUPLICATE":
            possible_duplicate = True
        if eff is None or reconcile != "NOT_REQUIRED":
            review = True
        if _attr(item, "source_pending_id", None) is None and eff is not None:
            unlinked_obligation_kinds.add(kind_group)

    totals = {}
    partial = False
    for kind_group, values in amounts.items():
        known = [value for value in values if value is not None]
        totals[kind_group] = sum(known, ZERO) if known else None
        partial |= not values or len(known) != len(values)

    pending_open_count = 0
    pending_open_with_value_count = 0
    pending_open_without_value_count = 0
    pending_open_values = []
    pending_resolved_values = []
    pending_discarded_values = []
    pending_unlinked_values = []
    pending_unlinked_open_values = []
    unlinked_pendings = []
    covered_pending_count = 0

    for pending in pendings:
        status = _attr(pending, "status", "OPEN")
        amount = _attr(pending, "amount", None)
        kind = _attr(pending, "kind", "OTHER")
        if status == "OPEN":
            pending_open_count += 1
            if amount is not None:
                pending_open_with_value_count += 1
                pending_open_values.append(amount)
            else:
                pending_open_without_value_count += 1
        elif status in ("RESOLVED", "REFERRED") and amount is not None:
            pending_resolved_values.append(amount)
        elif status == "DISCARDED" and amount is not None:
            pending_discarded_values.append(amount)

        if amount is None or status == "DISCARDED":
            continue

        if pending_is_covered(pending, obligations):
            covered_pending_count += 1
            continue

        pending_unlinked_values.append(amount)
        unlinked_pendings.append(pending)
        if status == "OPEN":
            pending_unlinked_open_values.append(amount)
        if pending_principal_kind(kind) in unlinked_obligation_kinds:
            possible_duplicate = True
            review = True

    pending_unlinked = sum(pending_unlinked_values, ZERO) if pending_unlinked_values else None
    pending_open = sum(pending_open_values, ZERO) if pending_open_values else None
    pending_resolved = sum(pending_resolved_values, ZERO) if pending_resolved_values else None
    pending_discarded = sum(pending_discarded_values, ZERO) if pending_discarded_values else None

    known_parts = [value for value in (totals["CONTRACT"], totals["EQUIPMENT"], totals["OTHER"], pending_unlinked) if value is not None]
    total = sum(known_parts, ZERO) if known_parts else None
    estimated = sum(estimated_values, ZERO) if estimated_values else None
    charged = sum(charged_values, ZERO) if charged_values else None
    paid = sum(paid_values, ZERO) if paid_values else None

    if total is not None:
        open_total = open_obligations_total + sum(pending_unlinked_open_values, ZERO)
    elif has_open_or_effective_obligation or pending_unlinked_open_values:
        open_total = open_obligations_total + sum(pending_unlinked_open_values, ZERO)
    else:
        open_total = None

    resolved_cancelled_parts = [v for v in (pending_resolved, pending_discarded) if v is not None]
    resolved_cancelled = sum(resolved_cancelled_parts, ZERO) if resolved_cancelled_parts else None

    return {
        "fine": totals["CONTRACT"],
        "equipment": totals["EQUIPMENT"],
        "other": totals["OTHER"],
        "pending_unlinked": pending_unlinked,
        "unlinked_pendings": unlinked_pendings,
        "covered_pending_count": covered_pending_count,
        "total": total,
        "estimated": estimated,
        "charged": charged,
        "paid": paid,
        "open": open_total,
        "pending_open": pending_open,
        "pending_resolved": pending_resolved,
        "pending_discarded": pending_discarded,
        "resolved_cancelled": resolved_cancelled,
        "pending_open_count": pending_open_count,
        "pending_open_with_value_count": pending_open_with_value_count,
        "pending_open_without_value_count": pending_open_without_value_count,
        "has_values": bool(known_parts),
        "partial": partial,
        "possible_duplicate": possible_duplicate,
        "review": bool(review or partial or possible_duplicate or pending_unlinked_open_values),
    }
