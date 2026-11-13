"""Persistent coordinate contracts, batching and incremental daily reuse."""
import hashlib
import json
from datetime import timedelta

from django.utils import timezone
from ..clients.public_data import PublicDataError, PublicDataClient
from ..models import WeatherGridCache, WeatherMonth, WeatherCollectionJob, WeatherRequestLog
from .geo_configuration import weather_source
from .weather_adapters import get_adapter
from .weather_history import consolidate, persist, missing_intervals, saved_requests, integrity_ok


def context_for(adapter):
    return f"daily-v1:{adapter.timezone}:default-land-elevation"


def save_cell(cfg, adapter, point, start, end, sample, *, collected_at=None, expires_at=None, refresh=None, observed=False):
    lat, lon = adapter.coordinate(point)
    context = context_for(adapter)
    source = weather_source(cfg)
    identity = hashlib.sha256(json.dumps([source,adapter.model,lat,lon,str(start),str(end),context]).encode()).hexdigest()
    hours=min(24,cfg["cache_hours"]) if end>=timezone.localdate().replace(day=1) else cfg["cache_hours"]
    values={"payload":sample,"collected_at":collected_at or timezone.now(),"expires_at":expires_at or timezone.now()+timedelta(hours=hours)}
    row,created=WeatherGridCache.objects.get_or_create(identity=identity,defaults={"source":source,"model":adapter.model,
        "latitude":lat,"longitude":lon,"date_from":start,"date_to":end,"context":context,**values})
    if created: return consolidate(row,cfg)
    if row.payload==sample and not observed: return consolidate(row,cfg)
    if row.consolidated_at and not refresh: return row
    return persist(row,values,cfg,refresh=refresh)



def cached_cell(cfg, adapter, point, start, end):
    lat, lon = adapter.coordinate(point)
    return WeatherGridCache.objects.filter(source=weather_source(cfg),model=adapter.model,latitude=lat,longitude=lon,
        context=context_for(adapter),date_from__lte=start,date_to__gte=start).order_by("-date_to").first()


def clip(sample, start, end):
    daily = sample["daily"]
    indices = [i for i, day in enumerate(daily["time"]) if str(start)<=day<=str(end)]
    return {**sample,"daily":{key:[values[i] for i in indices] for key,values in daily.items()}}


def seed_months(cfg, adapter, start):
    """No recollection and no modification of existing territorial evidence."""
    from .geo_configuration import weather_sources
    if adapter.key != "open_meteo":
        return
    for row in WeatherMonth.objects.filter(period=start,source__in=weather_sources(cfg),model=adapter.model,
                                          method="area-grid-v2").iterator():
        for sample in row.samples:
            times = sample.get("daily",{}).get("time",[])
            if not times:
                continue
            from datetime import date
            # Older territorial caches merged equal returned cells before
            # retaining each coordinate's downscaled values. Only the first
            # coordinate has a recoverable response; never invent the others.
            for lat, lon in sample.get("requested_coordinates",[])[:1]:
                point = {"latitude":lat,"longitude":lon}
                if not cached_cell(cfg,adapter,point,date.fromisoformat(times[0]),date.fromisoformat(times[-1])):
                    save_cell(cfg,adapter,point,date.fromisoformat(times[0]),date.fromisoformat(times[-1]),
                        {k:sample[k] for k in ("cell","daily","units")},collected_at=row.collected_at,expires_at=row.expires_at)


