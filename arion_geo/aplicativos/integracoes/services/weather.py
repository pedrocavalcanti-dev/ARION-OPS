"""Monthly spatial estimates, cached per UF. No network work in map GETs."""
import calendar
import json
import math
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from functools import lru_cache

from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from ..clients.public_data import PublicDataClient, PublicDataError
from ..models import ExternalAPIHealthCheck, ExternalAPIIntegration, IntegrationSyncState, WeatherCollectionJob, WeatherMonth, WeatherLocalCache
from .external_apis import decrypt_external_api_secret
from .geography import STATE_BY_UF
from .sync_lock import Lease
from .weather_history import consolidate, needs_collection, persist, saved_requests
from .geo_configuration import get_geo_integration, weather_configuration, weather_source, weather_sources, weather_source_info, weather_model

VARIABLES = {"precipitation_sum": "mm", "wind_speed_10m_max": "km/h", "wind_gusts_10m_max": "km/h",
             "temperature_2m_mean": "°C", "temperature_2m_min": "°C", "temperature_2m_max": "°C",
             "wind_direction_10m_dominant": "°"}
METHOD = "area-grid-v2"
MODEL = "era5"


def collection_readiness(integration=None):
    """Safe default configuration state, without network or automatic collection."""
    integration = integration if integration is not None else get_geo_integration("open_meteo")
    if not integration.enabled:
        return {"status":"disabled", "message":"Integração climática desabilitada na Central de Integrações."}
    try:
        configured_weather(integration)
    except PublicDataError as error:
        return {"status":"incomplete" if error.code=="credential" else "incompatible", "message":str(error)}
    return {"status":"ready", "message":"Integração configurada; coleta sob demanda pelo worker."}


def circular_mean(values):
    if not values:
        return None
    x=sum(math.cos(math.radians(v))*w for v,w in values)
    y=sum(math.sin(math.radians(v))*w for v,w in values)
    return math.degrees(math.atan2(y,x)) % 360 if math.hypot(x,y)>1e-8*sum(w for _,w in values) else None


def spatial_value(variable, present):
    if not present:
        return None
    if variable == "wind_direction_10m_dominant":
        return circular_mean(present)
    if variable in {"precipitation_sum", "temperature_2m_mean"}:
        return sum(v*w for v,w in present)/sum(w for _,w in present)
    return (min if variable == "temperature_2m_min" else max)(v for v,_ in present)


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


def configured_weather(integration, *, automatic=False):
    if not integration or not integration.enabled:
        raise PublicDataError("Integração climática desabilitada ou não configurada.", code="configuration")
    cfg = weather_configuration(integration)
    if cfg.get("preferred_provider", "open_meteo")=="open_meteo" and cfg["mode"] == "commercial" and (not integration.secret_ciphertext or cfg.get("credential_endpoint", cfg["endpoint"]) != cfg["endpoint"]):
        raise PublicDataError("Cadastre uma chave para o endpoint comercial selecionado.", code="credential")
    return cfg


# Compatibility for existing internal callers; no environment/license confirmation gate.
licensed_configuration = configured_weather


