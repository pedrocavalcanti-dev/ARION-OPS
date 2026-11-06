from django.urls import include, path

from . import views

app_name = "configuracoes"

urlpatterns = [
    path("documentacao/", views.documentation_home, name="documentation"),
    path("documentacao/explorador/", views.documentation_explorer, name="documentation-explorer"),
    path("documentacao/busca/", views.documentation_search, name="documentation-search"),
    path("documentacao/atualizacoes/", views.documentation_releases, name="documentation-releases"),
    path("documentacao/<slug:slug>/", views.documentation_page, name="documentation-page"),
    path("sobre/", views.about, name="about"),
    path("", views.index, name="index"),
    path("conta/", views.account, name="account"),
    path("seguranca/", views.security, name="security"),
    path("aparencia/", views.appearance, name="appearance"),
    path("administracao/usuarios/", views.user_list, name="users"),
    path("administracao/usuarios/novo/", views.user_create, name="user-create"),
    path("administracao/usuarios/<int:pk>/", views.user_detail, name="user-detail"),
    path("administracao/usuarios/<int:pk>/editar/", views.user_edit, name="user-edit"),
    path("administracao/grupos/", views.group_list, name="groups"),
    path("administracao/grupos/novo/", views.group_edit, name="group-create"),
    path("administracao/grupos/<int:pk>/editar/", views.group_edit, name="group-edit"),
    path("administracao/escopos/", views.scope_list, name="scopes"),
    path("administracao/escopos/<int:pk>/", views.scope_edit, name="scope-edit"),
    path("administracao/auditoria/", views.audit_list, name="audit"),
    path("administracao/auditoria/<int:pk>/", views.audit_detail, name="audit-detail"),
    path("administracao/integracoes/", views.integrations, name="integrations"),
    path("administracao/integracoes/", include(("aplicativos.integracoes.urls", "integracoes"), namespace="integracoes")),
    path("administracao/sistema/", views.system_info, name="system"),
]
