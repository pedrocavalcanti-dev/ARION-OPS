from django.urls import path

from . import views
from . import geo_views

app_name = "mapa"

urlpatterns = [
    path("", views.index, name="index"),
    path("intelligence/",geo_views.intelligence,name="intelligence"),
    path("clima/coletar/",geo_views.collect,name="weather-collect"),
    path("clima/local/",geo_views.local_weather,name="weather-local"),
    path("apresentar/",geo_views.start,name="presentation-start"),
    path("apresentacao/<str:key>/",geo_views.presentation,name="presentation"),
    path("apresentacao/<str:key>/dados/",geo_views.presentation_data,name="presentation-data"),
    path("apresentacao/<str:key>/detalhes/",geo_views.presentation_detail,name="presentation-detail"),
    path("apresentacao/<str:key>/intelligence/",geo_views.presentation_intelligence,name="presentation-intelligence"),
    path("apresentacao/<str:key>/encerrar/",geo_views.stop,name="presentation-stop"),
]
