from django.urls import path

from . import views
from . import geo_views

app_name = "integracoes"

urlpatterns = [
    path("geo/<str:provider>/", geo_views.detail, name="geo-detail"),
    path("geo/<str:provider>/salvar/", geo_views.save, name="geo-save"),
    path("geo/<str:provider>/testar/", geo_views.test, name="geo-test"),
    path("geo/<str:provider>/sincronizar/", geo_views.sync, name="geo-sync"),
    path("", views.home, name="home"),
    path("cmdb/", views.cmdb_detail, name="cmdb"),
    path("cmdb/testar/", views.cmdb_test, name="cmdb-test"),
    path("cmdb/diagnostico/", views.cmdb_diagnose, name="cmdb-diagnose"),
    path("otrs/", views.otrs_detail, name="otrs"),
    path("otrs/testar/", views.otrs_test, name="otrs-test"),
    path("otrs/estrutura/", views.otrs_schema, name="otrs-schema"),
    path("otrs/estrutura/<path:table_name>/", views.otrs_table_detail, name="otrs-table"),
    path("arion/", views.arion_detail, name="arion"),
    path("arion/salvar/", views.arion_save_configuration, name="arion-save"),
    path("arion/testar/", views.arion_test_connection, name="arion-test"),
    path("arion/etapa/<str:action>/", views.arion_migration_action, name="arion-action"),
    path("apis/brevo/", views.brevo_detail, name="brevo"),
    path("apis/brevo/salvar/", views.brevo_save, name="brevo-save"),
    path("apis/brevo/testar/", views.brevo_test, name="brevo-test"),
    path("apis/brevo/testar-smtp/", views.brevo_smtp_test, name="brevo-smtp-test"),
    path("apis/brevo/testar-roteamento/", views.brevo_routing_test, name="brevo-routing-test"),
    path("apis/brevo/revogar/", views.brevo_revoke, name="brevo-revoke"),
    path("apis/mapbox/", views.mapbox_detail, name="mapbox"),
    path("apis/mapbox/salvar/", views.mapbox_save, name="mapbox-save"),
    path("apis/mapbox/testar/", views.mapbox_test, name="mapbox-test"),
    path("apis/mapbox/revogar/", views.mapbox_revoke, name="mapbox-revoke"),
    path("runs/", views.run_history, name="runs"),
    path("runs/<int:pk>/", views.run_detail, name="run-detail"),
    path("<str:key>/salvar/", views.save_configuration, name="save-config"),
    path("<str:key>/alternar/", views.toggle_integration, name="toggle"),
    path("<str:key>/remover-credenciais/", views.remove_credentials, name="remove-credentials"),
]
