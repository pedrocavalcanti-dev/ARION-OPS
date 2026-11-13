import secrets
from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.permissions import access_required
from aplicativos.integracoes.services.analytics import LinkQueryService
from aplicativos.integracoes.services.external_apis import get_mapbox_public_token
from aplicativos.integracoes.services.project_names import canonical_project_name, project_alias_groups
from aplicativos.integracoes.services.weather import month_bounds, request_collection, collection_readiness
from aplicativos.integracoes.models import WeatherCollectionJob
from aplicativos.integracoes.services.incidents import IncidentRules
from .services.analytics import STATE_NAMES, MapAnalyticsService, _client_filter, _bounds
from .services.geo_intelligence import intelligence_payload
from .services.presentation import presentation_payload, presentation_ufs

SESSION_KEY = "geo_presentation"


def geo_json(data, *, status=200):
    response = JsonResponse(data,status=status)
    # These messages are controlled validation/configuration states, safe to expose.
    response._arion_error_response = True
    return response


def _fixed_params(request, key):
    session = request.session.get(SESSION_KEY,{})
    if not secrets.compare_digest(str(session.get("key","")),str(key)) or session.get("user_id") != request.user.pk:
        raise PermissionDenied("Apresentação indisponível nesta sessão.")
    if session.get("expires",0) < timezone.now().timestamp():
        request.session.pop(SESSION_KEY,None)
        raise PermissionDenied("Apresentação expirada.")
    values = request.GET.copy()
    if "client" in values and values["client"] != session.get("client"):
        raise PermissionDenied("O cliente da apresentação está fixado.")
    params = {name:values.get(name,"") for name in ("region","uf","provider","category","reason","status","priority")}
    params["client"] = session["client"]
    params["project"] = canonical_project_name(values.get("project",session.get("project","")))
    params["period"] = values.get("period",session.get("period"))
    params["date_from"],params["date_to"] = values.get("date_from",""),values.get("date_to","")
    _bounds(params)
    params["mode"] = values.get("mode","calls")
    params["cohort"] = "created"
    if params["mode"] not in MapAnalyticsService.MODES:
        raise ValueError("Modo de análise inválido.")
    # Recheck scope on every request, including revoked group scopes.
    if not _client_filter(LinkQueryService.base(request.user),params["client"]).exists():
        raise PermissionDenied("Cliente fora do escopo autorizado.")
    links = _client_filter(LinkQueryService.base(request.user),params["client"])
    projects = project_alias_groups(links.exclude(business_project="").values_list("business_project",flat=True).distinct())
    if params["project"] and params["project"] not in projects:
        raise PermissionDenied("Projeto fora do escopo autorizado do cliente.")
    provider = params["provider"]
    if provider and (not provider.isdigit() or not links.filter(operator_id=int(provider)).exists()):
        raise PermissionDenied("ISP fora do escopo autorizado do cliente.")
    allowed_ufs = presentation_ufs(request.user, params)
    if params["uf"] and params["uf"] not in allowed_ufs:
        raise PermissionDenied("UF fora do recorte autorizado do cliente.")
    if params["category"] == "financeiro" or (params["reason"] and IncidentRules.category(params["reason"]) in {"financeiro","sem_classificacao"}):
        raise PermissionDenied("Classificação indisponível na apresentação.")
    return params


@login_required
@access_required("view_map")
@require_GET
def intelligence(request):
    try:
        return geo_json(intelligence_payload(request.user,request.GET))
    except ValueError as error:
        return geo_json({"error":str(error)},status=400)


