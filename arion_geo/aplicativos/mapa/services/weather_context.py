"""Factual meteorological context; no projected/financial diagnoses or causality."""
from collections import defaultdict
from zoneinfo import ZoneInfo
from django.utils import timezone
from aplicativos.integracoes.models import WeatherLocalCache
from aplicativos.integracoes.services.geo_configuration import get_geo_integration, weather_configuration, weather_sources
from aplicativos.integracoes.services.weather_local import incident_window, locality_key
from aplicativos.integracoes.clients.public_data import PublicDataError


def correlate(events, first, end, eligible):
    if not first or not end:
        return {}
    try:
        cfg = weather_configuration(get_geo_integration("open_meteo"))
    except PublicDataError:
        return {"eligible_tickets":eligible,"located_tickets":0,"covered_tickets":0,"insufficient":eligible,"localities":[],"causality":False}
    keys = {locality_key(e["city"],e["uf"]) for e in events if e["city"] and e["uf"]}
    rows = WeatherLocalCache.objects.filter(location_key__in=keys,period__range=(first.replace(day=1),end),source__in=weather_sources(cfg)).order_by("collected_at")
    caches = {(r.location_key,r.period):r for r in rows}
    totals = {name:set() for name in ("located","covered","daily_covered","rain_days","rain","ruptures_rain","massive_rain","equipment_weather","wind_links","coincidence","contextual","none")}
    localities = {}
    regions = defaultdict(set)
    from aplicativos.integracoes.services.geography import STATE_BY_UF
    for e in events:
        if not e["city"] or not e["uf"]:
            continue
        key = locality_key(e["city"],e["uf"])
        info = localities.setdefault(key,{"city":e["city"],"uf":e["uf"],"tickets":set(),"rain_tickets":set(),"cache":None})
        info["tickets"].add(e["ticket"])
        totals["located"].add(e["ticket"])
        if not e["at"]:
            continue
        cache = caches.get((key,timezone.localtime(e["at"]).date().replace(day=1)))
        if not cache or cache.status!="success":
            continue
        info["cache"] = cache
        observed_date=e["at"].astimezone(ZoneInfo(cache.payload.get("timezone","UTC"))).date().isoformat()
        daily=next((d for d in cache.payload.get("summary",{}).get("daily",[]) if d["date"]==observed_date),{})
        if daily.get("precipitation_sum") is not None:
            totals["daily_covered"].add(e["ticket"])
            if daily["precipitation_sum"]>=1: totals["rain_days"].add(e["ticket"])
        window = incident_window(cache,e["at"])
        if window["state"]=="insufficient":
            continue
        totals["covered"].add(e["ticket"])
        totals[window["state"]].add(e["ticket"])
        rainy = any((r["precipitation"] or 0)>=1 for r in window["hourly"])
        windy = any((r["wind_gusts_10m"] or 0)>=60 for r in window["hourly"])
        if rainy:
            totals["rain"].add(e["ticket"])
            info["rain_tickets"].add(e["ticket"])
            regions[STATE_BY_UF[e["uf"]]["region_id"]].add(e["ticket"])
            if e["category"]=="rompimento": totals["ruptures_rain"].add(e["ticket"])
            if e["category"]=="massiva": totals["massive_rain"].add(e["ticket"])
        if (rainy or windy) and e["category"] in {"equipamento","energia","reset_remoto"}:
            totals["equipment_weather"].add(e["ticket"])
        if windy: totals["wind_links"].add(e["link"])
    output = []
    # One analytical state per distinct ticket, using the strongest available context.
    totals["coincidence"] -= totals["contextual"]
    totals["none"] -= totals["coincidence"] | totals["contextual"]
    for key,info in sorted(localities.items(),key=lambda pair:(-len(pair[1]["tickets"]),pair[0])):
        cache = info["cache"]
        output.append({"key":key,"city":info["city"],"uf":info["uf"],"incidents":len(info["tickets"]),"rain_incidents":len(info["rain_tickets"]),
                       "available":bool(cache),"ibge_code":cache.ibge_code if cache else "",
                       "coordinates":[cache.longitude,cache.latitude] if cache else None,
                       "precision":"Estimativa municipal IBGE · ERA5 aproximadamente 0,25°; não representa a posição ou o trajeto do link.",
                       "weather":cache.payload.get("summary") if cache else None,"source":cache.source if cache else None,
                       "stale":bool(cache and cache.expires_at and cache.expires_at<=timezone.now())})
    return {"eligible_tickets":eligible,"located_tickets":len(totals["located"]),"covered_tickets":len(totals["covered"]),
            "insufficient":max(0,eligible-len(totals["covered"])),**{k:len(v) for k,v in totals.items() if k!="located" and k!="covered"},
            "rain_localities":sum(bool(x["rain_incidents"]) for x in output),"localities":output,
            "region_rain_incidents":[{"region_id":key,"tickets":len(value)} for key,value in regions.items()],
            "time_basis":"Janela de 6 h antes a 2 h depois da abertura OTRS (aproximação; início real ausente). Horas em UTC.",
            "definition":"Tickets técnicos distintos; vínculo municipal CMDB e cache local ERA5. Cidade informada na CMDB não confirma o local da ruptura. Ausência de cache não equivale a ausência de chuva.",
            "causality":False}
