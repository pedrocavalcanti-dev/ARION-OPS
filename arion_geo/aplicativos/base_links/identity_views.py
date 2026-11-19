"""Central de Revisão de Registros Não Identificados (Identidade Operacional)."""
from __future__ import annotations

import csv
import json
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from aplicativos.chamados.services import otrs_ticket_url
from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.configuracoes.permissions import access_required
from aplicativos.integracoes.models import (
    CMDBLinkMirror,
    OTRSTicket,
    OTRSTicketConfigItem,
    OperationalIdentityRevision,
    OperationalLinkIdentity,
)
from aplicativos.integracoes.services.analytics import LinkQueryService, TicketQueryService
from aplicativos.integracoes.services.link_business_identity import (
    BUSINESS_ID_PATTERN,
    BusinessIdentityRules,
)
from aplicativos.integracoes.services.operational_identity import (
    OperationalIdentityService,
    _extract_link_business_id,
)
from aplicativos.integracoes.services.otrs_status import business_status_label
from aplicativos.integracoes.services.semantics import LinkSemantics, UNCLASSIFIED, provider_filter_queryset
from aplicativos.integracoes.services.ticket_business_identity import TicketBusinessIdentityService

ISSUE_LABELS = dict(OperationalLinkIdentity.IssueType.choices)
REVIEW_STATUS_LABELS = dict(OperationalLinkIdentity.ReviewStatus.choices)
CONFIDENCE_LABELS = {
    "HIGH": "Alta",
    "MEDIUM": "Média",
    "LOW": "Baixa",
    "UNCLASSIFIED": "Não classificada",
}
SOURCE_LABELS = {
    "MANUAL_CONFIRMED": "Manual confirmado",
    "MANUAL_INTERNAL_ARION": "Manual · Infra ARION/POP",
    "MANUAL_UNKNOWN": "Manual · Desconhecido",
    "CMDB_EXPLICIT_CLIENT": "CMDB explícita",
    "CMDB_PROJECT_RULE": "CMDB · Regra de projeto",
    "CMDB_CORPORATE_ALIAS": "CMDB · Alias corporativo",
    "CMDB_INTERNAL_ARION": "CMDB · Infra ARION/POP",
    "OTRS_DIRECT_RELATION": "OTRS · Vínculo direto",
    "OTRS_HISTORY_CONSENSUS": "OTRS · Consenso histórico (3+)",
    "OTRS_HISTORY_CANDIDATE": "OTRS · Candidato histórico",
    "OTRS_EVIDENCE": "OTRS · Evidência parcial",
    "UNKNOWN": "Sem evidência suficiente",
}

PENDING_ISSUE_TYPES = (
    OperationalLinkIdentity.IssueType.CONFLICT,
    OperationalLinkIdentity.IssueType.MISSING_BOTH,
    OperationalLinkIdentity.IssueType.MISSING_OPERATOR,
    OperationalLinkIdentity.IssueType.MISSING_CLIENT_PROJECT,
    OperationalLinkIdentity.IssueType.OTHER_VERIFY,
)


def _safe_csv(value):
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def _ensure_identities_exist(scoped_links):
    """Ensure every scoped active link has an OperationalLinkIdentity row."""
    missing_ids = list(
        scoped_links.filter(operational_identity__isnull=True).values_list("pk", flat=True)[:500]
    )
    if missing_ids:
        OperationalIdentityService.reconcile_all(apply=True, link_ids=missing_ids)


def _unlinked_tickets_queryset(user):
    """Return active/recent OTRS tickets lacking a trustworthy CMDB link relation.

    Restricted Data Scope users cannot see unlinked tickets whose tenant cannot be proven.
    """
    if not DataScopeService.is_global(user):
        return OTRSTicket.objects.none()
    has_active_link = OTRSTicketConfigItem.objects.filter(
        ticket_id=OuterRef("pk"),
        is_present_in_source=True,
        cmdb_link__is_present_in_source=True,
    )
    return (
        TicketQueryService.noc_tickets()
        .annotate(_has_link=Exists(has_active_link))
        .filter(_has_link=False)
    )


