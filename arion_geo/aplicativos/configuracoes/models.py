from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models


class UserPreference(models.Model):
    class Theme(models.TextChoices):
        DARK = "dark", "Escuro"
        LIGHT = "light", "Claro"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences")
    theme = models.CharField(max_length=5, choices=Theme.choices, default=Theme.DARK)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Preferência do usuário"
        verbose_name_plural = "Preferências dos usuários"

    def __str__(self):
        return f"{self.user} · {self.get_theme_display()}"


class GroupDataScope(models.Model):
    class Mode(models.TextChoices):
        ALL = "all", "Todos os dados"
        CUSTOM = "custom", "Personalizado"

    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="data_scope")
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.CUSTOM)
    allowed_operators = models.ManyToManyField("base_links.Operadora", blank=True, related_name="scoped_groups")
    allowed_projects = models.ManyToManyField("base_links.Projeto", blank=True, related_name="scoped_groups")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "escopo de dados do grupo"
        verbose_name_plural = "escopos de dados dos grupos"
        permissions = [
            ("view_dashboard", "Can view the executive dashboard"),
            ("view_cockpit", "Can view the analytics cockpit"),
            ("save_analytics_views", "Can save analytics views"),
            ("view_map", "Can view the operational map"),
            ("view_rankings", "Can view provider rankings"),
            ("view_base", "Can view the CMDB base"),
              ("view_operational_tickets", "Can view operational tickets"),
              ("manage_operational_tickets", "Can manage local ticket tracking"),
            ("import_cmdb", "Can import and update the CMDB"),
            ("export_base", "Can export CMDB data"),
            ("manage_users", "Can manage user accounts"),
            ("create_users", "Can create user accounts"),
            ("generate_access_invites", "Can issue first-access invitations"),
            ("revoke_access_invites", "Can revoke first-access invitations"),
            ("generate_password_reset_links", "Can issue administrative password reset links"),
            ("require_password_change", "Can require a user to change their password"),
            ("view_access_status", "Can view account activation and password status"),
            ("manage_groups", "Can manage access groups and permissions"),
            ("manage_data_scopes", "Can manage group data scopes"),
            ("view_audit_log", "Can view audit log"),
            ("view_integrations", "Can view configured integrations"),
            ("manage_integrations", "Can manage integration settings and credentials"),
            ("run_integrations", "Can test and run integration diagnostics"),
            ("inspect_integrations", "Can inspect external integration schemas and samples"),
            ("view_system_info", "Can view system information"),
            ("view_ari", "Can access the ARI I.A preview"),
        ]

    def __str__(self):
        return f"{self.group} · {self.get_mode_display()}"


class LegacyUserDataScope(models.Model):
    """Archived per-user scopes retained for migration traceability only."""

    class Mode(models.TextChoices):
        ALL = "all", "Todos os dados"
        CUSTOM = "custom", "Personalizado"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="legacy_data_scope")
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.ALL)
    allowed_operators = models.ManyToManyField("base_links.Operadora", blank=True, related_name="legacy_scoped_users", db_table="configuracoes_userdatascope_allowed_operators")
    allowed_projects = models.ManyToManyField("base_links.Projeto", blank=True, related_name="legacy_scoped_users", db_table="configuracoes_userdatascope_allowed_projects")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "configuracoes_userdatascope"
        verbose_name = "escopo legado de usuário"
        verbose_name_plural = "escopos legados de usuários"

    def __str__(self):
        return f"{self.user} · {self.get_mode_display()} (legado)"
