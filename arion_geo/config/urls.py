"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from aplicativos.autenticacao.views import home
from aplicativos.painel.views import cockpit, dashboard
from .views import api_version, health, pwa_manifest, pwa_service_worker

urlpatterns = [
    path('health/', health, name='health'),
    path('api/version/', api_version, name='api-version'),
    path('manifest.webmanifest', pwa_manifest, name='pwa-manifest'),
    path('service-worker.js', pwa_service_worker, name='pwa-service-worker'),
    path('favicon.ico', RedirectView.as_view(url='/static/favicon/favicon.ico', permanent=True), name='favicon'),
    path('admin/', admin.site.urls),
    path('', home, name='home'),
    path('', include('aplicativos.autenticacao.urls')),
    path('mapa/', include('aplicativos.mapa.urls')),
    path('rankings/', include('aplicativos.rankings.urls')),
    path('base/', include('aplicativos.base_links.urls')),
    path('chamados/', include('aplicativos.chamados.urls')),
    path('relatorios/', include('aplicativos.relatorios.urls')),
    path('qualidade/', include('aplicativos.qualidade.urls')),
    path('dashboard/', dashboard, name='dashboard'),
    path('cockpit/', cockpit, name='cockpit'),
    path('api/analytics/', include('aplicativos.painel.analytics_urls')),
    path('api/preferences/', include(('aplicativos.configuracoes.urls', 'preferences_api'), namespace='preferences_api')),
    path('configuracoes/', include('aplicativos.configuracoes.web_urls')),
    path('ari/', include('aplicativos.ari.urls')),
    path('painel/', include('aplicativos.painel.urls')),
]

handler400 = "config.errors.handler400"
handler403 = "config.errors.handler403"
handler404 = "config.errors.handler404"
handler500 = "config.errors.handler500"
