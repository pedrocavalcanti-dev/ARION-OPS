"""Durable source history, integrity and explicit immutable revisions.

HTTP cache age is not the lifetime of a meteorological observation.
"""
import calendar
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone as utc_timezone
from uuid import uuid4

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..clients.public_data import PublicDataError
from ..models import (WeatherMonth, WeatherLocalCache, WeatherGridCache,
                      WeatherHistoryRevision, IntegrationSyncState, WeatherCollectionJob)

KINDS = {WeatherMonth:"month",WeatherLocalCache:"local",WeatherGridCache:"grid"}
CONTENT = {"month":("uf","region","period","source","model","method","samples","summary","completeness"),
           "local":("location_key","city","uf","ibge_code","latitude","longitude","precision","period","source","model","method","payload"),
           "grid":("source","model","latitude","longitude","date_from","date_to","context","payload")}


def month_end(first):
    return first.replace(day=calendar.monthrange(first.year,first.month)[1])


def review_date(first, source, cfg=None):
    cfg=cfg or {}
    days=int(cfg.get("history_revision_days") or (120 if source=="nasa_power" else 90))
    return month_end(first)+timedelta(days=max(90,min(days,730)))


def document(row):
    return json.loads(json.dumps({key:getattr(row,key) for key in CONTENT[KINDS[type(row)]]},cls=DjangoJSONEncoder))


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")


def digest(row):
    return hashlib.sha256(encoded(document(row))).hexdigest()


def daily_integrity(sample, first, end, supported=None):
    from .weather import VARIABLES
    supported=supported or list(VARIABLES)
    try:
        days=[(first+timedelta(days=i)).isoformat() for i in range((end-first).days+1)]
        data=sample["daily"]
        if not days or data["time"]!=days or not supported or not set(supported)<=set(VARIABLES): return False
        if not all(sample["units"].get(key)==VARIABLES[key] for key in VARIABLES): return False
        for key in supported:
            values=data[key]
            if len(values)!=len(days): return False
            for value in values:
                if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value): return False
                if not key.startswith("temperature_") and value<0: return False
                if key=="wind_direction_10m_dominant" and value>360: return False
        return True
    except (KeyError,TypeError,ValueError):
        return False


def complete(row):
    """Full calendar month, complete supported variables, no invented hours."""
    from .weather import VARIABLES, summarize
    try:
        if isinstance(row,WeatherMonth):
            first,end=row.period,month_end(row.period)
            supported=row.summary.get("supported_variables")
            if row.method!="area-grid-v2" or not row.samples: return False
            if not all(p.get("area_km2",0)>0 and daily_integrity(p,first,end,supported) for p in row.samples): return False
            calculated=summarize(row.samples)
            # Summary is an observable contract too, not just a completeness flag.
            return all(row.summary.get(key)==calculated[key] for key in ("daily","points","precipitation_mm","temperature_mean_c","completeness"))
        if isinstance(row,WeatherGridCache):
            return row.date_from.day==1 and row.date_to==month_end(row.date_from) and daily_integrity(row.payload,row.date_from,row.date_to,row.payload.get("supported_variables"))
        from .weather_local import HOURLY
        from zoneinfo import ZoneInfo
        if row.method!="municipal-point-v1": return False
        summary=row.payload.get("summary",{})
        days=summary.get("daily",[])
        sample={"daily":{"time":[d["date"] for d in days],**{key:[d.get(key) for d in days] for key in VARIABLES}},"units":VARIABLES}
        if not daily_integrity(sample,row.period,month_end(row.period)): return False
        zone=ZoneInfo(row.payload["timezone"])
        lower=datetime.combine(row.period-timedelta(days=1),datetime.min.time(),tzinfo=zone)
        upper=datetime.combine(month_end(row.period)+timedelta(days=2),datetime.min.time(),tzinfo=zone)
        from .weather_local import parse_hourly
        parse_hourly({"hourly":row.payload["hourly"],"hourly_units":row.payload["hourly"]["units"]},lower.astimezone(utc_timezone.utc),upper.astimezone(utc_timezone.utc))
        return all(v is not None for key in HOURLY for v in row.payload["hourly"][key])
    except (KeyError,TypeError,ValueError,OverflowError):
        return False


