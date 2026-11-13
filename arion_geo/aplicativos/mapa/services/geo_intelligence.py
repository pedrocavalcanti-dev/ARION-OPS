"""Operational/monthly climate comparison, using only cached external data."""
from datetime import timedelta
from django.utils import timezone
from django.conf import settings

from aplicativos.integracoes.services.geography import REGIONS, STATES, region_states
from aplicativos.integracoes.services.weather import cached_weather, collection_readiness, spatial_value, VARIABLES, circular_mean, summarize, sampling_points
from aplicativos.integracoes.models import WeatherCollectionJob, IntegrationSyncState
from .analytics import MapAnalyticsService, _bounds
from aplicativos.integracoes.services.geo_operations import worker_state, allowed_weather_ufs


def weather_range(ufs, first, end):
    month = first.replace(day=1)
    grouped = {}
    while month<=end:
        for uf,row in cached_weather(ufs,month.strftime("%Y-%m")).items():
            grouped.setdefault(uf,[]).append(row)
        month = (month.replace(day=28)+timedelta(days=4)).replace(day=1)
    result = {}
    for uf,rows in grouped.items():
        if len({(row["model"],row.get("source_info",{}).get("timezone","America/Sao_Paulo")) for row in rows})>1:
            continue
        days = {d["date"]:d for row in rows for d in row["daily"] if first.isoformat()<=d["date"]<=end.isoformat()}
        series = [days[d] for d in sorted(days)]
        if not series: continue
        area = rows[-1]["area_km2"]
        sample = {"area_km2":area,"daily":{"time":[d["date"] for d in series],**{key:[d.get(key) for d in series] for key in VARIABLES}}}
        summary = summarize([sample])
        summary.update(points=max(row["points"] for row in rows),source=rows[-1]["source"],model=rows[-1]["model"],method=rows[-1]["method"],
            collected_at=max(row["collected_at"] for row in rows),stale=any(row["stale"] for row in rows),
            completeness=min(row["completeness"] for row in rows)*len(series)/((end-first).days+1),
            partial=len(series)<(end-first).days+1,source_info=rows[-1].get("source_info",{}),
            month_versions=[{"month":r["daily"][0]["date"][:7],"revision":r.get("revision",1),"consolidated":r.get("consolidated",False)} for r in rows if r.get("daily")])
        result[uf]=summary
    return result


def climate_rankings(weather):
    if len({(r.get("model","era5"),r.get("source_info",{}).get("timezone","America/Sao_Paulo")) for r in weather.values()})>1:
        return {}
    complete = {uf:row for uf,row in weather.items() if row.get("completeness",0)>=.99 and not row.get("partial") and not row.get("stale")}
    keys = {"precipitation_mm":False,"rainy_days":False,"gust_max_kmh":False,"temperature_max_c":False,"temperature_min_c":True}
    return {key:[{"uf":uf,"value":row[key]} for uf,row in sorted(complete.items(),key=lambda pair:((pair[1].get(key) if ascending else -(pair[1].get(key) or 0)) if pair[1].get(key) is not None else float('inf'),pair[0])) if row.get(key) is not None] for key,ascending in keys.items()}


def aggregate_weather(rows):
    """Area-weighted UF means; regional rainfall is never a sum of mm."""
    if not rows:
        return {"available":False,"daily":[]}
    identities={(r.get("model","era5"),r.get("source_info",{}).get("timezone","America/Sao_Paulo")) for r in rows.values()}
    if len(identities)>1:
        return {"available":False,"daily":[],"source":"Múltiplos modelos/referências temporais", "mixed_sources":True,
                "message":"Consulte as UFs separadamente: séries de modelos ou fusos distintos não são agregadas."}
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
            "partial":any(r.get("partial") for r in rows.values()),"timezone":next(iter(identities))[1],
            "source":", ".join(sorted({r.get("source_info",{}).get("attribution",r.get("source","Open-Meteo / Copernicus ERA5")) for r in rows.values()})), "method":", ".join(sorted({r.get("method","area-grid-v1") for r in rows.values()}))}


