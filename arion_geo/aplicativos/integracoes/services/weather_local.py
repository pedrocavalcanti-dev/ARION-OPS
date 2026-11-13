"""On-demand municipal weather, shared by locality/month, never per ticket."""
import math
import unicodedata
from datetime import datetime, timedelta, timezone as utc_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.utils import timezone
from ..clients.public_data import IBGE_BASE, PublicDataClient, PublicDataError
from ..models import GeographyCatalog, IntegrationSyncState, WeatherLocalCache, WeatherRequestLog
from .geography import STATE_BY_UF
from .geo_configuration import get_geo_integration, weather_source, weather_sources, weather_source_info
from .weather_history import consolidate, needs_collection, persist, missing_intervals, saved_requests, review_date, verified_at
from .weather import MODEL, VARIABLES, _inside, _count_request, configured_weather, month_bounds, parse_daily, summarize, weather_secret

HOURLY = {"precipitation":"mm","temperature_2m":"°C","wind_speed_10m":"km/h","wind_gusts_10m":"km/h","wind_direction_10m":"°"}


def normalize_city(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD",str(value)) if not unicodedata.combining(c)).casefold().split())


def locality_key(city, uf):
    return f"{uf}:{normalize_city(city)}"


def request_local(city, uf, period, user):
    if uf not in STATE_BY_UF or not city or len(city)>200:
        raise ValueError("Localidade inválida.")
    integration = get_geo_integration("open_meteo")
    cfg = configured_weather(integration)
    if cfg.get("preferred_provider","open_meteo")!="open_meteo":
        raise PublicDataError("A fonte selecionada não oferece série horária municipal neste adaptador.",code="capability")
    first, _ = month_bounds(period)
    row, _ = WeatherLocalCache.objects.get_or_create(location_key=locality_key(city,uf),period=first,source=weather_source(cfg),model=MODEL,
        defaults={"city":city,"uf":uf,"requested_by":user})
    if row.status=="error" or (row.status=="success" and needs_collection(row,cfg)):
        row.status,row.attempts,row.next_attempt_at = "pending",0,timezone.now()
        row.save(update_fields=("status","attempts","next_attempt_at","updated_at"))
    return row


def municipal_point(row, client):
    """IBGE identity + interior representative; no inferred coordinates written to CMDB."""
    key = f"municipios-{row.uf}"
    catalog = GeographyCatalog.objects.filter(key=key).first()
    if not catalog:
        data = client.get(IBGE_BASE+f"estados/{STATE_BY_UF[row.uf]['id']}/municipios")
        if not isinstance(data,list) or not data:
            raise PublicDataError("Catálogo municipal IBGE indisponível.")
        municipalities = [{"id":str(x["id"]),"nome":str(x["nome"])} for x in data
                          if len(str(x.get("id","")))==7 and str(x["id"]).startswith(str(STATE_BY_UF[row.uf]["id"]))]
        catalog,_ = GeographyCatalog.objects.update_or_create(key=key,defaults={"states":municipalities,"source":"ibge","synced_at":timezone.now()})
    matches = [x for x in catalog.states if normalize_city(x["nome"])==normalize_city(row.city)]
    if len(matches)!=1:
        raise PublicDataError("Cidade sem correspondência exata e única no IBGE; mantida apenas a referência de UF.")
    city = matches[0]
    geometry = client.get(f"https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{city['id']}",{"formato":"application/vnd.geo+json","qualidade":"minima"})
    try:
        shape = geometry["features"][0]["geometry"]
        polygons = shape["coordinates"] if shape["type"]=="MultiPolygon" else [shape["coordinates"]]
        coordinates = [p for polygon in polygons for ring in polygon for p in ring]
        west,east = min(p[0] for p in coordinates),max(p[0] for p in coordinates)
        south,north = min(p[1] for p in coordinates),max(p[1] for p in coordinates)
        points = [(west+(i+.5)*(east-west)/48,south+(j+.5)*(north-south)/48) for i in range(48) for j in range(48)]
        points = [p for p in points if _inside(*p,polygons)]
        cx,cy = sum(p[0] for p in points)/len(points),sum(p[1] for p in points)/len(points)
        lon,lat = min(points,key=lambda p:(p[0]-cx)**2+(p[1]-cy)**2)
        if not (-74<=lon<=-28 and -35<=lat<=6):
            raise ValueError
    except (KeyError,TypeError,ValueError,ZeroDivisionError):
        raise PublicDataError("Geometria municipal IBGE inválida; nenhuma localização artificial aplicada.") from None
    row.city,row.ibge_code,row.latitude,row.longitude = city["nome"],city["id"],round(lat,6),round(lon,6)
    row.save(update_fields=("city","ibge_code","latitude","longitude","updated_at"))


def parse_hourly(payload, start, end):
    try:
        hourly,units = payload["hourly"],payload["hourly_units"]
        # UTC epoch avoids ambiguous/missing wall-clock hours and historic DST.
        expected = int((end-start).total_seconds()/3600)
        times = hourly["time"]
        if len(times)!=expected or times!=[int(start.timestamp())+i*3600 for i in range(expected)]:
            raise ValueError
        for key,unit in HOURLY.items():
            if units[key]!=unit or len(hourly[key])!=expected:
                raise ValueError
            for value in hourly[key]:
                if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or (key!="temperature_2m" and value<0) or (key=="wind_direction_10m" and value>360)):
                    raise ValueError
        return {"time":times,**{key:hourly[key] for key in HOURLY},"units":HOURLY}
    except (KeyError,TypeError,ValueError,OverflowError):
        raise PublicDataError("Série horária inválida ou unidades incompatíveis.") from None