def integrity_ok(row):
    return not row.content_digest or row.content_digest==digest(row)


def verified_at(row):
    dates=[row.collected_at] if row.collected_at else []
    info=row.summary if isinstance(row,WeatherMonth) else row.payload
    value=parse_datetime(info.get("verified_at") or "")
    if value and timezone.is_aware(value): dates.append(value)
    return min(dates) if dates else None


def provisional(row):
    info=row.summary if isinstance(row,WeatherMonth) else row.payload
    return info.get("provider_metadata",{}).get("provisional") is True


def consolidate(row, cfg=None, *, allow_consolidate=True):
    """Metadata-only: never edits values, collection timestamps or provenance."""
    row=type(row).objects.get(pk=row.pk)
    valid=integrity_ok(row) and complete(row)
    first=row.date_from if isinstance(row,WeatherGridCache) else row.period
    boundary=review_date(first,row.source,cfg)
    frozen=row.consolidated_at
    verified=verified_at(row)
    if allow_consolidate and valid and not provisional(row) and verified and timezone.localtime(verified).date()>=boundary and timezone.localdate()>=boundary:
        frozen=frozen or timezone.now()
    values={"history_complete":valid,"stored_bytes":len(encoded(document(row))),"history_checked_at":timezone.now()}
    if valid or not row.content_digest: values["content_digest"]=digest(row)
    if valid: values["consolidated_at"]=frozen
    # A single conditional update avoids a read/write lock upgrade on SQLite,
    # and cannot stamp metadata over a concurrent MySQL publication.
    updated=type(row).objects.filter(pk=row.pk,revision=row.revision,content_digest=row.content_digest,collected_at=row.collected_at).update(**values)
    if updated:
        for key,value in values.items(): setattr(row,key,value)
    else: row.refresh_from_db()
    return row


def needs_collection(row, cfg=None, *, now=None):
    if row is None: return True
    now=now or timezone.now()
    valid=row.history_complete if row.content_digest else complete(row)
    if row.consolidated_at and valid: return False
    first=row.date_from if isinstance(row,WeatherGridCache) else row.period
    end=month_end(first)
    if valid and end<timezone.localdate(now):
        boundary=review_date(first,row.source,cfg)
        if provisional(row) and timezone.localdate(now)>=boundary and timezone.localtime(verified_at(row)).date()>=boundary:
            return not row.expires_at or row.expires_at<=now
        return timezone.localdate(now)>=boundary and timezone.localtime(verified_at(row)).date()<boundary
    available=min(end,timezone.localdate(now)-timedelta(days=5))
    if available==end and not valid: return True
    if isinstance(row,WeatherMonth):
        observed={d["date"] for d in row.summary.get("daily",[])}
    elif isinstance(row,WeatherGridCache):
        observed=set(row.payload.get("daily",{}).get("time",[]))
    else:
        observed={d["date"] for d in row.payload.get("summary",{}).get("daily",[])}
    if any((first+timedelta(days=i)).isoformat() not in observed for i in range(max(0,(available-first).days+1))): return True
    from .weather import VARIABLES
    if isinstance(row,WeatherGridCache):
        data=row.payload.get("daily",{})
        if any(v is None for key in row.payload.get("supported_variables",list(VARIABLES)) for v in data.get(key,[])): return True
    else:
        summary=row.summary if isinstance(row,WeatherMonth) else row.payload.get("summary",{})
        supported=summary.get("supported_variables",list(VARIABLES))
        if any(d.get(key) is None for d in summary.get("daily",[]) for key in supported): return True
        if isinstance(row,WeatherLocalCache):
            from .weather_local import HOURLY
            if any(v is None for key in HOURLY for v in row.payload.get("hourly",{}).get(key,[])): return True
    return not row.expires_at or row.expires_at<=now


