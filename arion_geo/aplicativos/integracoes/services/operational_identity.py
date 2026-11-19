"""Central, deterministic, auditable operational identity service for CMDB links and OTRS tickets.

Priority order (without mutating official CMDB/OTRS source fields):
1. Confirmed manual override (OperationalLinkIdentity.is_manual_override=True)
2. Explicit and reliable CMDB classification (including corporate aliases & internal ARION/POP)
3. Direct official OTRS ticket relations (OTRSTicketConfigItem)
4. Historical ticket consensus by unique circuit designation (3+ distinct tickets, conflict-free)
5. Existing canonical aliases and dictionaries (BusinessIdentityRules / project_names)
6. Suggestions only (no automatic promotion) when evidence is partial (<3 indirect tickets)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.models import (
    CMDBLinkMirror,
    OTRSTicket,
    OTRSTicketConfigItem,
    OperationalIdentityRevision,
    OperationalLinkIdentity,
)

from .link_business_identity import (
    BUSINESS_ID_PATTERN,
    BusinessIdentityRules,
    LinkBusinessIdentity,
    LinkBusinessIdentityService,
    comparison_key,
    normalized_words,
)
from .ticket_business_identity import TicketBusinessIdentityService

HISTORY_CONSENSUS_MIN_TICKETS = 3
MAJOR_OPERATORS = set(BusinessIdentityRules.MAJOR_OPERATORS)

MIRROR_IDENTITY_FIELDS = (
    "contracting_client_raw",
    "contracting_client",
    "business_project",
    "business_account",
    "business_id",
    "business_identity_confidence",
    "business_identity_source",
)


@dataclass(frozen=True)
class TicketEvidenceRow:
    ticket_id: int
    ticket_number: str
    title: str
    customer_id: str
    created_at_iso: str
    relation_kind: str  # "DIRECT_CI" | "HISTORY_DESIGNATION"
    operator: str
    operator_raw: str
    project: str
    final_client: str
    business_id: str
    source: str
    is_generic_project: bool


def _ticket_date_iso(ticket) -> str:
    dt = getattr(ticket, "source_created_at", None)
    return dt.date().isoformat() if dt else ""


def _extract_ticket_evidence(ticket, relation_kind: str) -> TicketEvidenceRow:
    parsed = TicketBusinessIdentityService.parse(
        getattr(ticket, "title", "") or "",
        customer_id=getattr(ticket, "customer_id", "") or "",
    )
    return TicketEvidenceRow(
        ticket_id=getattr(ticket, "pk", 0) or 0,
        ticket_number=getattr(ticket, "ticket_number", "") or "",
        title=getattr(ticket, "title", "") or "",
        customer_id=getattr(ticket, "customer_id", "") or "",
        created_at_iso=_ticket_date_iso(ticket),
        relation_kind=relation_kind,
        operator=parsed.client,
        operator_raw=parsed.client_raw,
        project=parsed.project,
        final_client=parsed.final_client,
        business_id=parsed.business_id,
        source=parsed.source,
        is_generic_project=parsed.is_generic_project,
    )


def _extract_link_business_id(link) -> str:
    for raw in (link.designation, link.name, link.circuit, link.operator_designation, link.identification):
        if not raw:
            continue
        match = BUSINESS_ID_PATTERN.search(str(raw))
        if match:
            return match.group(0).upper()
    return ""


def _summarize_ticket_evidences(evidences: list[TicketEvidenceRow]):
    """Aggregate independent ticket evidences by distinct ticket_id."""
    by_ticket = {}
    for row in evidences:
        if row.ticket_id not in by_ticket or row.relation_kind == "DIRECT_CI":
            by_ticket[row.ticket_id] = row
    unique_rows = list(by_ticket.values())

    op_counts = Counter()
    op_raws = {}
    op_dates = defaultdict(set)
    proj_counts = Counter()
    proj_dates = defaultdict(set)
    final_client_counts = Counter()

    for row in unique_rows:
        if row.operator:
            op_counts[row.operator] += 1
            op_raws.setdefault(row.operator, row.operator_raw or row.operator)
            if row.created_at_iso:
                op_dates[row.operator].add(row.created_at_iso)
        if row.project and not row.is_generic_project:
            proj_counts[row.project] += 1
            if row.created_at_iso:
                proj_dates[row.project].add(row.created_at_iso)
        if row.final_client and not row.is_generic_project:
            final_client_counts[row.final_client] += 1

    return {
        "distinct_tickets": len(unique_rows),
        "operators": dict(op_counts),
        "operator_raws": op_raws,
        "operator_distinct_dates": {k: len(v) for k, v in op_dates.items()},
        "projects": dict(proj_counts),
        "project_distinct_dates": {k: len(v) for k, v in proj_dates.items()},
        "final_clients": dict(final_client_counts),
        "sample_ticket_numbers": [row.ticket_number for row in unique_rows[:8] if row.ticket_number],
    }


def _compute_issue_type(*, contracting_client: str, business_project: str, final_client: str,
                        is_internal_arion: bool, has_conflict: bool, is_manual_override: bool,
                        review_status: str) -> str:
    if is_internal_arion:
        return OperationalLinkIdentity.IssueType.CONFLICT if has_conflict else OperationalLinkIdentity.IssueType.NONE
    if review_status == OperationalLinkIdentity.ReviewStatus.CONFIRMED_UNKNOWN:
        return OperationalLinkIdentity.IssueType.CONFLICT if has_conflict else OperationalLinkIdentity.IssueType.NONE
    if has_conflict:
        return OperationalLinkIdentity.IssueType.CONFLICT
    has_op = bool((contracting_client or "").strip())
    has_proj_or_client = bool((business_project or "").strip() or (final_client or "").strip())
    if not has_op and not has_proj_or_client:
        return OperationalLinkIdentity.IssueType.MISSING_BOTH
    if not has_op:
        return OperationalLinkIdentity.IssueType.MISSING_OPERATOR
    if not has_proj_or_client:
        return OperationalLinkIdentity.IssueType.MISSING_CLIENT_PROJECT
    if contracting_client not in MAJOR_OPERATORS and contracting_client != BusinessIdentityRules.INTERNAL_OPERATOR_LABEL and not is_manual_override:
        return OperationalLinkIdentity.IssueType.OTHER_VERIFY
    return OperationalLinkIdentity.IssueType.NONE


def evaluate_link_identity(
    link: CMDBLinkMirror,
    *,
    direct_tickets: Iterable[OTRSTicket] = (),
    history_tickets: Iterable[OTRSTicket] = (),
    existing_identity: OperationalLinkIdentity | None = None,
) -> dict:
    """Deterministically evaluate a link from raw CMDB + OTRS evidence only.

    Never reads previous derived fields on CMDBLinkMirror as evidence.
    """
    cmdb: LinkBusinessIdentity = LinkBusinessIdentityService.parse(
        link.name or "",
        designation=link.designation or "",
    )
    business_id = cmdb.business_id or _extract_link_business_id(link)

    direct_rows = [_extract_ticket_evidence(t, "DIRECT_CI") for t in direct_tickets]
    direct_ids = {row.ticket_id for row in direct_rows}
    history_rows = [
        _extract_ticket_evidence(t, "HISTORY_DESIGNATION")
        for t in history_tickets
        if getattr(t, "pk", 0) not in direct_ids
    ]

    direct_summary = _summarize_ticket_evidences(direct_rows)
    history_summary = _summarize_ticket_evidences(history_rows)
    all_summary = _summarize_ticket_evidences(direct_rows + history_rows)

    # Start from raw CMDB evaluation
    operator = cmdb.contracting_client
    operator_raw = cmdb.contracting_client_raw
    project = cmdb.business_project
    final_client = cmdb.final_client
    account = cmdb.business_account
    is_internal_arion = cmdb.is_internal_arion
    internal_detail = cmdb.internal_detail
    source = cmdb.source
    confidence = cmdb.confidence

    suggested_operator = ""
    suggested_project = ""
    suggested_final_client = ""
    suggested_is_internal_arion = False
    suggested_confidence = ""
    suggested_source = ""

    conflict_reasons = []

    # Check conflicts between CMDB and direct OTRS tickets
    direct_ops = direct_summary["operators"]
    if len(direct_ops) > 1:
        summary_str = ", ".join(f"{op} ({count})" for op, count in sorted(direct_ops.items()))
        conflict_reasons.append(f"Tickets OTRS vinculados apresentam operadoras divergentes: {summary_str}.")
    elif len(direct_ops) == 1:
        only_op, count = next(iter(direct_ops.items()))
        if operator and not is_internal_arion and operator != only_op:
            conflict_reasons.append(
                f"CMDB indica operadora {operator}, enquanto {count} ticket(s) OTRS vinculado(s) indicam {only_op}."
            )
        elif is_internal_arion and only_op != BusinessIdentityRules.INTERNAL_OPERATOR_LABEL:
            conflict_reasons.append(
                f"CMDB indica infraestrutura interna ({internal_detail}), mas {count} ticket(s) OTRS vinculado(s) mencionam {only_op}."
            )
        elif not operator:
            # Direct official OTRS relation with zero conflict resolves missing operator!
            operator = only_op
            operator_raw = direct_summary["operator_raws"].get(only_op, only_op)
            source = "OTRS_DIRECT_RELATION"
            confidence = "HIGH" if (count >= 2 or project or business_id) else "MEDIUM"

    # Direct OTRS project evidence (never lets generic DIVERSOS override a specific CMDB client/project)
    direct_projs = direct_summary["projects"]
    if len(direct_projs) > 1:
        proj_str = ", ".join(f"{p} ({c})" for p, c in sorted(direct_projs.items()))
        if not project or BusinessIdentityRules.is_generic_project(project):
            conflict_reasons.append(f"Tickets OTRS vinculados apresentam projetos divergentes: {proj_str}.")
    elif len(direct_projs) == 1:
        only_proj, count = next(iter(direct_projs.items()))
        if not project or BusinessIdentityRules.is_generic_project(project):
            # Only fill project from OTRS when CMDB has no specific project/corporate client
            if not final_client or BusinessIdentityRules.is_generic_project(final_client):
                project = only_proj
                if not final_client:
                    fc_counts = direct_summary["final_clients"]
                    final_client = max(fc_counts, key=fc_counts.get) if fc_counts else only_proj
                if source in {"UNKNOWN", "CMDB_CORPORATE_ALIAS"}:
                    source = "OTRS_DIRECT_RELATION"
                if operator and project:
                    confidence = "HIGH" if count >= 2 or business_id else "MEDIUM"
        elif project and not is_internal_arion and comparison_key(project) != comparison_key(only_proj):
            # E.g. CMDB says Carrefour (Banco CSF), ticket says something genuinely different (non-Diversos)
            conflict_reasons.append(
                f"CMDB indica projeto {project}, enquanto {count} ticket(s) OTRS vinculado(s) indicam {only_proj}."
            )

    # Step 4: Extended historical lookup by unique circuit designation (when still unresolved)
    if not is_internal_arion and (not operator or not project):
        hist_ops = history_summary["operators"]
        hist_projs = history_summary["projects"]
        if not operator and len(hist_ops) > 1:
            summary_str = ", ".join(f"{op} ({count})" for op, count in sorted(hist_ops.items()))
            conflict_reasons.append(f"Histórico por designação apresenta operadoras divergentes: {summary_str}.")
        elif not operator and len(hist_ops) == 1 and not conflict_reasons:
            only_op, count = next(iter(hist_ops.items()))
            distinct_dates = history_summary["operator_distinct_dates"].get(only_op, 0)
            if count >= HISTORY_CONSENSUS_MIN_TICKETS:
                operator = only_op
                operator_raw = history_summary["operator_raws"].get(only_op, only_op)
                source = "OTRS_HISTORY_CONSENSUS"
                confidence = "HIGH" if distinct_dates >= 2 else "MEDIUM"
            else:
                # Suggest only; do not auto-promote under 3 indirect historical tickets.
                suggested_operator = only_op
                suggested_source = "OTRS_HISTORY_CANDIDATE"
                suggested_confidence = "MEDIUM" if count == 2 else "LOW"

        if (not project or BusinessIdentityRules.is_generic_project(project)) and len(hist_projs) == 1 and not conflict_reasons:
            only_proj, count = next(iter(hist_projs.items()))
            if not final_client or BusinessIdentityRules.is_generic_project(final_client):
                if count >= HISTORY_CONSENSUS_MIN_TICKETS:
                    project = only_proj
                    if not final_client:
                        fc_counts = history_summary["final_clients"]
                        final_client = max(fc_counts, key=fc_counts.get) if fc_counts else only_proj
                    if source == "UNKNOWN":
                        source = "OTRS_HISTORY_CONSENSUS"
                    if operator and project and confidence == "LOW":
                        confidence = "MEDIUM"
                else:
                    suggested_project = only_proj
                    fc_counts = history_summary["final_clients"]
                    suggested_final_client = max(fc_counts, key=fc_counts.get) if fc_counts else only_proj
                    if not suggested_source:
                        suggested_source = "OTRS_HISTORY_CANDIDATE"
                        suggested_confidence = "MEDIUM" if count == 2 else "LOW"

    # Populate suggestion fields when unresolved or conflicted and a strong candidate exists
    if conflict_reasons and not suggested_operator:
        if len(all_summary["operators"]) == 1:
            suggested_operator = next(iter(all_summary["operators"]))
            suggested_source = "OTRS_EVIDENCE"
            suggested_confidence = "MEDIUM"
    if not suggested_operator and not operator and cmdb.business_project:
        rule_op = BusinessIdentityRules.project_rule_client(cmdb.business_project)
        if rule_op:
            suggested_operator = rule_op
            suggested_source = "CMDB_PROJECT_RULE"
            suggested_confidence = "MEDIUM"

    # Keep account aligned when final_client adds specificity beyond project (e.g. Banco CSF -> Carrefour)
    if final_client and project and comparison_key(final_client) != comparison_key(project) and not account:
        account = final_client

    has_conflict = bool(conflict_reasons)
    conflict_reason = " ".join(conflict_reasons)

    evidence_summary = {
        "cmdb_raw_name": link.name or "",
        "cmdb_designation": link.designation or "",
        "cmdb_parsed": {
            "operator": cmdb.contracting_client,
            "operator_raw": cmdb.contracting_client_raw,
            "project": cmdb.business_project,
            "final_client": cmdb.final_client,
            "account": cmdb.business_account,
            "business_id": business_id,
            "is_internal_arion": cmdb.is_internal_arion,
            "internal_detail": cmdb.internal_detail,
            "source": cmdb.source,
        },
        "direct_otrs": direct_summary,
        "history_otrs": history_summary,
    }

    # Respect confirmed manual override!
    if existing_identity is not None and existing_identity.is_manual_override:
        manual_conflict_reasons = []
        if existing_identity.review_status == OperationalLinkIdentity.ReviewStatus.CONFIRMED:
            if (
                cmdb.contracting_client
                and existing_identity.contracting_client
                and not existing_identity.is_internal_arion
                and cmdb.contracting_client != existing_identity.contracting_client
            ):
                manual_conflict_reasons.append(
                    f"Atualização CMDB indica operadora {cmdb.contracting_client}, divergindo da classificação manual ({existing_identity.contracting_client})."
                )
            if (
                len(direct_ops) == 1
                and existing_identity.contracting_client
                and not existing_identity.is_internal_arion
                and next(iter(direct_ops)) != existing_identity.contracting_client
            ):
                otrs_op = next(iter(direct_ops))
                manual_conflict_reasons.append(
                    f"Tickets OTRS vinculados indicam operadora {otrs_op}, divergindo da classificação manual ({existing_identity.contracting_client})."
                )
        manual_has_conflict = bool(manual_conflict_reasons)
        manual_issue = _compute_issue_type(
            contracting_client=existing_identity.contracting_client,
            business_project=existing_identity.business_project,
            final_client=existing_identity.final_client,
            is_internal_arion=existing_identity.is_internal_arion,
            has_conflict=manual_has_conflict,
            is_manual_override=True,
            review_status=existing_identity.review_status,
        )
        return {
            "review_status": existing_identity.review_status,
            "issue_type": manual_issue,
            "contracting_client_raw": existing_identity.contracting_client_raw,
            "contracting_client": existing_identity.contracting_client,
            "final_client": existing_identity.final_client,
            "business_project": existing_identity.business_project,
            "business_account": existing_identity.business_account,
            "business_id": existing_identity.business_id or business_id,
            "is_internal_arion": existing_identity.is_internal_arion,
            "internal_detail": existing_identity.internal_detail,
            "confidence": existing_identity.confidence,
            "source": existing_identity.source,
            "suggested_operator": operator or suggested_operator,
            "suggested_final_client": final_client or suggested_final_client,
            "suggested_project": project or suggested_project,
            "suggested_is_internal_arion": is_internal_arion or suggested_is_internal_arion,
            "suggested_confidence": confidence if operator else suggested_confidence,
            "suggested_source": source if operator else suggested_source,
            "has_suggestion": bool(operator or suggested_operator or project or suggested_project),
            "has_conflict": manual_has_conflict,
            "conflict_reason": " ".join(manual_conflict_reasons),
            "evidence_summary": evidence_summary,
            "related_ticket_count": all_summary["distinct_tickets"],
            "is_manual_override": True,
        }

    # Preserve NEEDS_REVIEW flag when manually forwarded unless resolved manually
    forwarded = (
        existing_identity is not None
        and existing_identity.review_status == OperationalLinkIdentity.ReviewStatus.NEEDS_REVIEW
    )

    if not operator and not project and not final_client and not is_internal_arion:
        confidence = "LOW"
        source = "UNKNOWN"
    elif operator and project and not has_conflict:
        confidence = "HIGH" if confidence in {"HIGH", "MEDIUM"} else "MEDIUM"
    elif operator or project or final_client:
        if confidence == "UNCLASSIFIED":
            confidence = "MEDIUM"

    issue_type = _compute_issue_type(
        contracting_client=operator,
        business_project=project,
        final_client=final_client,
        is_internal_arion=is_internal_arion,
        has_conflict=has_conflict,
        is_manual_override=False,
        review_status=(
            OperationalLinkIdentity.ReviewStatus.NEEDS_REVIEW if forwarded
            else OperationalLinkIdentity.ReviewStatus.PENDING
        ),
    )

    if forwarded and issue_type != OperationalLinkIdentity.IssueType.NONE:
        review_status = OperationalLinkIdentity.ReviewStatus.NEEDS_REVIEW
    elif issue_type == OperationalLinkIdentity.IssueType.NONE:
        review_status = OperationalLinkIdentity.ReviewStatus.AUTO_RESOLVED
    else:
        review_status = OperationalLinkIdentity.ReviewStatus.PENDING

    has_suggestion = bool(
        (suggested_operator and suggested_operator != operator)
        or (suggested_project and suggested_project != project)
        or (suggested_is_internal_arion and not is_internal_arion)
    )

    return {
        "review_status": review_status,
        "issue_type": issue_type,
        "contracting_client_raw": operator_raw,
        "contracting_client": operator,
        "final_client": final_client,
        "business_project": project,
        "business_account": account,
        "business_id": business_id,
        "is_internal_arion": is_internal_arion,
        "internal_detail": internal_detail,
        "confidence": confidence,
        "source": source,
        "suggested_operator": suggested_operator,
        "suggested_final_client": suggested_final_client,
        "suggested_project": suggested_project,
        "suggested_is_internal_arion": suggested_is_internal_arion,
        "suggested_confidence": suggested_confidence,
        "suggested_source": suggested_source,
        "has_suggestion": has_suggestion,
        "has_conflict": has_conflict,
        "conflict_reason": conflict_reason,
        "evidence_summary": evidence_summary,
        "related_ticket_count": all_summary["distinct_tickets"],
        "is_manual_override": False,
    }


class OperationalIdentityService:
    """Batch and single-link reconciliation, manual review application, and compact titles."""

    IDENTITY_FIELDS = (
        "review_status", "issue_type", "contracting_client_raw", "contracting_client",
        "final_client", "business_project", "business_account", "business_id",
        "is_internal_arion", "internal_detail", "confidence", "source",
        "suggested_operator", "suggested_final_client", "suggested_project",
        "suggested_is_internal_arion", "suggested_confidence", "suggested_source",
        "has_suggestion", "has_conflict", "conflict_reason", "evidence_summary",
        "related_ticket_count", "is_manual_override",
    )

    @classmethod
    def _build_unique_designation_ticket_index(cls, links: list[CMDBLinkMirror]):
        """Map unique CMDB business_ids to local OTRS tickets whose title contains that ID."""
        bid_to_links = defaultdict(list)
        for link in links:
            bid = _extract_link_business_id(link)
            if bid:
                bid_to_links[bid].append(link.pk)
        unique_bids = {bid for bid, pks in bid_to_links.items() if len(pks) == 1}
        if not unique_bids:
            return {}
        tickets_by_bid = defaultdict(list)
        for ticket in OTRSTicket.objects.only("id", "ticket_number", "title", "customer_id", "source_created_at").iterator(chunk_size=1000):
            title = ticket.title or ""
            if not title:
                continue
            for match in BUSINESS_ID_PATTERN.finditer(title):
                code = match.group(0).upper()
                if code in unique_bids:
                    tickets_by_bid[code].append(ticket)
        return tickets_by_bid

    @classmethod
    def reconcile_all(cls, *, apply: bool = False, include_absent: bool = False, link_ids=None) -> dict:
        queryset = CMDBLinkMirror.objects.all() if include_absent else CMDBLinkMirror.objects.filter(is_present_in_source=True)
        if link_ids is not None:
            queryset = queryset.filter(pk__in=link_ids)
        links = list(queryset.select_related("operator"))
        link_pks = [link.pk for link in links]

        existing_map = {
            row.link_id: row
            for row in OperationalLinkIdentity.objects.filter(link_id__in=link_pks)
        }
        direct_by_link = defaultdict(list)
        relations = (
            OTRSTicketConfigItem.objects.filter(
                cmdb_link_id__in=link_pks,
                is_present_in_source=True,
            )
            .select_related("ticket")
            .only(
                "cmdb_link_id",
                "ticket__id", "ticket__ticket_number", "ticket__title",
                "ticket__customer_id", "ticket__source_created_at",
            )
        )
        for rel in relations.iterator(chunk_size=1000):
            if rel.ticket_id and rel.ticket:
                direct_by_link[rel.cmdb_link_id].append(rel.ticket)

        tickets_by_bid = cls._build_unique_designation_ticket_index(links)

        before = Counter()
        after = Counter()
        sources = Counter()
        issues = Counter()
        confidences = Counter()
        mirror_updates = []
        identity_creates = []
        identity_updates = []
        changed_links = 0
        manual_preserved = 0
        now = timezone.now()

        for link in links:
            before["total"] += 1
            if not (link.contracting_client or "").strip():
                before["missing_operator"] += 1
            if not (link.business_project or "").strip():
                before["missing_project"] += 1
            if not (link.contracting_client or "").strip() and not (link.business_project or "").strip():
                before["missing_both"] += 1

            bid = _extract_link_business_id(link)
            existing = existing_map.get(link.pk)
            evaluated = evaluate_link_identity(
                link,
                direct_tickets=direct_by_link.get(link.pk, ()),
                history_tickets=tickets_by_bid.get(bid, ()) if bid else (),
                existing_identity=existing,
            )

            after["total"] += 1
            if not evaluated["contracting_client"]:
                after["missing_operator"] += 1
            if not evaluated["business_project"]:
                after["missing_project"] += 1
            if not evaluated["contracting_client"] and not evaluated["business_project"]:
                after["missing_both"] += 1
            if evaluated["is_internal_arion"]:
                after["internal_arion"] += 1
            if evaluated["has_conflict"]:
                after["conflicts"] += 1
            if evaluated["has_suggestion"]:
                after["suggestions"] += 1
            if evaluated["is_manual_override"]:
                manual_preserved += 1
            sources[evaluated["source"]] += 1
            issues[evaluated["issue_type"]] += 1
            confidences[evaluated["confidence"]] += 1

            mirror_values = {
                "contracting_client_raw": evaluated["contracting_client_raw"],
                "contracting_client": evaluated["contracting_client"],
                "business_project": evaluated["business_project"],
                "business_account": evaluated["business_account"],
                "business_id": evaluated["business_id"],
                "business_identity_confidence": evaluated["confidence"],
                "business_identity_source": evaluated["source"][:32],
            }
            mirror_changed = any(getattr(link, field) != mirror_values[field] for field in MIRROR_IDENTITY_FIELDS)
            if mirror_changed:
                changed_links += 1
                if apply:
                    for field, value in mirror_values.items():
                        setattr(link, field, value)
                    mirror_updates.append(link)

            if apply:
                if existing is None:
                    identity_creates.append(OperationalLinkIdentity(link=link, **evaluated))
                else:
                    id_changed = any(getattr(existing, field) != evaluated[field] for field in cls.IDENTITY_FIELDS)
                    if id_changed:
                        for field in cls.IDENTITY_FIELDS:
                            setattr(existing, field, evaluated[field])
                        existing.updated_at = now
                        identity_updates.append(existing)

        if apply:
            with transaction.atomic():
                if identity_creates:
                    OperationalLinkIdentity.objects.bulk_create(identity_creates, batch_size=500)
                if identity_updates:
                    OperationalLinkIdentity.objects.bulk_update(
                        identity_updates,
                        fields=[*cls.IDENTITY_FIELDS, "updated_at"],
                        batch_size=500,
                    )
                if mirror_updates:
                    CMDBLinkMirror.objects.bulk_update(
                        mirror_updates,
                        fields=MIRROR_IDENTITY_FIELDS,
                        batch_size=500,
                    )

        return {
            "mode": "APPLIED" if apply else "DRY_RUN",
            "total": len(links),
            "changed_links": changed_links,
            "identities_created": len(identity_creates),
            "identities_updated": len(identity_updates),
            "manual_preserved": manual_preserved,
            "before": dict(before),
            "after": dict(after),
            "reduction": {
                "missing_operator": before["missing_operator"] - after["missing_operator"],
                "missing_project": before["missing_project"] - after["missing_project"],
                "missing_both": before["missing_both"] - after["missing_both"],
            },
            "sources": dict(sources),
            "issues": dict(issues),
            "confidence": dict(confidences),
        }

    @classmethod
    def reconcile_link(cls, link: CMDBLinkMirror, *, apply: bool = True) -> OperationalLinkIdentity | dict:
        existing = OperationalLinkIdentity.objects.filter(link=link).first()
        direct_tickets = [
            rel.ticket
            for rel in OTRSTicketConfigItem.objects.filter(
                cmdb_link=link, is_present_in_source=True,
            ).select_related("ticket")
            if rel.ticket_id and rel.ticket
        ]
        bid = _extract_link_business_id(link)
        history_tickets = []
        if bid:
            # Confirm uniqueness of business_id across active CMDB links
            same_bid_count = CMDBLinkMirror.objects.filter(
                is_present_in_source=True, business_id__iexact=bid,
            ).count()
            if same_bid_count <= 1:
                history_tickets = list(
                    OTRSTicket.objects.filter(title__icontains=bid).only(
                        "id", "ticket_number", "title", "customer_id", "source_created_at",
                    )[:50]
                )
        evaluated = evaluate_link_identity(
            link,
            direct_tickets=direct_tickets,
            history_tickets=history_tickets,
            existing_identity=existing,
        )
        if not apply:
            return evaluated
        with transaction.atomic():
            identity, _created = OperationalLinkIdentity.objects.update_or_create(
                link=link,
                defaults=evaluated,
            )
            cls._project_to_mirror(link, identity)
        return identity

    @staticmethod
    def _project_to_mirror(link: CMDBLinkMirror, identity: OperationalLinkIdentity):
        values = {
            "contracting_client_raw": identity.contracting_client_raw,
            "contracting_client": identity.contracting_client,
            "business_project": identity.business_project,
            "business_account": identity.business_account,
            "business_id": identity.business_id,
            "business_identity_confidence": identity.confidence,
            "business_identity_source": identity.source[:32],
        }
        changed = [field for field, val in values.items() if getattr(link, field) != val]
        if changed:
            for field in changed:
                setattr(link, field, values[field])
            link.save(update_fields=changed)

    @classmethod
    def apply_manual_resolution(
        cls,
        *,
        request,
        link: CMDBLinkMirror,
        action: str,
        operator: str = "",
        final_client: str = "",
        project: str = "",
        internal_detail: str = "",
        justification: str = "",
    ) -> OperationalLinkIdentity:
        valid_actions = set(OperationalIdentityRevision.Action.values)
        if action not in valid_actions:
            raise ValueError("Ação de revisão inválida.")
        justification = " ".join((justification or "").split())[:1000]
        now = timezone.now()

        with transaction.atomic():
            identity = OperationalLinkIdentity.objects.select_for_update().filter(link=link).first()
            if identity is None:
                identity = cls.reconcile_link(link, apply=True)
                identity = OperationalLinkIdentity.objects.select_for_update().get(pk=identity.pk)

            previous_values = {
                "review_status": identity.review_status,
                "issue_type": identity.issue_type,
                "contracting_client": identity.contracting_client,
                "final_client": identity.final_client,
                "business_project": identity.business_project,
                "is_internal_arion": identity.is_internal_arion,
                "internal_detail": identity.internal_detail,
                "source": identity.source,
                "is_manual_override": identity.is_manual_override,
            }

            if action == OperationalIdentityRevision.Action.CONFIRM_SUGGESTION:
                if not identity.has_suggestion and not (identity.suggested_operator or identity.suggested_project or identity.suggested_is_internal_arion):
                    raise ValueError("Este registro não possui sugestão automática para confirmar.")
                if identity.suggested_is_internal_arion:
                    identity.is_internal_arion = True
                    identity.internal_detail = identity.internal_detail or "POP ARION"
                    identity.contracting_client = BusinessIdentityRules.INTERNAL_OPERATOR_LABEL
                    identity.contracting_client_raw = identity.internal_detail
                    identity.business_project = identity.internal_detail
                    identity.final_client = ""
                    identity.business_account = ""
                else:
                    if identity.suggested_operator:
                        identity.contracting_client = identity.suggested_operator
                        identity.contracting_client_raw = identity.suggested_operator
                    if identity.suggested_project:
                        identity.business_project = BusinessIdentityRules.canonical_project(identity.suggested_project)
                    if identity.suggested_final_client:
                        identity.final_client = identity.suggested_final_client
                        if comparison_key(identity.final_client) != comparison_key(identity.business_project):
                            identity.business_account = identity.final_client
                    identity.is_internal_arion = False
                identity.review_status = OperationalLinkIdentity.ReviewStatus.CONFIRMED
                identity.is_manual_override = True
                identity.has_conflict = False
                identity.conflict_reason = ""
                identity.confidence = "HIGH"
                identity.source = "MANUAL_CONFIRMED"

            elif action == OperationalIdentityRevision.Action.MARK_INTERNAL_ARION:
                detail = " ".join((internal_detail or "POP ARION").split())[:200] or "POP ARION"
                identity.is_internal_arion = True
                identity.internal_detail = detail
                identity.contracting_client = BusinessIdentityRules.INTERNAL_OPERATOR_LABEL
                identity.contracting_client_raw = detail
                identity.business_project = detail
                identity.final_client = ""
                identity.business_account = ""
                identity.review_status = OperationalLinkIdentity.ReviewStatus.CONFIRMED
                identity.is_manual_override = True
                identity.has_conflict = False
                identity.conflict_reason = ""
                identity.confidence = "HIGH"
                identity.source = "MANUAL_INTERNAL_ARION"

            elif action == OperationalIdentityRevision.Action.MARK_UNKNOWN:
                identity.is_internal_arion = False
                identity.internal_detail = ""
                identity.contracting_client = ""
                identity.contracting_client_raw = ""
                identity.final_client = ""
                identity.business_project = ""
                identity.business_account = ""
                identity.review_status = OperationalLinkIdentity.ReviewStatus.CONFIRMED_UNKNOWN
                identity.is_manual_override = True
                identity.has_conflict = False
                identity.conflict_reason = ""
                identity.confidence = "LOW"
                identity.source = "MANUAL_UNKNOWN"

            elif action == OperationalIdentityRevision.Action.FORWARD_REVIEW:
                identity.review_status = OperationalLinkIdentity.ReviewStatus.NEEDS_REVIEW
                identity.reviewed_by = request.user
                identity.reviewed_at = now
                if justification:
                    identity.review_note = justification

            elif action == OperationalIdentityRevision.Action.REOPEN_AUTO:
                identity.is_manual_override = False
                identity.review_status = OperationalLinkIdentity.ReviewStatus.PENDING
                identity.reviewed_by = request.user
                identity.reviewed_at = now
                identity.review_note = justification
                identity.save()
                identity = cls.reconcile_link(link, apply=True)

            elif action == OperationalIdentityRevision.Action.MANUAL_UPDATE:
                clean_op = " ".join((operator or "").split())[:300]
                clean_client = " ".join((final_client or "").split())[:300]
                clean_proj = " ".join((project or "").split())[:300]
                if not any((clean_op, clean_client, clean_proj)):
                    raise ValueError("Informe pelo menos operadora, cliente final ou projeto canônico.")
                canonical_op = BusinessIdentityRules.client_for_token(clean_op) or clean_op if clean_op else ""
                canonical_proj = BusinessIdentityRules.canonical_project(clean_proj) if clean_proj else ""
                # Corporate alias convenience if user entered Banco CSF as client without project
                if clean_client and not canonical_proj:
                    corp_c, corp_p = BusinessIdentityRules.corporate_client_match(clean_client)
                    if corp_p:
                        clean_client = corp_c
                        canonical_proj = corp_p
                identity.is_internal_arion = canonical_op == BusinessIdentityRules.INTERNAL_OPERATOR_LABEL
                identity.internal_detail = "Infraestrutura ARION" if identity.is_internal_arion else ""
                identity.contracting_client = canonical_op
                identity.contracting_client_raw = clean_op
                identity.final_client = "" if identity.is_internal_arion else (clean_client or canonical_proj)
                identity.business_project = canonical_proj or ("Infraestrutura ARION" if identity.is_internal_arion else clean_client)
                if identity.final_client and identity.business_project and comparison_key(identity.final_client) != comparison_key(identity.business_project):
                    identity.business_account = identity.final_client
                else:
                    identity.business_account = ""
                identity.review_status = OperationalLinkIdentity.ReviewStatus.CONFIRMED
                identity.is_manual_override = True
                identity.has_conflict = False
                identity.conflict_reason = ""
                identity.confidence = "HIGH" if (identity.contracting_client and identity.business_project) else "MEDIUM"
                identity.source = "MANUAL_CONFIRMED"

            if action != OperationalIdentityRevision.Action.REOPEN_AUTO:
                identity.issue_type = _compute_issue_type(
                    contracting_client=identity.contracting_client,
                    business_project=identity.business_project,
                    final_client=identity.final_client,
                    is_internal_arion=identity.is_internal_arion,
                    has_conflict=identity.has_conflict,
                    is_manual_override=identity.is_manual_override,
                    review_status=identity.review_status,
                )
                identity.reviewed_by = request.user
                identity.reviewed_at = now
                if justification or action != OperationalIdentityRevision.Action.FORWARD_REVIEW:
                    identity.review_note = justification
                identity.save()
                cls._project_to_mirror(link, identity)

            new_values = {
                "review_status": identity.review_status,
                "issue_type": identity.issue_type,
                "contracting_client": identity.contracting_client,
                "final_client": identity.final_client,
                "business_project": identity.business_project,
                "is_internal_arion": identity.is_internal_arion,
                "internal_detail": identity.internal_detail,
                "source": identity.source,
                "is_manual_override": identity.is_manual_override,
            }
            OperationalIdentityRevision.objects.create(
                identity=identity,
                link=link,
                action=action,
                previous_values=previous_values,
                new_values=new_values,
                justification=justification,
                actor=request.user,
            )
            AuditService.record(
                request=request,
                category="operational_identity",
                action=action.lower(),
                description=f"Identidade operacional do link CI {link.source_config_item_id} atualizada ({OperationalIdentityRevision.Action(action).label}).",
                module="base_links",
                metadata={
                    "link_id": link.pk,
                    "config_item_id": link.source_config_item_id,
                    "previous": previous_values,
                    "new": new_values,
                    "has_justification": bool(justification),
                },
            )
            return identity


def build_compact_ticket_title(ticket, links=()) -> str:
    """Build a compact operational headline for the Chamados drawer.

    Examples:
    - "Vivo · Bradesco · BL50000080775"
    - "Claro · Banco CSF (Carrefour) · BL150000094228"
    Falls back to neutral known identifiers when classification is not confirmed.
    """
    primary_link = links[0] if links else None
    operator = ""
    final_client = ""
    project = ""
    designation = ""
    is_internal = False

    if primary_link is not None:
        identity = getattr(primary_link, "operational_identity", None)
        operator = (getattr(primary_link, "contracting_client", "") or "").strip()
        project = (getattr(primary_link, "business_project", "") or "").strip()
        account = (getattr(primary_link, "business_account", "") or "").strip()
        if identity is not None:
            final_client = (identity.final_client or "").strip()
            is_internal = bool(identity.is_internal_arion)
        if not final_client and account:
            final_client = account
        designation = (
            (getattr(primary_link, "business_id", "") or "").strip()
            or (getattr(primary_link, "designation", "") or "").strip()
            or (getattr(primary_link, "source_number", "") or "").strip()
        )
        if designation.upper() == "N/A":
            designation = (getattr(primary_link, "name", "") or "").strip()

    parsed = TicketBusinessIdentityService.parse(
        getattr(ticket, "title", "") or "",
        customer_id=getattr(ticket, "customer_id", "") or "",
    )
    if not operator and parsed.source in {"OTRS_EXPLICIT_CLIENT", "OTRS_PROJECT_RULE"}:
        operator = parsed.client
    if not project and parsed.project and not parsed.is_generic_project:
        project = parsed.project
    if not final_client and parsed.final_client and not parsed.is_generic_project:
        final_client = parsed.final_client
    if not designation:
        designation = parsed.business_id or getattr(ticket, "ticket_number", "") or ""

    # Compose client/project label without redundancy: "Banco CSF (Carrefour)" or "Bradesco"
    client_project_label = ""
    if is_internal:
        client_project_label = project or "Infraestrutura ARION"
    elif final_client and project and comparison_key(final_client) != comparison_key(project):
        if BusinessIdentityRules.is_generic_project(project):
            client_project_label = final_client
        else:
            client_project_label = f"{final_client} ({project})"
    else:
        client_project_label = final_client or (project if project and not BusinessIdentityRules.is_generic_project(project) else "")

    parts = []
    if operator:
        parts.append(operator)
    if client_project_label and comparison_key(client_project_label) != comparison_key(operator):
        parts.append(client_project_label)
    if designation:
        parts.append(designation)

    if len(parts) >= 2:
        return " · ".join(parts)

    # Neutral fallback with known information
    fallback_parts = [p for p in (designation, getattr(ticket, "ticket_number", "")) if p]
    if fallback_parts:
        return " · ".join(dict.fromkeys(fallback_parts))
    return (getattr(ticket, "title", "") or "Chamado sem título")[:120]
