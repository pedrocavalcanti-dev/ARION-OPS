"""Small public-data jobs added to the existing integration worker."""
from datetime import timedelta
from django.utils import timezone

from ..clients.public_data import PublicDataClient, PublicDataError
from ..models import ExternalAPIHealthCheck, ExternalAPIIntegration, IntegrationSyncState
from .geography import sync_catalog
from .sync_lock import Lease
from .weather import licensed_configuration, month_bounds, request_collection, worker_tick
from .geo_configuration import bootstrap_geo_integrations, weather_configuration


def enqueue_ibge():
    IntegrationSyncState.objects.update_or_create(key="ibge", defaults={"change_cursor":timezone.now()})


def scheduler_tick():
    bootstrap_geo_integrations()
    now = timezone.now()
    integration = ExternalAPIIntegration.objects.filter(provider="ibge",enabled=True).first()
    if integration:
        state,_ = IntegrationSyncState.objects.get_or_create(key="ibge")
        pending = state.change_cursor and (not state.last_success_at or state.change_cursor > state.last_success_at)
        due = integration.config.get("automatic", "interval_hours" in integration.config) and (not state.last_success_at or state.last_success_at < now-timedelta(hours=int(integration.config.get("interval_hours",720))))
        retry_ready = not state.last_error_at or state.last_error_at < now-timedelta(minutes=min(60,2**min(state.failure_count,6)))
        lease = Lease("geo-ibge")
        if (pending or due) and retry_ready and lease.acquire():
            stop,thread = lease.keep_alive()
            state.last_started_at = now
            state.save()
            try:
                counts = sync_catalog(PublicDataClient(integration.config.get("timeout",15)))
                state.last_success_at, state.last_error_at, state.last_error, state.failure_count = timezone.now(),None,"",0
                state.records_count = counts["states"]
                integration.last_success_at, integration.health, integration.last_error = state.last_success_at,"available",""
                message = "Catálogo sincronizado: 5 regiões e 27 UFs."
                success = True
            except Exception as error:
                state.last_error = str(error)[:240] if isinstance(error,PublicDataError) else "Falha IBGE; catálogo anterior preservado."
                state.last_error_at, state.failure_count = timezone.now(),min(state.failure_count+1,10)
                integration.health, integration.last_error = "error",state.last_error
                message,success = state.last_error,False
            finally:
                state.save()
                integration.save(update_fields=("last_success_at","health","last_error","updated_at"))
                ExternalAPIHealthCheck.objects.create(integration=integration,status="success" if success else "error",message=message)
                stop.set()
                thread.join(timeout=2)
                lease.release()
    weather = ExternalAPIIntegration.objects.filter(provider="open_meteo",enabled=True).first()
    if weather:
        state,_=IntegrationSyncState.objects.get_or_create(key="weather-history")
        if not state.last_started_at or state.last_started_at<now-timedelta(hours=1):
            from .weather_history import maintain_history
            try:
                cfg=weather_configuration(weather)
                # No new territory is inferred. Automatic jobs remain bounded
                # by explicitly scheduled UFs and already stored periods.
                maintain_history(ufs=cfg.get("scheduled_ufs",[]) if cfg.get("automatic") else None,
                    enqueue=bool(cfg.get("automatic") and cfg.get("scheduled_ufs")),limit=500)
                state.last_error=""
            except PublicDataError as error:
                state.last_error=str(error)[:240]
            state.last_started_at=now;state.save()
    if weather and weather.config.get("automatic"):
        state,_ = IntegrationSyncState.objects.get_or_create(key="weather-schedule")
        if not state.last_started_at or state.last_started_at < now-timedelta(hours=int(weather.config.get("interval_hours",168))):
            try:
                licensed_configuration(weather,automatic=True)
                month,_ = month_bounds()
                ufs = weather_configuration(weather).get("scheduled_ufs", [])
                if not ufs:
                    raise PublicDataError("Agendamento sem UFs selecionadas; configure os recortes na Central.")
                request_collection(ufs,month.strftime("%Y-%m"),None)
                state.last_started_at,state.last_error = now,""
            except PublicDataError as error:
                state.last_started_at,state.last_error = now,str(error)[:240]
            state.save()
    worker_tick()
