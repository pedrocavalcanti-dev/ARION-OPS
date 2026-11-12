"""Read-only estimates and queue controls shared by Central and GEO."""
from django.utils import timezone
from django.core import signing
from ..models import IntegrationSyncState, WeatherCollectionJob
from .geo_configuration import get_geo_integration, weather_configuration, weather_source
from .weather import cached_weather, month_bounds, sampling_points


def plan_identity(user, ufs, period):
    cfg=weather_configuration(get_geo_integration("open_meteo"))
    return {"user":user.pk,"ufs":sorted(ufs),"period":period,"source":weather_source(cfg),"method":"area-grid-v2"}


def plan_token(user, ufs, period):
    return signing.dumps(plan_identity(user,ufs,period),salt="weather-plan")


def verify_plan(token, user, ufs, period):
    try:
        return signing.loads(token,salt="weather-plan",max_age=1800)==plan_identity(user,ufs,period)
    except signing.BadSignature:
        return False


def collection_plan(ufs, period):
    first, _ = month_bounds(period)
    rows = cached_weather(ufs, period)
    valid = {uf: row for uf, row in rows.items() if not row["stale"] and not row.get("partial") and row.get("completeness",0)>=.99}
    areas = {uf:sum(p["area_km2"] for p in sampling_points(uf)) for uf in ufs}
    total = sum(areas.values())
    jobs = list(WeatherCollectionJob.objects.filter(uf__in=ufs,period=first))
    counts = {status:sum(j.status==status for j in jobs) for status in ("pending","running","error","paused","cancelled")}
    cfg = weather_configuration(get_geo_integration("open_meteo"))
    used = IntegrationSyncState.objects.filter(key="meteo-"+timezone.localdate().isoformat()).values_list("records_count",flat=True).first() or 0
    missing = [uf for uf in ufs if uf not in valid]
    checkpoints = {j.uf:len(j.progress.get("samples",{})) for j in jobs if j.uf in missing and j.progress.get("fingerprint","").startswith(f"{weather_source(cfg)}:era5:area-grid-v2:{first}:")}
    requests = sum(max(0,len(sampling_points(uf))-checkpoints.get(uf,0)) for uf in missing)
    covered_area = sum(areas[uf]*rows[uf].get("completeness",0) for uf in rows)
    return {"expected_ufs":len(ufs),"completed_ufs":len(valid),"available_ufs":len(rows),"missing_ufs":missing,
            **counts,"cells":sum(r.get("points",0) for r in rows.values()),
            "checkpoint_cells":sum(checkpoints.values()),"territorial_percent":round(100*covered_area/total,2) if total else 0,
            "estimated_requests":requests,"requests_today":used,"daily_budget":int(cfg["daily_budget"]),
            "remaining_today":max(0,int(cfg["daily_budget"])-used),"method":"area-grid-v2",
            "last_update":max((r["collected_at"] for r in rows.values()),default=None),
            "label":f"Cobertura {'completa' if len(valid)==len(ufs) and ufs else 'parcial'}: {len(valid)} de {len(ufs)} UFs",
            "area_definition":"Quadratura territorial coberta × completude das variáveis; não é percentual de UFs nem observação contínua do território."}


def control_jobs(ufs, period, action):
    first, _ = month_bounds(period)
    jobs = WeatherCollectionJob.objects.filter(uf__in=ufs,period=first)
    if action=="pause":
        return jobs.filter(status__in=("pending","running")).update(status="paused",updated_at=timezone.now())
    if action=="resume":
        return jobs.filter(status__in=("paused","error","cancelled")).update(status="pending",attempts=0,next_attempt_at=timezone.now(),updated_at=timezone.now())
    if action=="cancel":
        return jobs.filter(status__in=("pending","paused")).update(status="cancelled",updated_at=timezone.now())
    raise ValueError("Ação de fila inválida.")
