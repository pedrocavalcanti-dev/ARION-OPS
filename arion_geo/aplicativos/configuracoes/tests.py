import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import UserPreference
from .access_control import GROUP_ANALYST, GROUP_MANAGER, GROUP_SYSTEM_ADMIN, is_system_admin
from .models import GroupDataScope
from aplicativos.base_links.models import CMDBLink, Operadora, Projeto, Provider


class UserPreferenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="theme-user", password="Senha-segura-123")
        self.user.groups.set([Group.objects.get(name=GROUP_ANALYST)])
        self.get_url = reverse("preferences_api:preferences")
        self.theme_url = reverse("preferences_api:theme")

    def test_user_without_preference_receives_default_dark(self):
        self.client.force_login(self.user)
        response = self.client.get(self.get_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"theme": "dark"})
        self.assertEqual(UserPreference.objects.get(user=self.user).theme, "dark")

    def test_get_returns_stored_preference(self):
        UserPreference.objects.create(user=self.user, theme=UserPreference.Theme.LIGHT)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.get_url).json(), {"theme": "light"})

    def test_authenticated_user_can_set_light_and_dark_and_preference_persists(self):
        self.client.force_login(self.user)
        for theme in ("light", "dark"):
            response = self.client.post(self.theme_url, data=json.dumps({"theme": theme}), content_type="application/json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"theme": theme})
            self.assertEqual(UserPreference.objects.get(user=self.user).theme, theme)

    def test_invalid_theme_returns_bad_request(self):
        self.client.force_login(self.user)
        response = self.client.post(self.theme_url, data=json.dumps({"theme": "blue"}), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_theme_api_rejects_unsupported_media_type_as_json(self):
        self.client.force_login(self.user)
        response = self.client.post(self.theme_url, data="theme=light", content_type="text/plain")
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_MEDIA_TYPE")

    def test_unauthenticated_user_cannot_read_or_change_preferences(self):
        response = self.client.get(self.get_url)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")
        response = self.client.post(self.theme_url, data=json.dumps({"theme": "light"}), content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(UserPreference.objects.exists())

    def test_authenticated_template_receives_saved_theme(self):
        UserPreference.objects.create(user=self.user, theme=UserPreference.Theme.LIGHT)
        self.client.force_login(self.user)
        response = self.client.get(reverse("painel:index"))
        self.assertContains(response, 'data-user-theme="light"')

    def test_theme_update_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(self.theme_url, data=json.dumps({"theme": "light"}), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"]["code"], "CSRF_FAILED")


class AccountSettingsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="account-user", password="Senha-segura-123", first_name="Ana", last_name="Silva", email="ana@example.com")
        self.other = user_model.objects.create_user(username="other-user", password="Senha-segura-123", first_name="Bia", email="bia@example.com")
        self.user.groups.set([Group.objects.get(name=GROUP_ANALYST)])
        self.other.groups.set([Group.objects.get(name=GROUP_ANALYST)])

    def test_settings_pages_require_login(self):
        for name in ("index", "account", "security", "appearance", "about"):
            response = self.client.get(reverse(f"configuracoes:{name}"))
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login/", response.url)

    def test_authenticated_shared_header_routes_render_without_reverse_errors(self):
        self.client.force_login(self.user)
        route_names = (
            "painel:index",
            "dashboard",
            "cockpit",
            "mapa:index",
            "rankings:index",
            "base_links:index",
            "configuracoes:index",
            "configuracoes:account",
            "configuracoes:security",
            "configuracoes:appearance",
            "configuracoes:about",
        )
        for route_name in route_names:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Minha conta")
                self.assertContains(response, "Configurações")

    def test_user_menu_separates_account_and_settings_destinations(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("painel:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("configuracoes:account")}"')
        self.assertContains(response, f'href="{reverse("configuracoes:index")}"')
        self.assertContains(response, f'href="{reverse("configuracoes:about")}"')
        self.assertNotEqual(reverse("configuracoes:account"), reverse("configuracoes:index"))

    def test_about_page_shows_product_identity_current_version_and_development_credit(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("configuracoes:about"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ARION OPS")
        self.assertContains(response, "Plataforma Operacional — NOC Atacado")
        self.assertContains(response, "Concepção e desenvolvimento")
        self.assertContains(response, "Pedro Cavalcanti")
        self.assertContains(response, "Versão 1.3.1")
        self.assertContains(response, "Django · MySQL · JavaScript · ECharts · Mapbox")
        self.assertContains(response, "Controle de acesso · Data Scope · auditoria · espelhos locais OTRS/CMDB")
        self.assertContains(response, "ARION Serviços de Telecomunicações")
        self.assertContains(response, "NOC Atacado")

    def test_settings_landing_shows_only_allowed_shortcuts_and_all_links_resolve(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("configuracoes:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gerencie sua conta, preferências e recursos administrativos disponíveis.")
        for label in ("Minha conta", "Segurança", "Aparência"):
            self.assertContains(response, label)
        self.assertNotContains(response, "ADMINISTRAÇÃO")
        self.assertNotContains(response, "Grupos e acessos")
        for route in ("account", "security", "appearance"):
            self.assertContains(response, f'href="{reverse(f"configuracoes:{route}")}"')

    def test_account_shows_only_signed_in_user_profile(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("configuracoes:account"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ana@example.com")
        self.assertContains(response, "account-user")
        self.assertNotContains(response, "bia@example.com")

    def test_profile_update_changes_only_current_user_allowed_fields(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("configuracoes:account"), {
            "first_name": "Ana Maria",
            "last_name": "Santos",
            "email": "ana.maria@example.com",
            "user_id": self.other.pk,
            "is_staff": "on",
            "username": "changed-user",
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.last_name, self.user.email), ("Ana Maria", "Santos", "ana.maria@example.com"))
        self.assertEqual(self.user.username, "account-user")
        self.assertFalse(self.user.is_staff)
        self.assertEqual((self.other.first_name, self.other.email), ("Bia", "bia@example.com"))

    def test_password_change_keeps_current_session_authenticated(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("configuracoes:security"), {
            "old_password": "Senha-segura-123",
            "new_password1": "Outra-senha-segura-456",
            "new_password2": "Outra-senha-segura-456",
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Outra-senha-segura-456"))
        self.assertEqual(self.client.get(reverse("configuracoes:account")).status_code, 200)

    def test_appearance_uses_and_updates_existing_user_preference(self):
        self.client.force_login(self.user)
        UserPreference.objects.create(user=self.user, theme=UserPreference.Theme.DARK)
        response = self.client.get(reverse("configuracoes:appearance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-theme-option="light"')
        self.assertContains(response, 'data-theme-option="dark"')
        update = self.client.post(reverse("preferences_api:theme"), data=json.dumps({"theme": "light"}), content_type="application/json")
        self.assertEqual(update.status_code, 200)
        self.assertEqual(UserPreference.objects.get(user=self.user).theme, UserPreference.Theme.LIGHT)


class AccessControlTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_user(username="access-admin", password="Senha-segura-123")
        self.admin.groups.set([Group.objects.get(name=GROUP_SYSTEM_ADMIN)])
        self.analyst = self.User.objects.create_user(username="access-analyst", password="Senha-segura-123")
        self.analyst.groups.set([Group.objects.get(name=GROUP_ANALYST)])

    def test_system_admin_sees_administration_shortcuts_on_settings_landing(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("configuracoes:index"))
        self.assertEqual(response.status_code, 200)
        for route, label in (("users", "Usuários"), ("groups", "Grupos e acessos"), ("scopes", "Escopos de dados"),
                             ("audit", "Auditoria"), ("integrations", "Integrações"), ("system", "Sistema")):
            with self.subTest(route=route):
                self.assertContains(response, f'href="{reverse(f"configuracoes:{route}")}"')
                self.assertContains(response, label)

    def test_system_admin_can_open_administration_pages(self):
        self.client.force_login(self.admin)
        for name in ("users", "groups", "scopes", "audit", "integrations", "system"):
            with self.subTest(route=name):
                response = self.client.get(reverse(f"configuracoes:{name}"))
                self.assertEqual(response.status_code, 200)
                # The integrations portal has its own section heading and shell.
                expected_heading = "FONTES DE DADOS E INFRAESTRUTURA" if name == "integrations" else "ADMINISTRAÇÃO"
                self.assertContains(response, expected_heading)

    def test_scope_administration_is_group_based_and_user_edit_only_shows_inherited_scope(self):
        self.client.force_login(self.admin)
        analyst_group = Group.objects.get(name=GROUP_ANALYST)
        scopes = self.client.get(reverse("configuracoes:scopes"))
        self.assertContains(scopes, "Defina quais dados cada grupo de acesso pode visualizar.")
        self.assertContains(scopes, analyst_group.name)
        scope_edit = self.client.get(reverse("configuracoes:scope-edit", args=(analyst_group.pk,)))
        self.assertEqual(scope_edit.status_code, 200)
        self.assertContains(scope_edit, "Usuários que herdam este escopo")
        self.assertContains(scope_edit, "Buscar operadora")
        self.assertContains(scope_edit, "Buscar projeto")
        self.assertContains(scope_edit, "Selecionar todas")
        edit = self.client.get(reverse("configuracoes:user-edit", args=(self.analyst.pk,)))
        self.assertContains(edit, "Escopo herdado")
        self.assertNotContains(edit, "Operadoras permitidas")
        self.assertNotContains(edit, "Projetos permitidos")

    def test_analyst_has_no_administration_navigation_and_gets_403(self):
        self.client.force_login(self.analyst)
        response = self.client.get(reverse("configuracoes:account"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse("ari:index")).status_code, 200)
        self.assertNotContains(response, "ADMINISTRAÇÃO")
        for name in ("users", "groups", "scopes", "audit", "integrations", "system"):
            with self.subTest(route=name):
                response = self.client.get(reverse(f"configuracoes:{name}"))
                self.assertEqual(response.status_code, 403)

    def test_manager_profile_has_module_access_but_no_administration(self):
        self.analyst.groups.set([Group.objects.get(name=GROUP_MANAGER)])
        self.client.force_login(self.analyst)
        self.assertEqual(self.client.get(reverse("cockpit")).status_code, 200)
        self.assertEqual(self.client.get(reverse("ari:index")).status_code, 200)
        self.assertEqual(self.client.get(reverse("configuracoes:users")).status_code, 403)

    def test_auditor_can_view_audit_but_not_other_administrative_areas(self):
        auditor = Group.objects.get(name="Consulta / Auditoria")
        self.analyst.groups.set([auditor])
        self.client.force_login(self.analyst)
        self.assertEqual(self.client.get(reverse("configuracoes:audit")).status_code, 200)
        self.assertEqual(self.client.get(reverse("ari:index")).status_code, 200)
        for name in ("users", "groups", "scopes", "integrations", "system"):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(f"configuracoes:{name}")).status_code, 403)

    def test_bootstrap_is_idempotent_and_leaves_unassigned_users_unassigned(self):
        self.analyst.groups.clear()
        call_command("bootstrap_access", verbosity=0)
        call_command("bootstrap_access", verbosity=0)
        self.analyst.refresh_from_db()
        self.assertFalse(self.analyst.groups.exists())
        self.assertEqual(Group.objects.filter(name__in=(GROUP_SYSTEM_ADMIN, GROUP_MANAGER, GROUP_ANALYST, "Consulta / Auditoria")).count(), 4)

    def test_admin_can_create_user_with_django_validated_initial_password(self):
        self.client.force_login(self.admin)
        analyst_group = Group.objects.get(name=GROUP_ANALYST)
        response = self.client.post(reverse("configuracoes:user-create"), {
            "username": "new-operator", "first_name": "Nina", "last_name": "Costa", "email": "nina@example.com",
            "password1": "XyZ!2026_SenhaForte", "password2": "XyZ!2026_SenhaForte",
            "group": analyst_group.pk,
        })
        self.assertEqual(response.status_code, 302)
        created = self.User.objects.get(username="new-operator")
        self.assertTrue(created.check_password("XyZ!2026_SenhaForte"))
        self.assertEqual(list(created.groups.all()), [analyst_group])
        self.assertEqual(created.groups.count(), 1)

    def test_new_user_requires_one_access_profile_and_does_not_offer_user_scope(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("configuracoes:user-create"), {
            "username": "missing-profile", "password1": "XyZ!2026_SenhaForte", "password2": "XyZ!2026_SenhaForte",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.User.objects.filter(username="missing-profile").exists())
        self.assertContains(response, "Perfil de acesso")
        self.assertNotContains(response, "Operadoras permitidas")
        self.assertNotContains(response, "scope_mode")

    def test_editing_user_replaces_previous_profile(self):
        self.client.force_login(self.admin)
        previous = Group.objects.get(name=GROUP_ANALYST)
        target = self.User.objects.create_user(username="profile-switch", password="Senha-segura-123")
        target.groups.set([previous])
        next_group = Group.objects.get(name=GROUP_MANAGER)
        response = self.client.post(reverse("configuracoes:user-edit", args=(target.pk,)), {
            "first_name": "", "last_name": "", "email": "", "is_active": "on", "group": next_group.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(target.groups.all()), [next_group])

    def test_group_permissions_are_presented_with_friendly_module_labels(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("configuracoes:group-create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Administração")
        self.assertContains(response, "Gerenciar usuários")
        self.assertNotContains(response, "configuracoes.manage_users")
        self.assertContains(response, "data-permission-search")
        self.assertContains(response, "data-permission-expand")
        self.assertContains(response, "data-permission-collapse")
        self.assertContains(response, "data-permission-selected-count")
        self.assertContains(response, "js/configuracoes/group-access.js")

    def test_system_admin_permission_set_cannot_be_removed_from_last_admin_group(self):
        self.client.force_login(self.admin)
        group = Group.objects.get(name=GROUP_SYSTEM_ADMIN)
        permission = Permission.objects.get(content_type__app_label="configuracoes", codename="view_dashboard")
        response = self.client.post(reverse("configuracoes:group-edit", args=(group.pk,)), {"name": group.name, "permissions": [permission.pk]})
        self.assertEqual(response.status_code, 200)
        group.refresh_from_db()
        self.assertTrue(group.permissions.filter(codename="manage_users").exists())
        self.assertContains(response, "último perfil de sistema ativo")
        self.assertContains(response, 'data-has-errors="true"')
        self.assertContains(response, "data-permission-unsaved")

    def test_last_active_system_administrator_cannot_be_deactivated(self):
        self.assertTrue(is_system_admin(self.admin))
        self.client.force_login(self.admin)
        response = self.client.post(reverse("configuracoes:user-edit", args=(self.admin.pk,)), {
            "first_name": "", "last_name": "", "email": "", "is_active": "",
            "group": Group.objects.get(name=GROUP_ANALYST).pk,
        })
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertContains(response, "último Administrador do Sistema")


class DataScopeIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="scope-claro", password="Senha-segura-123")
        self.client.force_login(self.user)
        from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror

        self.claro = Operadora.objects.create(name="Claro", normalized_name="claro")
        self.vivo = Operadora.objects.create(name="Vivo", normalized_name="vivo")
        self.bradesco = Projeto.objects.create(name="Bradesco", normalized_name="bradesco")
        self.fust = Projeto.objects.create(name="FUST", normalized_name="fust")
        self.claro_provider = Provider.objects.create(name="ISP Claro", normalized_name="isp-claro")
        self.vivo_provider = Provider.objects.create(name="ISP Vivo", normalized_name="isp-vivo")
        self.claro_mirror = CMDBOperatorMirror.objects.create(source_config_item_id="op-claro", name="Claro")
        self.vivo_mirror = CMDBOperatorMirror.objects.create(source_config_item_id="op-vivo", name="Vivo")
        created = timezone.now()
        self.links = [
            CMDBLinkMirror.objects.create(source_config_item_id="scope-1", state="SP", operator=self.claro_mirror, legacy_project="Bradesco", contracting_client="Claro", business_project="Bradesco", isp_raw="ISP Claro", source_created_at=created),
            CMDBLinkMirror.objects.create(source_config_item_id="scope-2", state="RJ", operator=self.claro_mirror, legacy_project="Bradesco", contracting_client="Claro", business_project="FUST", isp_raw="ISP Claro", source_created_at=created),
            CMDBLinkMirror.objects.create(source_config_item_id="scope-3", state="MG", operator=self.vivo_mirror, legacy_project="Bradesco", contracting_client="Vivo", business_project="Bradesco", isp_raw="ISP Vivo", source_created_at=created),
        ]
        self.group = Group.objects.get(name=GROUP_ANALYST)
        self.user.groups.set([self.group])
        self.scope, _ = GroupDataScope.objects.get_or_create(group=self.group)
        self.scope.mode = GroupDataScope.Mode.CUSTOM
        self.scope.save()
        self.scope.allowed_operators.set([self.claro])
        self.scope.allowed_projects.set([self.bradesco])

    def test_scope_is_operator_and_project_intersection_in_base_dashboard_and_rankings(self):
        base = self.client.get(reverse("base_links:index"))
        self.assertEqual(base.status_code, 200)
        self.assertEqual(base.context["page_obj"].paginator.count, 1)
        self.assertEqual(list(base.context["operators"]), [self.claro_mirror])
        self.assertEqual(list(base.context["projects"]), [{"name": "Bradesco"}])
        outside_detail = self.client.get(reverse("base_links:detail", args=(self.links[2].pk,)))
        self.assertEqual(outside_detail.status_code, 404)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.context["total_links"], 1)
        ranking = self.client.get(reverse("rankings:index"))
        self.assertEqual([row["name"] for row in ranking.context["operators"]], ["Claro"])

    def test_scope_is_applied_before_cockpit_filters_and_options(self):
        from aplicativos.integracoes.models import OTRSTicket, OTRSTicketConfigItem
        ticket = OTRSTicket.objects.create(external_id=9101, ticket_number="SCOPE-9101", queue_external_id=107, queue_name="NOC - ATACADO", external_state_id=1, external_state_name="em atendimento", business_status="OPEN", is_business_open=True, is_present_in_queue=True, source_created_at=timezone.now().replace(year=2026, month=10, day=15))
        OTRSTicketConfigItem.objects.create(ticket=ticket, config_item_id="scope-1", cmdb_link=self.links[0], relation_type="DependsOn", relation_state="Valid")
        response = self.client.post(reverse("analytics-query"), data=json.dumps({"metric": "links_affected", "group_by": "operator", "period_from": "2026-10", "period_to": "2026-10", "period": "2026-10", "filters": {}}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["value"], 1)
        self.assertEqual(response.json()["options"]["provider"], ["Claro"])
        outside = self.client.post(reverse("analytics-query"), data=json.dumps({"metric": "links_affected", "group_by": "operator", "period_from": "2026-10", "period_to": "2026-10", "period": "2026-10", "filters": {"operator": "Vivo"}}), content_type="application/json")
        self.assertEqual(outside.status_code, 200)
        self.assertEqual(outside.json()["value"], 0)

    def test_scope_is_applied_before_map_aggregation_and_filtering(self):
        all_data = self.client.get(reverse("base_links:map-aggregates"))
        self.assertEqual(all_data.status_code, 200)
        self.assertEqual(all_data.json()["filtered_source_total"], 1)
        self.assertEqual(all_data.json()["clients"], ["Claro"])
        outside = self.client.get(reverse("base_links:map-aggregates"), {"provider": self.vivo_mirror.pk})
        self.assertEqual(outside.status_code, 200)
        self.assertEqual(outside.json()["filtered_source_total"], 0)

    def test_custom_scope_with_empty_lists_and_user_without_group_return_no_data(self):
        self.scope.allowed_operators.clear()
        self.assertEqual(self.client.get(reverse("base_links:index")).context["page_obj"].paginator.count, 0)
        self.scope.allowed_operators.set([self.claro])
        self.scope.allowed_projects.clear()
        self.assertEqual(self.client.get(reverse("base_links:index")).context["page_obj"].paginator.count, 0)
        self.user.groups.clear()
        self.assertEqual(self.client.get(reverse("base_links:index")).status_code, 403)

    def test_all_scope_and_superuser_bypass(self):
        self.scope.mode = GroupDataScope.Mode.ALL
        self.scope.save()
        self.assertEqual(self.client.get(reverse("base_links:index")).context["page_obj"].paginator.count, 3)
        self.user.groups.clear()
        self.assertEqual(self.client.get(reverse("base_links:index")).status_code, 403)
        self.user.is_superuser = True
        self.user.save()
        self.assertEqual(self.client.get(reverse("base_links:index")).context["page_obj"].paginator.count, 3)


class DocumentationReleaseNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="release-reader", password="test-password")
        self.client.force_login(self.user)
        current = {
            "version": "2.0.0", "released_at": "2026-11-01", "released_on": date(2026, 11, 1),
            "title": "Release atual", "summary": "Conteúdo atual da release.",
            "sections": [{"category": "Novidades", "items": ["Item atual"]}],
        }
        older = {
            "version": "1.1.0", "released_at": "2026-10-28", "released_on": date(2026, 10, 28),
            "title": "Release anterior", "summary": "Conteúdo anterior da release.",
            "sections": [{"category": "Correções", "items": ["Item anterior"]}],
        }
        self.catalog = {"version": "2.0.0", "current_release": current, "releases": [current, older]}

    @patch("aplicativos.configuracoes.views.release_catalog")
    def test_release_page_navigates_selected_version_and_returns_to_arion(self, release_catalog):
        release_catalog.return_value = self.catalog
        url = reverse("configuracoes:documentation-releases")
        current = self.client.get(url)
        older = self.client.get(url, {"version": "1.1.0"})

        self.assertEqual(current.status_code, 200)
        self.assertContains(current, "Conteúdo atual da release.")
        self.assertContains(current, "data-release-picker")
        self.assertContains(current, "v2.0.0")
        self.assertContains(current, "v1.1.0")
        self.assertContains(current, "Voltar ao ARION")
        self.assertContains(current, f'{url}?version=1.1.0')
        self.assertEqual(older.status_code, 200)
        self.assertContains(older, "Conteúdo anterior da release.")
        self.assertNotContains(older, "Conteúdo atual da release.")
        self.assertContains(older, 'value="1.1.0" selected')
