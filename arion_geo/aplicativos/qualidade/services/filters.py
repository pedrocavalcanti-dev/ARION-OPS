"""Quality management windows and scoped CMDB/Quality filter relationships."""
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from aplicativos.integracoes.services.geography import REGIONS, STATES, region_states, state_query
from aplicativos.integracoes.services.project_names import canonical_project_name, display_project_name, project_comparison_key
from .constants import QUALITY_BUSINESS_PRIORITY, QUALITY_PRIORITY_DEFAULT_PERIOD, QUALITY_PRIORITY_PERIOD_OPTIONS


def quality_period(params, now=None):
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    period = str(params.get("period") or ("custom" if params.get("date_from") or params.get("date_to") else QUALITY_PRIORITY_DEFAULT_PERIOD))
    notices = []
    allowed = {value for value, _ in QUALITY_PRIORITY_PERIOD_OPTIONS} | {"180", "365", "month", "previous_month"}
    if period not in allowed:
        period = QUALITY_PRIORITY_DEFAULT_PERIOD
    if period == "custom":
        try:
            start, end = parse_date(str(params.get("date_from", ""))), parse_date(str(params.get("date_to", "")))
        except ValueError:
            start, end = None, None
        if not start or not end or start > end or end > today or (end-start).days > 365:
            notices.append("Informe datas válidas, sem futuro, em um intervalo de até 366 dias. Aplicada a janela padrão de 30 dias.")
            period = QUALITY_PRIORITY_DEFAULT_PERIOD
    if period in {"month", "previous_month"}:
        start = today.replace(day=1)
        end = today
        if period == "previous_month":
            end = start-timedelta(days=1)
            start = end.replace(day=1)
    elif period != "custom":
        start, end = today-timedelta(days=int(period)-1), today
    midnight = lambda day: timezone.make_aware(datetime.combine(day, time.min))
    label=f"{start:%d/%m/%Y} – {end:%d/%m/%Y}" if period=="custom" else dict(QUALITY_PRIORITY_PERIOD_OPTIONS).get(period, f"{(end-start).days+1} dias")
    options=QUALITY_PRIORITY_PERIOD_OPTIONS
    if period not in dict(options):
        options=(*options,(period,label))  # Preserve existing deep links to legacy windows.
    return {"period": period, "start_date": start, "end_date": end,
            "cutoff": midnight(start), "end_exclusive": min(midnight(end+timedelta(days=1)), now+timedelta(microseconds=1)),
            "date_from": start.isoformat() if period=="custom" else "", "date_to": end.isoformat() if period=="custom" else "",
            "period_label":label, "period_options":options,
            "notices": notices}


def strategic_rank(client, project):
    client, project = project_comparison_key(client), project_comparison_key(canonical_project_name(project))
    if client=="FUST" or project=="FUST":
        return 90
    return QUALITY_BUSINESS_PRIORITY.get((client, project), 99)


def normalized_uf(value):
    key = project_comparison_key(value)
    return next((row["sigla"] for row in STATES if key in {row["sigla"], project_comparison_key(row["nome"])}), "")


def dimension_catalog(links, orders=None, extra_records=()):
    records = [(client, project or legacy, uf) for client, project, legacy, uf in links.order_by().values_list("contracting_client", "business_project", "legacy_project", "state").distinct()]
    if orders is not None:
        for client, project, uf, live_client, live_project, legacy, live_uf in orders.order_by().values_list(
                "client_snapshot", "project_snapshot", "uf_snapshot", "link__contracting_client", "link__business_project", "link__legacy_project", "link__state").distinct():
            records.append((client or live_client, project or live_project or legacy, uf or live_uf))
            if live_client or live_project or legacy:
                records.append((live_client or client, live_project or legacy, live_uf))
    records.extend(extra_records)
    clients, groups, pairs, ufs, portfolios = {}, {}, {}, set(), {}
    for raw_client, raw_project, raw_uf in records:
        client = " ".join(str(raw_client or "").split())
        client_key = project_comparison_key(client)
        if client:
            clients.setdefault(client_key, client)
        project = display_project_name(raw_project)
        if project:
            groups.setdefault(canonical_project_name(project), set()).add(raw_project)
            pairs.setdefault(client_key, set()).add(project)
        rank = strategic_rank(client, project)
        portfolios.setdefault(client_key, {})[project] = "claro" if rank in {10,20} else "vivo" if rank in {30,40} else "other"
        uf = normalized_uf(raw_uf)
        if uf:
            ufs.add(uf)
    clients = sorted(clients.values(), key=lambda c: ({"CLARO":0,"VIVO":1,"ALGAR":2}.get(project_comparison_key(c),3), c.casefold()))
    return {"clients":clients, "projects":sorted({display_project_name(key) for key in groups}, key=str.casefold),
            "projects_by_client":{client:sorted(pairs.get(project_comparison_key(client),()),key=str.casefold) for client in clients},
            "project_groups":{key:tuple(values) for key, values in groups.items()},
            "ufs":sorted(ufs), "regions":[(r["sigla"],r["nome"]) for r in REGIONS],
            "ufs_by_region":{r["sigla"]:sorted(ufs.intersection(region_states(r["sigla"]))) for r in REGIONS},
            "portfolios":{client:portfolios.get(project_comparison_key(client),{}) for client in clients}}