@login_required
@access_required("view_map")
@require_POST
def collect(request):
    if not request.user.has_perm("configuracoes.run_integrations"):
        return geo_json({"status":"unauthorized","message":"Sem autorização para solicitar coleta climática."},status=403)
    try:
        payload = intelligence_payload(request.user,request.POST)
        readiness = collection_readiness()
        if readiness["status"] != "ready":
            return geo_json(readiness,status=409)
        if not payload["eligible_ufs"]:
            return geo_json({"status":"unavailable","message":"Sem UFs vinculadas no recorte autorizado."},status=422)
        from aplicativos.integracoes.services.weather_coverage import collection_plan, plan_token, verify_plan
        period=payload["period"]["month"]
        if len(payload["eligible_ufs"])>1 and not verify_plan(request.POST.get("preview_token",""),request.user,payload["eligible_ufs"],period):
            plan=collection_plan(payload["eligible_ufs"],period)
            return geo_json({"status":"preview","message":f"{plan['estimated_requests']} requisições previstas antes de retries; {plan['remaining_today']} disponíveis hoje. Autorize o lote para enfileirar.","preview_token":plan_token(request.user,payload["eligible_ufs"],period),"estimated_requests":plan["estimated_requests"]})
        jobs = request_collection(payload["eligible_ufs"],request.POST.get("period"),request.user)
    except ValueError as error:
        return geo_json({"status":"invalid","message":str(error),"error":str(error)},status=400)
    AuditService.record(request=request,category="integration",action="weather.collection_requested",description="Coleta climática do recorte autorizado solicitada.",metadata={"jobs":len(jobs),"period":payload["period"]["month"]})
    states = set(WeatherCollectionJob.objects.filter(pk__in=jobs).values_list("status",flat=True))
    status = "running" if "running" in states else "queued" if "pending" in states else "unavailable" if "error" in states else "paused" if "paused" in states else "cancelled" if "cancelled" in states else "completed"
    messages={"running":"Coleta em andamento no worker.","queued":"Coleta agendada no worker.","completed":"Coleta concluída; cache válido disponível.","unavailable":"Dados indisponíveis após falha de coleta. Consulte a saúde da integração."}
    messages.update(paused="Fila pausada. Retome na Central de Integrações.",cancelled="Pendências canceladas; retome explicitamente na Central.")
    return geo_json({"status":status,"message":messages[status],"jobs":len(jobs)},status=202 if status in {"queued","running"} else 200)


