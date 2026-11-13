"""Scoped management aggregates; never counts intake or sums individual pendings."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django import forms
from django.core.exceptions import PermissionDenied
from django.db.models import Exists, Max, OuterRef, Q
from django.utils import timezone

from aplicativos.base_links.services.cmdb.excel_provider import normalize_provider
from aplicativos.integracoes.services.link_business_identity import BusinessIdentityRules, comparison_key
from aplicativos.integracoes.services.project_names import canonical_project_name, display_project_name
from ..models import CancellationPending, CancellationProcess, FinancialObligation, LegacyCancellationReference
from .data_scope import CancellationScope
from .financial_totals import format_brl, money_columns, obligation_effective_amount, principal_kind

DIMENSIONS = ("contracting_client", "project", "final_client", "isp", "link__operator__name")
KINDS = ("CONTRACT", "EQUIPMENT", "OTHER")


def dimension(row, prefix=""):
    client = row[prefix + "contracting_client"] or ""
    client = BusinessIdentityRules.client_for_token(client) or " ".join(client.split())
    project = row[prefix + "project"] or row[prefix + "final_client"] or ""
    project = display_project_name(BusinessIdentityRules.canonical_project(canonical_project_name(project)))
    raw_isp = row[prefix + "link__operator__name"] or row[prefix + "isp"] or ""
    isp, isp_key, _ = normalize_provider(raw_isp)
    context_key = comparison_key(client) + " / " + comparison_key(project)
    return {"context_key": context_key, "context": f"{client or 'Cliente não identificado'} / {project or 'Projeto não identificado'}",
            "isp_key": isp_key or "__unidentified__", "isp": isp or "ISP não identificada"}


class DashboardFilterForm(forms.Form):
    period = forms.ChoiceField(label="Período", choices=(("year", "Este ano"), ("month", "Este mês"),
        ("3months", "Últimos 3 meses"), ("6months", "Últimos 6 meses"), ("custom", "Personalizado")))
    start = forms.DateField(label="De", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="Até", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    context = forms.ChoiceField(label="Cliente / projeto", required=False)
    isp = forms.ChoiceField(label="ISP", required=False)

    def __init__(self, *args, contexts, isps, default_year, **kwargs):
        self.today, self.default_year = timezone.localdate(), default_year
        super().__init__(*args, **kwargs)
        self.fields["context"].choices = [("", "Todos os clientes/projetos"), *sorted(contexts.items(), key=lambda p: p[1].casefold())]
        self.fields["isp"].choices = [("", "Todas as ISPs"), *sorted(isps.items(), key=lambda p: p[1].casefold())]
        if default_year != self.today.year:
            self.fields["period"].choices = [("history", f"Ano com registros · {default_year}"), *self.fields["period"].choices]

    def clean(self):
        data = super().clean()
        period, today = data.get("period"), self.today
        if period == "custom":
            if not data.get("start") or not data.get("end"):
                raise forms.ValidationError("Informe as duas datas do período personalizado.")
        elif period in {"year", "history"}:
            year = self.default_year if period == "history" else today.year
            data.update(start=date(year, 1, 1), end=today if year == today.year else date(year, 12, 31))
        elif period:
            months = {"month": 1, "3months": 3, "6months": 6}[period]
            index = today.year * 12 + today.month - months
            year, month = divmod(index, 12)
            data.update(start=date(year, month + 1, 1), end=today)
        if data.get("start") and data.get("end"):
            if not 1990 <= data["start"].year <= data["end"].year <= 2100:
                raise forms.ValidationError("Selecione datas entre 1990 e 2100.")
            if data["start"] > data["end"]:
                raise forms.ValidationError("A data inicial deve ser anterior à final.")
            if data["end"].year * 12 + data["end"].month - data["start"].year * 12 - data["start"].month > 119:
                raise forms.ValidationError("Selecione até dez anos por consulta.")
        return data


def _bucket():
    return {"count": 0, "completed": 0, "amounts": {k: None for k in KINDS},
            "known": defaultdict(int), "nulls": defaultdict(int),
            "estimated": None, "paid": None, "open": None}


def _finish(bucket):
    partial = any(bucket["known"][k] < bucket["count"] or bucket["nulls"][k] for k in KINDS)
    known = [v for v in bucket["amounts"].values() if v is not None]
    total = sum(known, Decimal(0)) if known else None
    return {"count": bucket["count"], "completed": bucket["completed"], "partial": partial,
            "total": total, "total_label": format_brl(total),
            "estimated": bucket["estimated"], "estimated_label": format_brl(bucket["estimated"]),
            "paid": bucket["paid"], "paid_label": format_brl(bucket["paid"]),
            "open": bucket["open"], "open_label": format_brl(bucket["open"]),
            **{k.lower(): bucket["amounts"][k] for k in KINDS},
            **{k.lower() + "_label": format_brl(bucket["amounts"][k]) for k in KINDS}}


def _local_datetime(value):
    # ORM datetimes are aware with USE_TZ; never guess missing/invalid historical dates.
    if not isinstance(value, datetime) or timezone.is_naive(value):
        return None
    try:
        return timezone.localtime(value)
    except (ValueError, OverflowError):
        return None


def _month_key(value):
    local = _local_datetime(value)
    return f"{local.year:04}-{local.month:02}" if local else None


def build_dashboard(user, params):
    if not user.has_perm("cancelamentos.view_cancellations"):
        raise PermissionDenied
    base = CancellationScope.processes(user)
    latest = _local_datetime(base.aggregate(latest=Max("created_at"))["latest"])
    default_year = latest.year if latest else timezone.localdate().year
    contexts, isps, catalog = {}, {}, []
    for raw in base.order_by().values(*DIMENSIONS).distinct():
        item = dimension(raw)
        contexts.setdefault(item["context_key"], item["context"])
        isps.setdefault(item["isp_key"], item["isp"])
        catalog.append((raw, item))
    data = params.copy()
    if not data.get("period"):
        data["period"] = "year" if default_year == timezone.localdate().year else "history"
    form = DashboardFilterForm(data, contexts=contexts, isps=isps, default_year=default_year)
    valid = form.is_valid()
    filters = form.cleaned_data if valid else {}
    processes = base if valid else base.none()
    if valid:
        start = timezone.make_aware(datetime.combine(filters["start"], time.min))
        end = timezone.make_aware(datetime.combine(filters["end"] + timedelta(days=1), time.min))
        processes = processes.filter(created_at__gte=start, created_at__lt=end)
        if filters.get("context") or filters.get("isp"):
            allowed = Q(pk__in=[])
            for raw, item in catalog:
                if ((not filters.get("context") or item["context_key"] == filters["context"])
                        and (not filters.get("isp") or item["isp_key"] == filters["isp"])):
                    allowed |= Q(**raw)
            processes = processes.filter(allowed)
    can_finance = user.has_perm("cancelamentos.view_cancellation_finance")
    total, months, providers, clients = _bucket(), defaultdict(_bucket), defaultdict(_bucket), defaultdict(_bucket)
    stages = {stage: 0 for stage, _ in CancellationProcess.Stage.choices}
    # Group raw timestamps in Python, preserving local timezone without requiring MySQL TZ tables.
    proc_rows = list(processes.order_by().values("pk", "created_at", "stage", *DIMENSIONS))
    obligations_by_proc = defaultdict(list)
    pendings_by_proc = defaultdict(list)
    if can_finance and proc_rows:
        for ob in (FinancialObligation.objects.filter(process__in=processes)
                   .values("process_id", "kind", "estimated_amount", "charged_amount",
                           "paid_amount", "financial_status", "legacy_field",
                           "reconciliation_state", "source_pending_id").iterator()):
            obligations_by_proc[ob["process_id"]].append(ob)
        legacy_ref = LegacyCancellationReference.objects.filter(pending_id=OuterRef("pk"))
        for pend in (CancellationPending.objects.filter(process__in=processes)
                     .annotate(_has_legacy=Exists(legacy_ref))
                     .values("pk", "process_id", "kind", "status", "amount", "_has_legacy").iterator()):
            pendings_by_proc[pend["process_id"]].append(pend)
    for row in proc_rows:
        item = dimension(row)
        key = _month_key(row["created_at"])
        provider, client = providers[item["isp_key"]], clients[item["context_key"]]
        provider["label"], client["label"] = isps[item["isp_key"]], contexts[item["context_key"]]
        stages[row["stage"]] += 1
        targets = (total, provider, client, *([months[key]] if key else []))
        for bucket in targets:
            bucket["count"] += 1
            bucket["completed"] += 1 if row["stage"] == "COMPLETED" else 0
        if can_finance:
            proc_obs = obligations_by_proc.get(row["pk"], ())
            fin = money_columns(proc_obs, pendings_by_proc.get(row["pk"], ()))
            proc_cats = {"CONTRACT": fin["fine"], "EQUIPMENT": fin["equipment"], "OTHER": fin["other"]}
            null_cats = {principal_kind(ob["kind"]) for ob in proc_obs if obligation_effective_amount(ob) is None}
            for bucket in targets:
                for category in KINDS:
                    if proc_cats[category] is not None:
                        bucket["amounts"][category] = (
                            (bucket["amounts"][category] or Decimal(0)) + proc_cats[category].quantize(Decimal("0.01"))
                        )
                        bucket["known"][category] += 1
                    if category in null_cats:
                        bucket["nulls"][category] += 1
                for metric_key in ("estimated", "paid", "open"):
                    if fin[metric_key] is not None:
                        bucket[metric_key] = (
                            (bucket[metric_key] or Decimal(0)) + fin[metric_key].quantize(Decimal("0.01"))
                        )
    # Calendar gaps are zero counts; absent monetary fields stay NULL, never zero.
    keys = []
    if valid:
        index = filters["start"].year * 12 + filters["start"].month - 1
        last = filters["end"].year * 12 + filters["end"].month - 1
        for i in range(index, last + 1):
            year, month = divmod(i, 12)
            keys.append(f"{year:04}-{month + 1:02}")
    monthly = [{"key": key, "label": f"{key[5:]}/{key[:4]}", **_finish(months[key])} for key in keys]
    provider_rows = sorted(({"label": b["label"], **_finish(b)} for b in providers.values()), key=lambda b: (-b["count"], b["label"].casefold()))[:8]
    client_rows = sorted(({"label": b["label"], **_finish(b)} for b in clients.values()), key=lambda b: (-b["count"], b["label"].casefold()))[:8]
    metrics = _finish(total)
    metrics["ongoing"] = metrics["count"] - metrics["completed"]
    charts = {"months": [{k: row[k] for k in ("label", "count")} for row in monthly],
              "stages": [{"name": label, "value": stages[stage]} for stage, label in CancellationProcess.Stage.choices],
              "providers": [{"label": r["label"], "count": r["count"],
                             **({"total_label": r["total_label"], "partial": r["partial"]} if can_finance else {})} for r in provider_rows]}
    if can_finance:
        charts["finance"] = [{k: row[k] for k in ("label", "contract", "equipment", "other", "contract_label", "equipment_label", "other_label", "total_label", "partial")} for row in monthly]
    else:
        # No monetary data reaches templates, inline JSON or ranking tooltips.
        for row in [metrics, *client_rows]:
            for key in list(row):
                if key not in {"count", "completed", "ongoing", "label"}:
                    del row[key]
    if valid:
        for name in ("start", "end"):
            form.data = form.data.copy()
            form.data[name] = filters[name].isoformat()
    return {"dashboard_form": form, "metrics": metrics, "can_finance": can_finance,
            "charts": charts, "client_rows": client_rows, "is_empty": not metrics["count"]}
