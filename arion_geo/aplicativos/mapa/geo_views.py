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
from aplicativos.integracoes.services.weather import month_bounds, request_collection
from .services.analytics import STATE_NAMES, _client_filter
from .services.geo_intelligence import intelligence_payload
from .services.presentation import presentation_payload

SESSION_KEY = "geo_presentation"


def _fixed_params(request, key):
    session = request.session.get(SESSION_KEY,{})
    if not secrets.compare_digest(str(session.get("key","")),str(key)) or session.get("user_id") != request.user.pk:
        raise PermissionDenied("Apresentação indisponível nesta sessão.")
    if session.get("expires",0) < timezone.now().timestamp():
        request.session.pop(SESSION_KEY,None)
        raise PermissionDenied("Apresentação expirada.")
    values = request.GET.copy()
    # Only regional/UF navigation and layer selection can vary in a presentation.
    if any(name in values and values[name] != session.get(name,"") for name in ("client","project","period")):
        raise PermissionDenied("O escopo da apresentação está fixado.")
    params = {name:values.get(name,"") for name in ("region","uf")}
    params.update({name:session.get(name,"") for name in ("client","project","period")})
    params["mode"] = values.get("mode","calls") if values.get("mode","calls") in {"base","calls"} else "calls"
    # Recheck scope on every request, including revoked group scopes.
    if not _client_filter(LinkQueryService.base(request.user),params["client"]).exists():
        raise PermissionDenied("Cliente fora do escopo autorizado.")
    return params


@login_required
@access_required("view_map")
@require_GET
def intelligence(request):
    try:
        return JsonResponse(intelligence_payload(request.user,request.GET))
    except ValueError as error:
        return JsonResponse({"error":str(error)},status=400)


@login_required
@access_required("view_map")
@access_required("run_integrations")
@require_POST
def collect(request):
    try:
        payload = intelligence_payload(request.user,request.POST)
        jobs = request_collection(payload["eligible_ufs"],request.POST.get("period"),request.user)
    except ValueError as error:
        return JsonResponse({"error":str(error)},status=400)
    AuditService.record(request=request,category="integration",action="weather.collection_requested",description="Coleta climática do recorte autorizado solicitada.",metadata={"jobs":len(jobs),"period":payload["period"]["month"]})
    return JsonResponse({"status":"queued","jobs":len(jobs)},status=202)


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
    params = _fixed_params(request,key)
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
        return JsonResponse(data)
    except ValueError as error:
        return JsonResponse({"error":str(error)},status=400)


@login_required
@access_required("view_map")
@access_required("present_geo")
@require_GET
@never_cache
def presentation_intelligence(request,key):
    try:
        return JsonResponse(intelligence_payload(request.user,_fixed_params(request,key),presentation=True))
    except ValueError as error:
        return JsonResponse({"error":str(error)},status=400)


@login_required
@access_required("view_map")
@access_required("present_geo")
@require_GET
@never_cache
def presentation_detail(request,key):
    params = _fixed_params(request,key)
    try:
        payload = presentation_payload(request.user,params)
    except ValueError as error:
        return JsonResponse({"error":str(error)},status=400)
    uf = params["uf"]
    state = payload["states"].get(uf,{})
    return JsonResponse({"uf":uf,"name":STATE_NAMES.get(uf,uf),"mode":params["mode"],"state":state,"links":[],"link_total":state.get("total_links",0),"tickets":[],"ticket_total":state.get("ticket_count",0),"ticket_page":1,"ticket_pages":1,"ticket_page_size":25,"recurrences":[],"tab":"overview"})


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
