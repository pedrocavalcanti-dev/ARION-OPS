from django.db.models import Count, Exists, OuterRef, Q

from ..forms import QueryForm, FINANCIAL_PENDING_KINDS
from ..models import CancellationPending, CancellationProcess, FinancialObligation
from .data_scope import CancellationScope
from .otrs_intake import current_intakes


TABS = (("intake", "Entrada OTRS"), ("order", "Pedido"), ("request", "Solicitação"),
        ("pending", "Pendências"), ("completed", "Concluídos"))


def query_module(user, params):
    intakes = CancellationScope.intakes(user).select_related("assigned_to", "link__operator")
    official_ticket = CancellationProcess.objects.filter(Q(ticket_number=OuterRef("ticket_number")) | Q(source_ticket_id=OuterRef("ticket_id")))
    intakes = intakes.annotate(_has_official=Exists(official_ticket))
    processes = CancellationScope.processes(user).select_related("intake", "source_ticket", "owner", "treatment", "link__operator")
    owner_rows = list(processes.exclude(owner_id=None).values_list("owner_id", "owner__username").order_by("owner__username").distinct())
    owner_rows += list(intakes.exclude(assigned_to_id=None).values_list("assigned_to_id", "assigned_to__username").order_by("assigned_to__username").distinct())
    owners = sorted(dict(owner_rows).items(), key=lambda pair: pair[1].casefold())
    form = QueryForm(params, owners=owners)
    if not form.is_valid():
        intakes, processes = intakes.none(), processes.none()
        data = {}
    else:
        data = form.cleaned_data
        q = data.get("q")
        if q:
            entry_search = (Q(ticket_number__icontains=q) | Q(designation_snapshot__icontains=q)
                            | Q(ticket__config_items__cmdb_link__designation__icontains=q)
                            | Q(client_snapshot__icontains=q) | Q(link__operator__name__icontains=q))
            process_search = (Q(intake__ticket_number__icontains=q) | Q(ticket_number__icontains=q) | Q(designation__icontains=q)
                              | Q(contracting_client__icontains=q) | Q(isp__icontains=q))
            numeric = q.upper().removeprefix("CANC-")
            if numeric.isdecimal() and len(numeric) <= 18:
                entry_search |= Q(ticket_external_id=int(numeric))
                process_search |= Q(pk=int(numeric)) | Q(intake__ticket_external_id=int(numeric)) | Q(source_ticket__external_id=int(numeric))
            intakes = intakes.filter(entry_search).distinct()
            processes = processes.filter(process_search)
        if data.get("context"):
            context = data["context"]
            intakes = intakes.filter(Q(client_snapshot__icontains=context) | Q(project_snapshot__icontains=context))
            processes = processes.filter(Q(contracting_client__icontains=context) | Q(project__icontains=context))
        for param, entry_field, process_field in (("client", "client_snapshot", "contracting_client"),
                                                 ("project", "project_snapshot", "project"), ("isp", "link__operator__name", "isp")):
            if data.get(param):
                intakes = intakes.filter(**{f"{entry_field}__icontains": data[param]})
                processes = processes.filter(**{f"{process_field}__icontains": data[param]})
        owner = data.get("owner")
        if owner:
            owner_id = user.pk if owner == "me" else None if owner == "none" else int(owner)
            intakes = intakes.filter(assigned_to_id=owner_id)
            processes = processes.filter(owner_id=owner_id)
        for key, lookup in (("start", "gte"), ("end", "lte")):
            if data.get(key):
                intakes = intakes.filter(**{f"source_created_at__date__{lookup}": data[key]})
                processes = processes.filter(Q(**{f"intake__source_created_at__date__{lookup}": data[key]}) | Q(**{f"ticket_opened_on__{lookup}": data[key]}))
    active_pending = CancellationPending.objects.filter(process_id=OuterRef("pk"), status="OPEN")
    if not user.has_perm("cancelamentos.view_cancellation_finance"):
        active_pending = active_pending.exclude(kind__in=FINANCIAL_PENDING_KINDS).filter(amount__isnull=True, legacy_reference__isnull=True)
    waiting_document = FinancialObligation.objects.filter(process_id=OuterRef("pk"), document_status="WAITING")
    processes = processes.annotate(has_pending=Exists(active_pending))
    tab = data.get("tab") or "intake"
    if tab != "intake" and data.get("pending_kind"):
        processes = processes.filter(Exists(active_pending.filter(kind=data["pending_kind"])))
    if tab != "intake" and data.get("sent"):
        processes = processes.filter(handoffs__isnull=False).distinct()
    pending_processes = processes.filter(has_pending=True) if data.get("with_open_pending") else processes.filter(stage="PENDING")
    if tab == "pending" and data.get("with_open_pending") and data.get("stage"):
        pending_processes = pending_processes.filter(stage=data["stage"])
    grouped = {row["stage"]: row["n"] for row in processes.values("stage").annotate(n=Count("pk", distinct=True))}
    documents_pending = active_pending.filter(kind__in=("BILL", "DOCUMENT", "RELEASE", "AGREEMENT"))
    metrics = {
        "intake": current_intakes(intakes).filter(status="WAITING", process__isnull=True, _has_official=False).count(), "order": grouped.get("ORDER", 0),
        "request": grouped.get("REQUEST", 0), "completed": grouped.get("COMPLETED", 0),
        "pending": grouped.get("PENDING", 0),
        "open_pending": processes.filter(has_pending=True).count(),
        "total": sum(grouped.values()),
        "documents": processes.exclude(stage="COMPLETED").filter(
            Q(Exists(documents_pending)) | Q(Exists(waiting_document))
        ).count(),
        "sent": processes.filter(handoffs__isnull=False).distinct().count(),
    }
    if tab == "intake":
        presence = data.get("presence")
        current_ids = current_intakes(intakes).values("pk")
        if presence == "history":
            intakes = intakes.exclude(pk__in=current_ids)
        elif presence != "all":
            intakes = intakes.filter(pk__in=current_ids)
        situation = data.get("situation") or "WAITING"
        queryset = intakes if situation == "all" else intakes.filter(status=situation)
        if situation == "WAITING":
            queryset = queryset.filter(process__isnull=True, _has_official=False)
    elif tab == "pending":
        queryset = pending_processes
    else:
        queryset = processes.filter(stage={"order": "ORDER", "request": "REQUEST", "completed": "COMPLETED"}[tab])
        if data.get("with_open_pending"):
            queryset = queryset.filter(has_pending=True)
    advanced = ["isp", "project", "start", "end"]
    advanced += ["situation", "presence"] if tab == "intake" else ["with_open_pending", "pending_kind", "sent"]
    if tab == "pending":
        advanced.append("stage")
    if data.get("client"):
        advanced.append("client")  # Preserve existing bookmarked client-only recortes.
    advanced_count = sum(bool(data.get(name)) for name in advanced if name != "stage" or data.get("with_open_pending"))
    return {"form": form, "queryset": queryset, "tab": tab, "metrics": metrics,
            "advanced_fields": [form[name] for name in advanced], "advanced_count": advanced_count,
            "owner_filter": data.get("owner") or ""}