def _quality_kpis(scoped_links, user):
    identities = OperationalLinkIdentity.objects.filter(link__in=scoped_links)
    agg = identities.aggregate(
        total=Count("pk"),
        missing_operator=Count(
            "pk",
            filter=Q(is_internal_arion=False, contracting_client="")
            & ~Q(review_status=OperationalLinkIdentity.ReviewStatus.CONFIRMED_UNKNOWN),
        ),
        missing_client_project=Count(
            "pk",
            filter=Q(is_internal_arion=False, business_project="", final_client="")
            & ~Q(review_status=OperationalLinkIdentity.ReviewStatus.CONFIRMED_UNKNOWN),
        ),
        conflicts=Count("pk", filter=Q(has_conflict=True)),
        suggestions=Count(
            "pk",
            filter=Q(has_suggestion=True, issue_type__in=PENDING_ISSUE_TYPES),
        ),
        manual_corrected=Count("pk", filter=Q(is_manual_override=True)),
        pending_links=Count(
            "pk",
            filter=Q(
                is_internal_arion=False,
                issue_type__in=PENDING_ISSUE_TYPES,
            ) | Q(review_status=OperationalLinkIdentity.ReviewStatus.NEEDS_REVIEW),
        ),
        internal_arion=Count("pk", filter=Q(is_internal_arion=True)),
    )
    agg["unlinked_tickets"] = _unlinked_tickets_queryset(user).count()
    return agg


def _read_filters(request):
    tab = request.GET.get("tab", "links").strip().lower()
    if tab not in {"links", "tickets", "arion"}:
        tab = "links"
    review_default = "pending" if tab == "links" else "all"
    return {
        "tab": tab,
        "q": request.GET.get("q", "").strip()[:160],
        "issue_type": request.GET.get("issue_type", "").strip(),
        "operator": request.GET.get("operator", "").strip(),
        "project": request.GET.get("project", "").strip(),
        "provider": request.GET.get("provider", "").strip(),
        "uf": request.GET.get("uf", "").strip(),
        "confidence": request.GET.get("confidence", "").strip(),
        "suggestion": request.GET.get("suggestion", "").strip(),
        "review_status": request.GET.get("review_status", review_default).strip(),
        "sort": request.GET.get("sort", "priority").strip(),
    }


