"""Shared, read-only GEO health for the portal, detail pages and map."""
import unicodedata
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import IntegrationLease, IntegrationSyncState, WeatherCollectionJob, WeatherMonth
from .geo_configuration import get_geo_integration, weather_configuration, weather_sources, weather_source_info
from .geography import STATES, catalog
from ..clients.public_data import PublicDataError


def worker_state():
    lease = IntegrationLease.objects.filter(key="worker").first()
    alive = bool(lease and lease.heartbeat_at and lease.expires_at > timezone.now())
    return {"active": alive, "label": "Ativo" if alive else "Parado", "heartbeat_at": lease.heartbeat_at if lease else None}


def allowed_weather_ufs(user):
    from .analytics import LinkQueryService
    if user.is_superuser:
        return [s["sigla"] for s in STATES]
    def norm(value):
        return "".join(c for c in unicodedata.normalize("NFD",str(value or "")) if not unicodedata.combining(c)).strip().casefold()
    present = {norm(v) for v in LinkQueryService.base(user).values_list("state",flat=True).distinct()}
    return [s["sigla"] for s in STATES if norm(s["sigla"]) in present or norm(s["nome"]) in present]


def geo_operational_context():
    from .weather import collection_readiness
    ibge, weather = (get_geo_integration(provider) for provider in ("ibge", "open_meteo"))
    geo = catalog()
    try:
        cfg = weather_configuration(weather)
        sources = weather_sources(cfg)
        source_info = weather_source_info(cfg)
    except PublicDataError:
        sources, source_info = [], {}
    caches = WeatherMonth.objects.filter(source__in=sources)
    jobs = WeatherCollectionJob.objects.values("status").annotate(count=Count("pk"))
    counts = {r["status"]:r["count"] for r in jobs}
    return {"geo_ibge":ibge, "geo_weather":weather, "geo_catalog":geo,
            "geo_catalog_synced_at":parse_datetime(geo["synced_at"]) if geo["synced_at"] else None,
            "geo_regions_count":len(geo["regions"]), "geo_states_count":len(geo["states"]),
            "geo_worker":worker_state(), "geo_readiness":collection_readiness(weather),
            "geo_source_info":source_info, "geo_cache_count":caches.count(),
            "geo_last_weather":caches.order_by("-collected_at").first(),
            "geo_requests":IntegrationSyncState.objects.filter(key__startswith="meteo-"+timezone.localdate().strftime("%Y-%m")).aggregate(total=Sum("records_count"))["total"] or 0,
            "geo_pending":counts.get("pending",0), "geo_running":counts.get("running",0), "geo_failed":counts.get("error",0),
            "geo_ibge_sync":IntegrationSyncState.objects.filter(key="ibge").first()}
