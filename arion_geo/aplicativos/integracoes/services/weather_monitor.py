"""Read-only source telemetry. A configured source is not a successful test."""
from datetime import timedelta
from django.db.models import Sum
from django.utils import timezone
from ..models import WeatherRequestLog, WeatherGridCache, WeatherMonth
from .geo_configuration import weather_configuration, weather_source, weather_source_info


def provider_monitor(integration):
    from ..clients.public_data import PublicDataError
    try:
        cfg = weather_configuration(integration)
    except PublicDataError:
        return []
    rows=[]
    for provider, label in (("open_meteo","Open-Meteo"),("nasa_power","NASA POWER")):
        config={**cfg,"preferred_provider":provider}
        source=weather_source(config)
        logs=WeatherRequestLog.objects.filter(source=source)
        recent=logs.filter(started_at__gte=timezone.now()-timedelta(days=1))
        last=logs.order_by("-started_at").first()
        rows.append({"label":label,"active":provider==cfg["preferred_provider"],"secondary":provider==cfg.get("secondary_provider"),
            "status":last.status if last else "Não testado", "last_attempt":last.started_at if last else None,
            "http":recent.count(),"locations":recent.aggregate(n=Sum("locations"))["n"] or 0,
            "units":round(recent.aggregate(n=Sum("estimated_units"))["n"] or 0,2),
            "grids":WeatherGridCache.objects.filter(source=source).count(),
            "months":WeatherMonth.objects.filter(source=source).count(),"info":weather_source_info(config),
            "limits":"Público: 600/min · 5.000/h · 10.000/dia; uso sujeito às condições. Comercial/self-hosted: contrato próprio." if provider=="open_meteo" else "Uma célula por vez; deduplicação da grade nativa. Quota externa restante não informada pela API."})
    return rows
