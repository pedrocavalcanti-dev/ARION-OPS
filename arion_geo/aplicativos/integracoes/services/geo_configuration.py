"""Idempotent GEO defaults and normalized non-secret weather configuration."""
from copy import deepcopy
from hashlib import sha256

from ..clients.public_data import ARCHIVE_ENDPOINTS, PublicDataError, validate_weather_endpoint

NAMES = {"ibge": "IBGE — Regiões e Estados", "open_meteo": "Open-Meteo — Clima e Histórico"}
WEATHER_DEFAULTS = {
    "mode": "public", "endpoint": ARCHIVE_ENDPOINTS["public"], "automatic": False,
    "scheduled_ufs": [], "interval_hours": 168, "timeout": 15, "retries": 1,
    "cache_hours": 720, "daily_budget": 1000, "batch_size": 8,
    "preferred_provider": "open_meteo", "secondary_provider": "", "allow_fallback": False,
    "supplier_daily_budget": 5000,
    "history_revision_days": "", "history_provisional_days": 7,
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
    if config["preferred_provider"] not in {"open_meteo", "nasa_power"} or config["secondary_provider"] not in {"", "open_meteo", "nasa_power"}:
        raise PublicDataError("Seleção de fornecedor inválida.", code="configuration")
    for key, minimum, maximum in (("daily_budget",1,100000),("supplier_daily_budget",1,1000000),("batch_size",1,8)):
        try:
            config[key] = int(config[key])
            if not minimum <= config[key] <= maximum:
                raise ValueError
        except (TypeError, ValueError):
            raise PublicDataError("Limites meteorológicos inválidos.", code="configuration") from None
    return config


def weather_source(config):
    if config.get("preferred_provider") == "nasa_power":
        return "nasa_power"
    if config["mode"] == "self_hosted":
        return "open_meteo_self_" + sha256(config["endpoint"].encode()).hexdigest()[:12]
    return "open_meteo_" + config["mode"]


def weather_source_info(config):
    if config.get("preferred_provider") == "nasa_power":
        return {"type":"nasa_power", "attribution":"NASA POWER / meteorologia GMAO",
                "endpoint":"https://power.larc.nasa.gov/api/temporal/daily/point",
                "terms_url":"https://power.larc.nasa.gov/docs/referencing/",
                "conditions":"Dados diários UTC; grade meteorológica 0,5° × 0,625°. Dados recentes podem ser provisórios. Sem rajada, vento máximo/direção ou série horária neste adaptador.",
                "timezone":"UTC", "resolution":"0.5° × 0.625°"}
    return {"type": config["mode"], "endpoint": config["endpoint"],
            "timezone":"America/Sao_Paulo", "resolution":"ERA5 · seleção terrestre e ajuste de elevação Open-Meteo",
            "attribution": ATTRIBUTION, "terms_url": TERMS_URL,
            "conditions": "Endpoint público sujeito às condições e quotas do fornecedor; não implica licença comercial."
            if config["mode"] == "public" else "Condições do contrato do fornecedor e atribuição dos dados aplicáveis."
            if config["mode"] == "commercial" else "Instância própria; observar a licença do software e as condições dos dados de origem."}


def weather_sources(config):
    sources = [weather_source(config)]
    if config.get("preferred_provider", "open_meteo") == "open_meteo" and config["mode"] == "public":
        sources.append("open_meteo_evaluation")
    return sources


def weather_model(config):
    from .weather_adapters import get_adapter
    return get_adapter(config).model
