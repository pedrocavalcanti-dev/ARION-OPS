"""Native public-data connectors using existing configuration, health and audit."""
import time
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.permissions import access_required
from .clients.public_data import ARCHIVE_ENDPOINTS, PublicDataError
from .forms import GeographyConfigurationForm, WeatherConfigurationForm, WeatherCollectionForm
from .models import ExternalAPIHealthCheck, WeatherCollectionJob, WeatherLocalCache
from .services.geography import fetch_catalog
from .services.secrets import get_secret_protector
from .services.sync_lock import Lease
from .services.weather import test_weather, month_bounds, request_collection
from .services.geo_configuration import NAMES, get_geo_integration, WEATHER_DEFAULTS
from .services.geo_operations import geo_operational_context



def _provider(provider):
    if provider not in NAMES:
        from django.http import Http404
        raise Http404
    return provider


@login_required
@access_required("view_integrations")
def detail(request, provider):
    _provider(provider)
    integration = get_geo_integration(provider)
    form_class = GeographyConfigurationForm if provider == "ibge" else WeatherConfigurationForm
    initial = {**integration.config,"enabled":integration.enabled,"credential_configured":bool(integration.secret_ciphertext)}
    if provider == "open_meteo":
        initial = {**WEATHER_DEFAULTS, **initial}
        initial["mode"] = "public" if initial.get("mode")=="evaluation" else initial.get("mode","public")
        initial["endpoint"] = integration.config.get("endpoint", ARCHIVE_ENDPOINTS.get(initial["mode"], ""))
        initial["credential_endpoint"] = integration.config.get("credential_endpoint", "https://customer-archive-api.open-meteo.com/v1/archive" if integration.config.get("mode")=="commercial" else "")
    form = form_class(initial=initial,**({"user":request.user} if provider=="open_meteo" else {})) if request.user.has_perm("configuracoes.manage_integrations") else None
    return _render(request,provider,integration,form)


def _render(request, provider, integration, form, status=200, collection_form=None, preview=None):
    if form is None and provider=="open_meteo" and request.user.has_perm("configuracoes.manage_integrations"):
        from .services.geo_configuration import weather_configuration
        try:
            initial=weather_configuration(integration)
        except PublicDataError:
            initial={**WEATHER_DEFAULTS,**integration.config}
        form=WeatherConfigurationForm(initial={**initial,"enabled":integration.enabled,"credential_configured":bool(integration.secret_ciphertext)},user=request.user)
    context = geo_operational_context()
    history = [{"at":check.created_at, "label":"Teste / operação", "status":check.get_status_display(), "message":check.message} for check in integration.checks.all()[:10]]
    jobs = list(WeatherCollectionJob.objects.order_by("-updated_at")[:10]) if provider=="open_meteo" else []
    history.extend({"at":job.updated_at, "label":f"Coleta {job.uf} · {job.period:%m/%Y}", "status":job.get_status_display(), "message":job.last_error or "Processamento pelo worker."} for job in jobs)
    if provider=="open_meteo":
        from .services.geo_operations import allowed_weather_ufs
        for row in WeatherLocalCache.objects.filter(uf__in=allowed_weather_ufs(request.user)).order_by("-updated_at")[:10]:
            history.append({"at":row.updated_at,"label":f"Localidade {row.city}/{row.uf} · {row.period:%m/%Y}","status":{"pending":"Pendente","running":"Coletando","success":"Concluído","error":"Falhou"}.get(row.status,row.status),"message":row.last_error or "Estimativa municipal IBGE · série diária e horária compartilhada por localidade."})
    context.update({
        "integration":integration, "provider":provider, "api_form":form,
        "can_manage_integrations":request.user.has_perm("configuracoes.manage_integrations"),
        "can_test_integrations":request.user.has_perm("configuracoes.run_integrations"),
        "can_collect_weather":request.user.has_perm("configuracoes.run_integrations") and request.user.has_perm("configuracoes.view_map"),
        "credential_configured":bool(integration.secret_ciphertext),
        "history":sorted(history,key=lambda row:row["at"],reverse=True)[:10], "jobs":jobs,
        "collection_form":collection_form or (WeatherCollectionForm(user=request.user,initial={"scope":"brazil","uf":"SP","period":month_bounds()[0].strftime("%Y-%m")}) if provider=="open_meteo" else None),
        "geo_preview":preview,
    })
    if provider=="open_meteo":
        from .services.weather_coverage import collection_plan
        from .services.geo_operations import allowed_weather_ufs
        context["geo_coverage"] = collection_plan(allowed_weather_ufs(request.user),month_bounds()[0].strftime("%Y-%m"))
    return render(request,"integracoes/geography_weather.html",context,status=status)