def _filter_identities(scoped_links, filters):
    qs = OperationalLinkIdentity.objects.filter(link__in=scoped_links).select_related(
        "link", "link__operator", "reviewed_by",
    )

    if filters["tab"] == "arion":
        qs = qs.filter(is_internal_arion=True)
    else:
        # Exclude confirmed internal ARION/POP links from the unidentified queue
        # unless explicitly searching/filtering all or they have a conflict.
        if filters["review_status"] in {"pending", "needs_review"} and filters["issue_type"] != OperationalLinkIdentity.IssueType.CONFLICT:
            qs = qs.filter(Q(is_internal_arion=False) | Q(has_conflict=True))

    rs = filters["review_status"]
    if filters["tab"] == "links":
        if rs == "pending":
            qs = qs.filter(
                Q(issue_type__in=PENDING_ISSUE_TYPES)
                | Q(review_status=OperationalLinkIdentity.ReviewStatus.NEEDS_REVIEW)
            )
        elif rs == "needs_review":
            qs = qs.filter(review_status=OperationalLinkIdentity.ReviewStatus.NEEDS_REVIEW)
        elif rs == "reviewed":
            qs = qs.filter(is_manual_override=True)
        elif rs == "resolved":
            qs = qs.filter(
                review_status__in=(
                    OperationalLinkIdentity.ReviewStatus.AUTO_RESOLVED,
                    OperationalLinkIdentity.ReviewStatus.CONFIRMED,
                ),
                issue_type=OperationalLinkIdentity.IssueType.NONE,
            )
        elif rs == "unknown":
            qs = qs.filter(review_status=OperationalLinkIdentity.ReviewStatus.CONFIRMED_UNKNOWN)

    if filters["issue_type"] in ISSUE_LABELS:
        qs = qs.filter(issue_type=filters["issue_type"])

    if filters["operator"]:
        op = filters["operator"]
        if op == "__empty__":
            qs = qs.filter(contracting_client="", suggested_operator="")
        else:
            qs = qs.filter(Q(contracting_client__iexact=op) | Q(suggested_operator__iexact=op))

    if filters["project"]:
        proj = filters["project"]
        qs = qs.filter(
            Q(business_project__iexact=proj)
            | Q(final_client__iexact=proj)
            | Q(suggested_project__iexact=proj)
            | Q(suggested_final_client__iexact=proj)
        )

    if filters["provider"]:
        matching_links = provider_filter_queryset(scoped_links, filters["provider"])
        qs = qs.filter(link__in=matching_links)

    if filters["uf"]:
        qs = qs.filter(link__state__iexact=filters["uf"])

    if filters["confidence"] in CONFIDENCE_LABELS:
        qs = qs.filter(confidence=filters["confidence"])

    if filters["suggestion"] == "with":
        qs = qs.filter(has_suggestion=True)
    elif filters["suggestion"] == "without":
        qs = qs.filter(has_suggestion=False)

    if filters["q"]:
        q = filters["q"]
        ticket_link_ids = OTRSTicketConfigItem.objects.filter(
            is_present_in_source=True,
            cmdb_link__in=scoped_links,
        ).filter(
            Q(ticket__ticket_number__icontains=q) | Q(ticket__title__icontains=q)
        ).values("cmdb_link_id")
        qs = qs.filter(
            Q(link__name__icontains=q)
            | Q(link__designation__icontains=q)
            | Q(link__source_config_item_id__icontains=q)
            | Q(link__source_number__icontains=q)
            | Q(business_id__icontains=q)
            | Q(contracting_client__icontains=q)
            | Q(final_client__icontains=q)
            | Q(business_project__icontains=q)
            | Q(suggested_operator__icontains=q)
            | Q(suggested_project__icontains=q)
            | Q(link__city__icontains=q)
            | Q(link__operator__name__icontains=q)
            | Q(link_id__in=ticket_link_ids)
        )

    priority_rank = Case(
        When(issue_type=OperationalLinkIdentity.IssueType.CONFLICT, then=Value(0)),
        When(review_status=OperationalLinkIdentity.ReviewStatus.NEEDS_REVIEW, then=Value(1)),
        When(issue_type=OperationalLinkIdentity.IssueType.MISSING_BOTH, then=Value(2)),
        When(issue_type=OperationalLinkIdentity.IssueType.MISSING_OPERATOR, then=Value(3)),
        When(issue_type=OperationalLinkIdentity.IssueType.OTHER_VERIFY, then=Value(4)),
        When(issue_type=OperationalLinkIdentity.IssueType.MISSING_CLIENT_PROJECT, then=Value(5)),
        default=Value(9),
        output_field=IntegerField(),
    )
    sort = filters["sort"]
    if sort == "tickets":
        qs = qs.order_by("-related_ticket_count", priority_rank, "link__designation", "link_id")
    elif sort == "designation":
        qs = qs.order_by("link__designation", "link__name", "link_id")
    elif sort == "uf":
        qs = qs.order_by("link__state", "link__city", "link__designation", "link_id")
    elif sort == "updated":
        qs = qs.order_by("-updated_at", "link_id")
    else:
        qs = qs.annotate(_priority=priority_rank).order_by(
            "_priority", "-has_suggestion", "-related_ticket_count", "link__designation", "link_id",
        )
    return qs


def _filter_unlinked_tickets(user, filters):
    qs = _unlinked_tickets_queryset(user)
    if filters["q"]:
        q = filters["q"]
        ticket_q = Q(ticket_number__icontains=q) | Q(title__icontains=q) | Q(customer_id__icontains=q)
        if q.isdigit():
            ticket_q |= Q(external_id=int(q))
        qs = qs.filter(ticket_q)
    return qs.order_by("-source_created_at", "-pk")