@login_required
@access_required("view_map")
@require_http_methods(["GET","POST"])
def local_weather(request):
    from aplicativos.integracoes.models import WeatherLocalCache
    from aplicativos.integracoes.services.weather_local import normalize_city, locality_key, request_local, incident_window
    from aplicativos.integracoes.services.geo_configuration import get_geo_integration, weather_configuration, weather_sources
    from aplicativos.integracoes.services.geography import state_query
    from aplicativos.integracoes.services.analytics import TicketQueryService
    values = request.POST if request.method=="POST" else request.GET
    uf,city = values.get("uf",""),values.get("city","")
    if uf not in STATE_NAMES or not city:
        return geo_json({"error":"Selecione cidade e UF."},status=400)
    links = _client_filter(LinkQueryService.base(request.user),values.get("client","all")).filter(state_query([uf]))
    if values.get("project"):
        aliases = project_alias_groups(links.values_list("business_project",flat=True).distinct()).get(canonical_project_name(values["project"]),())
        links=links.filter(business_project__in=aliases)
    if values.get("provider"):
        if not values["provider"].isdigit(): return geo_json({"error":"ISP inválido."},status=400)
        links=links.filter(operator_id=int(values["provider"]))
    cities = [name for name in links.exclude(city="").values_list("city",flat=True).distinct() if normalize_city(name)==normalize_city(city)]
    if not cities:
        raise PermissionDenied("Localidade fora do recorte autorizado.")
    links=links.filter(city__in=cities)
    try:
        first,_ = month_bounds(values.get("period"))
        cfg = weather_configuration(get_geo_integration("open_meteo"))
        if request.method=="POST":
            if not request.user.has_perm("configuracoes.run_integrations"): raise PermissionDenied
            row=request_local(cities[0],uf,first.strftime("%Y-%m"),request.user)
            AuditService.record(request=request,category="integration",action="weather.local_requested",description="Coleta meteorológica municipal solicitada.",metadata={"uf":uf,"period":first.strftime("%Y-%m")})
        else:
            row=WeatherLocalCache.objects.filter(location_key=locality_key(city,uf),period=first,source__in=weather_sources(cfg)).order_by("collected_at").last()
        data={"city":city,"uf":uf,"status":row.status if row else "unavailable","precision":"Estimativa municipal IBGE; não representa o trajeto físico do link.","weather":row.payload.get("summary") if row and (row.status=="success" or row.collected_at) else None,
              "resolution":row.payload.get("resolution") if row else None,"timezone":row.payload.get("timezone") if row else None,
              "last_error":row.last_error if row else "","source":row.source if row else None,"ibge_code":row.ibge_code if row else None,
              "model":row.model if row else None,"method":row.method if row else None,"revision":row.revision if row else None,"consolidated":bool(row and row.consolidated_at)}
        if row and (row.status=="success" or row.collected_at) and values.get("ticket"):
            if not values["ticket"].isdigit(): raise ValueError("TicketID inválido.")
            ticket=(
                IncidentRules.valid_for_operator(TicketQueryService.base(request.user,links=links),client=values.get("client","all"))
                .filter(external_id=int(values["ticket"]),config_items__cmdb_link__in=links,config_items__is_present_in_source=True)
                .distinct()
                .first()
            )
            ticket_link=links.filter(ticket_relations__ticket=ticket,ticket_relations__is_present_in_source=True).first() if ticket else None
            ticket_client=IncidentRules.resolve_ticket_operator(ticket,ticket_link) if ticket else ""
            if not ticket or not IncidentRules.is_valid_ticket(ticket,ticket_client) or IncidentRules.category(ticket.closing_code_raw) in {None,"financeiro","sem_classificacao"}: raise PermissionDenied
            data["window"]=incident_window(row,ticket.source_created_at)
        return geo_json(data,status=202 if request.method=="POST" and row.status in {"pending","running"} else 200)
    except ValueError as error:
        return geo_json({"error":str(error)},status=400)


class PresentationForm(forms.Form):
    client = forms.ChoiceField(label="Cliente")
    project = forms.ChoiceField(label="Projeto (opcional)",required=False)
    period = forms.CharField(label="Mês",widget=forms.DateInput(attrs={"type":"month"}))

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        links = LinkQueryService.base(user)
        self.fields["client"].choices = [("","Selecione um cliente"),*((value,value) for value in links.exclude(contracting_client="").exclude(contracting_client="TELMEX").order_by("contracting_client").values_list("contracting_client",flat=True).distinct())]
        self.project_catalog = {client: sorted(project_alias_groups(_client_filter(links, client).exclude(business_project="").values_list("business_project",flat=True).distinct())) for client, _ in self.fields["client"].choices if client}
        selected = self.data.get("client", self.initial.get("client", ""))
        self.fields["project"].choices = [("","Todos os projetos"),*((p,p) for p in self.project_catalog.get(selected, []))]

    def clean_period(self):
        return month_bounds(self.cleaned_data["period"])[0].strftime("%Y-%m")


