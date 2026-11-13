"""Factual meteorological context; no projected/financial diagnoses or causality."""
from collections import defaultdict
from zoneinfo import ZoneInfo
from django.db.models import Q
from django.utils import timezone
from aplicativos.integracoes.models import WeatherLocalCache
from aplicativos.integracoes.services.geo_configuration import get_geo_integration, weather_configuration, weather_sources
from aplicativos.integracoes.services.geography import STATE_BY_UF
from aplicativos.integracoes.services.incidents import IncidentRules
from aplicativos.integracoes.services.weather_local import (
    incident_window,
    load_municipal_catalogs,
    locality_key,
    resolve_municipality,
)
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

    all_ufs = {e.get("uf") for e in events if e.get("uf")} | {uf for uf, _ in (scoped_cities or ()) if uf}
    catalogs = load_municipal_catalogs(all_ufs)
    res_cache = {}

    def _resolve(city_val, uf_val):
        ck = (uf_val, city_val)
        if ck not in res_cache:
            res_cache[ck] = resolve_municipality(city_val, uf_val, catalogs)
        return res_cache[ck]

    def _canon_key(res):
        if res["status"] == "matched":
            return f"{res['uf']}:ibge:{res['ibge_code']}"
        if res["location_key"]:
            return res["location_key"]
        return f"{res['uf']}:raw:{res['raw_city'].casefold()}"

    candidate_keys = set()
    candidate_ibges = set()
    resolved_scoped = []
    for uf_s, city_s in (scoped_cities or ()):
        if not uf_s or not city_s:
            continue
        res = _resolve(city_s, uf_s)
        if res["status"] == "placeholder":
            continue
        if res["location_key"]:
            candidate_keys.add(res["location_key"])
        candidate_keys.add(locality_key(city_s, uf_s))
        if res["ibge_code"]:
            candidate_ibges.add(res["ibge_code"])
        resolved_scoped.append(res)

    for e in events:
        uf_e, city_e = e.get("uf") or "", e.get("city") or ""
        if not uf_e or not city_e:
            continue
        res = _resolve(city_e, uf_e)
        if res["status"] == "placeholder":
            continue
        if res["location_key"]:
            candidate_keys.add(res["location_key"])
        candidate_keys.add(locality_key(city_e, uf_e))
        if res["ibge_code"]:
            candidate_ibges.add(res["ibge_code"])

    cache_filter = Q(location_key__in=candidate_keys) if candidate_keys else Q(pk__in=[])
    if candidate_ibges and all_ufs:
        cache_filter = cache_filter | Q(uf__in=all_ufs, ibge_code__in=candidate_ibges)
    rows = WeatherLocalCache.objects.filter(
        cache_filter,
        period__range=(first.replace(day=1), end),
        source__in=weather_sources(cfg),
    ).order_by("collected_at", "updated_at")

    valid_by_key = {}
    valid_by_ibge = {}
    any_by_key = {}
    any_by_ibge = {}
    for r in rows:
        any_by_key[(r.location_key, r.period)] = r
        if r.ibge_code:
            any_by_ibge[(r.uf, r.ibge_code, r.period)] = r
        if r.status == "success" or r.collected_at:
            valid_by_key[(r.location_key, r.period)] = r
            if r.ibge_code:
                valid_by_ibge[(r.uf, r.ibge_code, r.period)] = r

    def _find_cache(res, month_period=None, *, only_valid=True):
        key_map = valid_by_key if only_valid else any_by_key
        ibge_map = valid_by_ibge if only_valid else any_by_ibge
        raw_lk = locality_key(res["raw_city"], res["uf"])
        if month_period is not None:
            if res["ibge_code"]:
                hit = ibge_map.get((res["uf"], res["ibge_code"], month_period))
                if hit:
                    return hit
            return key_map.get((res["location_key"], month_period)) or key_map.get((raw_lk, month_period))
        if res["ibge_code"]:
            hit = next((c for (u, code, _p), c in ibge_map.items() if u == res["uf"] and code == res["ibge_code"]), None)
            if hit:
                return hit
        return next((c for (k, _p), c in key_map.items() if k in {res["location_key"], raw_lk}), None)

    totals = {
        name: set() for name in (
            "located", "covered", "daily_covered", "rain_days", "rain",
            "ruptures_rain", "massive_rain", "equipment_weather", "wind_links",
            "wind_incidents", "wind_equipment_energy",
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
    day_rain_stats = defaultdict(lambda: {"tickets": set(), "coincident": set(), "cities": set()})
    uf_coincidence = defaultdict(lambda: {
        "incidents": set(), "covered": set(), "coincident": set(), "rain": set(), "wind": set(),
    })

    def _new_loc(res, valid_cache=None, any_cache=None):
        display_city = res["canonical_city"] or res["raw_city"]
        return {
            "city": display_city,
            "uf": res["uf"],
            "ibge_code": res["ibge_code"] or (valid_cache.ibge_code if valid_cache else ""),
            "normalization_status": res["status"],
            "catalog_available": res["catalog_available"],
            "eligible_for_collection": res["eligible_for_collection"],
            "raw_variants": {res["raw_city"]} if res["raw_city"] else set(),
            "tickets": set(),
            "covered_tickets": set(),
            "coincident_tickets": set(),
            "rain_tickets": set(),
            "wind_tickets": set(),
            "rain_day_tickets": set(),
            "ruptures_rain": set(),
            "massives_rain": set(),
            "equipment_weather": set(),
            "category_tickets": defaultdict(set),
            "cache": valid_cache,
            "queue_row": any_cache,
        }

    # Seed monitored localities that already have valid cache inside the authorized link scope.
    for res in resolved_scoped:
        valid_cache = _find_cache(res, only_valid=True)
        any_cache = _find_cache(res, only_valid=False)
        if valid_cache:
            ckey = _canon_key(res)
            entry = localities.get(ckey)
            if entry is None:
                entry = _new_loc(res, valid_cache=valid_cache, any_cache=any_cache)
                localities[ckey] = entry
            elif res["raw_city"]:
                entry["raw_variants"].add(res["raw_city"])

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

        res = _resolve(city, uf)
        if res["status"] == "placeholder":
            totals["uf_only"].add(tid)
            bucket["uf_only"].add(tid)
            continue

        key = _canon_key(res)
        ticket_cities[tid].add(key)
        any_cache = _find_cache(res, only_valid=False)
        info = localities.get(key)
        if info is None:
            info = _new_loc(res, valid_cache=None, any_cache=any_cache)
            localities[key] = info
        else:
            if res["raw_city"]:
                info["raw_variants"].add(res["raw_city"])
            if not info["ibge_code"] and res["ibge_code"]:
                info["ibge_code"] = res["ibge_code"]
            if not info["queue_row"] and any_cache:
                info["queue_row"] = any_cache
        info["tickets"].add(tid)
        info["category_tickets"][category].add(tid)
        uf_coincidence[uf]["incidents"].add(tid)
        totals["located"].add(tid)
        bucket["located"].add(tid)

        at = e.get("at")
        if not at:
            continue
        month_period = timezone.localtime(at).date().replace(day=1)
        cache = _find_cache(res, month_period=month_period, only_valid=True)
        q_row = _find_cache(res, month_period=month_period, only_valid=False)
        if q_row:
            info["queue_row"] = q_row
        if not cache:
            continue
        info["cache"] = cache
        if not info["ibge_code"] and cache.ibge_code:
            info["ibge_code"] = cache.ibge_code
        observed_date = at.astimezone(ZoneInfo(cache.payload.get("timezone", "UTC"))).date().isoformat()
        daily = next((d for d in cache.payload.get("summary", {}).get("daily", []) if d["date"] == observed_date), {})
        if daily.get("precipitation_sum") is not None:
            totals["daily_covered"].add(tid)
            if daily["precipitation_sum"] >= 1:
                totals["rain_days"].add(tid)
                info["rain_day_tickets"].add(tid)
                day_rain_stats[observed_date]["tickets"].add(tid)
                day_rain_stats[observed_date]["cities"].add(f"{info['city']}/{uf}")

        window = incident_window(cache, at)
        if window["state"] == "insufficient":
            continue
        totals["covered"].add(tid)
        bucket["covered"].add(tid)
        info["covered_tickets"].add(tid)
        uf_coincidence[uf]["covered"].add(tid)
        totals[window["state"]].add(tid)

        rainy = any((r.get("precipitation") or 0) >= 1 for r in window["hourly"])
        windy = any((r.get("wind_gusts_10m") or 0) >= 60 or (r.get("wind_speed_10m") or 0) >= 40 for r in window["hourly"])
        if rainy:
            totals["rain"].add(tid)
            info["rain_tickets"].add(tid)
            uf_coincidence[uf]["rain"].add(tid)
            day_rain_stats[observed_date]["coincident"].add(tid)
            day_rain_stats[observed_date]["cities"].add(f"{info['city']}/{uf}")
            if uf in STATE_BY_UF:
                regions[STATE_BY_UF[uf]["region_id"]].add(tid)
            if category == "rompimento":
                totals["ruptures_rain"].add(tid)
                info["ruptures_rain"].add(tid)
            if category == "massiva":
                totals["massive_rain"].add(tid)
                info["massives_rain"].add(tid)
        if windy:
            totals["wind_incidents"].add(tid)
            info["wind_tickets"].add(tid)
            uf_coincidence[uf]["wind"].add(tid)
            if category in {"equipamento", "energia"}:
                totals["wind_equipment_energy"].add(tid)
        if rainy or windy:
            coincident_here = False
            if category in {"rompimento", "massiva", "equipamento", "energia"} and rainy:
                bucket["coincident"].add(tid)
                coincident_here = True
            elif windy and category in {"equipamento", "energia"}:
                bucket["coincident"].add(tid)
                coincident_here = True
            elif rainy:
                bucket["coincident"].add(tid)
                coincident_here = True
            if coincident_here:
                info["coincident_tickets"].add(tid)
                uf_coincidence[uf]["coincident"].add(tid)
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

    _NORM_REASON = {
        "unmatched": "Sem correspondência exata no catálogo IBGE (mantida apenas UF)",
        "ambiguous_route": "Rota / múltiplos pontos informados na CMDB",
        "circuit_or_site": "Identificação de circuito/equipamento em vez de município",
        "ambiguous_catalog": "Nome ambíguo no catálogo municipal da UF",
        "catalog_pending": "Catálogo municipal IBGE da UF ainda não sincronizado",
    }

    output = []
    for key, info in sorted(localities.items(), key=lambda pair: (-len(pair[1]["tickets"]), pair[0])):
        cache = info["cache"]
        q_row = info["queue_row"]
        weather_summary = cache.payload.get("summary") if cache else None
        norm_status = info["normalization_status"]
        if cache:
            q_status = "collected"
        elif not info["eligible_for_collection"]:
            q_status = "validation_pending"
        elif q_row and q_row.status in {"pending", "running", "error"}:
            q_status = q_row.status
        else:
            q_status = "awaiting_queue"
        q_reason = ""
        if q_status == "error" and q_row:
            q_reason = q_row.last_error or "Falha na coleta municipal"
        elif not info["eligible_for_collection"] or norm_status == "catalog_pending":
            q_reason = _NORM_REASON.get(norm_status, "")
        raw_vars = sorted(v for v in info["raw_variants"] if v and v != info["city"])
        output.append({
            "key": key,
            "city": info["city"],
            "uf": info["uf"],
            "incidents": len(info["tickets"]),
            "covered_incidents": len(info["covered_tickets"]),
            "coincident_incidents": len(info["coincident_tickets"]),
            "rain_incidents": len(info["rain_tickets"]),
            "wind_incidents": len(info["wind_tickets"]),
            "rain_day_incidents": len(info["rain_day_tickets"]),
            "ruptures_rain": len(info["ruptures_rain"]),
            "massives_rain": len(info["massives_rain"]),
            "equipment_weather": len(info["equipment_weather"]),
            "category_counts": {IncidentRules.LABELS.get(c, c): len(ids) for c, ids in info["category_tickets"].items() if ids},
            "available": bool(cache),
            "ibge_code": info["ibge_code"] or (cache.ibge_code if cache else ""),
            "normalization_status": norm_status,
            "eligible_for_collection": info["eligible_for_collection"],
            "queue_status": q_status,
            "pending_reason": q_reason,
            "raw_variants": raw_vars[:8],
            "merged_variants": len(info["raw_variants"]),
            "coordinates": [cache.longitude, cache.latitude] if cache and cache.longitude is not None and cache.latitude is not None else None,
            "precision": "Estimativa municipal IBGE · ERA5 aproximadamente 0,25°; não representa a posição ou o trajeto completo de rotas entre localidades.",
            "weather": weather_summary,
            "source": cache.source if cache else None,
            "stale": bool(cache and needs_collection(cache, cfg)),
            "collected_at": cache.collected_at.isoformat() if cache and cache.collected_at else None,
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
            "evaluable": cov_c,
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
            {"uf": row["uf"], "city": row["city"], "value": row["rain_day_incidents"], "incidents": row["incidents"], "rain_mm": row["weather"].get("precipitation_mm")}
            for row in sorted(available_cities, key=lambda r: (-r["rain_day_incidents"], -(r["weather"].get("precipitation_mm") or 0), r["uf"], r["city"]))
            if row["rain_day_incidents"] > 0
        ][:12],
        "coincident_incidents": [
            {"uf": row["uf"], "city": row["city"], "value": row["coincident_incidents"], "incidents": row["incidents"], "rain_incidents": row["rain_incidents"], "wind_incidents": row["wind_incidents"]}
            for row in sorted(available_cities, key=lambda r: (-r["coincident_incidents"], -r["incidents"], r["uf"], r["city"]))
            if row["coincident_incidents"] > 0
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

    coincident_by_uf = [
        {
            "uf": uf_code,
            "incidents": len(vals["incidents"]),
            "covered_incidents": len(vals["covered"]),
            "coincident_incidents": len(vals["coincident"]),
            "rain_incidents": len(vals["rain"]),
            "wind_incidents": len(vals["wind"]),
        }
        for uf_code, vals in sorted(uf_coincidence.items(), key=lambda pair: (-len(pair[1]["coincident"]), -len(pair[1]["incidents"]), pair[0]))
        if vals["covered"]
    ]

    top_rainy_days = [
        {
            "date": day_str,
            "incidents": len(stats["tickets"]),
            "coincident": len(stats["coincident"]),
            "cities_count": len(stats["cities"]),
        }
        for day_str, stats in sorted(day_rain_stats.items(), key=lambda pair: (-len(pair[1]["coincident"]), -len(pair[1]["tickets"]), pair[0]))
        if stats["coincident"] or stats["tickets"]
    ][:8]

    municipal_coverage_summary = {
        "total_localities": len(output),
        "ibge_matched": sum(1 for row in output if row["ibge_code"]),
        "deduplicated_variants": sum(max(0, row["merged_variants"] - 1) for row in output),
        "cache_collected": sum(1 for row in output if row["available"]),
        "cache_pending": sum(1 for row in output if row["queue_status"] == "pending"),
        "cache_running": sum(1 for row in output if row["queue_status"] == "running"),
        "cache_error": sum(1 for row in output if row["queue_status"] == "error"),
        "awaiting_queue": sum(1 for row in output if row["queue_status"] == "awaiting_queue"),
        "awaiting_validation": sum(1 for row in output if row["queue_status"] == "validation_pending"),
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
        "coincident_by_uf": coincident_by_uf,
        "top_rainy_incident_days": top_rainy_days,
        "municipal_coverage": municipal_coverage_summary,
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
