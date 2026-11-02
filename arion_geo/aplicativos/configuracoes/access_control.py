from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import models

from .models import GroupDataScope


GROUP_SYSTEM_ADMIN = "Administrador do Sistema"
GROUP_MANAGER = "Gestor"
GROUP_ANALYST = "Analista NOC"
GROUP_AUDITOR = "Consulta / Auditoria"

MODULE_PERMISSIONS = (
    "view_dashboard", "view_cockpit", "save_analytics_views", "view_map",
    "view_rankings", "view_base", "view_operational_tickets", "manage_operational_tickets",
    "view_ari", "import_cmdb", "export_base",
    "view_quality", "manage_quality_orders",
)
ADMIN_PERMISSIONS = ("manage_users", "manage_groups", "manage_data_scopes", "view_audit_log", "view_integrations", "manage_integrations", "run_integrations", "inspect_integrations", "view_system_info")
ALL_ACCESS_PERMISSIONS = MODULE_PERMISSIONS + ADMIN_PERMISSIONS
REPORTS_PERMISSIONS = ("view_reports", "edit_treatments", "override_treatments", "finalize_treatments", "finalize_with_pending", "reopen_treatments", "export_treatments", "delete_penaltycompetence", "delete_finalized_penaltycompetence")

ROLE_PERMISSIONS = {
    GROUP_SYSTEM_ADMIN: ALL_ACCESS_PERMISSIONS + REPORTS_PERMISSIONS,
GROUP_MANAGER: ("view_dashboard", "view_cockpit", "save_analytics_views", "view_map", "view_rankings", "view_base", "view_operational_tickets", "manage_operational_tickets", "view_ari", "view_quality", "manage_quality_orders", "view_reports", "edit_treatments", "override_treatments", "finalize_treatments", "export_treatments", "delete_penaltycompetence"),
GROUP_ANALYST: ("view_dashboard", "view_cockpit", "save_analytics_views", "view_map", "view_rankings", "view_base", "view_operational_tickets", "manage_operational_tickets", "view_ari", "view_quality"),
GROUP_AUDITOR: ("view_dashboard", "view_cockpit", "view_map", "view_rankings", "view_base", "view_operational_tickets", "view_ari", "view_quality", "view_audit_log"),
}

PERMISSION_LABELS = {
    "view_reports": ("Relatórios", "Visualizar relatórios"),
    "edit_treatments": ("Relatórios", "Editar tratativas"),
    "override_treatments": ("Relatórios", "Aplicar overrides de cálculo"),
    "finalize_treatments": ("Relatórios", "Finalizar competências sem pendências"),
    "finalize_with_pending": ("Relatórios", "Finalizar competências com pendências"),
    "reopen_treatments": ("Relatórios", "Reabrir competências finalizadas"),
    "export_treatments": ("Relatórios", "Exportar planilhas de tratativa"),
    "delete_penaltycompetence": ("Relatórios", "Excluir competências não finalizadas"),
    "delete_finalized_penaltycompetence": ("Relatórios", "Arquivar competências finalizadas"),
    "view_dashboard": ("Dashboard", "Visualizar"),
    "view_cockpit": ("Cockpit", "Visualizar"),
    "save_analytics_views": ("Cockpit", "Salvar visões"),
    "view_map": ("Mapa", "Visualizar"),
    "view_rankings": ("Rankings", "Visualizar"),
    "view_base": ("Base", "Visualizar"),
    "view_operational_tickets": ("Chamados", "Visualizar fila operacional"),
    "manage_operational_tickets": ("Chamados", "Gerenciar acompanhamento interno"),
    "import_cmdb": ("Base", "Importar / atualizar CMDB"),
    "export_base": ("Base", "Exportar"),
    "manage_users": ("Administração", "Gerenciar usuários"),
    "manage_groups": ("Administração", "Gerenciar grupos e permissões"),
    "manage_data_scopes": ("Administração", "Gerenciar escopos de dados"),
    "view_audit_log": ("Administração", "Visualizar auditoria"),
    "view_integrations": ("Administração", "Visualizar integrações"),
    "manage_integrations": ("Administração", "Configurar integrações"),
    "run_integrations": ("Administração", "Testar integrações"),
    "inspect_integrations": ("Administração", "Inspecionar fontes externas"),
    "view_system_info": ("Administração", "Visualizar informações do sistema"),
    "view_ari": ("ARI I.A", "Acessar a experiência em modo Preview"),
    "view_quality": ("Qualidade", "Visualizar Qualidade Operacional"),
    "manage_quality_orders": ("Qualidade", "Gerenciar Ordens de Qualidade"),
}


def managed_permissions(using="default"):
    content_type = ContentType.objects.db_manager(using).get_for_model(GroupDataScope)
    report_model = GroupDataScope._meta.apps.get_model("relatorios", "PenaltyCompetence")
    report_content_type = ContentType.objects.db_manager(using).get_for_model(report_model)
    quality_model = GroupDataScope._meta.apps.get_model("qualidade", "QualityOrder")
    quality_content_type = ContentType.objects.db_manager(using).get_for_model(quality_model)
    return Permission.objects.using(using).filter(
        models.Q(content_type=content_type, codename__in=ALL_ACCESS_PERMISSIONS)
        | models.Q(content_type=report_content_type, codename__in=REPORTS_PERMISSIONS)
        | models.Q(content_type=quality_content_type, codename__in=("view_quality", "manage_quality_orders"))
    )


def bootstrap_access(*, using="default"):
    """Provision access profiles and restrictive default group scopes idempotently."""
    app_config = GroupDataScope._meta.apps.get_app_config("configuracoes")
    create_permissions(app_config, verbosity=0, interactive=False, using=using)
    reports_app_config = GroupDataScope._meta.apps.get_app_config("relatorios")
    create_permissions(reports_app_config, verbosity=0, interactive=False, using=using)
    quality_app_config = GroupDataScope._meta.apps.get_app_config("qualidade")
    create_permissions(quality_app_config, verbosity=0, interactive=False, using=using)
    available = {permission.codename: permission for permission in managed_permissions(using)}
    for name, codenames in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=name)
        group.permissions.remove(*managed_permissions(using))
        group.permissions.add(*(available[codename] for codename in codenames if codename in available))
        default_mode = GroupDataScope.Mode.ALL if name == GROUP_SYSTEM_ADMIN else GroupDataScope.Mode.CUSTOM
        GroupDataScope.objects.using(using).get_or_create(group=group, defaults={"mode": default_mode})
def is_system_admin(user):
    return user.is_superuser or all(user.has_perm(f"configuracoes.{codename}") for codename in ADMIN_PERMISSIONS)


def after_migrate(sender, using="default", **kwargs):
    bootstrap_access(using=using)
