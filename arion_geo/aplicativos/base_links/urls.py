from django.urls import path

from . import identity_views, views

app_name = "base_links"

urlpatterns = [
    path("", views.index, name="index"),
    path("provedores/", views.providers, name="providers"),
    path("nao-identificados/", identity_views.unidentified_index, name="unidentified"),
    path("nao-identificados/exportar/", identity_views.unidentified_export_csv, name="unidentified-export"),
    path("nao-identificados/api/link/<int:pk>/", identity_views.unidentified_link_detail, name="unidentified-link-detail"),
    path("nao-identificados/api/link/<int:pk>/resolver/", identity_views.unidentified_link_resolve, name="unidentified-link-resolve"),
    path("nao-identificados/api/ticket/<int:pk>/", identity_views.unidentified_ticket_detail, name="unidentified-ticket-detail"),
    path("exportar/", views.export_csv, name="export"),
    path("importar/", views.import_cmdb, name="import"),
    path("importar/confirmar/", views.confirm_import, name="confirm"),
    path("api/mapa/", views.map_aggregates, name="map-aggregates"),
    path("api/mapa/uf/", views.map_state_detail, name="map-state-detail"),
    path("api/provedor/<int:pk>/", views.provider_detail, name="provider-detail"),
    path("api/<int:pk>/", views.detail, name="detail"),
]