def weather_secret(integration, cfg):
    if cfg["mode"] == "commercial" or (cfg["mode"] == "self_hosted" and cfg.get("credential_endpoint") == cfg["endpoint"]):
        return decrypt_external_api_secret(integration)
    return ""


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
                if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)
                                          or (value < 0 and not variable.startswith("temperature_"))
                                          or (variable == "wind_direction_10m_dominant" and value > 360)):
                    raise ValueError
        lat, lon = float(payload["latitude"]), float(payload["longitude"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PublicDataError("Série climática inválida ou unidades incompatíveis.", code="contract") from None
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
            row[variable] = spatial_value(variable, present)
        row["rain_coverage"] = sum(p["area_km2"] for p in samples if p["daily"]["precipitation_sum"][i] is not None)/area
        daily.append(row)
    rain = [r["precipitation_sum"] for r in daily if r["precipitation_sum"] is not None]
    winds = [r["wind_speed_10m_max"] for r in daily if r["wind_speed_10m_max"] is not None]
    gusts = [r["wind_gusts_10m_max"] for r in daily if r["wind_gusts_10m_max"] is not None]
    temperatures = [r["temperature_2m_mean"] for r in daily if r["temperature_2m_mean"] is not None]
    low = [r["temperature_2m_min"] for r in daily if r["temperature_2m_min"] is not None]
    high = [r["temperature_2m_max"] for r in daily if r["temperature_2m_max"] is not None]
    return {"temperature_mean_c":sum(temperatures)/len(temperatures) if temperatures else None,
            "temperature_min_c":min(low) if low else None, "temperature_max_c":max(high) if high else None,
            "wind_direction_deg":circular_mean([(r["wind_direction_10m_dominant"],1) for r in daily if r["wind_direction_10m_dominant"] is not None]),
            "variables":list(VARIABLES), "precipitation_mm": round(sum(rain),2) if rain else None,
            "rainy_days": sum(v>=1 for v in rain) if rain else None,
            "rainy_days_percent": round(sum(v>=1 for v in rain)/len(rain)*100,1) if rain else None,
            "wind_max_kmh": max(winds) if winds else None, "gust_max_kmh": max(gusts) if gusts else None,
            "observed_days": len(rain), "expected_days": len(days), "points": len(samples),
            "area_km2": area, "completeness": valid_values/(len(samples)*len(days)*len(VARIABLES)), "daily": daily}


def _count_request(integration, cfg, *, locations=1, days=31, variables=7):
    """Count HTTP attempts separately from conservative supplier-weight estimates."""
    from ..models import WeatherRequestLog
    source = weather_source(cfg)
    now = timezone.now()
    key = ("nasa-" if source=="nasa_power" else "meteo-") + timezone.localdate().isoformat()
    tomorrow=timezone.make_aware(datetime.combine(timezone.localdate()+timedelta(days=1),datetime.min.time()))
    with transaction.atomic():
        state, _ = IntegrationSyncState.objects.get_or_create(key=key, defaults={"records_count":0})
        state = IntegrationSyncState.objects.select_for_update().get(pk=state.pk)
        if (state.records_count or 0) >= int(cfg.get("daily_budget", 1000)):
            raise PublicDataError("Orçamento HTTP interno esgotado; retomada automática no próximo dia.", code="budget", retry_after=max(60,math.ceil((tomorrow-now).total_seconds())))
        # Estimates are deliberately conservative. The provider does not return
        # billed units; batching never implies a one-unit supplier charge.
        units = locations * max(1,days/14) * max(1,variables/10) if source!="nasa_power" else locations
        limits = [(timedelta(days=1),int(cfg.get("supplier_daily_budget",5000)))]
        if source=="open_meteo_public":
            limits += [(timedelta(minutes=1),600),(timedelta(hours=1),5000)]
            limits[0]=(timedelta(days=1),min(10000,limits[0][1]))
        for window, limit in limits:
            recent = WeatherRequestLog.objects.filter(source=source,started_at__gt=now-window)
            used = recent.aggregate(total=Sum("estimated_units"))["total"] or 0
            if used+units>limit:
                oldest=recent.order_by("started_at").first()
                delay=max(60,math.ceil(((oldest.started_at+window)-now).total_seconds())) if oldest else 86400
                raise PublicDataError("Orçamento estimado do fornecedor atingido; aguarde renovação da janela.",code="budget",retry_after=delay)
        IntegrationSyncState.objects.filter(pk=state.pk).update(records_count=F("records_count")+1,last_started_at=now)
        return WeatherRequestLog.objects.create(source=source,locations=locations,estimated_units=units)


@sensitive_variables()
def collect_month(uf, period, integration, *, client=None, job=None):
    cfg = configured_weather(integration)
    start, end = month_bounds(period.strftime("%Y-%m"))
    # Archive has delayed availability; the current month is explicitly partial.
    end = min(end, timezone.localdate()-timedelta(days=5))
    if end < start:
        raise PublicDataError("Histórico do mês ainda não disponível no modelo ERA5.")
    from .weather_adapters import get_adapter
    from .weather_grid import seed_months, fetch_samples
    adapter = get_adapter(cfg)
    source, model = weather_source(cfg), adapter.model
    refresh=job.refresh_request if job else {}
    if refresh:
        from .weather_history import validate_refresh
        validate_refresh(refresh,uf)
    if refresh and (refresh.get("source")!=source or refresh.get("model")!=model or refresh.get("method")!=METHOD):
        raise PublicDataError("Fonte da revisão histórica mudou; refaça a solicitação.",code="history_conflict")
    cache=WeatherMonth.objects.filter(uf=uf,period=start,source__in=weather_sources(cfg),model=model,method=METHOD).order_by("-collected_at").first()
    if cache:
        cache=consolidate(cache,cfg)
        if cache.consolidated_at and not cache.history_complete and not refresh:
            raise PublicDataError("Integridade do histórico consolidado exige revisão manual.",code="history_locked")
        if not refresh and not needs_collection(cache,cfg):
            saved_requests(len(sampling_points(uf)),min(cfg["batch_size"],adapter.batch_size))
            return cache
    baseline=refresh.get("expected_revision") if refresh else cache.revision if cache else None
    seed_months(cfg, adapter, start)
    fingerprint = f"{source}:{model}:{METHOD}:{start}:{end}"+(":"+refresh["id"] if refresh else "")
    if cache and cache.history_complete and not refresh and needs_collection(cache,cfg):
        from .weather_history import review_date
        fingerprint+=":final:"+review_date(start,source,cfg).isoformat()
    original = job.progress if job else {}
    variants = dict(original.get("variants",{}))
    if original.get("fingerprint"):
        variants[original["fingerprint"]] = original.get("samples",{})
    completed = variants.get(fingerprint,{})
    def checkpoint(samples):
        if job:
            if not WeatherCollectionJob.objects.filter(pk=job.pk,status="running").exists():
                raise PublicDataError("Coleta pausada pelo operador.", code="paused")
            job.progress = {"fingerprint":fingerprint,"samples":samples,"variants":variants}
            WeatherCollectionJob.objects.filter(pk=job.pk,status="running").update(progress=job.progress,updated_at=timezone.now())
    checkpoint(completed)
    points = sampling_points(uf)
    completed = fetch_samples(integration,cfg,points,start,end,client=client,completed=completed,checkpoint=checkpoint,force_refresh=bool(refresh),refresh=refresh)
    cells = {}
    for index, point in enumerate(points):
        sample = completed[str(index)]
        # Returned cell alone is insufficient: elevation downscaling may differ.
        # Merge only cells whose validated daily values are identical.
        cell_key = json.dumps([sample["cell"],sample["daily"]],sort_keys=True)
        if cell_key in cells:
            cells[cell_key]["area_km2"] += point["area_km2"]
            cells[cell_key]["requested_coordinates"].append([point["latitude"],point["longitude"]])
        else:
            cells[cell_key] = {**sample,"area_km2":point["area_km2"],"requested_coordinates":[[point["latitude"],point["longitude"]]],"model":model,"source":source,"collected_at":sample.get("verified_at") or timezone.now().isoformat()}
    samples = list(cells.values())
    summary = summarize(samples)
    summary["partial"] = end < start.replace(day=calendar.monthrange(start.year,start.month)[1])
    summary["source_info"] = weather_source_info(cfg)
    summary["supported_variables"] = samples[0].get("supported_variables",list(VARIABLES))
    summary["missing_capabilities"] = samples[0].get("missing_capabilities",[])
    summary["provider_metadata"] = samples[0].get("provider_metadata",{})
    summary["verified_at"]=min(p["collected_at"] for p in samples)
    summary["supported_completeness"] = sum(v is not None for sample in samples for var in summary["supported_variables"] for v in sample["daily"][var])/(len(samples)*len(samples[0]["daily"]["time"])*len(summary["supported_variables"]))
    now = timezone.now()
    values={"region":str(STATE_BY_UF[uf]["region_id"]),"samples":samples,"summary":summary,"completeness":summary["completeness"],
        "collected_at":now,"expires_at":now+timedelta(hours=int(cfg.get("cache_hours",720)) if not summary["partial"] else min(24,int(cfg.get("cache_hours",720))))}
    if cache:
        row=persist(cache,values,cfg,refresh=refresh,expected_revision=baseline)
    else:
        row,created=WeatherMonth.objects.get_or_create(uf=uf,period=start,source=source,model=model,method=METHOD,defaults=values)
        if not created: raise PublicDataError("Outra coleta publicou este recorte; reutilize o histórico.",code="history_conflict")
        row=consolidate(row,cfg)
    return row


@sensitive_variables()
def test_weather(integration):
    """One cell verifies the contract; it is not persisted as a UF estimate."""
    cfg = configured_weather(integration)
    first,end = month_bounds()
    from .weather_grid import fetch_samples
    result = fetch_samples(integration,cfg,[sampling_points("SP")[0]],first,end,force_refresh=True,store=False)
    return len(result["0"]["daily"]["time"])



def request_collection(ufs, period, user):
    integration = get_geo_integration("open_meteo")
    cfg = configured_weather(integration)
    start, _ = month_bounds(period)
    now = timezone.now()
    jobs = []
    with transaction.atomic():
        # One row per UF/month, including repeated requests from different users.
        for uf in sorted(set(ufs)):
            if uf not in STATE_BY_UF:
                raise ValueError("UF inválida.")
            cached=WeatherMonth.objects.filter(uf=uf,period=start,source__in=weather_sources(cfg),model=weather_model(cfg),method=METHOD).first()
            if cached: cached=consolidate(cached,cfg)
            if cached and not needs_collection(cached,cfg):
                from .weather_adapters import get_adapter
                saved_requests(len(sampling_points(uf)),min(cfg["batch_size"],get_adapter(cfg).batch_size))
                continue
            job, created = WeatherCollectionJob.objects.get_or_create(uf=uf, period=start, defaults={"requested_by":user})
            if not created and (job.status == "success" or (job.status == "error" and job.next_attempt_at <= now and job.updated_at < now-timedelta(minutes=5))):
                job.status, job.attempts, job.next_attempt_at, job.last_error = "pending",0,now,""
                job.requested_by = user
                job.save()
            jobs.append(job.pk)
    return jobs


def cached_weather(ufs, period):
    start, _ = month_bounds(period)
    integration = get_geo_integration("open_meteo")
    try:
        cfg = weather_configuration(integration)
    except PublicDataError:
        return {}
    sources = weather_sources(cfg)
    rows = WeatherMonth.objects.filter(uf__in=ufs, period=start,source__in=sources,model=weather_model(cfg),method__in=("area-grid-v1",METHOD)).defer("samples").order_by("uf","method","collected_at")
    result = {}
    if cfg.get("allow_fallback") and cfg.get("secondary_provider") and cfg["secondary_provider"]!=cfg["preferred_provider"]:
        alternative = {**cfg,"preferred_provider":cfg["secondary_provider"]}
        for row in WeatherMonth.objects.filter(uf__in=ufs,period=start,source__in=weather_sources(alternative),model=weather_model(alternative),method=METHOD).defer("samples").order_by("collected_at"):
            if row.consolidated_at and not row.history_complete: continue
            result[row.uf] = {**row.summary,"source":row.source,"model":row.model,"method":row.method,"revision":row.revision,"consolidated":bool(row.consolidated_at),"collected_at":row.collected_at.isoformat(),"stale":needs_collection(row,cfg),"fallback":True}
    # Current method always wins over v1, then use the latest collection.
    for row in rows:
        if row.consolidated_at and not row.history_complete: continue
        result[row.uf] = {**row.summary, "source":row.source, "model":row.model, "method":row.method,"revision":row.revision,"consolidated":bool(row.consolidated_at),
                          "collected_at":row.collected_at.isoformat(), "stale":needs_collection(row,cfg) or row.method!=METHOD}
    return result


def worker_tick():
    """One job at a time across processes; recover interrupted work and back off."""
    lease = Lease("geo-climate")
    if not lease.acquire():
        return
    stop, thread = lease.keep_alive()
    try:
        now = timezone.now()
        cooldown, _ = IntegrationSyncState.objects.get_or_create(key="weather-cooldown")
        if cooldown.change_cursor and cooldown.change_cursor > now:
            return
        turn,_ = IntegrationSyncState.objects.get_or_create(key="weather-turn",defaults={"records_count":0})
        local_due = WeatherLocalCache.objects.filter(status="pending",next_attempt_at__lte=now).exists()
        if local_due and (turn.records_count or 0)%2==0:
            from .weather_local import local_worker_tick
            local_worker_tick()
            IntegrationSyncState.objects.filter(pk=turn.pk).update(records_count=F("records_count")+1)
            return
        WeatherCollectionJob.objects.filter(status="running",updated_at__lt=now-timedelta(minutes=10)).update(status="pending",next_attempt_at=now)
        job = WeatherCollectionJob.objects.filter(status="pending",next_attempt_at__lte=now).order_by("created_at").first()
        if not job:
            from .weather_local import local_worker_tick
            local_worker_tick()
            return
        job.status, job.attempts = "running",job.attempts+1
        job.save()
        IntegrationSyncState.objects.filter(pk=turn.pk).update(records_count=F("records_count")+1)
        integration = get_geo_integration("open_meteo")
        try:
            try:
                collect_month(job.uf,job.period,integration,job=job)
            except PublicDataError as primary_error:
                cfg = configured_weather(integration)
                secondary = cfg.get("secondary_provider")
                # Only transport/5xx failure. Never quotas, auth, contract or licensing.
                if job.refresh_request or primary_error.code!="unavailable" or not cfg.get("allow_fallback") or not secondary or secondary==cfg["preferred_provider"]:
                    raise
                from copy import copy
                alternative = copy(integration)
                alternative.config = {**cfg,"preferred_provider":secondary,"allow_fallback":False}
                collect_month(job.uf,job.period,alternative,job=job)
                ExternalAPIHealthCheck.objects.create(integration=integration,status="success",message=f"Fallback autorizado {job.uf}: {secondary}; cache separado, referência temporal e variáveis próprias.")
            job.status, job.last_error = "success",""
            integration.last_success_at, integration.last_error = timezone.now(),""
            integration.health = "available"
        except Exception as error:
            if getattr(error,"code","") == "paused":
                return
            # Never persist provider response bodies, raw exception strings or URLs.
            job.last_error = str(error)[:240] if isinstance(error,PublicDataError) else "Falha climática; cache anterior preservado."
            job.status = "pending" if job.attempts < 3 else "error"
            try:
                job.next_attempt_at = now+timedelta(seconds=max(60*2**job.attempts,getattr(error,"retry_after",0)))
            except OverflowError:
                # An unrepresentable provider delay requires operator review, never an earlier automatic retry.
                job.status = "error"
                job.next_attempt_at = datetime.max.replace(tzinfo=datetime_timezone.utc)
            if getattr(error,"code","") in {"budget","rate_limited"}:
                job.attempts=max(0,job.attempts-1)
                if job.next_attempt_at.year<9999: job.status="pending"
                cooldown.change_cursor, cooldown.last_error = job.next_attempt_at, error.code
                cooldown.save(update_fields=("change_cursor","last_error"))
            if integration:
                integration.last_error, integration.health = job.last_error,"error"
        # A pause/cancel request made while the final HTTP response arrived wins.
        WeatherCollectionJob.objects.filter(pk=job.pk,status="running").update(status=job.status,attempts=job.attempts,next_attempt_at=job.next_attempt_at,last_error=job.last_error,refresh_request={} if job.status=="success" else job.refresh_request,updated_at=timezone.now())
        if integration:
            integration.save(update_fields=("last_success_at","last_error","health","updated_at"))
            ExternalAPIHealthCheck.objects.create(integration=integration,status="success" if job.status=="success" else "error",
                message=f"Clima {job.uf} {job.period:%Y-%m}: " + ("cache mensal atualizado." if job.status=="success" else job.last_error))
    finally:
        stop.set()
        thread.join(timeout=2)
        lease.release()
