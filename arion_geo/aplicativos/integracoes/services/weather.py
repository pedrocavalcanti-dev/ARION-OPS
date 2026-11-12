"""Monthly spatial estimates, cached per UF. No network work in map GETs."""
import calendar
import json
import math
from datetime import date, timedelta
from functools import lru_cache

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from ..clients.public_data import ARCHIVE_ENDPOINTS, PublicDataClient, PublicDataError
from ..models import ExternalAPIHealthCheck, ExternalAPIIntegration, IntegrationSyncState, WeatherCollectionJob, WeatherMonth
from .external_apis import decrypt_external_api_secret
from .geography import STATE_BY_UF
from .sync_lock import Lease

VARIABLES = {"precipitation_sum": "mm", "wind_speed_10m_max": "km/h", "wind_gusts_10m_max": "km/h"}
METHOD = "area-grid-v1"
MODEL = "era5"


def month_bounds(value=None):
    today = timezone.localdate()
    if not value or value == "last_month":
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    elif value == "this_month":
        first = today.replace(day=1)
    else:
        try:
            if len(str(value)) != 7:
                raise ValueError
            first = date.fromisoformat(f"{value}-01")
        except (ValueError, TypeError):
            raise ValueError("Selecione um mês válido (AAAA-MM).") from None
    if first.year < 1940 or first > today:
        raise ValueError("Período fora do histórico disponível.")
    end = min(first.replace(day=calendar.monthrange(first.year, first.month)[1]), today)
    return first, end


def licensed_configuration(integration, *, automatic=False):
    if not integration or not integration.enabled:
        raise PublicDataError("Integração climática desabilitada ou não configurada.")
    cfg = integration.config or {}
    mode = cfg.get("mode", "evaluation")
    if mode == "commercial":
        if not cfg.get("license_confirmed") or not integration.secret_ciphertext:
            raise PublicDataError("Licença de histórico e chave comercial pendentes.")
    elif mode == "evaluation":
        if automatic or getattr(settings, "ARION_PRODUCTION", False) or not cfg.get("evaluation_authorized"):
            raise PublicDataError("Avaliação requer homologação autorizada fora de produção, sem coleta periódica.")
    else:
        raise PublicDataError("Modo climático inválido.")
    if cfg.get("endpoint", ARCHIVE_ENDPOINTS[mode]) != ARCHIVE_ENDPOINTS[mode]:
        raise PublicDataError("Endpoint incompatível com o modo da integração.")
    return cfg


def _inside_ring(x, y, ring):
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous[:2]
        x2, y2 = current[:2]
        if (y1 > y) != (y2 > y) and x < (x2-x1)*(y-y1)/(y2-y1)+x1:
            inside = not inside
        previous = current
    return inside


def _inside(x, y, polygons):
    return any(_inside_ring(x,y,p[0]) and not any(_inside_ring(x,y,h) for h in p[1:]) for p in polygons)


@lru_cache(maxsize=27)
def sampling_points(uf):
    """4x4 strata; 8x8 territorial quadrature per stratum, spherical area weights.

    Representatives are inside the existing UF polygon, never just its capital.
    Tiny polygons fall back to a finer quadrature, not an out-of-state centroid.
    """
    path = settings.BASE_DIR / "static" / "geo" / "brasil_estados.geojson"
    geo = json.loads(path.read_text(encoding="utf-8"))
    feature = next(f for f in geo["features"] if f["properties"]["sigla"] == uf)
    geometry = feature["geometry"]
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    coordinates = [point for p in polygons for ring in p for point in ring]
    west, east = min(p[0] for p in coordinates), max(p[0] for p in coordinates)
    south, north = min(p[1] for p in coordinates), max(p[1] for p in coordinates)
    dx, dy = (east-west)/4, (north-south)/4
    points = []
    for ix in range(4):
        for iy in range(4):
            members = []
            for a in range(8):
                for b in range(8):
                    x, y = west+(ix+(a+.5)/8)*dx, south+(iy+(b+.5)/8)*dy
                    if _inside(x,y,polygons):
                        members.append((x,y,math.cos(math.radians(y))))
            if not members:
                continue
            weight = sum(p[2] for p in members)
            cx = sum(p[0]*p[2] for p in members)/weight
            cy = sum(p[1]*p[2] for p in members)/weight
            representative = min(members, key=lambda p:(p[0]-cx)**2+(p[1]-cy)**2)
            area = weight * math.radians(dx/8)*math.radians(dy/8)*6371.0088**2
            points.append({"longitude": round(representative[0],6), "latitude": round(representative[1],6), "area_km2": area})
    if not points:
        raise PublicDataError("Geometria sem cobertura amostral; coleta não aplicada.")
    return points