def _catalog_options(scoped_links):
    identities = OperationalLinkIdentity.objects.filter(link__in=scoped_links)
    operators = sorted(
        {
            *(BusinessIdentityRules.MAJOR_OPERATORS),
            BusinessIdentityRules.INTERNAL_OPERATOR_LABEL,
            *identities.exclude(contracting_client="").values_list("contracting_client", flat=True).distinct(),
            *identities.exclude(suggested_operator="").values_list("suggested_operator", flat=True).distinct(),
        },
        key=str.casefold,
    )
    projects = sorted(
        {
            *set(BusinessIdentityRules.PROJECT_ALIASES.values()),
            *identities.exclude(business_project="").values_list("business_project", flat=True).distinct(),
            *identities.exclude(suggested_project="").values_list("suggested_project", flat=True).distinct(),
        },
        key=str.casefold,
    )
    final_clients = sorted(
        {
            "Banco CSF",
            "Bradesco",
            "Banco do Brasil",
            "Correios",
            "Caixa Econômica Federal",
            "Carrefour",
            "Arcos Dourados",
            "Santander",
            *identities.exclude(final_client="").values_list("final_client", flat=True).distinct(),
        },
        key=str.casefold,
    )
    ufs = list(scoped_links.exclude(state="").values_list("state", flat=True).distinct().order_by("state"))
    providers = list(
        scoped_links.exclude(operator__isnull=True)
        .values_list("operator__name", flat=True)
        .distinct()
        .order_by("operator__name")
    )
    return {
        "operators": operators,
        "projects": projects,
        "final_clients": final_clients,
        "ufs": ufs,
        "providers": providers,
    }


