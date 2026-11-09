from django.db.models import Count, Exists, OuterRef, Q

from ..forms import QueryForm
from ..models import CancellationPending, FinancialObligation
from .data_scope import CancellationScope


TABS = (("intake", "Entrada OTRS"), ("order", "Pedido"), ("request", "Solicitação"),
        ("pending", "Pendências"), ("completed", "Concluídos"))


def query_module(user, params):
    intakes = CancellationScope.intakes(user).select_related("assigned_to", "link__operator")
    processes = CancellationScope.processes(user).select_related("intake", "owner", "treatment", "link__operator")
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
            intakes = intakes.filter(Q(ticket_number__icontains=q) | Q(designation_snapshot__icontains=q)
                                    | Q(ticket__config_items__cmdb_link__designation__icontains=q)).distinct()
            processes = processes.filter(Q(intake__ticket_number__icontains=q) | Q(designation__icontains=q))
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
                processes = processes.filter(**{f"intake__source_created_at__date__{lookup}": data[key]})
    active_pending = CancellationPending.objects.filter(process_id=OuterRef("pk"), status="OPEN")
    waiting_document = FinancialObligation.objects.filter(process_id=OuterRef("pk"), document_status="WAITING")
    processes = processes.annotate(has_pending=Exists(active_pending))
    if data.get("pending_kind"):
        processes = processes.filter(Exists(active_pending.filter(kind=data["pending_kind"])))
        intakes = intakes.none()
    if data.get("sent"):
        processes = processes.filter(handoffs__isnull=False).distinct()
        intakes = intakes.none()
    grouped = {row["stage"]: row["n"] for row in processes.values("stage").annotate(n=Count("pk", distinct=True))}
    documents_pending = active_pending.filter(kind__in=("BILL", "DOCUMENT", "RELEASE", "AGREEMENT"))
    metrics = {
        "intake": intakes.filter(status="WAITING").count(), "order": grouped.get("ORDER", 0),
        "request": grouped.get("REQUEST", 0), "completed": grouped.get("COMPLETED", 0),
        "pending": processes.exclude(stage="COMPLETED").filter(has_pending=True).count(),
        "documents": processes.exclude(stage="COMPLETED").filter(
            Q(Exists(documents_pending)) | Q(Exists(waiting_document))
        ).count(),
        "sent": processes.filter(handoffs__isnull=False).distinct().count(),
    }
    tab = data.get("tab") or "intake"
    if tab == "intake":
        situation = data.get("situation") or "WAITING"
        queryset = intakes if situation == "all" else intakes.filter(status=situation)
    elif tab == "pending":
        queryset = processes.exclude(stage="COMPLETED").filter(has_pending=True)
    else:
        queryset = processes.filter(stage={"order": "ORDER", "request": "REQUEST", "completed": "COMPLETED"}[tab])
    return {"form": form, "queryset": queryset, "tab": tab, "metrics": metrics}
