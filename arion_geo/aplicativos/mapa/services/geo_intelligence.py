"""Operational/monthly climate comparison, using only cached external data."""
from datetime import timedelta
from django.utils import timezone

from aplicativos.integracoes.services.geography import REGIONS, STATES, region_states
from aplicativos.integracoes.services.weather import cached_weather, month_bounds, collection_readiness, spatial_value, VARIABLES, circular_mean
from aplicativos.integracoes.models import WeatherCollectionJob, IntegrationLease
from .analytics import MapAnalyticsService


def aggregate_weather(rows):
    """Area-weighted UF means; regional rainfall is never a sum of mm."""
    if not rows:
        return {"available":False,"daily":[]}
    daily = {}
    for row in rows.values():
        for item in row["daily"]:
            daily.setdefault(item["date"],[]).append((item,row["area_km2"]))
    series = []
    for day,values in sorted(daily.items()):
        rain = [(r["precipitation_sum"],weight) for r,weight in values if r["precipitation_sum"] is not None]
        wind = [r["wind_speed_10m_max"] for r,_ in values if r["wind_speed_10m_max"] is not None]
        gust = [r["wind_gusts_10m_max"] for r,_ in values if r["wind_gusts_10m_max"] is not None]
        series.append({"date":day,"precipitation_sum":sum(v*w for v,w in rain)/sum(w for _,w in rain) if rain else None,
                       "wind_speed_10m_max":max(wind) if wind else None,"wind_gusts_10m_max":max(gust) if gust else None})
        for variable in VARIABLES:
            if variable not in series[-1]:
                series[-1][variable] = spatial_value(variable,[(r[variable],w) for r,w in values if r.get(variable) is not None])
    rains = [r["precipitation_sum"] for r in series if r["precipitation_sum"] is not None]
    winds = [r["wind_speed_10m_max"] for r in series if r["wind_speed_10m_max"] is not None]
    gusts = [r["wind_gusts_10m_max"] for r in series if r["wind_gusts_10m_max"] is not None]
    temperatures = [r["temperature_2m_mean"] for r in series if r["temperature_2m_mean"] is not None]
    low = [r["temperature_2m_min"] for r in series if r["temperature_2m_min"] is not None]
    high = [r["temperature_2m_max"] for r in series if r["temperature_2m_max"] is not None]
    return {"available":True,"temperature_mean_c":sum(temperatures)/len(temperatures) if temperatures else None,
            "temperature_min_c":min(low) if low else None,"temperature_max_c":max(high) if high else None,
            "wind_direction_deg":circular_mean([(r["wind_direction_10m_dominant"],1) for r in series if r["wind_direction_10m_dominant"] is not None]),
            "heavy_rain_days":sum(r>=20 for r in rains) if rains else None,
            "precipitation_mm":round(sum(rains),2) if rains else None,
            "rainy_days":sum(r>=1 for r in rains) if rains else None,"rainy_days_percent":round(sum(r>=1 for r in rains)/len(rains)*100,1) if rains else None,
            "wind_max_kmh":max(winds) if winds else None,"gust_max_kmh":max(gusts) if gusts else None,
            "points":sum(r["points"] for r in rows.values()),"daily":series,"covered_ufs":len(rows),
            "completeness":min(r["completeness"] for r in rows.values()),"stale":any(r["stale"] for r in rows.values()),
            "partial":any(r.get("partial") for r in rows.values()),"source":"Open-Meteo / Copernicus ERA5", "method":", ".join(sorted({r.get("method","area-grid-v1") for r in rows.values()}))}


def intelligence_payload(user,params,*,presentation=False,summary=None):
    selected = dict(params.items())
    selected["mode"] = "calls"
    first,end = month_bounds(selected.get("period"))
    selected["period"] = first.strftime("%Y-%m")
    if summary is not None:
        pass
    elif presentation:
        from .presentation import presentation_payload
        summary = presentation_payload(user,selected)
    else:
        summary = MapAnalyticsService.query(user,selected)
    ufs = [uf for uf,row in summary["states"].items() if row.get("total_links") or row.get("ticket_count")]
    weather = cached_weather(ufs,first.strftime("%Y-%m"))
    regional = aggregate_weather(weather)
    # Daily ticket observations are deduplicated; no projection enters coincidence.
    operational = summary["technical_daily_counts"]
    regional["coincident_days"] = sum(1 for day in regional["daily"] if day["precipitation_sum"] is not None and day["precipitation_sum"]>=1 and operational.get(day["date"],0)>0)
    for uf,row in weather.items():
        daily = summary["states"].get(uf,{}).get("technical_daily_counts",{})
        row["coincident_days"] = sum(1 for day in row["daily"] if day["precipitation_sum"] is not None and day["precipitation_sum"]>=1 and daily.get(day["date"],0)>0)
    series = []
    for i in range((end-first).days+1):
        day = (first+timedelta(days=i)).isoformat()
        climate = next((r for r in regional["daily"] if r["date"]==day),{})
        series.append({"date":day,"rain_mm":climate.get("precipitation_sum"),"tickets":summary["daily_counts"].get(day,0),"technical_tickets":operational.get(day,0),
                       "temperature_mean_c":climate.get("temperature_2m_mean"),"temperature_min_c":climate.get("temperature_2m_min"),"temperature_max_c":climate.get("temperature_2m_max"),
                       "wind_max_kmh":climate.get("wind_speed_10m_max"),"gust_max_kmh":climate.get("wind_gusts_10m_max"),"wind_direction_deg":climate.get("wind_direction_10m_dominant")})
    readiness = collection_readiness()
    jobs = list(WeatherCollectionJob.objects.filter(uf__in=ufs,period=first).values("uf","status"))
    if readiness["status"] == "ready":
        statuses={j["status"] for j in jobs}
        state = "running" if "running" in statuses else "queued" if "pending" in statuses else "completed" if weather and not any(r["stale"] for r in weather.values()) and len(weather)==len(ufs) else "unavailable"
        messages={"running":"Coleta em andamento.","queued":"Coleta agendada no worker.","completed":"Cache climático disponível.","unavailable":"Dados indisponíveis para todo ou parte do recorte. Solicite a coleta autorizada."}
        readiness={"status":state,"message":messages[state]}
        if state == "queued" and not IntegrationLease.objects.filter(key="worker",expires_at__gt=timezone.now()).exists():
            readiness["message"] = "Coleta agendada. Worker sem lease ativo; verifique o serviço na Central."
    if presentation:
        readiness={"status":"completed" if weather else "unavailable","message":"Contexto climático disponível." if weather else "Contexto climático indisponível neste recorte."}
    return {"summary":summary,"weather":weather,"climate":regional,"daily":series,
            "collection":readiness,
            "period":{"month":first.strftime("%Y-%m"),"partial":first==timezone.localdate().replace(day=1)},
            "missing_ufs":[uf for uf in ufs if uf not in weather],"eligible_ufs":ufs,
            "notice":"Coincidência temporal é contexto; não confirma causa meteorológica. Chuva representa estimativa territorial, não medição única da UF.",
            "catalog":{"regions":list(REGIONS),"states":[s for s in STATES if s["sigla"] in region_states(params.get("region",""))]}}
