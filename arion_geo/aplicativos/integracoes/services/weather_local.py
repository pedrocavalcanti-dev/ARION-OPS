"""On-demand municipal weather, shared by locality/month, never per ticket."""
import math
import unicodedata
from datetime import datetime, timedelta, timezone as utc_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.utils import timezone
from ..clients.public_data import IBGE_BASE, PublicDataClient, PublicDataError
from ..models import GeographyCatalog, IntegrationSyncState, WeatherLocalCache
from .geography import STATE_BY_UF
from .geo_configuration import get_geo_integration, weather_source, weather_sources, weather_source_info
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
    first, _ = month_bounds(period)
    row, _ = WeatherLocalCache.objects.get_or_create(location_key=locality_key(city,uf),period=first,source=weather_source(cfg),model=MODEL,
        defaults={"city":city,"uf":uf,"requested_by":user})
    if row.status=="error" or (row.status=="success" and row.expires_at and row.expires_at<=timezone.now()):
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
    cfg = configured_weather(integration)
    if row.source!=weather_source(cfg):
        raise PublicDataError("Fonte da tarefa local mudou; solicite o recorte na fonte atual.")
    client = client or PublicDataClient(cfg["timeout"],cfg["retries"],weather_endpoint=cfg["endpoint"])
    if row.latitude is None or row.longitude is None:
        municipal_point(row,client)
    first,end = month_bounds(row.period.strftime("%Y-%m"))
    end = min(end,timezone.localdate()-timedelta(days=5))
    if end<first:
        raise PublicDataError("Histórico ERA5 ainda indisponível para este mês.")
    params = {"latitude":row.latitude,"longitude":row.longitude,"start_date":first.isoformat(),"end_date":end.isoformat(),
              "daily":",".join(VARIABLES),"hourly":",".join(HOURLY),"wind_speed_unit":"kmh","timezone":"auto","timeformat":"unixtime","models":MODEL}
    secret = weather_secret(integration,cfg)
    if secret:
        params["apikey"] = secret
    # Month plus both adjacent days ensures cross-midnight incident windows.
    params["start_date"] = (first-timedelta(days=1)).isoformat()
    params["end_date"] = (end+timedelta(days=1)).isoformat()
    payload = client.get(cfg["endpoint"],params,before_attempt=lambda:_count_request(integration,cfg))
    try:
        local_zone = ZoneInfo(payload["timezone"])
    except (KeyError,TypeError,ValueError,ZoneInfoNotFoundError):
        raise PublicDataError("Fuso horário da localidade inválido.") from None
    start_utc = datetime.combine(first-timedelta(days=1),datetime.min.time(),tzinfo=local_zone).astimezone(utc_timezone.utc)
    end_utc = datetime.combine(end+timedelta(days=2),datetime.min.time(),tzinfo=local_zone).astimezone(utc_timezone.utc)
    hourly = parse_hourly(payload,start_utc,end_utc)
    # Convert daily epoch values before the existing territorial parser validates them.
    payload["daily"]["time"] = [datetime.fromtimestamp(t,local_zone).date().isoformat() for t in payload["daily"]["time"]]
    sample = parse_daily(payload,first-timedelta(days=1),end+timedelta(days=1))
    sample["area_km2"] = 1
    sample["daily"]={key:[v for i,v in enumerate(values) if first.isoformat()<=sample["daily"]["time"][i]<=end.isoformat()] for key,values in sample["daily"].items()}
    row.payload = {"summary":summarize([sample]),"hourly":hourly,"cell":sample["cell"],"source_info":weather_source_info(cfg),
                   "resolution":"ERA5 · aproximadamente 0,25°","precision":"Estimativa municipal em ponto interior IBGE; não representa o trajeto da fibra.","timezone":str(local_zone),"hourly_timezone":"UTC"}
    row.collected_at,row.expires_at,row.status,row.last_error = timezone.now(),timezone.now()+timedelta(hours=int(cfg["cache_hours"])),"success",""
    row.save()


def incident_window(row, occurred_at):
    if not occurred_at or timezone.is_naive(occurred_at) or row.status!="success":
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
            IntegrationSyncState.objects.update_or_create(key="weather-cooldown",defaults={"change_cursor":row.next_attempt_at})
        row.save()