@login_required
@access_required("view_map")
@access_required("present_geo")
@require_http_methods(["GET","POST"])
@never_cache
def start(request):
    try:
        first,_ = month_bounds(request.GET.get("period"))
    except ValueError:
        first,_ = month_bounds()
    form = PresentationForm(request.POST or None,user=request.user,initial={
        "period":first.strftime("%Y-%m"),"client":request.GET.get("client",""),
        "project":canonical_project_name(request.GET.get("project","")),
    })
    if request.method=="POST" and form.is_valid():
        values = form.cleaned_data
        links = _client_filter(LinkQueryService.base(request.user),values["client"])
        groups = project_alias_groups(links.exclude(business_project="").values_list("business_project",flat=True).distinct())
        if values["project"] and values["project"] not in groups:
            form.add_error("project","Projeto indisponível para este cliente no seu escopo.")
        else:
            key = secrets.token_hex(16)
            request.session[SESSION_KEY] = {**values,"key":key,"user_id":request.user.pk,"expires":(timezone.now()+timedelta(hours=2)).timestamp()}
            AuditService.record(request=request,category="mapa",action="presentation.start",description="Apresentação de cliente iniciada.",metadata={"client":values["client"],"project":values["project"],"period":values["period"]})
            return redirect("mapa:presentation",key=key)
    return render(request,"mapa/presentation_start.html",{"form":form,"project_catalog":form.project_catalog})


@login_required
@access_required("view_map")
@access_required("present_geo")
@require_GET
@never_cache
def presentation(request,key):
    try:
        params = _fixed_params(request,key)
    except ValueError as error:
        return geo_json({"error":str(error)},status=400)
    return render(request,"mapa/presentation.html",{
        "default_period":params["period"],"current_period":timezone.localdate().strftime("%Y-%m"),
        "presentation":True,"presentation_client":params["client"],"presentation_project":params["project"],"presentation_period":params["period"],
        "mapbox_token":get_mapbox_public_token(),"state_flags":{code:static(f"imagens/bandeiras/estados/{code.lower()}.svg") for code in STATE_NAMES},
        "presentation_api":reverse("mapa:presentation-data",kwargs={"key":key}),
        "presentation_detail":reverse("mapa:presentation-detail",kwargs={"key":key}),
        "intelligence_url":reverse("mapa:presentation-intelligence",kwargs={"key":key}),
        "presentation_stop":reverse("mapa:presentation-stop",kwargs={"key":key}),
    })


@login_required
@access_required("view_map")
@access_required("present_geo")
@require_GET
@never_cache
def presentation_data(request,key):
    try:
        params = _fixed_params(request,key)
        data = presentation_payload(request.user,params)
        if request.GET.get("include_intelligence")=="1" and data["mode"]=="calls":
            data["geo_intelligence"] = intelligence_payload(request.user,params,presentation=True,summary={**data})
        return geo_json(data)
    except ValueError as error:
        return geo_json({"error":str(error)},status=400)


@login_required
@access_required("view_map")
@access_required("present_geo")
@require_GET
@never_cache
def presentation_intelligence(request,key):
    try:
        return geo_json(intelligence_payload(request.user,_fixed_params(request,key),presentation=True))
    except ValueError as error:
        return geo_json({"error":str(error)},status=400)


@login_required
@access_required("view_map")
@access_required("present_geo")
@require_GET
@never_cache
def presentation_detail(request,key):
    try:
        params = _fixed_params(request,key)
        payload = presentation_payload(request.user,params)
    except ValueError as error:
        return geo_json({"error":str(error)},status=400)
    uf = params["uf"]
    state = payload["states"].get(uf,{})
    return geo_json({"uf":uf,"name":STATE_NAMES.get(uf,uf),"mode":params["mode"],"state":state,"links":[],"link_total":state.get("total_links",0),"tickets":[],"ticket_total":state.get("ticket_count",0),"ticket_page":1,"ticket_pages":1,"ticket_page_size":25,"recurrences":[],"tab":"overview"})


@login_required
@access_required("view_map")
@require_POST
@never_cache
def stop(request,key):
    session = request.session.get(SESSION_KEY,{})
    if not secrets.compare_digest(str(session.get("key","")),str(key)) or session.get("user_id")!=request.user.pk:
        raise PermissionDenied
    request.session.pop(SESSION_KEY,None)
    AuditService.record(request=request,category="mapa",action="presentation.stop",description="Apresentação encerrada; retorno à visão interna autorizado.")
    return redirect("mapa:index")
