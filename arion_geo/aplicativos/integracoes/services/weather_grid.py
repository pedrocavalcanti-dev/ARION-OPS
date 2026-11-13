"""Persistent coordinate contracts, batching and incremental daily reuse."""
import hashlib
import json
from datetime import timedelta

from django.utils import timezone
from ..clients.public_data import PublicDataError, PublicDataClient
from ..models import WeatherGridCache, WeatherMonth, WeatherCollectionJob, WeatherRequestLog
from .geo_configuration import weather_source
from .weather_adapters import get_adapter


def context_for(adapter):
    return f"daily-v1:{adapter.timezone}:default-land-elevation"


def save_cell(cfg, adapter, point, start, end, sample, *, collected_at=None, expires_at=None):
    lat, lon = adapter.coordinate(point)
    context = context_for(adapter)
    source = weather_source(cfg)
    identity = hashlib.sha256(json.dumps([source,adapter.model,lat,lon,str(start),str(end),context]).encode()).hexdigest()
    return WeatherGridCache.objects.update_or_create(identity=identity, defaults={"source":source,"model":adapter.model,
        "latitude":lat,"longitude":lon,"date_from":start,"date_to":end,"context":context,"payload":sample,
        "collected_at":collected_at or timezone.now(),"expires_at":expires_at or timezone.now()+timedelta(hours=cfg["cache_hours"])})[0]


def cached_cell(cfg, adapter, point, start, end):
    lat, lon = adapter.coordinate(point)
    return WeatherGridCache.objects.filter(source=weather_source(cfg),model=adapter.model,latitude=lat,longitude=lon,
        context=context_for(adapter),date_from__lte=start,date_to__gte=start,expires_at__gt=timezone.now()).order_by("-date_to").first()


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
                                          method="area-grid-v2",expires_at__gt=timezone.now()).iterator():
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


def fetch_samples(integration, cfg, points, start, end, *, client=None, completed=None, checkpoint=None, force_refresh=False):
    from .weather import _count_request, weather_secret
    adapter = get_adapter(cfg)
    client = client or PublicDataClient(cfg["timeout"],cfg["retries"],weather_endpoint=cfg["endpoint"])
    completed = dict(completed or {})
    pending = {}
    for index, point in enumerate(points):
        if str(index) in completed:
            # Promote old checkpoints into the shared grid without new HTTP.
            save_cell(cfg,adapter,point,start,end,completed[str(index)])
            continue
        row = None if force_refresh else cached_cell(cfg,adapter,point,start,end)
        if row and row.date_to>=end:
            completed[str(index)] = clip(row.payload,start,end)
            if checkpoint: checkpoint(completed)
            continue
        request_start = max(start,row.date_to+timedelta(days=1)) if row else start
        key = (*adapter.coordinate(point),request_start)
        pending.setdefault(key,{"point":point,"indices":[],"prefix":clip(row.payload,start,row.date_to) if row else None})["indices"].append(index)
    # Different missing suffixes are not extended or fetched a second time.
    starts = sorted({key[2] for key in pending})
    for request_start in starts:
        entries = [entry for key,entry in pending.items() if key[2]==request_start]
        batch_size = min(cfg["batch_size"],adapter.batch_size)
        for offset in range(0,len(entries),batch_size):
            batch = entries[offset:offset+batch_size]
            attempts = []
            def before_attempt():
                log = _count_request(integration,cfg,locations=len(batch),days=(end-request_start).days+1,
                                     variables=7 if adapter.key=="open_meteo" else 4)
                if log: attempts.append(log.pk)
            try:
                samples = adapter.fetch(client,cfg,[entry["point"] for entry in batch],request_start,end,
                                        weather_secret(integration,cfg) if adapter.key=="open_meteo" else "",before_attempt)
            except PublicDataError as error:
                if attempts: WeatherRequestLog.objects.filter(pk=attempts[-1]).update(status=error.code)
                raise
            if attempts: WeatherRequestLog.objects.filter(pk=attempts[-1]).update(status="success")
            for entry, sample in zip(batch,samples):
                prefix = entry["prefix"]
                if prefix:
                    sample = {**sample,"daily":{key:prefix["daily"][key]+values for key,values in sample["daily"].items()}}
                save_cell(cfg,adapter,entry["point"],start,end,sample)
                for index in entry["indices"]:
                    completed[str(index)] = sample
                if checkpoint: checkpoint(completed)
    return completed