@login_required
@access_required("view_base")
@require_GET
def unidentified_index(request):
    scoped_links = LinkQueryService.base(request.user)
    _ensure_identities_exist(scoped_links)
    filters = _read_filters(request)
    kpis = _quality_kpis(scoped_links, request.user)
    options = _catalog_options(scoped_links)

    try:
        page_size = int(request.GET.get("page_size", "50"))
    except (TypeError, ValueError):
        page_size = 50
    if page_size not in (25, 50, 100):
        page_size = 50

    context = {
        "view": "unidentified",
        "filters": filters,
        "kpis": kpis,
        "options": options,
        "page_size": page_size,
        "issue_choices": OperationalLinkIdentity.IssueType.choices,
        "confidence_choices": list(CONFIDENCE_LABELS.items()),
        "review_status_choices": (
            ("pending", "Pendentes (fila)"),
            ("needs_review", "Encaminhados para revisão"),
            ("reviewed", "Corrigidos manualmente"),
            ("resolved", "Resolvidos (auto + manual)"),
            ("unknown", "Desconhecido confirmado"),
            ("all", "Todos os links"),
        ),
        "sort_choices": (
            ("priority", "Prioridade (conflitos e impacto)"),
            ("tickets", "Mais chamados"),
            ("designation", "Designação A–Z"),
            ("uf", "UF / Cidade"),
            ("updated", "Atualização recente"),
        ),
        "can_manage_identity": request.user.has_perm("configuracoes.manage_operational_identity"),
        "source_labels": SOURCE_LABELS,
        "issue_labels": ISSUE_LABELS,
        "confidence_labels": CONFIDENCE_LABELS,
        "review_status_labels": REVIEW_STATUS_LABELS,
    }

    if filters["tab"] == "tickets":
        tickets_qs = _filter_unlinked_tickets(request.user, filters)
        page_obj = Paginator(tickets_qs, page_size).get_page(request.GET.get("page"))
        ticket_rows = []
        for ticket in page_obj.object_list:
            parsed = TicketBusinessIdentityService.parse(ticket.title or "", customer_id=ticket.customer_id or "")
            ticket_rows.append({
                "id": ticket.pk,
                "external_id": ticket.external_id,
                "ticket_number": ticket.ticket_number,
                "title": ticket.title,
                "created_at": ticket.source_created_at,
                "status_label": business_status_label(ticket.business_status),
                "otrs_url": otrs_ticket_url(ticket.external_id),
                "detected_operator": parsed.client,
                "detected_project": parsed.project,
                "detected_business_id": parsed.business_id,
                "detected_ci": parsed.ci_reference,
            })
        context.update({
            "page_obj": page_obj,
            "ticket_rows": ticket_rows,
            "row_ids_json": json.dumps([row["id"] for row in ticket_rows]),
            "is_global_scope": DataScopeService.is_global(request.user),
        })
    else:
        identities_qs = _filter_identities(scoped_links, filters)
        page_obj = Paginator(identities_qs, page_size).get_page(request.GET.get("page"))
        rows = []
        for ident in page_obj.object_list:
            link = ident.link
            semantics = LinkSemantics.from_link(link)
            rows.append({
                "link_id": link.pk,
                "config_item_id": link.source_config_item_id,
                "designation": link.designation or link.name or link.source_number or link.source_config_item_id,
                "name": link.name,
                "operator": ident.contracting_client,
                "final_client": ident.final_client,
                "project": ident.business_project,
                "provider": semantics.provider_name,
                "city": link.city,
                "state": link.state,
                "issue_type": ident.issue_type,
                "issue_label": ISSUE_LABELS.get(ident.issue_type, ident.issue_type),
                "review_status": ident.review_status,
                "review_status_label": REVIEW_STATUS_LABELS.get(ident.review_status, ident.review_status),
                "confidence": ident.confidence,
                "confidence_label": CONFIDENCE_LABELS.get(ident.confidence, ident.confidence),
                "source_label": SOURCE_LABELS.get(ident.source, ident.source),
                "is_internal_arion": ident.is_internal_arion,
                "internal_detail": ident.internal_detail,
                "is_manual_override": ident.is_manual_override,
                "has_conflict": ident.has_conflict,
                "conflict_reason": ident.conflict_reason,
                "has_suggestion": ident.has_suggestion,
                "suggested_operator": ident.suggested_operator,
                "suggested_project": ident.suggested_project,
                "suggested_final_client": ident.suggested_final_client,
                "related_ticket_count": ident.related_ticket_count,
            })
        context.update({
            "page_obj": page_obj,
            "identity_rows": rows,
            "row_ids_json": json.dumps([row["link_id"] for row in rows]),
        })

    return render(request, "base_links/unidentified.html", context)


