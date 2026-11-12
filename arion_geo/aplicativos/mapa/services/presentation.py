"""Sanitized client presentation; projections never become source diagnoses."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from django.conf import settings

from aplicativos.integracoes.services.geography import REGIONS, STATE_BY_UF
from aplicativos.integracoes.services.incidents import IncidentRules
from .analytics import MapAnalyticsService

TECHNICAL = ("Massiva","Rompimento","Equipamento","Energia","Configuração","Reset remoto","Demais motivos técnicos","Sem classificação")


def project_distribution(total, weights):
    if isinstance(total,bool) or not isinstance(total,int) or total < 0:
        raise ValueError("Total de projeção inválido.")
    try:
        values = [(label,Decimal(str(weight))) for label,weight in weights.items()]
        if not values or any(label not in TECHNICAL or not weight.is_finite() or weight<0 for label,weight in values):
            raise ValueError
        denominator = sum(weight for _,weight in values)
        if denominator <= 0:
            raise ValueError
    except (AttributeError, InvalidOperation, TypeError, ValueError):
        raise ValueError("Pesos de projeção inválidos.") from None
    exact = [(label,Decimal(total)*weight/denominator) for label,weight in values]
    output = {label:int(value.to_integral_value(rounding=ROUND_FLOOR)) for label,value in exact}
    # Input order breaks equal remainders deterministically (29,22,21 for 72).
    order = sorted(range(len(exact)),key=lambda i: (-(exact[i][1]-output[exact[i][0]]),i))
    for i in order[:total-sum(output.values())]:
        output[exact[i][0]] += 1
    return output


def _causes(row):
    source = row.get("category_counts",{})
    proven = {key:value for key,value in source.items() if key in TECHNICAL}
    eligible = source.get("Pendência Financeira",0)
    weights = getattr(settings,"GEO_PRESENTATION_WEIGHTS",{"Massiva":40,"Rompimento":30,"Equipamento":30})
    if getattr(settings,"GEO_PRESENTATION_WEIGHT_MODE","configured") == "technical" and sum(proven.values()):
        weights = {key:value for key,value in proven.items() if key != "Sem classificação"} or weights
    return proven,project_distribution(eligible,weights)


def presentation_payload(user,params):
    source = MapAnalyticsService.query(user,params,fixed_client=True)
    technical,projection = _causes(source)
    state_fields = ("total_links","ticket_count","affected_links","recurrence_links","recurrence_tickets","daily_counts","technical_daily_counts","project_counts","provider_counts","ticket_project_counts","ticket_provider_counts")
    states = {}
    region_links = {region["nome"]:0 for region in REGIONS}
    for uf,row in source["states"].items():
        region_name = next(r["nome"] for r in REGIONS if r["id"]==STATE_BY_UF[uf]["region_id"])
        region_links[region_name] += row["total_links"]
        proven,estimated = _causes(row)
        states[uf] = {key:row.get(key,0) for key in state_fields}
        states[uf].update({"ticket_reason_counts":proven,"reason_counts":proven,"category_counts":proven,
                           "distribution_projected":estimated,"client_counts":{params["client"]:row["total_links"]}})
    # Explicit allowlist: no original motives, titles, tickets, accounts or financial data.
    return {"mode":params.get("mode","calls"),"states":states,"period":source["period"],"region_link_counts":region_links,
            "region":source["region"],"allowed_ufs":source["allowed_ufs"],"projects":source["projects"],
            "selected_project":params.get("project",""),"providers":source["providers"],"clients":[params["client"]],"priority_options":source.get("priority_options",[]),
            "filtered_source_total":source["filtered_source_total"],"source_total":source["filtered_source_total"],
            "ticket_count":source["ticket_count"],"affected_link_count":source["affected_link_count"],
            "recurrence_link_count":source["recurrence_link_count"],"recurrence_ticket_count":source["recurrence_ticket_count"],
            "terminal_ticket_count":source["ticket_count"],
            "ticket_reason_counts":technical,"category_counts":technical,"distribution_projected":projection,
            "daily_counts":source["daily_counts"],"technical_daily_counts":source["technical_daily_counts"],
            "definitions":{"source_total":"Links CMDB no recorte autorizado; não representa tickets.","filtered_source_total":"Links CMDB no recorte autorizado; não representa tickets.","ticket_count":"Incidentes válidos distintos por TicketID, incluindo os sem UF elegível.","category_counts":"Classificações técnicas registradas; não inclui estimativas.","distribution_projected":"Estimativa estatística separada das classificações registradas.","technical_daily_counts":"Tickets distintos com classificação técnica comprovada por dia de criação."},
            "projection_label":"Distribuição projetada · estimativa, não diagnóstico",
            "reasons":list(technical),"reason_options":[{"value":o["value"],"label":o["label"]} for o in source["reason_options"] if IncidentRules.category(o["value"]) not in {"financeiro","sem_classificacao"}],"presentation":True}
