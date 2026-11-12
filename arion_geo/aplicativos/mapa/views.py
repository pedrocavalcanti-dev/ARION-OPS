from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.templatetags.static import static

from aplicativos.configuracoes.permissions import access_required
from aplicativos.integracoes.services.analytics import LinkQueryService
from aplicativos.integracoes.services.external_apis import get_mapbox_public_token

from aplicativos.painel.views import STATE_NAMES


@login_required
@access_required("view_map")
def index(request):
    from aplicativos.integracoes.services.weather import month_bounds
    from django.utils import timezone
    links = LinkQueryService.base(request.user)
    flags = {code: static(f"imagens/bandeiras/estados/{code.lower()}.svg") for code in STATE_NAMES}
    return render(request, "mapa/index.html", {
        "mapbox_token": get_mapbox_public_token(),
        "state_flags": flags,
        "link_count": links.count(),
        "default_period":month_bounds()[0].strftime("%Y-%m"),
        "current_period":timezone.localdate().strftime("%Y-%m"),
    })