def intelligence_payload(user,params,*,presentation=False,summary=None):
    selected = dict(params.items())
    selected["mode"] = "calls"
    first,end = _bounds(selected)
    if summary is not None:
        pass
    elif presentation:
        from .presentation import presentation_payload
        summary = presentation_payload(user,selected)
    else:
        summary = MapAnalyticsService.query(user,selected)
    authorized_ufs=set(allowed_weather_ufs(user))
    ufs = [uf for uf in region_states(selected.get("region","")) if uf in authorized_ufs]
    if selected.get("uf"):
        ufs=[uf for uf in ufs if uf==selected["uf"]]
    if presentation or selected.get("client") not in {None,"","all"} or selected.get("project"):
        ufs=[uf for uf in ufs if summary["states"].get(uf,{}).get("total_links") or summary["states"].get(uf,{}).get("ticket_count")]
    weather = weather_range(ufs,first,end)
    regional = aggregate_weather(weather)
    total_area = sum(sum(p["area_km2"] for p in sampling_points(uf)) for uf in ufs)
    covered_area = sum(row["area_km2"]*row["completeness"] for row in weather.values())
    complete_ufs = sum(row["completeness"]>=.99 and not row["partial"] and not row["stale"] for row in weather.values())
    regional.update(temporal_partial=any(row.get("partial") for row in weather.values()),expected_ufs=len(ufs),complete_ufs=complete_ufs,territorial_percent=round(100*covered_area/total_area,2) if total_area else 0,
        coverage_label=f"Cobertura {'completa' if complete_ufs==len(ufs) and ufs else 'parcial'}: {complete_ufs} de {len(ufs)} UFs",
        partial=complete_ufs<len(ufs),estimate_scope="Recorte coberto; não representa todo o território enquanto a cobertura for parcial.")
    # Daily ticket observations are deduplicated; no projection enters coincidence.
    operational = summary["technical_daily_counts"]
    for uf,row in weather.items():
        daily = summary["states"].get(uf,{}).get("technical_daily_counts",{})
        row["coincident_days"] = sum(1 for day in row["daily"] if day["precipitation_sum"] is not None and day["precipitation_sum"]>=1 and daily.get(day["date"],0)>0) if row.get("source_info",{}).get("timezone","America/Sao_Paulo")=="America/Sao_Paulo" and any(day["precipitation_sum"] is not None for day in row["daily"]) else None
    compatible_time=regional.get("available") and regional.get("timezone","America/Sao_Paulo")=="America/Sao_Paulo"
    regional["coincident_days"] = len({day["date"] for uf,row in weather.items() for day in row["daily"] if day.get("precipitation_sum") is not None and day["precipitation_sum"]>=1 and summary["states"].get(uf,{}).get("technical_daily_counts",{}).get(day["date"],0)>0}) if compatible_time else None
    series = []
    for i in range((end-first).days+1):
        day = (first+timedelta(days=i)).isoformat()
        climate = next((r for r in regional["daily"] if r["date"]==day),{})
        series.append({"date":day,"rain_mm":climate.get("precipitation_sum"),"tickets":summary["daily_counts"].get(day,0),"technical_tickets":operational.get(day,0),
                       "temperature_mean_c":climate.get("temperature_2m_mean"),"temperature_min_c":climate.get("temperature_2m_min"),"temperature_max_c":climate.get("temperature_2m_max"),
                       "wind_max_kmh":climate.get("wind_speed_10m_max"),"gust_max_kmh":climate.get("wind_gusts_10m_max"),"wind_direction_deg":climate.get("wind_direction_10m_dominant")})
    readiness = collection_readiness()
    worker=worker_state()
    jobs = list(WeatherCollectionJob.objects.filter(uf__in=ufs,period__range=(first.replace(day=1),end)).values("uf","status"))
    can_collect = readiness["status"] == "ready"
    configuration_status = readiness.get("configuration_status", readiness["status"])
    if readiness["status"] == "ready":
        statuses={j["status"] for j in jobs}
        state = "running" if "running" in statuses else "queued" if "pending" in statuses else "failed" if "error" in statuses else "paused" if "paused" in statuses else "cancelled" if "cancelled" in statuses else "completed" if weather and not any(r["stale"] for r in weather.values()) and len(weather)==len(ufs) else "awaiting_collection"
        messages={"running":"Processando coleta no worker.","queued":"Na fila de coleta do worker.","completed":"Concluído · cache climático disponível.","failed":"Falha na coleta. Consulte a saúde da integração antes de tentar novamente.","awaiting_collection":"Aguardando coleta para todo ou parte do recorte."}
        messages.update(paused="Fila pausada pelo operador. Retome na Central de Integrações.",cancelled="Pendências canceladas pelo operador; cache preservado.")
        readiness={"status":state,"message":messages[state]}
        if state == "queued" and not worker["active"]:
            readiness["message"] = "Coleta agendada. Worker sem lease ativo; verifique o serviço na Central."
        cooldown=IntegrationSyncState.objects.filter(key="weather-cooldown",change_cursor__gt=timezone.now()).first()
        if state=="queued" and cooldown:
            readiness["message"]=f"Aguardando orçamento ou liberação da fonte até {timezone.localtime(cooldown.change_cursor):%d/%m %H:%M}. Worker {worker['label'].lower()}."
    readiness.update(configuration_status=configuration_status, can_collect=can_collect and bool(ufs) and first.replace(day=1)==end.replace(day=1), cache_available=bool(weather),worker=worker,
        jobs={s:sum(j["status"]==s for j in jobs) for s in ("pending","running","error","paused","cancelled")})
    from aplicativos.integracoes.services.geo_configuration import get_geo_integration, weather_configuration
    from aplicativos.integracoes.clients.public_data import PublicDataError
    try:
        cfg=weather_configuration(get_geo_integration("open_meteo"))
        readiness["daily_budget"]=int(cfg["daily_budget"])
        readiness["requests_today"]=IntegrationSyncState.objects.filter(key=("nasa-" if cfg["preferred_provider"]=="nasa_power" else "meteo-")+timezone.localdate().isoformat()).values_list("records_count",flat=True).first() or 0
    except PublicDataError:
        pass
    readiness["checkpoint_cells"]=sum(len(p.get("samples",{})) for p in WeatherCollectionJob.objects.filter(uf__in=ufs,period__range=(first.replace(day=1),end),status__in=("running","pending","paused","error")).values_list("progress",flat=True))
    regional["last_update"]=max((row["collected_at"] for row in weather.values()),default=None)
    if presentation:
        readiness={"status":"completed" if weather else "unavailable","message":"Contexto climático disponível." if weather else "Contexto climático indisponível neste recorte."}
    if presentation:
        for row in weather.values():
            info=row.get("source_info",{})
            row["source_info"]={key:info[key] for key in ("type","attribution","conditions","terms_url") if key in info}
        # Option catalogs belong to /dados/, never to the local daily series.
        summary={key:value for key,value in summary.items() if key not in {"projects","providers","clients","priority_options","reasons","reason_options"}}
    return {"summary":summary,"weather":weather,"climate":regional,"daily":series,"comparison_time_compatible":bool(compatible_time),"rankings":climate_rankings(weather),"correlation":summary.get("correlation",{}),
            "icon_thresholds":getattr(settings,"GEO_WEATHER_THRESHOLDS",{"rain":1,"heavy_rain":20,"heat":35,"cold":{"N":18,"NE":16,"CO":12,"SE":10,"S":5},"wind":40}),
            "collection":readiness,
            "period":{"month":first.strftime("%Y-%m") if first.replace(day=1)==end.replace(day=1) else f"{first:%d/%m/%Y} – {end:%d/%m/%Y}","date_from":first.isoformat(),"date_to":end.isoformat(),"partial":end>=timezone.localdate()-timedelta(days=5)},
            "missing_ufs":[uf for uf in ufs if uf not in weather],"eligible_ufs":ufs,
            "notice":"Coincidência temporal é contexto; não confirma causa meteorológica. Chuva representa estimativa territorial, não medição única da UF.",
            "catalog":{"regions":list(REGIONS),"states":[s for s in STATES if s["sigla"] in region_states(params.get("region",""))]}}