def missing_intervals(daily, first, end, cfg, *, source, collected_at=None, expires_at=None, force=False, supported=None, observed_at=None):
    """Whole provider days are the minimum daily API request granularity."""
    from .weather import VARIABLES
    supported=supported or list(VARIABLES)
    values={day:{key:daily.get(key,[])[i] if i<len(daily.get(key,[])) else None for key in supported}
            for i,day in enumerate(daily.get("time",[]))}
    days=[first+timedelta(days=i) for i in range((end-first).days+1)]
    missing={day for day in days if day.isoformat() not in values or any(values[day.isoformat()].get(k) is None for k in supported)}
    boundary=review_date(first.replace(day=1),source,cfg)
    final_due=collected_at and timezone.localdate()>=boundary and timezone.localtime(collected_at).date()<boundary
    if observed_at is not None and timezone.localdate()>=boundary:
        missing.update(day for day in days if day.isoformat() in observed_at and timezone.localtime(observed_at[day.isoformat()]).date()<boundary)
        final_due=False
    if force or final_due:
        missing.update(days)
    elif expires_at and expires_at<=timezone.now() and end>=timezone.localdate().replace(day=1):
        tail=max(1,min(31,int(cfg.get("history_provisional_days") or 7)))
        missing.update(days[-tail:])
    intervals=[]
    for day in sorted(missing):
        if intervals and intervals[-1][1]+timedelta(days=1)==day: intervals[-1]=(intervals[-1][0],day)
        else: intervals.append((day,day))
    return intervals


def snapshot(row, reason, request=None):
    request=request or {}
    content={**document(row),"collected_at":row.collected_at.isoformat() if row.collected_at else None,
             "consolidated_at":row.consolidated_at.isoformat() if row.consolidated_at else None}
    WeatherHistoryRevision.objects.get_or_create(kind=KINDS[type(row)],object_id=row.pk,version=row.revision,
        defaults={"snapshot":content,"digest":digest(row),"stored_bytes":len(encoded(content)),"reason":reason[:240],
                  "request_id":request.get("id",""),"actor_id":request.get("actor")})


def persist(row, changes, cfg, *, refresh=None, expected_revision=None):
    """Publish atomically; a failed download never replaces the active history."""
    if refresh: validate_refresh(refresh,getattr(row,"uf",None))
    with transaction.atomic():
        row=type(row).objects.select_for_update().get(pk=row.pk)
        if expected_revision is not None and row.revision!=expected_revision:
            raise PublicDataError("Histórico alterado durante a coleta; reavalie a revisão.",code="history_conflict")
        if row.consolidated_at and not refresh:
            raise PublicDataError("Histórico consolidado exige revisão manual identificada.",code="history_locked")
        if any(key in changes and changes[key]!=getattr(row,key) for key in ("source","model","method")):
            raise PublicDataError("Fonte, modelo e método não podem substituir a identidade histórica.",code="history_conflict")
        reason=(refresh or {}).get("reason") or "Atualização incremental / revisão final da fonte"
        initial=row.collected_at is None
        if not initial: snapshot(row,reason,refresh)
        for key,value in changes.items(): setattr(row,key,value)
        if not initial: row.revision+=1
        row.consolidated_at=None
        row.content_digest=""
        row.history_complete=False
        row.save()
        row=consolidate(row,cfg)
        snapshot(row,reason,refresh)
        if refresh and not isinstance(row,WeatherGridCache):
            from aplicativos.auditoria.services import AuditService
            from django.contrib.auth import get_user_model
            AuditService.record(user=get_user_model().objects.filter(pk=refresh.get("actor")).first(),category="integration",action="weather.history_published",
                description="Revisão climática publicada; versão anterior preservada.",metadata={"kind":KINDS[type(row)],"record":row.pk,"revision":row.revision,"request_id":refresh.get("id","")})
        return row


