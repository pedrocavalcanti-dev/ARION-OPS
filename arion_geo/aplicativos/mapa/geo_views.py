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
from .services.analytics import STATE_NAMES, MapAnalyticsService, _client_filter
from .services.geo_intelligence import intelligence_payload
from .services.presentation import presentation_payload

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
    params = {name:values.get(name,"") for name in ("region","uf","provider","category","reason")}
    params["client"] = session["client"]
    params["project"] = canonical_project_name(values.get("project",session.get("project","")))
    params["period"] = month_bounds(values.get("period",session.get("period")))[0].strftime("%Y-%m")
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
        jobs = request_collection(payload["eligible_ufs"],request.POST.get("period"),request.user)
    except ValueError as error:
        return geo_json({"status":"invalid","message":str(error),"error":str(error)},status=400)
    AuditService.record(request=request,category="integration",action="weather.collection_requested",description="Coleta climática do recorte autorizado solicitada.",metadata={"jobs":len(jobs),"period":payload["period"]["month"]})
    states = set(WeatherCollectionJob.objects.filter(pk__in=jobs).values_list("status",flat=True))
    status = "running" if "running" in states else "queued" if "pending" in states else "unavailable" if "error" in states else "completed"
    messages={"running":"Coleta em andamento no worker.","queued":"Coleta agendada no worker.","completed":"Coleta concluída; cache válido disponível.","unavailable":"Dados indisponíveis após falha de coleta. Consulte a saúde da integração."}
    return geo_json({"status":status,"message":messages[status],"jobs":len(jobs)},status=202 if status in {"queued","running"} else 200)


class PresentationForm(forms.Form):
    client = forms.ChoiceField(label="Cliente")
    project = forms.ChoiceField(label="Projeto (opcional)",required=False)
    period = forms.CharField(label="Mês",widget=forms.DateInput(attrs={"type":"month"}))

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        links = LinkQueryService.base(user)
        self.fields["client"].choices = [("","Selecione um cliente"),*((value,value) for value in links.exclude(contracting_client="").exclude(contracting_client="TELMEX").order_by("contracting_client").values_list("contracting_client",flat=True).distinct())]
        self.fields["project"].choices = [("","Todos os projetos"),*((p,p) for p in sorted(project_alias_groups(links.exclude(business_project="").values_list("business_project",flat=True).distinct())))]

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
    return render(request,"mapa/presentation_start.html",{"form":form})


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
