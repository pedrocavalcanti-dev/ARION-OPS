"""Informative Decimal totals, independent from reconciliation and payment."""
from decimal import Decimal


def format_brl(value):
    if value is None:
        return "—"
    return "R$ " + format(value, ",.2f").translate(str.maketrans({",": ".", ".": ","}))


def money_columns(obligations, pending_amounts=()):
    """Sum the principal ledger once; pending amounts never enter this sum."""
    amounts = {"CONTRACT": [], "EQUIPMENT": [], "OTHER": []}
    review = False
    for item in obligations:
        value = item.charged_amount if item.charged_amount is not None else item.estimated_amount
        amounts[item.kind if item.kind in amounts else "OTHER"].append(value)
        review |= value is None or item.reconciliation_state != "NOT_REQUIRED"
    totals = {}
    partial = False
    for kind, values in amounts.items():
        known = [value for value in values if value is not None]
        totals[kind] = sum(known, Decimal("0")) if known else None
        partial |= not values or len(known) != len(values)
    known = [value for value in totals.values() if value is not None]
    return {"fine": totals["CONTRACT"], "equipment": totals["EQUIPMENT"], "other": totals["OTHER"],
            "total": sum(known, Decimal("0")) if known else None,
            "has_values": bool(known), "partial": partial,
            "review": review or partial or bool(pending_amounts)}