def resolve_dimensions(selected, catalog, *, client_key="client", project_key="project"):
    notices, blocked = [], False
    requested_client = selected.get(client_key, "")
    client = next((c for c in catalog["clients"] if project_comparison_key(c)==project_comparison_key(requested_client)), requested_client)
    selected[client_key] = client
    if client and client not in catalog["clients"]:
        blocked=True
        notices.append("Operadora indisponível no escopo autorizado.")
    project = display_project_name(selected.get(project_key, ""))
    if project and project not in catalog["projects"]:
        blocked=True
        notices.append("Projeto indisponível no escopo autorizado.")
    elif project and client and project not in catalog["projects_by_client"].get(client, []):
        project=""
        notices.append("O projeto anterior foi limpo porque não pertence à operadora selecionada.")
    selected[project_key]=project
    region = str(selected.get("region", "")).upper()
    if region and region not in {code for code,_ in catalog["regions"]}:
        blocked=True
        notices.append("Macrorregião inválida.")
    selected["region"]=region
    requested_uf=selected.get("uf", "")
    uf=normalized_uf(requested_uf)
    if requested_uf and (not uf or uf not in catalog["ufs"]):
        blocked=True
        notices.append("UF indisponível no escopo autorizado.")
    elif uf and region and uf not in catalog["ufs_by_region"].get(region, []):
        uf=""
        notices.append("A UF anterior foi limpa porque não pertence à macrorregião selecionada.")
    selected["uf"]=uf
    portfolio=selected.get("portfolio", "")
    if portfolio:
        candidates=[value for c, projects in catalog["portfolios"].items() if not client or c==client for p,value in projects.items() if not project or p==project]
        if portfolio not in candidates:
            blocked=True
            notices.append("A carteira estratégica não é compatível com a operadora/projeto deste recorte. Revise os filtros.")
    return {"selected":selected,"blocked":blocked,"notices":notices,"catalog":catalog,
            "aliases":catalog["project_groups"].get(canonical_project_name(project), ())}


def filter_links(links, dimensions, *, client_key="client", project_key="project"):
    selected=dimensions["selected"]
    if dimensions["blocked"]:
        return links.none()
    if selected.get(client_key):
        links=links.filter(contracting_client__iexact=selected[client_key])
    if selected.get(project_key):
        aliases=dimensions["aliases"]
        links=links.filter(Q(business_project__in=aliases)|Q(business_project="",legacy_project__in=aliases))
    if selected.get("region"):
        links=links.filter(state_query(region_states(selected["region"])))
    if selected.get("uf"):
        links=links.filter(state_query([selected["uf"]]))
    return links


def filter_orders(orders, dimensions, *, client_key="client", project_key="project"):
    selected=dimensions["selected"]
    if dimensions["blocked"]:
        return orders.none()
    if selected.get(client_key):
        orders=orders.filter(Q(client_snapshot__iexact=selected[client_key])|Q(link__contracting_client__iexact=selected[client_key]))
    if selected.get(project_key):
        aliases=dimensions["aliases"]
        orders=orders.filter(Q(project_snapshot__in=aliases)|Q(link__business_project__in=aliases)|Q(link__business_project="",link__legacy_project__in=aliases))
    codes=[selected["uf"]] if selected.get("uf") else region_states(selected["region"]) if selected.get("region") else []
    if codes:
        orders=orders.filter(state_query(codes, "uf_snapshot")|state_query(codes,"link__state"))
    return orders