def validate_refresh(refresh, uf=None):
    from django.contrib.auth import get_user_model
    from .geo_operations import allowed_weather_ufs
    actor=get_user_model().objects.filter(pk=refresh.get("actor"),is_active=True).first()
    if not actor or not all(actor.has_perm("configuracoes."+p) for p in ("manage_integrations","run_integrations","view_map")) or (uf and uf not in allowed_weather_ufs(actor)):
        raise PublicDataError("Autorização da revisão histórica não é mais válida.",code="unauthorized")


def saved_requests(points, batch=8):
    count=math.ceil(points/max(1,batch))
    if count:
        state,_=IntegrationSyncState.objects.get_or_create(key="weather-history-saved",defaults={"records_count":0})
        IntegrationSyncState.objects.filter(pk=state.pk).update(records_count=F("records_count")+count)


def request_revision(ufs, period, user, reason):
    from django.core.exceptions import PermissionDenied
    from .geo_operations import allowed_weather_ufs
    if not user or not user.is_active or not all(user.has_perm("configuracoes."+p) for p in ("manage_integrations","run_integrations","view_map")) or not set(ufs)<=set(allowed_weather_ufs(user)):
        raise PermissionDenied
    from .geo_configuration import get_geo_integration, weather_configuration, weather_source, weather_model
    from .weather import month_bounds, METHOD, configured_weather
    cfg=configured_weather(get_geo_integration("open_meteo"))
    first,_=month_bounds(period)
    if first>=timezone.localdate().replace(day=1) or len(reason.strip())<12 or len(reason)>240:
        raise ValueError("Revisão histórica exige mês encerrado e justificativa de 12 a 240 caracteres.")
    if re.search(r"(?i)(api[_-]?key|token|password|senha|authorization|secret)\s*[:=]",reason):
        raise ValueError("Não inclua credenciais na justificativa.")
    count=0
    with transaction.atomic():
        for uf in sorted(set(ufs)):
            row=WeatherMonth.objects.filter(uf=uf,period=first,source=weather_source(cfg),model=weather_model(cfg),method=METHOD).first()
            if not row: continue
            job,_=WeatherCollectionJob.objects.get_or_create(uf=uf,period=first)
            job=WeatherCollectionJob.objects.select_for_update().get(pk=job.pk)
            if job.status=="running" or job.refresh_request:
                raise PublicDataError("Já existe operação ou revisão nesse recorte.",code="busy")
            job.refresh_request={"id":str(uuid4()),"actor":user.pk,"reason":reason.strip(),"source":row.source,"model":row.model,"method":row.method,"object_id":row.pk,"expected_revision":row.revision}
            job.status,job.attempts,job.next_attempt_at,job.requested_by="pending",0,timezone.now(),user
            # Original checkpoints remain available under variants; the revision
            # has a new fingerprint and cannot consume an earlier generation.
            job.save();count+=1
        for row in WeatherLocalCache.objects.select_for_update().filter(uf__in=ufs,period=first,source=weather_source(cfg),model=weather_model(cfg),collected_at__isnull=False):
            if row.status=="running" or row.refresh_request:
                raise PublicDataError("Já existe operação ou revisão local nesse recorte.",code="busy")
            row.refresh_request={"id":str(uuid4()),"actor":user.pk,"reason":reason.strip(),"source":row.source,"model":row.model,"method":row.method,"expected_revision":row.revision}
            row.status,row.attempts,row.next_attempt_at,row.requested_by="pending",0,timezone.now(),user
            row.save(update_fields=("refresh_request","status","attempts","next_attempt_at","requested_by","updated_at"));count+=1
    return count