@login_required
@access_required("manage_integrations")
@require_POST
@sensitive_post_parameters("api_key")
def save(request,provider):
    _provider(provider)
    integration = get_geo_integration(provider)
    cls = GeographyConfigurationForm if provider=="ibge" else WeatherConfigurationForm
    form = cls(request.POST,initial={"credential_configured":bool(integration.secret_ciphertext),"credential_endpoint":integration.config.get("credential_endpoint","https://customer-archive-api.open-meteo.com/v1/archive" if integration.config.get("mode")=="commercial" else "")},**({"user":request.user} if provider=="open_meteo" else {}))
    if not form.is_valid():
        return _render(request,provider,integration,form,400)
    data = dict(form.cleaned_data)
    secret = data.pop("api_key","")
    if secret:
        try:
            integration.secret_ciphertext = get_secret_protector().protect(secret)
            data["credential_endpoint"] = data["endpoint"]
        except Exception:
            form.add_error("api_key","Não foi possível proteger a chave nesta identidade Windows.")
            return _render(request,provider,integration,form,400)
    previous_endpoint = integration.config.get("credential_endpoint", "https://customer-archive-api.open-meteo.com/v1/archive" if integration.config.get("mode")=="commercial" and integration.secret_ciphertext else "")
    if not secret and previous_endpoint:
        data["credential_endpoint"] = previous_endpoint
    integration.enabled = data.pop("enabled")
    integration.config, integration.updated_by = data,request.user
    integration.save()
    AuditService.record(request=request,category="integration",action="geo.configure",obj=integration,description="Configuração de integração geográfica atualizada.",metadata={"provider":provider,"enabled":integration.enabled})
    messages.success(request,"Configuração salva. Coletas sob demanda e agendamentos explícitos são processados pelo worker.")
    return redirect("configuracoes:integracoes:geo-detail",provider=provider)


@login_required
@access_required("run_integrations")
@require_POST
def test(request,provider):
    _provider(provider)
    integration = get_geo_integration(provider)
    if integration.last_test_at and integration.last_test_at > timezone.now()-timedelta(minutes=1):
        messages.warning(request,"Aguarde um minuto entre testes de conectividade.")
        return redirect("configuracoes:integracoes:geo-detail",provider=provider)
    # Shared lease also bounds concurrent weather diagnostics and worker calls.
    lease = Lease("geo-climate" if provider=="open_meteo" else "geo-ibge")
    if not lease.acquire():
        messages.warning(request,"Uma operação desta integração já está em andamento.")
        return redirect("configuracoes:integracoes:geo-detail",provider=provider)
    stop, thread = lease.keep_alive()
    start = time.monotonic()
    success = False
    try:
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
        if not request.user.has_perm("configuracoes.view_map"):
            raise PermissionDenied
        form = WeatherCollectionForm(request.POST,user=request.user)
        if not form.is_valid():
            messages.error(request,"Selecione uma UF permitida e um mês válido.")
        else:
            try:
                values=form.cleaned_data
                from .services.weather_coverage import collection_plan, control_jobs, plan_token, verify_plan
                operation = request.POST.get("operation","collect")
                if operation=="preview":
                    preview = collection_plan(values["ufs"],values["period"])
                    preview["token"] = plan_token(request.user,values["ufs"],values["period"])
                    return _render(request,provider,get_geo_integration(provider),None,collection_form=form,preview=preview)
                if operation in {"pause","resume","cancel"}:
                    count = control_jobs(values["ufs"],values["period"],operation)
                    messages.success(request,f"Fila atualizada: {count} tarefa(s). Cache e progresso preservados.")
                    AuditService.record(request=request,category="integration",action="weather.queue_control",description="Controle da fila climática.",metadata={"operation":operation,"jobs":count})
                    return redirect("configuracoes:integracoes:geo-detail",provider=provider)
                if len(values["ufs"])>1:
                    if not verify_plan(request.POST.get("preview_token",""),request.user,values["ufs"],values["period"]):
                        raise ValueError("Visualize a estimativa do recorte antes de iniciar a coleta em lote.") from None
                jobs=request_collection(values["ufs"],values["period"],request.user)
                messages.success(request,"Coleta solicitada ao worker." if jobs else "Cache válido já disponível para esta UF e mês.")
                AuditService.record(request=request,category="integration",action="weather.collection_requested",description="Coleta climática progressiva solicitada pela Central.",metadata={"ufs":values["ufs"],"period":values["period"],"jobs":len(jobs)})
            except (PublicDataError,ValueError) as error:
                messages.error(request,str(error))
    return redirect("configuracoes:integracoes:geo-detail",provider=provider)