def fetch_samples(integration, cfg, points, start, end, *, client=None, completed=None, checkpoint=None, force_refresh=False, refresh=None, store=True):
    from .weather import _count_request, weather_secret, VARIABLES
    from .weather_history import daily_integrity
    adapter=get_adapter(cfg)
    client=client or PublicDataClient(cfg["timeout"],cfg["retries"],weather_endpoint=cfg["endpoint"])
    completed=dict(completed or {})
    groups={}
    reused=set()
    for index,point in enumerate(points):
        key=adapter.coordinate(point)
        entry=groups.setdefault(key,{"point":point,"indices":[],"daily":{},"observed":{},"sample":None,"rows":[]})
        entry["indices"].append(index)
        if str(index) in completed:
            entry["sample"]=completed[str(index)]
            entry["daily"]={day:{v:completed[str(index)]["daily"][v][i] for v in VARIABLES} for i,day in enumerate(completed[str(index)]["daily"]["time"])}
            from django.utils.dateparse import parse_datetime
            checked=parse_datetime(entry["sample"].get("verified_at","")) or timezone.now()
            entry["observed"]={day:checked for day in entry["daily"]}
    pending={}
    for key,entry in groups.items():
        if entry["sample"] is not None and daily_integrity(entry["sample"],start,end,entry["sample"].get("supported_variables")):
            reused.add(key)
            continue
        rows=list(WeatherGridCache.objects.filter(source=weather_source(cfg),model=adapter.model,latitude=key[0],longitude=key[1],
            context=context_for(adapter),date_from__lte=end,date_to__gte=start).order_by("collected_at"))
        for row in rows:
            if not integrity_ok(row): continue
            sample=row.payload
            if not sample.get("daily"): continue
            entry["sample"]=sample
            for i,day in enumerate(sample["daily"]["time"]):
                if str(start)<=day<=str(end):
                    entry["daily"][day]={v:sample["daily"][v][i] for v in VARIABLES}
                    entry["observed"][day]=row.collected_at
        latest=max(rows,key=lambda r:r.collected_at,default=None)
        daily={"time":sorted(entry["daily"]),**{v:[entry["daily"][d][v] for d in sorted(entry["daily"])] for v in VARIABLES}}
        intervals=missing_intervals(daily,start,end,cfg,source=weather_source(cfg),collected_at=latest.collected_at if latest else None,
            expires_at=latest.expires_at if latest else None,force=force_refresh,supported=entry["sample"].get("supported_variables") if entry["sample"] else None,observed_at=entry["observed"])
        if not intervals: reused.add(key)
        for lower,upper in intervals: pending.setdefault((lower,upper),[]).append(entry)
    saved_requests(len(reused),min(cfg["batch_size"],adapter.batch_size))
    def publish(entry):
        times=[(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
        if not all(day in entry["daily"] for day in times): return
        checked=min(entry["observed"][day] for day in times)
        sample={**entry["sample"],"verified_at":checked.isoformat(),"daily":{"time":times,**{v:[entry["daily"][day][v] for day in times] for v in VARIABLES}}}
        if store: save_cell(cfg,adapter,entry["point"],start,end,sample,refresh=refresh,collected_at=checked)
        for index in entry["indices"]: completed[str(index)]=sample
        if checkpoint: checkpoint(completed)
    for entry in groups.values():
        if adapter.coordinate(entry["point"]) in reused: publish(entry)
    for (lower,upper),entries in sorted(pending.items()):
        batch_size=min(cfg["batch_size"],adapter.batch_size)
        for offset in range(0,len(entries),batch_size):
            batch=entries[offset:offset+batch_size]
            attempts=[]
            def before_attempt():
                log=_count_request(integration,cfg,locations=len(batch),days=(upper-lower).days+1,variables=7 if adapter.key=="open_meteo" else 4)
                if log: attempts.append(log.pk)
            try:
                samples=adapter.fetch(client,cfg,[e["point"] for e in batch],lower,upper,
                    weather_secret(integration,cfg) if adapter.key=="open_meteo" else "",before_attempt)
            except PublicDataError as error:
                if attempts: WeatherRequestLog.objects.filter(pk=attempts[-1]).update(status=error.code)
                raise
            if attempts: WeatherRequestLog.objects.filter(pk=attempts[-1]).update(status="success")
            for entry,sample in zip(batch,samples):
                checked=timezone.now();sample["verified_at"]=checked.isoformat()
                if store: save_cell(cfg,adapter,entry["point"],lower,upper,sample,refresh=refresh,observed=True,collected_at=checked)
                entry["sample"]=sample
                for i,day in enumerate(sample["daily"]["time"]):
                    entry["daily"][day]={v:sample["daily"][v][i] for v in VARIABLES}
                    entry["observed"][day]=checked
                publish(entry)
    return completed
