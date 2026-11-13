"""Factual meteorological context; no projected/financial diagnoses or causality."""
from collections import defaultdict
from zoneinfo import ZoneInfo
from django.utils import timezone
from aplicativos.integracoes.models import WeatherLocalCache
from aplicativos.integracoes.services.geo_configuration import get_geo_integration, weather_configuration, weather_sources
from aplicativos.integracoes.services.geography import STATE_BY_UF
from aplicativos.integracoes.services.incidents import IncidentRules
from aplicativos.integracoes.services.weather_local import incident_window, locality_key
from aplicativos.integracoes.services.weather_history import needs_collection
from aplicativos.integracoes.clients.public_data import PublicDataError

MONITORED_CAUSE_KEYS = ("rompimento", "massiva", "equipamento", "energia")


def _status_classification(*, total, covered, coincident, confirmed):
    if confirmed > 0:
        return "confirmed_cause", "Causa confirmada"
    if coincident > 0:
        return "coincidence", "Coincidência identificada"
    if covered > 0:
        return "none", "Sem coincidência identificada"
    return "insufficient", "Sem dados suficientes"


def correlate(events, first, end, eligible, scoped_cities=None):
    if not first or not end:
        return {}
    try:
        cfg = weather_configuration(get_geo_integration("open_meteo"))
    except PublicDataError:
        return {
            "eligible_tickets": eligible, "located_tickets": 0, "covered_tickets": 0,
            "insufficient": eligible, "coincidence_identified": 0, "confirmed_cause": 0,
            "status_counts": {"insufficient": eligible, "none": 0, "coincidence": 0, "confirmed_cause": 0},
            "status_labels": {
                "insufficient": "Sem dados suficientes",
                "none": "Sem coincidência identificada",
                "coincidence": "Coincidência identificada",
                "confirmed_cause": "Causa confirmada",
            },
            "by_category": {}, "local_rankings": {}, "localities": [], "causality": False,
        }

    event_keys = {locality_key(e["city"], e["uf"]) for e in events if e.get("city") and e.get("uf")}
    extra_keys = {locality_key(city, uf): (city, uf) for uf, city in (scoped_cities or ()) if city and uf}
    all_keys = event_keys | set(extra_keys)

    rows = WeatherLocalCache.objects.filter(
        location_key__in=all_keys,
        period__range=(first.replace(day=1), end),
        source__in=weather_sources(cfg),
    ).order_by("collected_at")
    caches = {(r.location_key, r.period): r for r in rows}

    totals = {
        name: set() for name in (
            "located", "covered", "daily_covered", "rain_days", "rain",
            "ruptures_rain", "massive_rain", "equipment_weather", "wind_links",
            "coincidence", "contextual", "none", "confirmed_cause", "uf_only",
        )
    }
    cat_buckets = defaultdict(lambda: {
        "all": set(), "located": set(), "covered": set(),
        "coincident": set(), "confirmed": set(), "uf_only": set(),
    })
    ticket_cities = defaultdict(set)
    ticket_categories = {}
    localities = {}
    regions = defaultdict(set)

    # Seed monitored localities that already have valid cache inside the authorized link scope.
    for key, (city, uf) in extra_keys.items():
        cache = next((c for (k, _p), c in caches.items() if k == key and (c.status == "success" or c.collected_at)), None)
        if cache:
            localities.setdefault(key, {
                "city": city, "uf": uf, "tickets": set(), "rain_tickets": set(),
                "rain_day_tickets": set(), "ruptures_rain": set(), "massives_rain": set(),
                "equipment_weather": set(), "category_tickets": defaultdict(set), "cache": cache,
            })

    for e in events:
        tid = e["ticket"]
        category = e.get("category") or "demais"
        ticket_categories[tid] = category
        bucket = cat_buckets[category]
        bucket["all"].add(tid)
        if e.get("confirmed_weather_cause") is True and e.get("confirmed_weather_evidence"):
            totals["confirmed_cause"].add(tid)
            bucket["confirmed"].add(tid)

        uf = e.get("uf") or ""
        city = e.get("city") or ""
        if not city or not uf:
            if uf:
                totals["uf_only"].add(tid)
                bucket["uf_only"].add(tid)
            continue

        key = locality_key(city, uf)
        ticket_cities[tid].add(key)
        info = localities.setdefault(key, {
            "city": city, "uf": uf, "tickets": set(), "rain_tickets": set(),
            "rain_day_tickets": set(), "ruptures_rain": set(), "massives_rain": set(),
            "equipment_weather": set(), "category_tickets": defaultdict(set), "cache": None,
        })
        info["tickets"].add(tid)
        info["category_tickets"][category].add(tid)
        totals["located"].add(tid)
        bucket["located"].add(tid)

        at = e.get("at")
        if not at:
            continue
        cache = caches.get((key, timezone.localtime(at).date().replace(day=1)))
        if not cache or (cache.status != "success" and not cache.collected_at):
            continue
        info["cache"] = cache
        observed_date = at.astimezone(ZoneInfo(cache.payload.get("timezone", "UTC"))).date().isoformat()
        daily = next((d for d in cache.payload.get("summary", {}).get("daily", []) if d["date"] == observed_date), {})
        if daily.get("precipitation_sum") is not None:
            totals["daily_covered"].add(tid)
            if daily["precipitation_sum"] >= 1:
                totals["rain_days"].add(tid)
                info["rain_day_tickets"].add(tid)

        window = incident_window(cache, at)
        if window["state"] == "insufficient":
            continue
        totals["covered"].add(tid)
        bucket["covered"].add(tid)
        totals[window["state"]].add(tid)

        rainy = any((r.get("precipitation") or 0) >= 1 for r in window["hourly"])
        windy = any((r.get("wind_gusts_10m") or 0) >= 60 for r in window["hourly"])
        if rainy:
            totals["rain"].add(tid)
            info["rain_tickets"].add(tid)
            if uf in STATE_BY_UF:
                regions[STATE_BY_UF[uf]["region_id"]].add(tid)
            if category == "rompimento":
                totals["ruptures_rain"].add(tid)
                info["ruptures_rain"].add(tid)
            if category == "massiva":
                totals["massive_rain"].add(tid)
                info["massives_rain"].add(tid)
        if rainy or windy:
            if category in {"rompimento", "massiva", "equipamento", "energia"} and rainy:
                bucket["coincident"].add(tid)
            elif windy and category in {"equipamento", "energia"}:
                bucket["coincident"].add(tid)
            elif rainy:
                bucket["coincident"].add(tid)
        if (rainy or windy) and category in {"equipamento", "energia", "reset_remoto"}:
            totals["equipment_weather"].add(tid)
            if category in {"equipamento", "energia"}:
                info["equipment_weather"].add(tid)
        if windy and e.get("link"):
            totals["wind_links"].add(e["link"])

    # One analytical state per distinct ticket, using the strongest available context.
    totals["coincidence"] -= totals["contextual"] | totals["confirmed_cause"]
    totals["contextual"] -= totals["confirmed_cause"]
    totals["none"] -= totals["coincidence"] | totals["contextual"] | totals["confirmed_cause"]

    effective_eligible = max(eligible, len(ticket_categories))
    covered_count = len(totals["covered"])
    located_count = len(totals["located"])
    insufficient_count = max(0, effective_eligible - covered_count)
    coincidence_identified = len(totals["coincidence"] | totals["contextual"])
    confirmed_count = len(totals["confirmed_cause"])

    output = []
    for key, info in sorted(localities.items(), key=lambda pair: (-len(pair[1]["tickets"]), pair[0])):
        cache = info["cache"]
        weather_summary = cache.payload.get("summary") if cache else None
        output.append({
            "key": key,
            "city": info["city"],
            "uf": info["uf"],
            "incidents": len(info["tickets"]),
            "rain_incidents": len(info["rain_tickets"]),
            "rain_day_incidents": len(info["rain_day_tickets"]),
            "ruptures_rain": len(info["ruptures_rain"]),
            "massives_rain": len(info["massives_rain"]),
            "equipment_weather": len(info["equipment_weather"]),
            "category_counts": {IncidentRules.LABELS.get(c, c): len(ids) for c, ids in info["category_tickets"].items() if ids},
            "available": bool(cache),
            "ibge_code": cache.ibge_code if cache else "",
            "coordinates": [cache.longitude, cache.latitude] if cache and cache.longitude is not None and cache.latitude is not None else None,
            "precision": "Estimativa municipal IBGE · ERA5 aproximadamente 0,25°; não representa a posição ou o trajeto completo de rotas entre localidades.",
            "weather": weather_summary,
            "source": cache.source if cache else None,
            "stale": bool(cache and needs_collection(cache, cfg)),
        })

    by_category = {}
    for cat_key in MONITORED_CAUSE_KEYS:
        b = cat_buckets[cat_key]
        total_c = len(b["all"])
        loc_c = len(b["located"])
        cov_c = len(b["covered"])
        coinc_c = len(b["coincident"])
        conf_c = len(b["confirmed"])
        uf_only_c = len(b["uf_only"] - b["located"])
        without_loc_c = max(0, total_c - loc_c)
        without_cache_c = max(0, loc_c - cov_c)
        s_key, s_label = _status_classification(total=total_c, covered=cov_c, coincident=coinc_c, confirmed=conf_c)
        if total_c == 0:
            denom = "Sem registros desta categoria no recorte."
        elif cov_c == 0:
            denom = f"0 avaliáveis de {total_c} ({without_loc_c} sem localidade · {without_cache_c} sem cache local)."
        else:
            denom = f"{cov_c} avaliáveis de {total_c}; {coinc_c} coincidiram com evento climático local."
        by_category[cat_key] = {
            "key": cat_key,
            "label": IncidentRules.LABELS.get(cat_key, cat_key),
            "total": total_c,
            "located": loc_c,
            "covered": cov_c,
            "coincident": coinc_c,
            "confirmed": conf_c,
            "none": max(0, cov_c - coinc_c),
            "insufficient": max(0, total_c - cov_c),
            "without_location": without_loc_c,
            "without_cache": without_cache_c,
            "uf_only": uf_only_c,
            "status_key": s_key,
            "status_label": s_label,
            "denominator_label": denom,
        }

    overall_key, overall_label = _status_classification(
        total=effective_eligible, covered=covered_count,
        coincident=coincidence_identified, confirmed=confirmed_count,
    )

    available_cities = [row for row in output if row["available"] and row.get("weather")]
    local_rankings = {
        "precipitation_mm": [
            {"uf": row["uf"], "city": row["city"], "value": row["weather"].get("precipitation_mm"), "incidents": row["incidents"]}
            for row in sorted(available_cities, key=lambda r: (-(r["weather"].get("precipitation_mm") or 0), r["uf"], r["city"]))
            if row["weather"].get("precipitation_mm") is not None
        ][:12],
        "rainy_days": [
            {"uf": row["uf"], "city": row["city"], "value": row["weather"].get("rainy_days"), "incidents": row["incidents"]}
            for row in sorted(available_cities, key=lambda r: (-(r["weather"].get("rainy_days") or 0), r["uf"], r["city"]))
            if row["weather"].get("rainy_days") is not None
        ][:12],
        "rain_day_incidents": [
            {"uf": row["uf"], "city": row["city"], "value": row["rain_day_incidents"], "rain_mm": row["weather"].get("precipitation_mm")}
            for row in sorted(available_cities, key=lambda r: (-r["rain_day_incidents"], -(r["weather"].get("precipitation_mm") or 0), r["uf"], r["city"]))
            if row["rain_day_incidents"] > 0
        ][:12],
        "ruptures_rain": [
            {"uf": row["uf"], "city": row["city"], "value": row["ruptures_rain"], "incidents": row["incidents"]}
            for row in sorted(available_cities, key=lambda r: (-r["ruptures_rain"], r["uf"], r["city"]))
            if row["ruptures_rain"] > 0
        ][:12],
        "massives_rain": [
            {"uf": row["uf"], "city": row["city"], "value": row["massives_rain"], "incidents": row["incidents"]}
            for row in sorted(available_cities, key=lambda r: (-r["massives_rain"], r["uf"], r["city"]))
            if row["massives_rain"] > 0
        ][:12],
        "equipment_weather": [
            {"uf": row["uf"], "city": row["city"], "value": row["equipment_weather"], "incidents": row["incidents"]}
            for row in sorted(available_cities, key=lambda r: (-r["equipment_weather"], r["uf"], r["city"]))
            if row["equipment_weather"] > 0
        ][:12],
    }

    multi_city_tickets = sum(1 for keys_set in ticket_cities.values() if len(keys_set) >= 2)
    uf_only_tickets = len(totals["uf_only"] - totals["located"])
    without_cache_tickets = max(0, located_count - covered_count)

    return {
        "eligible_tickets": effective_eligible,
        "located_tickets": located_count,
        "covered_tickets": covered_count,
        "insufficient": insufficient_count,
        "without_location_tickets": max(0, effective_eligible - located_count),
        "without_cache_tickets": without_cache_tickets,
        "uf_only_tickets": uf_only_tickets,
        "multi_city_tickets": multi_city_tickets,
        **{k: len(v) for k, v in totals.items() if k not in {"located", "covered", "uf_only"}},
        "coincidence_identified": coincidence_identified,
        "overall_status": overall_key,
        "overall_status_label": overall_label,
        "status_counts": {
            "insufficient": insufficient_count,
            "none": len(totals["none"]),
            "coincidence": coincidence_identified,
            "confirmed_cause": confirmed_count,
        },
        "status_labels": {
            "insufficient": "Sem dados suficientes",
            "none": "Sem coincidência identificada",
            "coincidence": "Coincidência identificada",
            "confirmed_cause": "Causa confirmada",
        },
        "by_category": by_category,
        "local_rankings": local_rankings,
        "rain_localities": sum(bool(x["rain_incidents"]) for x in output),
        "localities": output,
        "region_rain_incidents": [{"region_id": key, "tickets": len(value)} for key, value in regions.items()],
        "levels": {
            "territorial": "Estimativa territorial por UF/região/Brasil (WeatherMonth · grade ERA5).",
            "local": "Clima próximo aos incidentes (município do link / ponto IBGE · WeatherLocalCache).",
        },
        "route_caveat": "Enlaces entre cidades e trechos extensos de fibra não ficam integralmente representados por um único município ou ponto IBGE.",
        "time_basis": "Janela de 6 h antes a 2 h depois da abertura OTRS (aproximação; início real ausente). Horas em UTC.",
        "definition": "Tickets técnicos distintos; vínculo municipal CMDB e cache local ERA5. Cidade informada na CMDB não confirma o local da ruptura, e estimativa estadual não confirma chuva no link.",
        "causality": False,
    }