def collect_local(row, integration, client=None):
    cfg=configured_weather(integration)
    refresh=row.refresh_request
    if refresh:
        from .weather_history import validate_refresh
        validate_refresh(refresh,row.uf)
    if row.method!="municipal-point-v1":
        raise PublicDataError("Método municipal incompatível; preserve o histórico anterior.",code="history_conflict")
    if row.source!=weather_source(cfg) or cfg.get("preferred_provider","open_meteo")!="open_meteo":
        raise PublicDataError("Fonte da tarefa local mudou; solicite o recorte na fonte atual.",code="history_conflict")
    if row.collected_at:
        current=consolidate(row,cfg)
        if current.consolidated_at and not current.history_complete and not refresh:
            raise PublicDataError("Integridade do histórico consolidado exige revisão manual.",code="history_locked")
        if not refresh and not needs_collection(current,cfg):
            saved_requests(1,1)
            # Status is queue metadata; historical values remain untouched.
            WeatherLocalCache.objects.filter(pk=row.pk).update(status="success",last_error="")
            return current
        row=current
    baseline=refresh.get("expected_revision") if refresh else row.revision
    client=client or PublicDataClient(cfg["timeout"],cfg["retries"],weather_endpoint=cfg["endpoint"])
    if row.latitude is None or row.longitude is None: municipal_point(row,client)
    first,end=month_bounds(row.period.strftime("%Y-%m"))
    available=timezone.localdate()-timedelta(days=5)
    end=min(end,available)
    if end<first: raise PublicDataError("Histórico ERA5 ainda indisponível para este mês.")
    lower,upper=first-timedelta(days=1),min(end+timedelta(days=1),available)
    daily={d["date"]:{key:d.get(key) for key in VARIABLES} for d in row.payload.get("summary",{}).get("daily",[])}
    hourly={}
    def add_hours(payload):
        data=payload.get("hourly",{})
        for i,at in enumerate(data.get("time",[])):
            hourly[at]={key:data.get(key,[])[i] if i<len(data.get(key,[])) else None for key in HOURLY}
    zone_name=row.payload.get("timezone")
    boundary=review_date(first,row.source,cfg)
    contributors=[verified_at(row)] if row.collected_at else []
    fingerprint=f"{row.source}:{row.model}:{row.method}:{first}:{row.latitude}:{row.longitude}:"+refresh.get("id","")
    if timezone.localdate()>=boundary: fingerprint+=":final:"+str(boundary)
    progress=row.progress if row.progress.get("fingerprint")==fingerprint else {}
    # Public weather can be shared only for identical coordinate/model/time contracts.
    peers=WeatherLocalCache.objects.filter(source=row.source,model=row.model,method=row.method,latitude=row.latitude,longitude=row.longitude,collected_at__isnull=False).exclude(pk=row.pk)
    for peer in peers.order_by("collected_at"):
        if not zone_name: zone_name=peer.payload.get("timezone")
        if peer.payload.get("timezone")==zone_name:
            add_hours(peer.payload);contributors.append(verified_at(peer))
    add_hours(row.payload)
    if progress:
        daily.update(progress.get("daily",{}))
        for at,values in progress.get("hours",{}).items(): hourly[int(at)]=values
        zone_name=progress.get("timezone") or zone_name
    if zone_name=="America/Sao_Paulo":
        from .weather_grid import context_for
        from .weather_adapters import get_adapter
        from ..models import WeatherGridCache
        for grid in WeatherGridCache.objects.filter(source=row.source,model=row.model,latitude=row.latitude,longitude=row.longitude,
                context=context_for(get_adapter(cfg)),date_from__lte=end,date_to__gte=first).order_by("collected_at"):
            contributors.append(verified_at(grid))
            for i,day in enumerate(grid.payload.get("daily",{}).get("time",[])):
                daily.setdefault(day,{key:grid.payload["daily"][key][i] for key in VARIABLES})
    values={"time":sorted(daily),**{key:[daily[d][key] for d in sorted(daily)] for key in VARIABLES}}
    missing_daily={lower+timedelta(days=i) for i in range((upper-lower).days+1)} if not zone_name else set()
    checked=min((d for d in contributors if d),default=None)
    for a,b in missing_intervals(values,first,end,cfg,source=row.source,collected_at=checked,
            expires_at=row.expires_at,force=bool(refresh)):
        missing_daily.update(a+timedelta(days=i) for i in range((b-a).days+1))
    zone=ZoneInfo(zone_name) if zone_name else None
    missing_hours=set()
    day=lower
    while day<=upper:
        if zone:
            a=datetime.combine(day,datetime.min.time(),tzinfo=zone).astimezone(utc_timezone.utc)
            b=datetime.combine(day+timedelta(days=1),datetime.min.time(),tzinfo=zone).astimezone(utc_timezone.utc)
            expected=[int(a.timestamp())+i*3600 for i in range(int((b-a).total_seconds()/3600))]
            if any(at not in hourly or any(hourly[at].get(key) is None for key in HOURLY) for at in expected): missing_hours.add(day)
        else: missing_hours.add(day)
        day+=timedelta(days=1)
    final_due=checked and timezone.localdate()>=boundary and timezone.localtime(checked).date()<boundary
    if refresh or final_due: missing_hours.update(lower+timedelta(days=i) for i in range((upper-lower).days+1))
    elif row.expires_at and row.expires_at<=timezone.now() and end>=timezone.localdate().replace(day=1):
        missing_hours.update(end-timedelta(days=i) for i in range(min(int(cfg.get("history_provisional_days") or 7),(end-lower).days+1)))
    from datetime import date
    missing_daily.difference_update(date.fromisoformat(d) for d in progress.get("daily_days",[]))
    missing_hours.difference_update(date.fromisoformat(d) for d in progress.get("hourly_days",[]))
    ranges=[]
    for day in sorted(missing_daily|missing_hours):
        flags=(day in missing_daily,day in missing_hours)
        if ranges and ranges[-1][1]+timedelta(days=1)==day and ranges[-1][2]==flags: ranges[-1]=(ranges[-1][0],day,flags)
        else: ranges.append((day,day,flags))
    for start,stop,(need_daily,need_hourly) in ranges:
        params={"latitude":row.latitude,"longitude":row.longitude,"start_date":start.isoformat(),"end_date":stop.isoformat(),
                "wind_speed_unit":"kmh","timezone":zone_name or "auto","timeformat":"unixtime","models":MODEL}
        if need_daily: params["daily"]=",".join(VARIABLES)
        if need_hourly: params["hourly"]=",".join(HOURLY)
        secret=weather_secret(integration,cfg)
        if secret: params["apikey"]=secret
        attempts=[]
        def before_attempt():
            log=_count_request(integration,cfg,days=(stop-start).days+1,variables=(len(VARIABLES) if need_daily else 0)+(len(HOURLY) if need_hourly else 0))
            if log: attempts.append(log.pk)
        try:
            payload=client.get(cfg["endpoint"],params,before_attempt=before_attempt)
            returned_zone=payload["timezone"]
            if zone_name and returned_zone!=zone_name: raise PublicDataError("Fuso horário mudou; revisão explícita necessária.",code="contract")
            zone_name=returned_zone;zone=ZoneInfo(zone_name)
            if need_daily:
                payload["daily"]["time"]=[datetime.fromtimestamp(t,zone).date().isoformat() for t in payload["daily"]["time"]]
                sample=parse_daily(payload,start,stop)
                for i,day in enumerate(sample["daily"]["time"]): daily[day]={key:sample["daily"][key][i] for key in VARIABLES}
            if need_hourly:
                a=datetime.combine(start,datetime.min.time(),tzinfo=zone).astimezone(utc_timezone.utc)
                b=datetime.combine(stop+timedelta(days=1),datetime.min.time(),tzinfo=zone).astimezone(utc_timezone.utc)
                validated=parse_hourly(payload,a,b)
                add_hours({"hourly":validated})
        except PublicDataError as error:
            if attempts: WeatherRequestLog.objects.filter(pk=attempts[-1]).update(status=error.code)
            raise
        if attempts: WeatherRequestLog.objects.filter(pk=attempts[-1]).update(status="success")
        selected=[(start+timedelta(days=i)).isoformat() for i in range((stop-start).days+1)]
        progress={"fingerprint":fingerprint,"daily":daily,"hours":{str(at):values for at,values in hourly.items()},"timezone":zone_name,
            "daily_days":sorted(set(progress.get("daily_days",[]))|set(selected if need_daily else [])),
            "hourly_days":sorted(set(progress.get("hourly_days",[]))|set(selected if need_hourly else []))}
        WeatherLocalCache.objects.filter(pk=row.pk).update(progress=progress)
    times=[(first+timedelta(days=i)).isoformat() for i in range((end-first).days+1)]
    sample={"cell":row.payload.get("cell",[row.latitude,row.longitude]),"units":dict(VARIABLES),"area_km2":1,
            "daily":{"time":times,**{key:[daily.get(day,{}).get(key) for day in times] for key in VARIABLES}}}
    a=datetime.combine(lower,datetime.min.time(),tzinfo=zone).astimezone(utc_timezone.utc)
    b=datetime.combine(upper+timedelta(days=1),datetime.min.time(),tzinfo=zone).astimezone(utc_timezone.utc)
    hours=[int(a.timestamp())+i*3600 for i in range(int((b-a).total_seconds()/3600))]
    merged={"time":hours,"units":HOURLY,**{key:[hourly.get(at,{}).get(key) for at in hours] for key in HOURLY}}
    series_verified=timezone.now() if refresh or final_due or not checked else checked
    sample["verified_at"]=series_verified.isoformat()
    if zone_name=="America/Sao_Paulo":
        from .weather_adapters import get_adapter
        from .weather_grid import save_cell
        save_cell(cfg,get_adapter(cfg),{"latitude":row.latitude,"longitude":row.longitude},first,end,
            {key:sample[key] for key in ("cell","daily","units","verified_at")},refresh=refresh,collected_at=series_verified)
    payload={"summary":summarize([sample]),"verified_at":series_verified.isoformat(),"daily_units":VARIABLES,"hourly":merged,"cell":sample["cell"],"source_info":weather_source_info(cfg),
        "resolution":"ERA5 · aproximadamente 0,25°","precision":"Estimativa municipal em ponto interior IBGE; não representa o trajeto da fibra.",
        "timezone":zone_name,"hourly_timezone":"UTC"}
    hours=min(24,cfg["cache_hours"]) if end>=timezone.localdate().replace(day=1) else cfg["cache_hours"]
    changes={"payload":payload,"collected_at":timezone.now(),"expires_at":timezone.now()+timedelta(hours=hours),"status":"success","last_error":"","refresh_request":{},"progress":{}}
    return persist(row,changes,cfg,refresh=refresh,expected_revision=baseline)


