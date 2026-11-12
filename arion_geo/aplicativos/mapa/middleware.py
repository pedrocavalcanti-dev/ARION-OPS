"""While presenting, prevent internal data responses in the same session."""
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.urls import resolve, Resolver404


class PresentationIsolationMiddleware:
    def __init__(self,get_response):
        self.get_response = get_response

    def __call__(self,request):
        presentation = request.session.get("geo_presentation")
        if presentation and getattr(request.user,"is_authenticated",False):
            if presentation.get("expires",0) < timezone.now().timestamp():
                request.session.pop("geo_presentation",None)
            elif not request.path.startswith("/static/"):
                try:
                    match = resolve(request.path_info)
                    allowed = match.app_name=="mapa" and match.url_name in {"presentation","presentation-data","presentation-detail","presentation-intelligence","presentation-stop"}
                    allowed = allowed or (match.app_name=="autenticacao" and match.url_name in {"logout","required-password","password-reset"})
                except Resolver404:
                    allowed = False
                if not allowed:
                    if request.method=="GET" and "text/html" in request.headers.get("Accept",""):
                        return redirect("mapa:presentation",key=presentation["key"])
                    return JsonResponse({"error":"Encerre a apresentação para acessar a visão interna."},status=403)
        return self.get_response(request)
