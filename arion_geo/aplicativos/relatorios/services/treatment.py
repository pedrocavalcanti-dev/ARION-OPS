"""Financial workflow helpers for Claro penalty treatment."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
import re

from django.db import transaction
from django.db.models import Count, DateField, DecimalField, F, Q, Sum
from django.db.models.functions import Cast
from django.utils import timezone

from aplicativos.integracoes.models import OTRSTicket
from aplicativos.integracoes.services.otrs_status import (
    business_status_label, normalize_business_status, status_for_external_state,
)
from aplicativos.relatorios.models import (
    PenaltyCompetence,
    PenaltyFinancialChange,
    PenaltyTreatmentTicket,
    PenaltyTreatmentRow,
)
from .calculations import row_has_penalty, source_total_claro
from .workbooks import _parse_decimal, canonical_ticket_number
from .penalty_ticket_lookup import (
    MAX_LOOKUP_SPECS, MAX_REMOTE_CANDIDATES, REMOTE_LOOKUP_ERROR,
    REMOTE_LOOKUP_LIMIT_ERROR, lookup_general_otrs,
)


TERMINAL_DECISIONS = (
    PenaltyTreatmentRow.Decision.DUE,
    PenaltyTreatmentRow.Decision.REDUCED,
    PenaltyTreatmentRow.Decision.INVALID,
)
TICKET_ID_RE = re.compile(r"^[0-9]{8,80}$")


def record_financial_change(*, treatment, actor, previous_total, new_total, reason, source):
    if previous_total == new_total:
        return None
    return PenaltyFinancialChange.objects.create(
        treatment=treatment, actor=actor,
        previous_total=previous_total, new_total=new_total,
        reason=(reason or "Alteração financeira sem justificativa informada.")[:500],
        source=source[:24],
    )


def validate_financial_decision(treatment):
    """Require a recorded financial result consistent with the analyst decision."""
    from django.core.exceptions import ValidationError
    decision = treatment.decision
    if decision not in TERMINAL_DECISIONS:
        return
    if not treatment.has_penalty:
        raise ValidationError("Linhas sem multa não recebem decisão financeira de cobrança.")
    if not treatment.decision_reason.strip():
        raise ValidationError("A decisão financeira exige justificativa registrada.")
    if treatment.total_claro is None or treatment.total_arion is None:
        raise ValidationError("Complete os valores Claro e ARION antes de decidir a cobrança.")
    if decision == PenaltyTreatmentRow.Decision.DUE and treatment.total_arion != treatment.total_claro:
        raise ValidationError("Cobrança devida exige valor efetivo ARION igual ao total Claro.")
    if decision == PenaltyTreatmentRow.Decision.REDUCED and not (Decimal("0") < treatment.total_arion < treatment.total_claro):
        raise ValidationError("Cobrança reduzida exige valor efetivo entre zero e o total Claro.")
    if decision == PenaltyTreatmentRow.Decision.INVALID and treatment.total_arion != Decimal("0.00"):
        raise ValidationError("Cobrança indevida exige valor efetivo ARION igual a zero.")


FINE_FIELDS = {
    "inter": "interconnection_fine",
    "penal": "penalty_fine",
    "indisp": "unavailability_fine",
}


def _positive_fine_component(queryset, field):
    """Return rows whose normalized source amount for one component is positive."""
    if field not in FINE_FIELDS.values():
        raise ValueError("Componente de multa inválido.")
    amount = DecimalField(max_digits=18, decimal_places=4)
    alias = f"_fine_{field}"
    return queryset.annotate(**{
        alias: Cast(f"source_row__canonical_data__{field}", output_field=amount),
    }).filter(**{f"{alias}__gt": Decimal("0")})


def _canonical_decimal(value):
    if value is None or value == "":
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return _parse_decimal(value)


def claro_treatment_candidates(queryset):
    """The default operational Claro cut: Multas/Indisp. must be greater than zero."""
    return _positive_fine_component(queryset, "unavailability_fine")


def eligible_treatments(queryset):
    """Analytical set with any positive Claro fine component."""
    conditions = Q(pk__in=[])
    for field in FINE_FIELDS.values():
        amount = DecimalField(max_digits=18, decimal_places=4)
        alias = f"_fine_{field}"
        queryset = queryset.annotate(**{
            alias: Cast(f"source_row__canonical_data__{field}", output_field=amount),
        })
        conditions |= Q(**{f"{alias}__gt": Decimal("0")})
    return queryset.filter(conditions)


def divergent_treatments(queryset):
    return queryset.annotate(
        _claro_opening_date=Cast("source_row__canonical_data__opening", output_field=DateField()),
        _claro_fetec_date=Cast("source_row__canonical_data__fetec", output_field=DateField()),
    ).filter(
        (Q(arion_opening_at__isnull=False, _claro_opening_date__isnull=False) & ~Q(arion_opening_at__date=F("_claro_opening_date")))
        | (Q(arion_fetec_at__isnull=False, _claro_fetec_date__isnull=False) & ~Q(arion_fetec_at__date=F("_claro_fetec_date")))
    )


def treatment_queryset(competence, params=None):
    """Shared, composable backend filters for the treatment workspace."""
    params = params or {}
    rows = PenaltyTreatmentRow.objects.filter(source_row__competence=competence).select_related(
        "source_row", "ticket",
    ).prefetch_related("pauses", "overrides", "ticket_relations__otrs_ticket")
    penalty = (params.get("multa") or "claro").strip().casefold()
    if penalty in ("claro", "para_tratar", "eligible"):
        rows = claro_treatment_candidates(rows)
    elif penalty in ("com", "qualquer", "any"):
        rows = eligible_treatments(rows)
    elif penalty == "sem":
        eligible_ids = eligible_treatments(PenaltyTreatmentRow.objects.filter(source_row__competence=competence)).values("pk")
        rows = rows.exclude(pk__in=eligible_ids)
    elif penalty in FINE_FIELDS:
        rows = _positive_fine_component(rows, FINE_FIELDS[penalty])
    elif penalty not in ("todas", "all"):
        rows = claro_treatment_candidates(rows)
    decision = params.get("decisao", "")
    if decision in PenaltyTreatmentRow.Decision.values:
        rows = rows.filter(decision=decision)
    row_status = params.get("estado", "")
    if row_status in PenaltyTreatmentRow.Status.values:
        rows = rows.filter(status=row_status)
    filters = {
        "cliente": ("source_row__canonical_data__client__icontains", 100),
        "uf": ("source_row__canonical_data__uf__iexact", 8),
        "causa": ("root_cause__icontains", 240),
    }
    for param, (field, limit) in filters.items():
        value = (params.get(param) or "").strip()[:limit]
        if value:
            rows = rows.filter(**{field: value})
    isp_filter = (params.get("isp") or "").strip()[:160]
    if isp_filter:
        # ISP filters accept business labels only; a numeric query is a technical ID.
        rows = rows.none() if isp_filter.isdecimal() else rows.filter(isp__icontains=isp_filter)
    ticket = params.get("ticket", "")
    if ticket == "sem":
        rows = rows.filter(evidence_status__in=(PenaltyTreatmentRow.EvidenceStatus.NOT_FOUND, PenaltyTreatmentRow.EvidenceStatus.NOT_CHECKED))
    if params.get("isp_ausente") == "1":
        rows = rows.filter(Q(isp="") | Q(isp__isnull=True) | Q(isp__regex=r"^\d+$"))
    if params.get("pausa") == "1":
        rows = rows.filter(pauses__isnull=False).distinct()
    if params.get("override") == "1":
        rows = rows.filter(overrides__removed_at__isnull=True).distinct()
    if params.get("divergencia") == "1":
        rows = divergent_treatments(rows)
    value_limit = (params.get("acima") or "").strip().replace(",", ".")[:24]
    if value_limit:
        try:
            threshold = Decimal(value_limit)
        except Exception:
            threshold = None
        if threshold is not None and threshold >= 0:
            rows = rows.filter(total_claro__gte=threshold)
    query = (params.get("q") or "").strip()[:100]
    if query:
        rows = rows.filter(
            Q(source_row__canonical_data__rec__icontains=query)
            | Q(source_row__canonical_data__designation__icontains=query)
            | Q(ticket_identifier__icontains=query)
            | Q(ticket__ticket_number__icontains=query)
            | Q(ticket_relations__otrs_ticket__ticket_number__icontains=query)
        )
    return rows.order_by("source_row__source_row_number", "pk").distinct()


def competence_financial_summary(competence):
    all_rows = PenaltyTreatmentRow.objects.filter(source_row__competence=competence)
    eligible = claro_treatment_candidates(all_rows)
    any_fine = eligible_treatments(all_rows)
    counts = all_rows.aggregate(
        imported=Count("pk"),
        claro_candidates=Count("pk", filter=Q(pk__in=eligible.values("pk"))),
        any_fine=Count("pk", filter=Q(pk__in=any_fine.values("pk"))),
    )
    eligible_totals = eligible.aggregate(
        total_claro_sum=Sum("total_claro"), total_arion_sum=Sum("total_arion"),
        arion_incomplete=Count("pk", filter=Q(total_arion__isnull=True)),
        inter_arion_incomplete=Count("pk", filter=Q(inter_effective__isnull=True)),
        indisp_arion_incomplete=Count("pk", filter=Q(unavailability_effective__isnull=True)),
        contested=Sum("amount_contested"),
        inter_arion=Sum("inter_effective"),
        indisp_arion=Sum("unavailability_effective"),
    )
    # The three Claro component totals are stored as decimal strings in JSON;
    # calculate their exact sums in bounded batches rather than relying on DB casts.
    component_totals = {
        "inter_claro": Decimal("0"),
        "penalty_claro": Decimal("0"),
        "indisp_claro": Decimal("0"),
    }
    for source in eligible.values_list("source_row__canonical_data", flat=True).iterator(chunk_size=500):
        for key, field in (
            ("inter_claro", "interconnection_fine"),
            ("penalty_claro", "penalty_fine"),
            ("indisp_claro", "unavailability_fine"),
        ):
            component_totals[key] += _canonical_decimal(source.get(field)) or Decimal("0")
    decisions = {value: eligible.filter(decision=value).count() for value, _label in PenaltyTreatmentRow.Decision.choices}
    count_with_fine = counts["claro_candidates"] or 0
    count_any_fine = counts["any_fine"] or 0
    total_claro = eligible_totals["total_claro_sum"] if count_with_fine else Decimal("0.00")
    total_arion = None if eligible_totals["arion_incomplete"] else (eligible_totals["total_arion_sum"] or Decimal("0.00"))
    contested = max(Decimal("0.00"), total_claro - total_arion) if total_arion is not None else None
    percent = (contested * Decimal("100") / total_claro).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if contested is not None and total_claro > 0 else None
    return {
        "original_imported": counts["imported"] or 0,
        "with_penalty": count_with_fine,
        "any_fine_count": count_any_fine,
        "without_penalty": (counts["imported"] or 0) - count_any_fine,
        "outside_claro_cut": (counts["imported"] or 0) - count_with_fine,
        "pending": decisions[PenaltyTreatmentRow.Decision.PENDING],
        "in_analysis": eligible.filter(status=PenaltyTreatmentRow.Status.IN_ANALYSIS).count(),
        "due_count": decisions[PenaltyTreatmentRow.Decision.DUE],
        "reduced_count": decisions[PenaltyTreatmentRow.Decision.REDUCED],
        "invalid_count": decisions[PenaltyTreatmentRow.Decision.INVALID],
        "review_count": decisions[PenaltyTreatmentRow.Decision.REVIEW],
        "not_terminal": sum(decisions[value] for value in PenaltyTreatmentRow.Decision.values if value not in TERMINAL_DECISIONS),
        "total_claro": total_claro,
        "total_arion": total_arion,
        "contested": contested,
        "reduction_percent": percent,
        "inter_claro": component_totals["inter_claro"],
        "penalty_claro": component_totals["penalty_claro"],
        "inter_arion": None if eligible_totals["inter_arion_incomplete"] else (eligible_totals["inter_arion"] or Decimal("0.00")),
        "indisp_claro": component_totals["indisp_claro"],
        "indisp_arion": None if eligible_totals["indisp_arion_incomplete"] else (eligible_totals["indisp_arion"] or Decimal("0.00")),
    }


def _ticket_identifier(source, *, source_data=None, column_mapping=None):
    source = source or {}
    value = canonical_ticket_number(source.get("pu_fornecedor_ticket"))
    if not value:
        value = canonical_ticket_number(source.get("provider_unit_price"))
    if not value and source_data and column_mapping:
        index = next((int(index) for index, field in column_mapping.items() if field == "provider_unit_price"), None)
        if index is not None and index < len(source_data):
            cell = source_data[index] or {}
            raw_value = cell.get("cached_value") if cell.get("type") == "formula" else cell.get("value")
            value = canonical_ticket_number(raw_value)
    return value if TICKET_ID_RE.fullmatch(value) else ""


def _ticket_evidence(ticket):
    """Return exact REC/CI and LP evidence available in the local OTRS/CMDB mirrors."""
    ci_values = set()
    lp_values = set()
    for item in ticket.config_items.all():
        if item.config_item_id:
            ci_values.add(str(item.config_item_id))
        link = item.cmdb_link
        if not link:
            continue
        for value in (link.source_config_item_id, link.source_number, link.identification):
            if value:
                ci_values.add(str(value))
        for value in (link.source_number, link.circuit, link.designation, link.operator_designation, link.identification):
            if value:
                lp_values.add(str(value))

    ci_values.update(_title_ci_values(ticket.title))
    return ci_values, lp_values


def _title_ci_values(title):
    return {
        match.group(1)
        for match in re.finditer(
            r"(?i)(?<![A-Z0-9])CI\s*(?:[:#=\-]\s*)?([A-Z0-9][A-Z0-9._/-]{0,79})",
            title or "",
        )
    }


def _normalise_evidence(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _matches_exact_token(value, target):
    target = _normalise_evidence(target)
    if not target:
        return False
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", str(value or ""), re.IGNORECASE)
    return any(_normalise_evidence(token) == target for token in tokens)


def _ticket_candidate_data(ticket, *, rec, lp):
    ci_values, lp_values = _ticket_evidence(ticket)
    return _candidate_evidence(
        ticket.ticket_number, ticket.title or "", ci_values=ci_values, lp_values=lp_values,
        rec=rec, lp=lp,
    ) | {
        "status": normalize_business_status(ticket.business_status),
        "source_state": ticket.external_state_name,
        "queue": ticket.queue_name,
        "title": ticket.title,
        "external_id": ticket.external_id,
        "created_at": ticket.source_created_at.isoformat() if ticket.source_created_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "closed_at": ticket.terminal_state_at.isoformat() if ticket.terminal_state_at else None,
        "responsible": ticket.responsible_user_id,
        "source": "LOCAL_MIRROR",
        "isps": [],
    }


def _candidate_evidence(number, title, *, ci_values=(), lp_values=(), rec, lp):
    normal_rec = _normalise_evidence(rec)
    matched_ci = sorted(value for value in ci_values if normal_rec and _normalise_evidence(value) == normal_rec)
    matched_lp = sorted(value for value in lp_values if _matches_exact_token(value, lp))
    if _matches_exact_token(title, lp):
        matched_lp.append(lp)
    return {
        "number": number,
        "title": title,
        "ci": sorted(ci_values),
        "lp": sorted(lp_values),
        "matches_rec": bool(matched_ci),
        "matches_lp": bool(matched_lp),
        "matched_ci": matched_ci,
        "matched_lp": sorted(set(matched_lp)),
    }


def _candidate_rank(candidate, *, prefix):
    prefix_match = bool(prefix and candidate["number"].startswith(prefix))
    rec_match, lp_match = candidate["matches_rec"], candidate["matches_lp"]
    if prefix_match and rec_match and lp_match:
        return 4, "PU_PREFIX_REC_LP", "HIGH"
    if rec_match and lp_match:
        return 3, "REC_CI_LP", "HIGH"
    return 0, "", ""


def _remote_candidate_data(candidate, *, rec, lp):
    data = _candidate_evidence(
        candidate.get("number", ""), candidate.get("title", ""),
        ci_values=_title_ci_values(candidate.get("title", "")), rec=rec, lp=lp,
    )
    data.update({
        "status": status_for_external_state(candidate.get("state", "")),
        "source_state": candidate.get("state", ""), "queue": candidate.get("queue", ""),
        "external_id": candidate.get("external_id"), "created_at": candidate.get("created_at"),
        "resolved_at": None, "closed_at": None, "responsible": candidate.get("responsible"),
        "source": "OTRS_GENERAL", "isps": [], "retrieved_at": timezone.now().isoformat(),
    })
    return data


def _snapshot_datetime(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(result):
        result = timezone.make_aware(result, timezone.get_default_timezone())
    return result


def _snapshot_from_ticket(ticket, *, source="LOCAL_MIRROR"):
    return {
        "external_id": ticket.external_id, "number": ticket.ticket_number, "title": ticket.title,
        "queue": ticket.queue_name, "state": ticket.external_state_name or ticket.business_status,
        "source_state": ticket.external_state_name,
        "business_status": normalize_business_status(ticket.business_status), "created_at": ticket.source_created_at.isoformat() if ticket.source_created_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "closed_at": ticket.terminal_state_at.isoformat() if ticket.terminal_state_at else None,
        "responsible": ticket.responsible_user_id, "ci": [], "lp": [], "isps": [],
        "source": source, "retrieved_at": timezone.now().isoformat(),
    }


def _snapshot_from_candidate(candidate):
    raw_state = candidate.get("source_state", candidate.get("status", ""))
    return {
        "external_id": candidate.get("external_id"), "number": candidate.get("number", ""),
        "title": candidate.get("title", ""), "queue": candidate.get("queue", ""),
        "state": raw_state, "source_state": raw_state,
        "business_status": normalize_business_status(candidate.get("status") or status_for_external_state(raw_state)),
        "created_at": candidate.get("created_at"), "resolved_at": candidate.get("resolved_at"),
        "closed_at": candidate.get("closed_at"), "responsible": candidate.get("responsible"),
        "ci": candidate.get("ci", []), "lp": candidate.get("lp", []),
        "isps": candidate.get("isps", []), "source": candidate.get("source", "OTRS_GENERAL"),
        "retrieved_at": timezone.now().isoformat(),
    }


def _auto_allowed(value, origin):
    if value is None or value == "":
        return True
    return origin in (PenaltyTreatmentRow.Origin.OTRS, PenaltyTreatmentRow.Origin.CMDB, PenaltyTreatmentRow.Origin.IMPORTED)


def cmdb_isp_identity(link):
    """Resolve a display name and preserve the CMDB identifiers as separate evidence."""
    raw_isp = str(getattr(link, "isp_raw", "") or "").strip()
    operator = getattr(link, "operator", None)
    full_name = (operator.name or "").strip() if operator else ""
    raw_without_trailing_id = re.sub(r"\s*\(\s*(\d+)\s*\)\s*$", "", raw_isp).strip()
    raw_id_match = re.search(r"\(\s*(\d+)\s*\)\s*$", raw_isp)
    raw_technical_id = raw_isp if raw_isp.isdecimal() else (raw_id_match.group(1) if raw_id_match else "")
    operator_name = " ".join(re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", full_name).split())
    operator_label = operator_name.split("(", 1)[0].strip()
    raw_name = " ".join(raw_without_trailing_id.split())
    raw_label = raw_name.split("(", 1)[0].strip()
    raw_matches_operator = False
    if operator and raw_isp:
        operator_ids = {
            str(operator.source_config_item_id or "").strip().casefold(),
            str(operator.source_number or "").strip().casefold(),
        }
        raw_matches_operator = raw_isp.casefold() in operator_ids
        if not raw_matches_operator and not raw_isp.isdecimal():
            raw_matches_operator = raw_name.casefold() == operator_name.casefold()
            # The explicit ISP property often stores only the provider label,
            # while the associated Operadora CI includes its legal name.
            if not raw_matches_operator and "(" not in raw_name:
                raw_matches_operator = raw_label.casefold() == operator_label.casefold()
    conflict = bool(operator and raw_isp and not raw_matches_operator)
    if conflict:
        raw_display = raw_label or raw_name
        operator_display = operator_label or operator_name
        return {
            "name": "", "full_name": "", "id": str(operator.source_config_item_id or ""),
            "number": str(operator.source_number or ""),
            "raw_id": raw_technical_id,
            "raw": raw_isp,
            "config_item_id": getattr(link, "source_config_item_id", ""),
            "confidence": "AMBIGUOUS", "conflict": True,
            "candidates": sorted({value for value in (raw_display if not raw_isp.isdecimal() else "", operator_display) if value}),
        }
    if operator:
        full_name = operator_name
        name = operator_label or full_name
        confidence = "HIGH"
    elif raw_isp and not raw_isp.isdecimal():
        full_name = raw_name
        name = raw_label or raw_name
        confidence = "HIGH"
    else:
        name = ""
        confidence = "NONE"
    return {
        "name": name,
        "full_name": full_name,
        "id": str(operator.source_config_item_id or "") if operator else raw_technical_id,
        "number": str(operator.source_number or "") if operator else "",
        "raw_id": raw_technical_id,
        "raw": raw_isp,
        "config_item_id": getattr(link, "source_config_item_id", ""),
        "confidence": confidence,
        "conflict": False,
        "candidates": [],
    }


@transaction.atomic
def enrich_competence(competence_id, *, user=None, batch_size=500, treatment_ids=None, include_direct=True, eligible_only=True, remote_lookup=False):
    """Resolve Claro PU evidence locally, with an explicit general-OTRS fallback."""
    competence = PenaltyCompetence.objects.select_for_update().get(pk=competence_id)
    rows_query = PenaltyTreatmentRow.objects.filter(source_row__competence=competence)
    if eligible_only:
        rows_query = eligible_treatments(rows_query)
    if treatment_ids is not None:
        rows_query = rows_query.filter(pk__in=treatment_ids)
    rows = list(rows_query.select_related("source_row", "ticket").prefetch_related(
        "ticket_relations__otrs_ticket__config_items__cmdb_link__operator",
    ).order_by("pk"))
    identifier_by_pk = {
        row.pk: _ticket_identifier(
            row.source_row.canonical_data,
            source_data=row.source_row.source_data,
            column_mapping=competence.column_mapping,
        )
        for row in rows
    }
    identifiers = {value for value in identifier_by_pk.values() if value}
    ticket_by_number = {}
    identifier_list = list(identifiers)
    for start in range(0, len(identifier_list), batch_size):
        tickets = OTRSTicket.objects.filter(ticket_number__in=identifier_list[start:start + batch_size]).prefetch_related(
            "config_items__cmdb_link__operator",
        )
        ticket_by_number.update((ticket.ticket_number, ticket) for ticket in tickets)

    # Excel preserves only 15 significant decimal digits. A PU whose suffix
    # beyond that precision is all zeroes may have been rounded; only its
    # 15-digit prefix is used to collect candidates, never to invent a digit.
    prefix_by_pk = {
        row.pk: identifier[:15]
        for row in rows
        if (identifier := identifier_by_pk[row.pk])
        and len(identifier) > 15
        and set(identifier[15:]) == {"0"}
    }
    prefix_list = sorted(set(prefix_by_pk.values()))
    prefix_candidates = {prefix: [] for prefix in prefix_list}
    for start in range(0, len(prefix_list), batch_size):
        prefix_query = Q(pk__in=[])
        for prefix in prefix_list[start:start + batch_size]:
            prefix_query |= Q(ticket_number__startswith=prefix)
        for ticket in OTRSTicket.objects.filter(prefix_query).prefetch_related(
            "config_items__cmdb_link__operator",
        ):
            ticket_by_number[ticket.ticket_number] = ticket
            for prefix in prefix_list[start:start + batch_size]:
                if ticket.ticket_number.startswith(prefix):
                    prefix_candidates[prefix].append(ticket)
    for candidates in prefix_candidates.values():
        candidates.sort(key=lambda ticket: ticket.ticket_number)

    prior_by_pk = {row.pk: dict(row.arion_values or {}).get("ticket_match", {}) for row in rows}
    cached_candidates_by_pk = {
        row.pk: [
            item for item in (row.arion_values or {}).get("ticket_candidates", [])
            if item.get("source") == "OTRS_GENERAL" and item.get("number")
        ]
        for row in rows
    }
    cached_remote_snapshots_by_pk = {
        row.pk: (row.arion_values or {}).get("ticket_snapshot")
        for row in rows
        if (row.arion_values or {}).get("ticket_snapshot", {}).get("source") == "OTRS_GENERAL"
        and (row.arion_values or {}).get("ticket_match", {}).get("selection_source") == "AUTOMATIC"
    }
    manual_local_by_pk = {}
    legacy_local_by_pk = {}
    manual_remote_by_pk = {}
    for row in rows:
        existing_relations = list(row.ticket_relations.all())
        primary = next((relation for relation in existing_relations if relation.is_primary), None)
        if primary and primary.source == PenaltyTreatmentTicket.Source.MANUAL:
            manual_local_by_pk[row.pk] = primary.otrs_ticket
        elif not primary and row.ticket_id:
            prior = prior_by_pk[row.pk]
            known_automatic = (
                prior.get("selection_source") == "AUTOMATIC"
                or prior.get("method") in {"EXACT_PU", "PU_EXACT", "PU_PREFIX_REC_LP", "REC_CI_LP", "PU_PREFIX_REC_CI", "PU_PREFIX_LP"}
            )
            if not known_automatic:
                manual_local_by_pk[row.pk] = row.ticket
                if not any(item.otrs_ticket_id == row.ticket_id for item in existing_relations):
                    legacy_local_by_pk[row.pk] = row.ticket
        prior = prior_by_pk[row.pk]
        if prior.get("selection_source") == "MANUAL":
            snapshot = (row.arion_values or {}).get("ticket_snapshot")
            if snapshot and snapshot.get("number"):
                manual_remote_by_pk[row.pk] = snapshot

    # For X/blank/invalid PUs (and a failed rounded-prefix lookup), search local
    # title and structured CI/CMDB fields by LP. Every candidate is then checked
    # with exact REC/CI and LP token comparisons; substring hits alone never link.
    evidence_rows = [
        row for row in rows
        if row.pk not in manual_local_by_pk and row.pk not in manual_remote_by_pk
        and not ticket_by_number.get(identifier_by_pk[row.pk])
        and not prefix_candidates.get(prefix_by_pk.get(row.pk, ""))
        and (row.source_row.canonical_data or {}).get("rec")
        and (row.source_row.canonical_data or {}).get("lp")
    ]
    evidence_query = Q(pk__in=[])
    evidence_row_ids = {row.pk for row in evidence_rows}
    for row in evidence_rows:
        lp = str((row.source_row.canonical_data or {}).get("lp") or "").strip()
        if not lp:
            continue
        evidence_query |= Q(title__icontains=lp)
        for field in ("source_number", "circuit", "designation", "operator_designation", "identification"):
            evidence_query |= Q(**{f"config_items__cmdb_link__{field}__icontains": lp})
    evidence_tickets = {}
    if evidence_rows:
        evidence_tickets = {
            ticket.ticket_number: ticket
            for ticket in OTRSTicket.objects.filter(evidence_query).distinct().prefetch_related(
                "config_items__cmdb_link__operator",
            )
        }
        ticket_by_number.update(evidence_tickets)

    local_candidates_by_pk = {}
    local_ranked_by_pk = {}
    unresolved_specs = []
    for row in rows:
        identifier = identifier_by_pk[row.pk]
        source = row.source_row.canonical_data or {}
        rec, lp = str(source.get("rec") or "").strip(), str(source.get("lp") or "").strip()
        exact = ticket_by_number.get(identifier) if identifier else None
        pool = {ticket.ticket_number: ticket for ticket in prefix_candidates.get(prefix_by_pk.get(row.pk, ""), [])}
        if row.pk in evidence_row_ids:
            for ticket in evidence_tickets.values():
                candidate_data = _ticket_candidate_data(ticket, rec=rec, lp=lp)
                if candidate_data["matches_rec"] or candidate_data["matches_lp"]:
                    pool.setdefault(ticket.ticket_number, ticket)
        snapshots = [_ticket_candidate_data(ticket, rec=rec, lp=lp) for ticket in pool.values()]
        prefix = prefix_by_pk.get(row.pk, "")
        ranked = [(candidate, *_candidate_rank(candidate, prefix=prefix)) for candidate in snapshots]
        best_rank = max((item[1] for item in ranked), default=0)
        local_candidates_by_pk[row.pk] = snapshots
        local_ranked_by_pk[row.pk] = [item for item in ranked if item[1] == best_rank] if best_rank else ranked
        has_manual = row.pk in manual_local_by_pk or row.pk in manual_remote_by_pk
        if not has_manual and not exact and (best_rank == 0 or not ranked) and (identifier or prefix or (rec and lp)):
            unresolved_specs.append({"treatment_id": row.pk, "identifier": identifier, "prefix": prefix, "rec": rec, "lp": lp})

    remote_by_number = {}
    remote_error = ""
    remote_query_succeeded = False
    remote_truncated = False
    if remote_lookup and unresolved_specs:
        try:
            remote_results = []
            for start in range(0, len(unresolved_specs), MAX_LOOKUP_SPECS):
                batch_results = lookup_general_otrs(unresolved_specs[start:start + MAX_LOOKUP_SPECS])
                remote_results.extend(batch_results)
                remote_truncated = remote_truncated or bool(getattr(batch_results, "truncated", False))
                if len(remote_results) >= MAX_REMOTE_CANDIDATES:
                    remote_results = remote_results[:MAX_REMOTE_CANDIDATES]
                    remote_truncated = True
                    break
            remote_query_succeeded = True
            remote_by_number = {
                item["number"]: item for item in remote_results
                if item.get("number")
            }
            if remote_truncated:
                remote_error = REMOTE_LOOKUP_LIMIT_ERROR
        except Exception:
            # External errors and configuration details are never logged or sent to the UI.
            remote_error = REMOTE_LOOKUP_ERROR
    unresolved_ids = {item["treatment_id"] for item in unresolved_specs}

    resolved_by_pk = {}
    auto_resolved_pks = set()
    remote_snapshots_by_pk = {}
    match_by_pk = {}
    candidates_by_pk = {}
    for row in rows:
        identifier = identifier_by_pk[row.pk]
        source = row.source_row.canonical_data or {}
        rec, lp = str(source.get("rec") or "").strip(), str(source.get("lp") or "").strip()
        exact = ticket_by_number.get(identifier) if identifier else None
        prefix = prefix_by_pk.get(row.pk, "")
        prior_match = prior_by_pk[row.pk]
        manual_local = manual_local_by_pk.get(row.pk)
        manual_remote = manual_remote_by_pk.get(row.pk)
        if manual_local:
            resolved_by_pk[row.pk] = manual_local.ticket_number
            match_by_pk[row.pk] = {
                **prior_match, "method": "MANUAL", "confidence": "USER_CONFIRMED",
                "pu_received": identifier, "prefix": prefix, "rec": rec, "lp": lp,
                "selected_ticket_number": manual_local.ticket_number, "selection_source": "MANUAL",
                "source": "LOCAL_MIRROR",
            }
            candidates_by_pk[row.pk] = []
            continue
        if manual_remote:
            remote_snapshots_by_pk[row.pk] = manual_remote
            match_by_pk[row.pk] = {
                **prior_match, "method": "MANUAL", "confidence": "USER_CONFIRMED",
                "pu_received": identifier, "prefix": prefix, "rec": rec, "lp": lp,
                "selected_ticket_number": manual_remote.get("number", ""), "selection_source": "MANUAL",
                "source": manual_remote.get("source", "OTRS_GENERAL"),
            }
            candidates_by_pk[row.pk] = []
            continue
        if exact:
            resolved_by_pk[row.pk] = exact.ticket_number
            auto_resolved_pks.add(row.pk)
            match_by_pk[row.pk] = {
                "method": "EXACT_PU", "confidence": "HIGH", "pu_received": identifier,
                "prefix": "", "rec": rec, "lp": lp, "selected_ticket_number": exact.ticket_number,
                "selection_source": "AUTOMATIC", "source": "LOCAL_MIRROR",
            }
            candidates_by_pk[row.pk] = []
            continue

        exact_remote = remote_by_number.get(identifier) if identifier else None
        if exact_remote:
            exact_candidate = _remote_candidate_data(exact_remote, rec=rec, lp=lp)
            match_by_pk[row.pk] = {
                "method": "EXACT_PU", "confidence": "HIGH", "pu_received": identifier,
                "prefix": prefix, "rec": rec, "lp": lp,
                "selected_ticket_number": exact_candidate["number"], "selection_source": "AUTOMATIC",
                "source": "OTRS_GENERAL",
            }
            remote_snapshots_by_pk[row.pk] = _snapshot_from_candidate(exact_candidate)
            candidates_by_pk[row.pk] = []
            continue

        cached_remote = cached_remote_snapshots_by_pk.get(row.pk)
        if cached_remote and not remote_query_succeeded:
            remote_snapshots_by_pk[row.pk] = cached_remote
            match_by_pk[row.pk] = prior_match
            candidates_by_pk[row.pk] = cached_candidates_by_pk.get(row.pk, [])
            continue

        ranked = list(local_ranked_by_pk.get(row.pk, []))
        candidates = list(local_candidates_by_pk.get(row.pk, []))
        reuse_cached_candidates = False
        if not any(item[1] > 0 for item in ranked) and remote_query_succeeded:
            remote_ranked = []
            for candidate in remote_by_number.values():
                item = _remote_candidate_data(candidate, rec=rec, lp=lp)
                rank, method, confidence = _candidate_rank(item, prefix=prefix)
                if rank:
                    remote_ranked.append((item, rank, method, confidence))
                elif prefix and candidate.get("number", "").startswith(prefix):
                    remote_ranked.append((item, 0, "", ""))
            if remote_ranked:
                best_remote = max(item[1] for item in remote_ranked)
                ranked = [item for item in remote_ranked if item[1] == best_remote]
                candidates = [item[0] for item in ranked]
        if not candidates and not any(item[1] > 0 for item in ranked) and (not remote_query_succeeded or remote_error):
            candidates = cached_candidates_by_pk.get(row.pk, [])
            reuse_cached_candidates = bool(candidates)
            if reuse_cached_candidates:
                ranked = []

        best_rank = max((item[1] for item in ranked), default=0)
        best_candidates = [item for item in ranked if item[1] == best_rank] if best_rank else ranked
        truncated_remote_candidates = remote_truncated and any(item.get("source") == "OTRS_GENERAL" for item in candidates)
        if not reuse_cached_candidates and not truncated_remote_candidates and best_rank > 0 and len(best_candidates) == 1:
            candidate, _, method, confidence = best_candidates[0]
            match_by_pk[row.pk] = {
                "method": method, "confidence": confidence, "pu_received": identifier,
                "prefix": prefix, "rec": rec, "lp": lp,
                "selected_ticket_number": candidate["number"], "selection_source": "AUTOMATIC",
                "source": candidate.get("source", "LOCAL_MIRROR"),
            }
            if candidate.get("source") == "OTRS_GENERAL":
                remote_snapshots_by_pk[row.pk] = _snapshot_from_candidate(candidate)
            else:
                resolved_by_pk[row.pk] = candidate["number"]
                auto_resolved_pks.add(row.pk)
            candidates_by_pk[row.pk] = []
        elif candidates:
            match_by_pk[row.pk] = {
                "method": "PU_PREFIX_REC_LP" if prefix else "REC_CI_LP",
                "confidence": "AMBIGUOUS", "pu_received": identifier,
                "prefix": prefix, "rec": rec, "lp": lp,
                "candidate_numbers": [item[0]["number"] for item in best_candidates] if best_candidates else [item["number"] for item in candidates],
                "selected_ticket_number": "", "selection_source": "",
            }
            candidates_by_pk[row.pk] = [item[0] for item in best_candidates] if best_candidates else candidates
        else:
            match_by_pk[row.pk] = {
                "method": "PU_PREFIX_REC_LP" if prefix else "REC_CI_LP",
                "confidence": "NONE", "pu_received": identifier,
                "prefix": prefix, "rec": rec, "lp": lp,
                "selected_ticket_number": "", "selection_source": "",
            }
            candidates_by_pk[row.pk] = []

    changed, found, missing, ambiguous, ticket_ambiguous, isp_count = [], 0, 0, 0, 0, 0
    proposed_relations = []
    for row in rows:
        identifier = identifier_by_pk[row.pk]
        row.ticket_identifier = identifier
        row.field_origins = dict(row.field_origins or {})
        if identifier:
            row.field_origins["ticket_identifier"] = PenaltyTreatmentRow.Origin.IMPORTED
        target_number = resolved_by_pk.get(row.pk, "")
        ticket = ticket_by_number.get(target_number) if target_number else None
        if row.pk in legacy_local_by_pk:
            proposed_relations.append(PenaltyTreatmentTicket(
                treatment=row, otrs_ticket=legacy_local_by_pk[row.pk],
                source=PenaltyTreatmentTicket.Source.LEGACY, is_primary=True,
            ))
        elif ticket and include_direct and row.pk in auto_resolved_pks:
            proposed_relations.append(PenaltyTreatmentTicket(
                treatment=row, otrs_ticket=ticket, source=PenaltyTreatmentTicket.Source.AUTOMATIC,
                is_primary=True,
            ))
        row.arion_values = dict(row.arion_values or {})
        row.arion_values["ticket_match"] = match_by_pk[row.pk]
        row.arion_values["ticket_candidates"] = candidates_by_pk[row.pk]
        if remote_error and row.pk in unresolved_ids:
            row.arion_values["ticket_lookup_error"] = remote_error
        elif remote_lookup:
            row.arion_values.pop("ticket_lookup_error", None)
        remote_snapshot = remote_snapshots_by_pk.get(row.pk)
        if remote_snapshot:
            row.arion_values["ticket_snapshot"] = remote_snapshot
        elif ticket:
            row.arion_values.pop("ticket_snapshot", None)
        if match_by_pk[row.pk].get("confidence") == "AMBIGUOUS":
            row.evidence_status = PenaltyTreatmentRow.EvidenceStatus.AMBIGUOUS
            ticket_ambiguous += 1
        elif ticket or remote_snapshot:
            row.evidence_status = PenaltyTreatmentRow.EvidenceStatus.FOUND
        elif identifier or (row.source_row.canonical_data or {}).get("rec") and (row.source_row.canonical_data or {}).get("lp"):
            row.evidence_status = PenaltyTreatmentRow.EvidenceStatus.NOT_FOUND
        else:
            row.evidence_status = PenaltyTreatmentRow.EvidenceStatus.NOT_CHECKED
        row.version += 1
        row.updated_at = timezone.now()
        changed.append(row)

    if proposed_relations:
        PenaltyTreatmentTicket.objects.bulk_create(proposed_relations, ignore_conflicts=True, batch_size=batch_size)

    relations = list(
        PenaltyTreatmentTicket.objects.filter(treatment_id__in=[row.pk for row in rows])
        .select_related("otrs_ticket")
        .prefetch_related("otrs_ticket__config_items__cmdb_link")
        .order_by("treatment_id", "-is_primary", "created_at", "pk")
    )
    relations_by_row = {}
    for relation in relations:
        relations_by_row.setdefault(relation.treatment_id, []).append(relation)

    relation_updates = []
    for row in rows:
        linked = relations_by_row.get(row.pk, [])
        target_number = resolved_by_pk.get(row.pk, "")
        remote_snapshot = remote_snapshots_by_pk.get(row.pk)
        direct = next((item for item in linked if item.otrs_ticket.ticket_number == target_number), None)
        primary = None if remote_snapshot else (direct or next((item for item in linked if item.is_primary), None))
        if primary is None and linked and not remote_snapshot:
            primary = linked[0]
        for item in linked:
            should_be_primary = item.pk == primary.pk if primary else False
            direct_ticket = item.otrs_ticket.ticket_number == target_number
            should_be_automatic = direct_ticket and row.pk in auto_resolved_pks
            changed_relation = False
            if item.is_primary != should_be_primary:
                item.is_primary = should_be_primary
                changed_relation = True
            if should_be_automatic and item.source != PenaltyTreatmentTicket.Source.AUTOMATIC:
                item.source = PenaltyTreatmentTicket.Source.AUTOMATIC
                changed_relation = True
            if changed_relation:
                relation_updates.append(item)
        ticket = primary.otrs_ticket if primary else None
        row.ticket = ticket
        row.arion_values = dict(row.arion_values or {})
        if ticket or remote_snapshot:
            row.evidence_status = PenaltyTreatmentRow.EvidenceStatus.FOUND
            row.field_origins["ticket"] = PenaltyTreatmentRow.Origin.OTRS
            found += 1
            ticket_snapshot = remote_snapshot or _snapshot_from_ticket(ticket)
            opened_at = _snapshot_datetime(ticket_snapshot.get("created_at"))
            resolved_at = _snapshot_datetime(ticket_snapshot.get("resolved_at"))
            if opened_at:
                row.arion_opening_auto_at = opened_at
                if _auto_allowed(row.arion_opening_at, row.arion_opening_origin):
                    row.arion_opening_at = opened_at
                    row.arion_opening_origin = PenaltyTreatmentRow.Origin.OTRS
            if resolved_at:
                row.arion_fetec_auto_at = resolved_at
                if _auto_allowed(row.arion_fetec_at, row.arion_fetec_origin):
                    row.arion_fetec_at = resolved_at
                    row.arion_fetec_origin = PenaltyTreatmentRow.Origin.OTRS
            row.arion_values.update({
                "otrs_status": ticket_snapshot.get("business_status") or ticket_snapshot.get("state", ""),
                "otrs_queue": ticket_snapshot.get("queue", ""),
                "otrs_responsible_user_id": ticket_snapshot.get("responsible"),
                "otrs_ticket_number": ticket_snapshot.get("number", ""),
                "otrs_title": ticket_snapshot.get("title", ""),
                "otrs_opened_at": ticket_snapshot.get("created_at"),
                "otrs_resolved_at": ticket_snapshot.get("resolved_at"),
                "otrs_closing_code": ticket_snapshot.get("closing_code", ""),
            })
            if remote_snapshot:
                row.arion_values["ticket_snapshot"] = ticket_snapshot
            for evidence_field in ("otrs_status", "otrs_queue", "otrs_responsible_user_id", "otrs_ticket_number", "otrs_title", "otrs_opened_at", "otrs_resolved_at", "otrs_closing_code"):
                row.field_origins[evidence_field] = PenaltyTreatmentRow.Origin.OTRS
        elif linked:
            row.evidence_status = PenaltyTreatmentRow.EvidenceStatus.FOUND
            found += 1
        elif (identifier_by_pk[row.pk] or ((row.source_row.canonical_data or {}).get("rec") and (row.source_row.canonical_data or {}).get("lp"))) and row.evidence_status != PenaltyTreatmentRow.EvidenceStatus.AMBIGUOUS:
            missing += 1

        ticket_summaries = []
        if remote_snapshot:
            ticket_summaries.append({
                "number": remote_snapshot.get("number", ""), "title": remote_snapshot.get("title", ""),
                "status": remote_snapshot.get("business_status") or remote_snapshot.get("state", ""),
                "source_state": remote_snapshot.get("source_state") or remote_snapshot.get("state", ""),
                "queue": remote_snapshot.get("queue", ""), "responsible_user_id": remote_snapshot.get("responsible"),
                "opened_at": remote_snapshot.get("created_at"), "resolved_at": remote_snapshot.get("resolved_at"),
                "source": remote_snapshot.get("source", "OTRS_GENERAL"),
            })
        isp_values = set()
        isp_sources = {}
        unresolved_isp_ids = set()
        unresolved_isp_sources = []
        ambiguous_isp_candidates = set()
        procedence_values = set()
        for linked_relation in linked:
            linked_ticket = linked_relation.otrs_ticket
            ticket_summaries.append({
                "number": linked_ticket.ticket_number, "title": linked_ticket.title,
                "status": normalize_business_status(linked_ticket.business_status),
                "source_state": linked_ticket.external_state_name,
                "queue": linked_ticket.queue_name,
                "responsible_user_id": linked_ticket.responsible_user_id,
                "opened_at": linked_ticket.source_created_at.isoformat() if linked_ticket.source_created_at else None,
                "resolved_at": linked_ticket.resolved_at.isoformat() if linked_ticket.resolved_at else None,
                "source": linked_relation.source,
            })
            if linked_ticket.responsibility_normalized in ("PROCEDENTE", "IMPROCEDENTE"):
                procedence_values.add(linked_ticket.responsibility_normalized)
            for config_item in linked_ticket.config_items.all():
                link = config_item.cmdb_link
                if config_item.is_present_in_source and link and link.is_present_in_source:
                    source_info = cmdb_isp_identity(link)
                    if source_info["name"]:
                        isp_values.add(source_info["name"])
                        isp_sources.setdefault(source_info["name"], []).append(source_info)
                    elif source_info["conflict"]:
                        ambiguous_isp_candidates.update(source_info["candidates"])
                        unresolved_isp_sources.append(source_info)
                        unresolved_isp_ids.update(value for value in (source_info["id"], source_info["raw_id"]) if value)
                    elif source_info["id"]:
                        unresolved_isp_ids.add(source_info["id"])
                        unresolved_isp_sources.append(source_info)
        if unresolved_isp_ids:
            row.arion_values["cmdb_isp_unresolved_ids"] = sorted(unresolved_isp_ids)
            row.arion_values["cmdb_isp_unresolved_id"] = next(iter(unresolved_isp_ids)) if len(unresolved_isp_ids) == 1 else ""
        else:
            row.arion_values.pop("cmdb_isp_unresolved_ids", None)
            row.arion_values.pop("cmdb_isp_unresolved_id", None)
        if len(isp_values) == 1 and not unresolved_isp_ids:
            resolved_isp = next(iter(isp_values))
            row.arion_values["cmdb_isp_sources"] = isp_sources.get(resolved_isp, [])
            row.arion_values.pop("cmdb_isp_candidates", None)
            if _auto_allowed(row.isp, row.field_origins.get("isp", "")):
                row.isp = resolved_isp
                row.field_origins["isp"] = PenaltyTreatmentRow.Origin.CMDB
                isp_count += 1
        elif len(isp_values) > 1 or unresolved_isp_ids or ambiguous_isp_candidates:
            if row.field_origins.get("isp") == PenaltyTreatmentRow.Origin.CMDB:
                row.isp = ""
                row.field_origins.pop("isp", None)
            candidates = set(isp_values)
            candidates.update(ambiguous_isp_candidates)
            if unresolved_isp_ids:
                candidates.add("ISP não identificado")
            row.arion_values["cmdb_isp_candidates"] = sorted(candidates)
            row.arion_values["cmdb_isp_sources"] = [source for label in sorted(isp_sources) for source in isp_sources[label]] + unresolved_isp_sources
            ambiguous += 1
        else:
            row.arion_values.pop("cmdb_isp_candidates", None)
            row.arion_values.pop("cmdb_isp_sources", None)
            if row.field_origins.get("isp") == PenaltyTreatmentRow.Origin.CMDB:
                row.isp = ""
                row.field_origins.pop("isp", None)

        if row.procedence_origin != PenaltyTreatmentRow.Origin.MANUAL:
            if len(procedence_values) == 1:
                row.procedence = (
                    PenaltyTreatmentRow.Procedence.PROCEDENT
                    if next(iter(procedence_values)) == "PROCEDENTE"
                    else PenaltyTreatmentRow.Procedence.IMPROCEDENT
                )
                row.procedence_origin = PenaltyTreatmentRow.Origin.OTRS
                row.arion_values.pop("otrs_procedence_candidates", None)
            elif len(procedence_values) > 1:
                row.procedence = PenaltyTreatmentRow.Procedence.UNSPECIFIED
                row.procedence_origin = PenaltyTreatmentRow.Origin.OTRS
                row.arion_values["otrs_procedence_candidates"] = sorted(procedence_values)
            elif row.procedence_origin == PenaltyTreatmentRow.Origin.OTRS:
                row.procedence = PenaltyTreatmentRow.Procedence.UNSPECIFIED
                row.procedence_origin = PenaltyTreatmentRow.Origin.UNSPECIFIED
                row.arion_values.pop("otrs_procedence_candidates", None)
        if not linked and not remote_snapshot:
            for field, attribute in (("arion_opening_at", "arion_opening_origin"), ("arion_fetec_at", "arion_fetec_origin")):
                if getattr(row, attribute) == PenaltyTreatmentRow.Origin.OTRS:
                    setattr(row, field, None)
                    setattr(row, attribute, PenaltyTreatmentRow.Origin.MANUAL)
            if row.arion_opening_origin == PenaltyTreatmentRow.Origin.MANUAL and not row.arion_opening_at:
                row.arion_opening_auto_at = None
            if row.arion_fetec_origin == PenaltyTreatmentRow.Origin.MANUAL and not row.arion_fetec_at:
                row.arion_fetec_auto_at = None
            for key in ("otrs_status", "otrs_queue", "otrs_responsible_user_id", "otrs_ticket_number", "otrs_title", "otrs_opened_at", "otrs_resolved_at", "otrs_closing_code", "otrs_tickets"):
                row.arion_values.pop(key, None)
            row.field_origins.pop("ticket", None)
        row.arion_values["otrs_tickets"] = ticket_summaries
        if not remote_snapshot and not linked:
            row.arion_values.pop("ticket_snapshot", None)

    if relation_updates:
        PenaltyTreatmentTicket.objects.bulk_update(relation_updates, ("is_primary", "source"), batch_size=batch_size)
    fields = ("ticket_identifier", "ticket", "evidence_status", "arion_opening_auto_at", "arion_opening_at", "arion_opening_origin",
              "arion_fetec_auto_at", "arion_fetec_at", "arion_fetec_origin", "isp", "field_origins", "arion_values", "procedence", "procedence_origin", "version", "updated_at")
    for start in range(0, len(changed), batch_size):
        PenaltyTreatmentRow.objects.bulk_update(changed[start:start + batch_size], fields, batch_size=batch_size)
    from .calculations import calculate_treatment
    previous_totals = {row.pk: row.total_arion for row in changed}
    financial_changes = 0
    # Recalculate in bounded chunks. Pauses and overrides are prefetched, avoiding N+1 reads.
    for start in range(0, len(changed), batch_size):
        ids = [item.pk for item in changed[start:start + batch_size]]
        recalc_rows = PenaltyTreatmentRow.objects.filter(pk__in=ids).select_related("source_row__competence").prefetch_related("pauses", "overrides")
        for row in recalc_rows.iterator(chunk_size=batch_size):
            calculate_treatment(row)
            if record_financial_change(
                treatment=row, actor=user, previous_total=previous_totals.get(row.pk), new_total=row.total_arion,
                reason="Recalculado após enriquecimento dos mirrors locais OTRS/CMDB.", source="ENRICHMENT",
            ):
                financial_changes += 1
    remote_found = sum(snapshot.get("source") == "OTRS_GENERAL" for snapshot in remote_snapshots_by_pk.values())
    return {"processed": len(rows), "found": found, "not_found": missing, "ambiguous": ambiguous,
            "ticket_ambiguous": ticket_ambiguous, "remote_found": remote_found,
            "remote_unavailable": bool(remote_error), "remote_message": remote_error,
            "isp_found": isp_count, "financial_changes": financial_changes}


def date_divergences(treatment):
    source = treatment.source_row.canonical_data or {}
    result = []
    for claro_key, arion_value, label in (
        ("opening", treatment.arion_opening_at, "abertura"),
        ("fetec", treatment.arion_fetec_at, "FETEC"),
    ):
        claro_value = source.get(claro_key)
        if claro_value and arion_value and str(claro_value)[:16] != arion_value.strftime("%Y-%m-%dT%H:%M"):
            result.append(label)
    return result


def treatment_export_dataset(competence, *, scope="eligible", decision=None):
    rows = PenaltyTreatmentRow.objects.filter(source_row__competence=competence).select_related("source_row", "ticket").prefetch_related("pauses", "overrides", "ticket_relations__otrs_ticket").order_by("source_row__source_row_number")
    if scope == "eligible":
        rows = claro_treatment_candidates(rows)
    elif scope == "any":
        rows = eligible_treatments(rows)
    elif scope == "treated":
        rows = rows.filter(decision__in=TERMINAL_DECISIONS)
    elif scope == "divergences":
        rows = divergent_treatments(rows)
    elif scope == "invalid":
        rows = rows.filter(decision=PenaltyTreatmentRow.Decision.INVALID)
    elif scope != "all":
        raise ValueError("Escopo de exportação inválido.")
    if decision in PenaltyTreatmentRow.Decision.values:
        rows = rows.filter(decision=decision)
    for row in rows.iterator(chunk_size=500):
        source = row.source_row.canonical_data or {}
        related_ticket_numbers = [item.otrs_ticket.ticket_number for item in row.ticket_relations.all()]
        snapshot_ticket_number = (row.arion_values or {}).get("ticket_snapshot", {}).get("number")
        if snapshot_ticket_number and snapshot_ticket_number not in related_ticket_numbers:
            related_ticket_numbers.append(snapshot_ticket_number)
        isp_internal_id = (row.arion_values or {}).get("cmdb_isp_unresolved_id", "")
        for isp_source in (row.arion_values or {}).get("cmdb_isp_sources", []):
            if isp_source.get("id"):
                isp_internal_id = isp_source["id"]
                break
        isp_label = row.isp if row.isp and not row.isp.isdecimal() else None
        if not isp_internal_id and row.isp and row.isp.isdecimal():
            isp_internal_id = row.isp
        yield {
            "REC": source.get("rec"), "Designação": source.get("designation"), "Cliente": source.get("client"),
            "UF": source.get("uf"), "LP": source.get("lp"), "Fornecedor": source.get("provider"),
            "Contrato": source.get("contract"), "Ticket / PU Fornecedor": row.ticket_identifier or source.get("provider_unit_price"),
            "Tickets ARION relacionados": ", ".join(related_ticket_numbers),
            "Banda": source.get("bandwidth"), "Abertura Claro": source.get("opening"), "Abertura ARION": row.arion_opening_at,
            "Fetec Claro": source.get("fetec"), "Fetec ARION": row.arion_fetec_at,
            "Tempo Claro": source.get("duration"), "TMP ARION calculado": row.tmp_gross_minutes,
            "TMP ARION efetivo": row.tmp_effective_minutes,
            "EV Claro": source.get("events"), "EV ARION calculado": row.ev_calculated, "EV ARION efetivo": row.ev_effective,
            "Vlr Mensal": source.get("monthly_value"), "Inter Claro": source.get("interconnection_fine"),
            "Inter ARION": row.inter_effective, "Indisp Claro": source.get("unavailability_fine"),
            "Indisp ARION": row.unavailability_effective, "Penal Claro": source.get("penalty_fine"),
            "Penal ARION calculado": row.penalty_arion, "Penal ARION efetivo": row.penalty_effective,
            "Qtd/Horas": source.get("hours_quantity"), "ISP": isp_label,
            "ISP ID interno CMDB": isp_internal_id or None,
            "Causa Raiz": row.root_cause or None, "Observação": row.observation or None,
            "Procedência": row.get_procedence_display(),
            "Valor cobrado Claro": row.total_claro, "Valor calculado ARION": row.total_calculated_arion,
            "Valor efetivo ARION": row.total_arion,
            "Valor devido ARION": row.amount_due, "Valor não cobrado": row.amount_not_charged,
            "Total Geral ARION": row.total_arion, "Valor contestado": row.amount_contested,
            "Redução %": row.reduction_percent, "Decisão": row.get_decision_display(), "Status": row.get_status_display(),
        }
