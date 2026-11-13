from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import models

from .models import GroupDataScope


GROUP_SYSTEM_ADMIN = "Administrador do Sistema"
GROUP_MANAGER = "Gestor"
GROUP_ANALYST = "Analista NOC"
GROUP_AUDITOR = "Consulta / Auditoria"
GROUP_EXECUTIVE = "Gestão"

MODULE_PERMISSIONS = (
    "view_dashboard", "view_cockpit", "save_analytics_views", "view_map", "present_geo",
    "view_rankings", "view_base", "view_operational_tickets", "manage_operational_tickets",
    "view_ari", "import_cmdb", "export_base",
    "view_quality", "manage_quality_orders",
)
ADMIN_PERMISSIONS = (
    "manage_users", "create_users", "generate_access_invites", "revoke_access_invites",
    "generate_password_reset_links", "require_password_change", "view_access_status",
    "manage_groups", "manage_data_scopes", "view_audit_log", "view_integrations",
    "manage_integrations", "run_integrations", "inspect_integrations", "view_system_info",
)
ACCOUNT_SECURITY_PERMISSIONS = (
    "create_users", "generate_access_invites", "revoke_access_invites",
    "generate_password_reset_links", "require_password_change", "view_access_status",
)
ALL_ACCESS_PERMISSIONS = MODULE_PERMISSIONS + ADMIN_PERMISSIONS
REPORTS_PERMISSIONS = ("view_reports", "edit_treatments", "override_treatments", "finalize_treatments", "finalize_with_pending", "reopen_treatments", "export_treatments", "delete_penaltycompetence", "delete_finalized_penaltycompetence")

# Exposed for explicit administration only: no existing group gains these grants.
from aplicativos.cancelamentos.models import PERMISSIONS as CANCELLATION_PERMISSION_CATALOG
CANCELLATION_PERMISSIONS = tuple(code for code, _label in CANCELLATION_PERMISSION_CATALOG)

ROLE_PERMISSIONS = {
GROUP_SYSTEM_ADMIN: ALL_ACCESS_PERMISSIONS + REPORTS_PERMISSIONS + ("delete_qualityorder",),
GROUP_EXECUTIVE: ("view_dashboard", "view_cockpit", "view_map", "view_rankings", "view_base", "view_operational_tickets", "view_ari", "view_quality", "view_reports"),
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
    "present_geo": ("Mapa", "Apresentar GEO Intelligence por cliente"),
    "view_rankings": ("Rankings", "Visualizar"),
    "view_base": ("Base", "Visualizar"),
    "view_operational_tickets": ("Chamados", "Visualizar fila operacional"),
    "manage_operational_tickets": ("Chamados", "Gerenciar acompanhamento interno"),
    "import_cmdb": ("Base", "Importar / atualizar CMDB"),
    "export_base": ("Base", "Exportar"),
    "manage_users": ("Administração", "Gerenciar usuários"),
    "create_users": ("Administração", "Criar usuários"),
    "generate_access_invites": ("Administração", "Gerar convites de acesso"),
    "revoke_access_invites": ("Administração", "Revogar convites de acesso"),
    "generate_password_reset_links": ("Administração", "Gerar links de redefinição de senha"),
    "require_password_change": ("Administração", "Exigir troca de senha"),
    "view_access_status": ("Administração", "Consultar situação de acesso"),
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
    "delete_qualityorder": ("Qualidade", "Excluir Ordens de Qualidade"),
}
PERMISSION_LABELS.update({code: ("Cancelamentos", label) for code, label in CANCELLATION_PERMISSION_CATALOG})


def managed_permissions(using="default"):
    content_type = ContentType.objects.db_manager(using).get_for_model(GroupDataScope)
    report_model = GroupDataScope._meta.apps.get_model("relatorios", "PenaltyCompetence")
    report_content_type = ContentType.objects.db_manager(using).get_for_model(report_model)
    quality_model = GroupDataScope._meta.apps.get_model("qualidade", "QualityOrder")
    quality_content_type = ContentType.objects.db_manager(using).get_for_model(quality_model)
    cancellation_model = GroupDataScope._meta.apps.get_model("cancelamentos", "CancellationProcess")
    cancellation_content_type = ContentType.objects.db_manager(using).get_for_model(cancellation_model)
    return Permission.objects.using(using).filter(
        models.Q(content_type=content_type, codename__in=ALL_ACCESS_PERMISSIONS)
        | models.Q(content_type=report_content_type, codename__in=REPORTS_PERMISSIONS)
        | models.Q(content_type=quality_content_type, codename__in=("view_quality", "manage_quality_orders", "delete_qualityorder"))
        | models.Q(content_type=cancellation_content_type, codename__in=CANCELLATION_PERMISSIONS)
    )


def bootstrap_access(*, using="default"):
    """Provision access profiles and restrictive default group scopes idempotently."""
    app_config = GroupDataScope._meta.apps.get_app_config("configuracoes")
    create_permissions(app_config, verbosity=0, interactive=False, using=using)
    reports_app_config = GroupDataScope._meta.apps.get_app_config("relatorios")
    create_permissions(reports_app_config, verbosity=0, interactive=False, using=using)
    quality_app_config = GroupDataScope._meta.apps.get_app_config("qualidade")
    create_permissions(quality_app_config, verbosity=0, interactive=False, using=using)
    create_permissions(GroupDataScope._meta.apps.get_app_config("cancelamentos"), verbosity=0, interactive=False, using=using)
    available = {permission.codename: permission for permission in managed_permissions(using)}
    for name, codenames in ROLE_PERMISSIONS.items():
        group, created = Group.objects.using(using).get_or_create(name=name)
        if created or (name == GROUP_EXECUTIVE and not group.permissions.exists()):
            group.permissions.add(*(available[codename] for codename in codenames if codename in available))
        elif name == GROUP_SYSTEM_ADMIN:
            group.permissions.add(*(available[codename] for codename in ACCOUNT_SECURITY_PERMISSIONS if codename in available))
        default_mode = GroupDataScope.Mode.ALL if name == GROUP_SYSTEM_ADMIN else GroupDataScope.Mode.CUSTOM
        GroupDataScope.objects.using(using).get_or_create(group=group, defaults={"mode": default_mode})
def is_system_admin(user):
    return user.is_superuser or all(user.has_perm(f"configuracoes.{codename}") for codename in ADMIN_PERMISSIONS)


def after_migrate(sender, using="default", **kwargs):
    bootstrap_access(using=using)
