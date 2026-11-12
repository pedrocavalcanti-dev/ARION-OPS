"""Idempotent GEO defaults and normalized non-secret weather configuration."""
from copy import deepcopy
from hashlib import sha256

from ..clients.public_data import ARCHIVE_ENDPOINTS, PublicDataError, validate_weather_endpoint

NAMES = {"ibge": "IBGE — Regiões e Estados", "open_meteo": "Open-Meteo — Clima e Histórico"}
WEATHER_DEFAULTS = {
    "mode": "public", "endpoint": ARCHIVE_ENDPOINTS["public"], "automatic": False,
    "scheduled_ufs": [], "interval_hours": 168, "timeout": 15, "retries": 1,
    "cache_hours": 720, "daily_budget": 250,
}
ATTRIBUTION = "Open-Meteo / Copernicus ERA5"
TERMS_URL = "https://open-meteo.com/en/terms"


def get_geo_integration(provider, *, using="default"):
    from ..models import ExternalAPIIntegration
    config = WEATHER_DEFAULTS if provider == "open_meteo" else {"automatic": False, "interval_hours": 720, "timeout": 15}
    return ExternalAPIIntegration.objects.using(using).get_or_create(
        provider=provider, defaults={"name": NAMES[provider], "enabled": True, "config": deepcopy(config)},
    )[0]


def bootstrap_geo_integrations(sender=None, using="default", **kwargs):
    from django.db import router
    from ..models import ExternalAPIIntegration
    if router.allow_migrate_model(using, ExternalAPIIntegration):
        for provider in NAMES:
            get_geo_integration(provider, using=using)


def weather_configuration(integration):
    """Keep legacy evaluation caches/settings readable without environment gates."""
    config = {**deepcopy(WEATHER_DEFAULTS), **(integration.config or {})}
    config["mode"] = {"evaluation": "public"}.get(config["mode"], config["mode"])
    if "endpoint" not in (integration.config or {}):
        config["endpoint"] = ARCHIVE_ENDPOINTS.get(config["mode"], "")
    if config["mode"] not in {"public", "commercial", "self_hosted"}:
        raise PublicDataError("Fonte meteorológica inválida.", code="configuration")
    config["endpoint"] = validate_weather_endpoint(config["mode"], config["endpoint"])
    return config


def weather_source(config):
    if config["mode"] == "self_hosted":
        return "open_meteo_self_" + sha256(config["endpoint"].encode()).hexdigest()[:12]
    return "open_meteo_" + config["mode"]


def weather_source_info(config):
    return {"type": config["mode"], "endpoint": config["endpoint"],
            "attribution": ATTRIBUTION, "terms_url": TERMS_URL,
            "conditions": "Endpoint público sujeito às condições e quotas do fornecedor; não implica licença comercial."
            if config["mode"] == "public" else "Condições do contrato do fornecedor e atribuição dos dados aplicáveis."
            if config["mode"] == "commercial" else "Instância própria; observar a licença do software e as condições dos dados de origem."}


def weather_sources(config):
    sources = [weather_source(config)]
    if config["mode"] == "public":
        sources.append("open_meteo_evaluation")
    return sources