def maintain_history(*, ufs=None, period=None, user=None, enqueue=False, limit=500):
    """No network. Queue only explicit missing/stored slices, never reset progress."""
    from .geo_configuration import get_geo_integration, weather_configuration, weather_sources, weather_model
    from .weather import request_collection
    cfg=weather_configuration(get_geo_integration("open_meteo"))
    source_filter={"source__in":weather_sources(cfg),"model":weather_model(cfg)}
    months=WeatherMonth.objects.filter(**source_filter)
    locals_=WeatherLocalCache.objects.filter(**source_filter)
    if ufs is not None: months=months.filter(uf__in=ufs);locals_=locals_.filter(uf__in=ufs)
    if period: months=months.filter(period=period);locals_=locals_.filter(period=period)
    result={"inspected":0,"consolidated":0,"missing":0,"queued":0}
    remaining=max(1,min(int(limit),5000))
    per_kind=max(1,remaining//3)
    for queryset in (months,locals_):
        # Inspect unconsolidated rows first so maintenance remains bounded.
        for row in queryset.order_by("history_checked_at","pk")[:min(per_kind,remaining)]:
            row=consolidate(row,cfg)
            result["inspected"]+=1;remaining-=1
            result["consolidated"]+=bool(row.consolidated_at)
            due=needs_collection(row,cfg)
            result["missing"]+=due
            if enqueue and due and not row.consolidated_at:
                if isinstance(row,WeatherMonth):
                    result["queued"]+=len(request_collection([row.uf],str(row.period)[:7],user))
                elif not row.refresh_request and row.status not in {"pending","running","paused","cancelled"} and row.next_attempt_at<=timezone.now():
                    WeatherLocalCache.objects.filter(pk=row.pk,status=row.status).update(status="pending",attempts=0,next_attempt_at=timezone.now())
                    result["queued"]+=1
        if remaining<=0: break
    if enqueue and period and ufs:
        present=set(months.values_list("uf",flat=True))
        result["queued"]+=len(request_collection(sorted(set(ufs)-present),str(period)[:7],user))
    # Grid metadata needs no network and cannot change any UF evidence.
    for row in WeatherGridCache.objects.order_by("history_checked_at","pk")[:max(0,min(per_kind,remaining))]:
        selected=row.source in weather_sources(cfg) and row.model==weather_model(cfg)
        result["consolidated"]+=bool(consolidate(row,cfg if selected else {},allow_consolidate=selected).consolidated_at)
        result["inspected"]+=1
    return result


def history_metrics(ufs, *, include_grid=False):
    """Logical UTF-8 payload footprint, scoped before aggregation; not disk size."""
    months=WeatherMonth.objects.filter(uf__in=ufs)
    locals_=WeatherLocalCache.objects.filter(uf__in=ufs,collected_at__isnull=False)
    versions=WeatherHistoryRevision.objects.filter(kind="month",object_id__in=months.values("pk"))|WeatherHistoryRevision.objects.filter(kind="local",object_id__in=locals_.values("pk"))
    if include_grid: versions=versions|WeatherHistoryRevision.objects.filter(kind="grid")
    size=(months.aggregate(n=Sum("stored_bytes"))["n"] or 0)+(locals_.aggregate(n=Sum("stored_bytes"))["n"] or 0)+(versions.aggregate(n=Sum("stored_bytes"))["n"] or 0)
    if include_grid: size+=WeatherGridCache.objects.aggregate(n=Sum("stored_bytes"))["n"] or 0
    return {"months":months.values("period").distinct().count(),"complete_ufs":months.filter(history_complete=True).values("uf").distinct().count(),
        "consolidated":months.filter(consolidated_at__isnull=False).count(),"localities":locals_.values("location_key").distinct().count(),
        "bytes":size,"mib":round(size/1024/1024,2),"grid_included":include_grid,"pending":WeatherCollectionJob.objects.filter(uf__in=ufs,status__in=("pending","running","error")).count()+WeatherLocalCache.objects.filter(uf__in=ufs,status__in=("pending","running","error")).count(),
        "versions":versions.count(),"saved_requests":None,"recent_versions":list(versions.order_by("-created_at")[:10]),"rows":list(months.defer("samples","summary").order_by("-period","uf")[:60])}
