"""Native public-data connectors using existing configuration, health and audit."""
import time
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.permissions import access_required
from .clients.public_data import PublicDataError
from .forms import GeographyConfigurationForm, WeatherConfigurationForm
from .models import ExternalAPIHealthCheck, ExternalAPIIntegration, IntegrationSyncState, WeatherCollectionJob, WeatherMonth
from .services.geography import catalog, fetch_catalog
from .services.secrets import get_secret_protector
from .services.sync_lock import Lease
from .services.weather import test_weather

NAMES = {"ibge":"IBGE — Regiões e Estados", "open_meteo":"Open-Meteo — Clima e Histórico"}


def _provider(provider):
    if provider not in NAMES:
        from django.http import Http404
        raise Http404
    return provider


@login_required
@access_required("view_integrations")
def detail(request, provider):
    _provider(provider)
    integration = ExternalAPIIntegration.objects.filter(provider=provider).first() or ExternalAPIIntegration(provider=provider,name=NAMES[provider])
    form_class = GeographyConfigurationForm if provider == "ibge" else WeatherConfigurationForm
    initial = {**integration.config,"enabled":integration.enabled,"credential_configured":bool(integration.secret_ciphertext)}
    if provider == "open_meteo":
        initial.setdefault("mode","evaluation")
        initial.setdefault("endpoint","https://archive-api.open-meteo.com/v1/archive")
    form = form_class(initial=initial) if request.user.has_perm("configuracoes.manage_integrations") else None
    return _render(request,provider,integration,form)


def _render(request, provider, integration, form, status=200):
    geo = catalog()
    usage = IntegrationSyncState.objects.filter(key__startswith="meteo-"+timezone.localdate().strftime("%Y-%m")).aggregate(total=Sum("records_count"))["total"] or 0
    return render(request,"integracoes/geography_weather.html",{
        "integration":integration, "provider":provider, "api_form":form,
        "can_manage_integrations":request.user.has_perm("configuracoes.manage_integrations"),
        "can_test_integrations":request.user.has_perm("configuracoes.run_integrations"),
        "credential_configured":bool(integration.secret_ciphertext),
        "geo_catalog":geo, "regions_count":len(geo["regions"]), "states_count":len(geo["states"]),
        "weather_count":WeatherMonth.objects.count(), "request_count":usage,
        "last_weather":WeatherMonth.objects.order_by("-collected_at").first(),
        "jobs":WeatherCollectionJob.objects.exclude(status="success").order_by("-updated_at")[:10] if provider=="open_meteo" else (),
        "checks":integration.checks.all()[:10] if integration.pk else (),
    },status=status)


@login_required
@access_required("manage_integrations")
@require_POST
@sensitive_post_parameters("api_key")
def save(request,provider):
    _provider(provider)
    integration = ExternalAPIIntegration.objects.filter(provider=provider).first() or ExternalAPIIntegration(provider=provider,name=NAMES[provider])
    cls = GeographyConfigurationForm if provider=="ibge" else WeatherConfigurationForm
    form = cls(request.POST,initial={"credential_configured":bool(integration.secret_ciphertext)})
    if not form.is_valid():
        return _render(request,provider,integration,form,400)
    data = dict(form.cleaned_data)
    secret = data.pop("api_key","")
    if secret:
        try:
            integration.secret_ciphertext = get_secret_protector().protect(secret)
        except Exception:
            form.add_error("api_key","Não foi possível proteger a chave nesta identidade Windows.")
            return _render(request,provider,integration,form,400)
    integration.enabled = data.pop("enabled")
    integration.config, integration.updated_by = data,request.user
    integration.save()
    AuditService.record(request=request,category="integration",action="geo.configure",obj=integration,description="Configuração de integração geográfica atualizada.",metadata={"provider":provider,"enabled":integration.enabled})
    messages.success(request,"Configuração salva. A coleta depende de teste e execução do worker.")
    return redirect("configuracoes:integracoes:geo-detail",provider=provider)


@login_required
@access_required("run_integrations")
@require_POST
def test(request,provider):
    _provider(provider)
    integration,_ = ExternalAPIIntegration.objects.get_or_create(provider=provider,defaults={"name":NAMES[provider]})
    # Shared lease also bounds concurrent weather diagnostics and worker calls.
    lease = Lease("geo-climate" if provider=="open_meteo" else "geo-ibge")
    if not lease.acquire():
        messages.warning(request,"Uma operação desta integração já está em andamento.")
        return redirect("configuracoes:integracoes:geo-detail",provider=provider)
    stop, thread = lease.keep_alive()
    start = time.monotonic()
    success = False
    try:
        if integration.last_test_at and integration.last_test_at > timezone.now()-timedelta(minutes=1):
            raise PublicDataError("Aguarde um minuto entre testes de conectividade.")
        if provider=="ibge":
            regions,states = fetch_catalog()
            message = f"Conectividade confirmada: {len(regions)} regiões e {len(states)} UFs."
        else:
            days = test_weather(integration)
            message = f"Conectividade e formato validados: {days} dias em uma célula de diagnóstico; não representa a UF."
        success = True
    except PublicDataError as error:
        message = str(error)[:240]
    except Exception:
        message = "Falha de validação da integração; detalhes externos omitidos."
    finally:
        stop.set()
        thread.join(timeout=2)
        lease.release()
    now = timezone.now()
    integration.last_test_at, integration.last_latency_ms = now,int((time.monotonic()-start)*1000)
    integration.health = "available" if success else "error"
    integration.last_error = "" if success else message
    if success:
        integration.last_success_at = now
    integration.save()
    ExternalAPIHealthCheck.objects.create(integration=integration,status="success" if success else "error",message=message,latency_ms=integration.last_latency_ms,checked_by=request.user)
    AuditService.record(request=request,category="integration",action="geo.test",obj=integration,result="SUCCESS" if success else "ERROR",description=message)
    (messages.success if success else messages.error)(request,message)
    return redirect("configuracoes:integracoes:geo-detail",provider=provider)


@login_required
@access_required("run_integrations")
@require_POST
def sync(request,provider):
    _provider(provider)
    from .services.geo_scheduler import enqueue_ibge
    if provider=="ibge":
        enqueue_ibge()
        messages.success(request,"Sincronização IBGE solicitada ao worker; o catálogo local continua disponível.")
        AuditService.record(request=request,category="integration",action="ibge.sync_requested",description="Atualização do catálogo solicitada.")
    else:
        raise PermissionDenied("Solicite clima pelo recorte autorizado do Mapa.")
    return redirect("configuracoes:integracoes:geo-detail",provider=provider)