@login_required
@access_required("export_base")
@require_GET
def unidentified_export_csv(request):
    scoped_links = LinkQueryService.base(request.user)
    _ensure_identities_exist(scoped_links)
    filters = _read_filters(request)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response.write("\ufeff")
    writer = csv.writer(response)

    if filters["tab"] == "tickets":
        response["Content-Disposition"] = 'attachment; filename="arion-tickets-sem-vinculo-cmdb.csv"'
        writer.writerow([
            "Ticket", "Data criação", "Status", "Título OTRS",
            "Operadora detectada", "Projeto detectado", "Designação detectada", "CI detectado",
        ])
        for ticket in _filter_unlinked_tickets(request.user, filters).iterator(chunk_size=500):
            parsed = TicketBusinessIdentityService.parse(ticket.title or "", customer_id=ticket.customer_id or "")
            writer.writerow([_safe_csv(v) for v in (
                ticket.ticket_number,
                timezone.localtime(ticket.source_created_at).strftime("%d/%m/%Y %H:%M") if ticket.source_created_at else "",
                business_status_label(ticket.business_status),
                ticket.title,
                parsed.client or "Não identificada",
                parsed.project or "Não identificado",
                parsed.business_id or "",
                parsed.ci_reference or "",
            )])
        return response

    response["Content-Disposition"] = 'attachment; filename="arion-identidade-pendentes.csv"'
    writer.writerow([
        "ConfigItemID", "Designação", "Nome original CMDB", "Tipo de problema", "Status revisão",
        "Operadora atual", "Cliente final", "Projeto canônico", "ISP físico", "UF", "Cidade",
        "Confiança", "Origem", "Sugestão operadora", "Sugestão projeto", "Conflito",
        "Chamados vinculados", "Infra ARION", "Override manual",
    ])
    for ident in _filter_identities(scoped_links, filters).iterator(chunk_size=500):
        link = ident.link
        semantics = LinkSemantics.from_link(link)
        writer.writerow([_safe_csv(v) for v in (
            link.source_config_item_id,
            link.designation or link.name or link.source_number,
            link.name,
            ISSUE_LABELS.get(ident.issue_type, ident.issue_type),
            REVIEW_STATUS_LABELS.get(ident.review_status, ident.review_status),
            ident.contracting_client or "Não identificada",
            ident.final_client or "",
            ident.business_project or "Não identificado",
            semantics.provider_name,
            link.state,
            link.city,
            CONFIDENCE_LABELS.get(ident.confidence, ident.confidence),
            SOURCE_LABELS.get(ident.source, ident.source),
            ident.suggested_operator,
            ident.suggested_project,
            ident.conflict_reason,
            ident.related_ticket_count,
            "Sim" if ident.is_internal_arion else "Não",
            "Sim" if ident.is_manual_override else "Não",
        )])
    return response