def incident_window(row, occurred_at):
    if not occurred_at or timezone.is_naive(occurred_at) or (row.status!="success" and not row.collected_at):
        return {"state":"insufficient","hourly":[]}
    lower,upper = (occurred_at-timedelta(hours=6)).timestamp(),(occurred_at+timedelta(hours=2)).timestamp()
    hourly = row.payload.get("hourly",{})
    series = [{"at":datetime.fromtimestamp(t,utc_timezone.utc).isoformat(),**{k:hourly[k][i] for k in HOURLY}} for i,t in enumerate(hourly.get("time",[])) if lower<=t<=upper]
    rainy = any((r.get("precipitation") or 0)>=1 for r in series)
    intense = any((r.get("precipitation") or 0)>=10 or (r.get("wind_gusts_10m") or 0)>=60 for r in series)
    covered = len(series)>=8 and all(r.get("precipitation") is not None for r in series)
    return {"state":"insufficient" if not covered else "contextual" if intense else "coincidence" if rainy else "none", "hourly":series,
            "time_basis":"Abertura do ticket · aproximação; início real da indisponibilidade ausente no espelho.","causality":False}


def local_worker_tick():
    now = timezone.now()
    WeatherLocalCache.objects.filter(status="running",updated_at__lt=now-timedelta(minutes=10)).update(status="pending")
    row = WeatherLocalCache.objects.filter(status="pending",next_attempt_at__lte=now).order_by("updated_at").first()
    if not row:
        return
    row.status,row.attempts = "running",row.attempts+1
    row.save()
    try:
        collect_local(row,get_geo_integration("open_meteo"))
    except Exception as error:
        row.status = "pending" if row.attempts<3 else "error"
        row.last_error = str(error)[:240] if isinstance(error,PublicDataError) else "Coleta local indisponível; cache anterior preservado."
        delay = max(120*2**min(row.attempts,6),getattr(error,"retry_after",0))
        try:
            row.next_attempt_at = now+timedelta(seconds=delay)
        except OverflowError:
            row.status="error"
            row.next_attempt_at=datetime.max.replace(tzinfo=utc_timezone.utc)
        if getattr(error,"code","") in {"budget","rate_limited"}:
            row.attempts=max(0,row.attempts-1)
            if row.next_attempt_at.year<9999: row.status="pending"
            IntegrationSyncState.objects.update_or_create(key="weather-cooldown",defaults={"change_cursor":row.next_attempt_at,"last_error":error.code})
        row.save()
