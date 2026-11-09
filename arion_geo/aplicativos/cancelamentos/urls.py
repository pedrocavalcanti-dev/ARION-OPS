from django.urls import path

from . import views

app_name = "cancelamentos"

urlpatterns = [
    path("", views.index, name="index"),
    path("exportar/", views.export, name="export"),
    path("configuracao/", views.configuration, name="configuration"),
    path("entrada/<int:pk>/", views.intake_detail, name="intake_detail"),
    path("entrada/<int:pk>/acao/<str:action>/", views.intake_action, name="intake_action"),
    path("processos/<int:pk>/", views.detail, name="detail"),
    path("processos/<int:pk>/acao/<str:action>/", views.process_action, name="process_action"),
]