@login_required
@access_required("view_base")
@require_GET
def unidentified_link_detail(request, pk):
    scoped_links = LinkQueryService.base(request.user)
    link = get_object_or_404(scoped_links.select_related("operator"), pk=pk)
    identity = OperationalLinkIdentity.objects.filter(link=link).select_related("reviewed_by").first()
    if identity is None:
        identity = OperationalIdentityService.reconcile_link(link, apply=True)

    semantics = LinkSemantics.from_link(link)

    direct_relations = list(
        OTRSTicketConfigItem.objects.filter(cmdb_link=link, is_present_in_source=True)
        .select_related("ticket")
        .order_by("-ticket__source_created_at", "-pk")[:30]
    )
    direct_ticket_ids = set()
    tickets_payload = []
    for rel in direct_relations:
        t = rel.ticket
        if not t or t.pk in direct_ticket_ids:
            continue
        direct_ticket_ids.add(t.pk)
        parsed = TicketBusinessIdentityService.parse(t.title or "", customer_id=t.customer_id or "")
        tickets_payload.append({
            "id": t.pk,
            "external_id": t.external_id,
            "ticket_number": t.ticket_number,
            "title": t.title,
            "status_label": business_status_label(t.business_status),
            "created_at": timezone.localtime(t.source_created_at).strftime("%d/%m/%Y %H:%M") if t.source_created_at else "Sem data",
            "relation_kind": "Vínculo direto OTRS",
            "detected_operator": parsed.client,
            "detected_operator_raw": parsed.client_raw,
            "detected_project": parsed.project,
            "detected_final_client": parsed.final_client,
            "detected_business_id": parsed.business_id,
            "otrs_url": otrs_ticket_url(t.external_id),
        })

    # Also include historical tickets matching unique business_id if not already listed
    bid = _extract_link_business_id(link)
    if bid:
        same_bid = CMDBLinkMirror.objects.filter(is_present_in_source=True, business_id__iexact=bid).count()
        if same_bid <= 1:
            hist_tickets = (
                OTRSTicket.objects.filter(title__icontains=bid)
                .exclude(pk__in=direct_ticket_ids)
                .order_by("-source_created_at", "-pk")[:15]
            )
            for t in hist_tickets:
                parsed = TicketBusinessIdentityService.parse(t.title or "", customer_id=t.customer_id or "")
                tickets_payload.append({
                    "id": t.pk,
                    "external_id": t.external_id,
                    "ticket_number": t.ticket_number,
                    "title": t.title,
                    "status_label": business_status_label(t.business_status),
                    "created_at": timezone.localtime(t.source_created_at).strftime("%d/%m/%Y %H:%M") if t.source_created_at else "Sem data",
                    "relation_kind": f"Histórico por designação ({bid})",
                    "detected_operator": parsed.client,
                    "detected_operator_raw": parsed.client_raw,
                    "detected_project": parsed.project,
                    "detected_final_client": parsed.final_client,
                    "detected_business_id": parsed.business_id,
                    "otrs_url": otrs_ticket_url(t.external_id),
                })

    revisions = [
        {
            "action_label": rev.get_action_display(),
            "actor": (rev.actor.get_full_name() or rev.actor.get_username()) if rev.actor else "Sistema",
            "created_at": timezone.localtime(rev.created_at).strftime("%d/%m/%Y %H:%M"),
            "justification": rev.justification,
            "previous": rev.previous_values,
            "new": rev.new_values,
        }
        for rev in identity.revisions.select_related("actor")[:15]
    ]

    return JsonResponse({
        "link": {
            "id": link.pk,
            "config_item_id": link.source_config_item_id,
            "source_number": link.source_number or "",
            "name": link.name,
            "designation": link.designation or link.name or link.source_number or link.source_config_item_id,
            "operator_designation": link.operator_designation,
            "circuit": link.circuit,
            "identification": link.identification,
            "provider": semantics.provider_name,
            "city": link.city,
            "state": link.state,
            "deployment_state": link.deployment_state,
            "incident_state": link.incident_state,
            "base_url": reverse("base_links:index") + "?" + urlencode({
                "view": "links", "config_item_id": link.source_config_item_id,
            }),
        },
        "identity": {
            "review_status": identity.review_status,
            "review_status_label": REVIEW_STATUS_LABELS.get(identity.review_status, identity.review_status),
            "issue_type": identity.issue_type,
            "issue_label": ISSUE_LABELS.get(identity.issue_type, identity.issue_type),
            "contracting_client": identity.contracting_client,
            "contracting_client_raw": identity.contracting_client_raw,
            "final_client": identity.final_client,
            "business_project": identity.business_project,
            "business_account": identity.business_account,
            "business_id": identity.business_id,
            "is_internal_arion": identity.is_internal_arion,
            "internal_detail": identity.internal_detail,
            "confidence": identity.confidence,
            "confidence_label": CONFIDENCE_LABELS.get(identity.confidence, identity.confidence),
            "source": identity.source,
            "source_label": SOURCE_LABELS.get(identity.source, identity.source),
            "is_manual_override": identity.is_manual_override,
            "reviewed_by": (identity.reviewed_by.get_full_name() or identity.reviewed_by.get_username()) if identity.reviewed_by else "",
            "reviewed_at": timezone.localtime(identity.reviewed_at).strftime("%d/%m/%Y %H:%M") if identity.reviewed_at else "",
            "review_note": identity.review_note,
            "has_conflict": identity.has_conflict,
            "conflict_reason": identity.conflict_reason,
            "has_suggestion": identity.has_suggestion,
            "suggested_operator": identity.suggested_operator,
            "suggested_final_client": identity.suggested_final_client,
            "suggested_project": identity.suggested_project,
            "suggested_is_internal_arion": identity.suggested_is_internal_arion,
            "suggested_confidence": CONFIDENCE_LABELS.get(identity.suggested_confidence, identity.suggested_confidence),
            "suggested_source": SOURCE_LABELS.get(identity.suggested_source, identity.suggested_source),
            "evidence_summary": identity.evidence_summary,
        },
        "tickets": tickets_payload,
        "revisions": revisions,
        "can_manage": request.user.has_perm("configuracoes.manage_operational_identity"),
        "resolve_url": reverse("base_links:unidentified-link-resolve", args=[link.pk]),
    })