def parse_daily(payload, start, end):
    try:
        daily, units = payload["daily"], payload["daily_units"]
        days = (end-start).days+1
        expected = [(start+timedelta(days=i)).isoformat() for i in range(days)]
        if daily["time"] != expected:
            raise ValueError
        for variable, unit in VARIABLES.items():
            if units[variable] != unit or len(daily[variable]) != days:
                raise ValueError
            for value in daily[variable]:
                if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0):
                    raise ValueError
        lat, lon = float(payload["latitude"]), float(payload["longitude"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PublicDataError("Série climática inválida ou unidades incompatíveis.") from None
    return {"cell": [lat,lon], "daily": {"time": expected, **{v:daily[v] for v in VARIABLES}}, "units": dict(VARIABLES)}


def summarize(samples):
    if not samples:
        return {}
    area = sum(p["area_km2"] for p in samples)
    days = samples[0]["daily"]["time"]
    daily = []
    valid_values = 0
    for i, day in enumerate(days):
        row = {"date": day}
        for variable in VARIABLES:
            present = [(p["daily"][variable][i], p["area_km2"]) for p in samples if p["daily"][variable][i] is not None]
            valid_values += len(present)
            row[variable] = (sum(v*w for v,w in present)/sum(w for _,w in present) if variable == "precipitation_sum" else max(v for v,_ in present)) if present else None
        row["rain_coverage"] = sum(p["area_km2"] for p in samples if p["daily"]["precipitation_sum"][i] is not None)/area
        daily.append(row)
    rain = [r["precipitation_sum"] for r in daily if r["precipitation_sum"] is not None]
    winds = [r["wind_speed_10m_max"] for r in daily if r["wind_speed_10m_max"] is not None]
    gusts = [r["wind_gusts_10m_max"] for r in daily if r["wind_gusts_10m_max"] is not None]
    return {"precipitation_mm": round(sum(rain),2) if rain else None,
            "rainy_days": sum(v>=1 for v in rain) if rain else None,
            "rainy_days_percent": round(sum(v>=1 for v in rain)/len(rain)*100,1) if rain else None,
            "wind_max_kmh": max(winds) if winds else None, "gust_max_kmh": max(gusts) if gusts else None,
            "observed_days": len(rain), "expected_days": len(days), "points": len(samples),
            "area_km2": area, "completeness": valid_values/(len(samples)*len(days)*len(VARIABLES)), "daily": daily}


def _count_request(integration, cfg):
    key = "meteo-" + timezone.localdate().isoformat()
    state, _ = IntegrationSyncState.objects.get_or_create(key=key, defaults={"records_count":0})
    if (state.records_count or 0) >= int(cfg.get("daily_budget", 250)):
        raise PublicDataError("Limite diário de consultas climáticas atingido.")
    IntegrationSyncState.objects.filter(pk=state.pk).update(records_count=F("records_count")+1, last_started_at=timezone.now())
    ExternalAPIIntegration.objects.filter(pk=integration.pk).update(last_test_at=timezone.now())


@sensitive_variables()
def collect_month(uf, period, integration, *, client=None):
    cfg = licensed_configuration(integration)
    start, end = month_bounds(period.strftime("%Y-%m"))
    # Archive has delayed availability; the current month is explicitly partial.
    end = min(end, timezone.localdate()-timedelta(days=5))
    if end < start:
        raise PublicDataError("Histórico do mês ainda não disponível no modelo ERA5.")
    mode = cfg.get("mode", "evaluation")
    source = "open_meteo_" + mode
    cache = WeatherMonth.objects.filter(uf=uf, period=start, source=source, model=MODEL, method=METHOD, expires_at__gt=timezone.now()).first()
    if cache:
        return cache
    client = client or PublicDataClient(cfg.get("timeout",15), cfg.get("retries",1))
    secret = decrypt_external_api_secret(integration) if mode == "commercial" else ""
    cells = {}
    for point in sampling_points(uf):
        params = {"latitude":point["latitude"], "longitude":point["longitude"], "start_date":start.isoformat(), "end_date":end.isoformat(),
                  "daily":",".join(VARIABLES), "wind_speed_unit":"kmh", "timezone":"America/Sao_Paulo", "models":MODEL}
        if secret:
            params["apikey"] = secret
        payload = client.get(ARCHIVE_ENDPOINTS[mode], params, before_attempt=lambda:_count_request(integration,cfg))
        sample = parse_daily(payload,start,end)
        cell_key = tuple(sample["cell"])
        if cell_key in cells:
            cells[cell_key]["area_km2"] += point["area_km2"]
            cells[cell_key]["requested_coordinates"].append([point["latitude"],point["longitude"]])
        else:
            cells[cell_key] = {**sample, "area_km2":point["area_km2"], "requested_coordinates":[[point["latitude"],point["longitude"]]], "model":MODEL, "source":source, "collected_at":timezone.now().isoformat()}
    samples = list(cells.values())
    summary = summarize(samples)
    summary["partial"] = end < start.replace(day=calendar.monthrange(start.year,start.month)[1])
    summary["license_mode"] = mode
    now = timezone.now()
    row, _ = WeatherMonth.objects.update_or_create(uf=uf, period=start, source=source, model=MODEL, method=METHOD, defaults={
        "region":str(STATE_BY_UF[uf]["region_id"]), "samples":samples, "summary":summary, "completeness":summary["completeness"],
        "collected_at":now, "expires_at":now+timedelta(hours=int(cfg.get("cache_hours",720)) if not summary["partial"] else min(24,int(cfg.get("cache_hours",720))))})
    return row


@sensitive_variables()
def test_weather(integration):
    """One cell verifies the contract; it is not persisted as a UF estimate."""
    cfg = licensed_configuration(integration)
    first,end = month_bounds()
    mode = cfg.get("mode","evaluation")
    point = sampling_points("SP")[0]
    params = {"latitude":point["latitude"],"longitude":point["longitude"],"start_date":first.isoformat(),"end_date":end.isoformat(),
              "daily":",".join(VARIABLES),"wind_speed_unit":"kmh","timezone":"America/Sao_Paulo","models":MODEL}
    if mode=="commercial":
        params["apikey"] = decrypt_external_api_secret(integration)
    payload = PublicDataClient(cfg.get("timeout",15),cfg.get("retries",1)).get(ARCHIVE_ENDPOINTS[mode],params,before_attempt=lambda:_count_request(integration,cfg))
    result = parse_daily(payload,first,end)
    return len(result["daily"]["time"])


def request_collection(ufs, period, user):
    integration = ExternalAPIIntegration.objects.filter(provider="open_meteo").first()
    licensed_configuration(integration)
    start, _ = month_bounds(period)
    now = timezone.now()
    jobs = []
    with transaction.atomic():
        # One row per UF/month, including repeated requests from different users.
        for uf in sorted(set(ufs)):
            if uf not in STATE_BY_UF:
                raise ValueError("UF inválida.")
            cached = WeatherMonth.objects.filter(uf=uf,period=start,source="open_meteo_"+integration.config.get("mode","evaluation"),model=MODEL,method=METHOD,expires_at__gt=now).exists()
            if cached:
                continue
            job, created = WeatherCollectionJob.objects.get_or_create(uf=uf, period=start, defaults={"requested_by":user})
            if not created and job.status in {"error","success"} and job.updated_at < now-timedelta(minutes=5):
                job.status, job.attempts, job.next_attempt_at, job.last_error = "pending",0,now,""
                job.requested_by = user
                job.save()
            jobs.append(job.pk)
    return jobs


def cached_weather(ufs, period):
    start, _ = month_bounds(period)
    integration = ExternalAPIIntegration.objects.filter(provider="open_meteo").first()
    mode = (integration.config or {}).get("mode","evaluation") if integration else "evaluation"
    if getattr(settings,"ARION_PRODUCTION",False) and mode!="commercial":
        return {}
    rows = WeatherMonth.objects.filter(uf__in=ufs, period=start,source="open_meteo_"+mode,model=MODEL,method=METHOD)
    return {row.uf:{**row.summary, "source":row.source, "model":row.model, "method":row.method, "collected_at":row.collected_at.isoformat(), "stale":row.expires_at<=timezone.now()} for row in rows}


def worker_tick():
    """One job at a time across processes; recover interrupted work and back off."""
    lease = Lease("geo-climate")
    if not lease.acquire():
        return
    stop, thread = lease.keep_alive()
    try:
        now = timezone.now()
        WeatherCollectionJob.objects.filter(status="running",updated_at__lt=now-timedelta(minutes=10)).update(status="pending",next_attempt_at=now)
        job = WeatherCollectionJob.objects.filter(status="pending",next_attempt_at__lte=now).order_by("created_at").first()
        if not job:
            return
        job.status, job.attempts = "running",job.attempts+1
        job.save()
        integration = ExternalAPIIntegration.objects.filter(provider="open_meteo").first()
        try:
            collect_month(job.uf,job.period,integration)
            job.status, job.last_error = "success",""
            integration.last_success_at, integration.last_error = timezone.now(),""
            integration.health = "available"
        except Exception as error:
            # Never persist provider response bodies, raw exception strings or URLs.
            job.last_error = str(error)[:240] if isinstance(error,PublicDataError) else "Falha climática; cache anterior preservado."
            job.status = "pending" if job.attempts < 3 else "error"
            job.next_attempt_at = now+timedelta(minutes=2**job.attempts)
            if integration:
                integration.last_error, integration.health = job.last_error,"error"
        job.save()
        if integration:
            integration.save(update_fields=("last_success_at","last_error","health","updated_at"))
            ExternalAPIHealthCheck.objects.create(integration=integration,status="success" if job.status=="success" else "error",
                message=f"Clima {job.uf} {job.period:%Y-%m}: " + ("cache mensal atualizado." if job.status=="success" else job.last_error))
    finally:
        stop.set()
        thread.join(timeout=2)
        lease.release()