@login_required
@access_required("manage_operational_identity")
@require_POST
def unidentified_link_resolve(request, pk):
    scoped_links = LinkQueryService.base(request.user)
    link = get_object_or_404(scoped_links, pk=pk)

    action = (request.POST.get("action") or "").strip().upper()
    operator = (request.POST.get("operator") or "").strip()
    operator_other = (request.POST.get("operator_other") or "").strip()
    if operator == "__other__":
        operator = operator_other
    final_client = (request.POST.get("final_client") or "").strip()
    project = (request.POST.get("project") or "").strip()
    internal_detail = (request.POST.get("internal_detail") or "").strip()
    justification = (request.POST.get("justification") or "").strip()

    try:
        identity = OperationalIdentityService.apply_manual_resolution(
            request=request,
            link=link,
            action=action,
            operator=operator,
            final_client=final_client,
            project=project,
            internal_detail=internal_detail,
            justification=justification,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)

    kpis = _quality_kpis(scoped_links, request.user)
    return JsonResponse({
        "ok": True,
        "message": "Classificação operacional atualizada e auditada com sucesso.",
        "identity": {
            "link_id": link.pk,
            "operator": identity.contracting_client,
            "final_client": identity.final_client,
            "project": identity.business_project,
            "issue_type": identity.issue_type,
            "issue_label": ISSUE_LABELS.get(identity.issue_type, identity.issue_type),
            "review_status": identity.review_status,
            "review_status_label": REVIEW_STATUS_LABELS.get(identity.review_status, identity.review_status),
            "confidence": identity.confidence,
            "confidence_label": CONFIDENCE_LABELS.get(identity.confidence, identity.confidence),
            "source_label": SOURCE_LABELS.get(identity.source, identity.source),
            "is_internal_arion": identity.is_internal_arion,
            "internal_detail": identity.internal_detail,
            "is_manual_override": identity.is_manual_override,
            "has_conflict": identity.has_conflict,
            "conflict_reason": identity.conflict_reason,
        },
        "kpis": kpis,
    })


@login_required
@access_required("view_base")
@require_GET
def unidentified_ticket_detail(request, pk):
    ticket = get_object_or_404(_unlinked_tickets_queryset(request.user), pk=pk)
    parsed = TicketBusinessIdentityService.parse(ticket.title or "", customer_id=ticket.customer_id or "")

    candidates = []
    if parsed.business_id:
        scoped_links = LinkQueryService.base(request.user)
        matches = scoped_links.filter(
            Q(business_id__iexact=parsed.business_id)
            | Q(designation__icontains=parsed.business_id)
            | Q(name__icontains=parsed.business_id)
        ).select_related("operator")[:8]
        for link in matches:
            semantics = LinkSemantics.from_link(link)
            candidates.append({
                "link_id": link.pk,
                "config_item_id": link.source_config_item_id,
                "name": link.name,
                "designation": link.designation or link.name or link.source_config_item_id,
                "operator": link.contracting_client or "Não identificada",
                "project": link.business_project or "Não identificado",
                "provider": semantics.provider_name,
                "state": link.state,
                "base_url": reverse("base_links:index") + "?" + urlencode({
                    "view": "links", "config_item_id": link.source_config_item_id,
                }),
            })

    return JsonResponse({
        "ticket": {
            "id": ticket.pk,
            "external_id": ticket.external_id,
            "ticket_number": ticket.ticket_number,
            "title": ticket.title,
            "queue": ticket.queue_name,
            "status_label": business_status_label(ticket.business_status),
            "external_state": ticket.external_state_name,
            "priority": ticket.priority_name or "Não informada",
            "created_at": timezone.localtime(ticket.source_created_at).strftime("%d/%m/%Y %H:%M") if ticket.source_created_at else "Sem data",
            "otrs_url": otrs_ticket_url(ticket.external_id),
        },
        "parsed": {
            "operator": parsed.client,
            "operator_raw": parsed.client_raw,
            "project": parsed.project,
            "final_client": parsed.final_client,
            "business_id": parsed.business_id,
            "ci_reference": parsed.ci_reference,
            "source": parsed.source,
        },
        "candidates": candidates,
    })
