from importlib import import_module
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aplicativos.configuracoes.access_control import GROUP_ANALYST, GROUP_SYSTEM_ADMIN
from aplicativos.configuracoes.models import UserPreference

from .models import AccessToken, UserSecurityProfile
from .security import get_security_profile, issue_access_token, password_access_status


@override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"], ARION_PUBLIC_BASE_URL="http://127.0.0.1:8100")
class InvitationRefinementTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_user(username="review-admin", password="Admin-Review-2026!")
        self.admin.groups.add(Group.objects.get(name=GROUP_SYSTEM_ADMIN))
        self.invitee = self.User.objects.create_user(username="review-invitee", first_name="Lucas", password=None)
        self.invitee.groups.add(Group.objects.get(name=GROUP_ANALYST))
        UserSecurityProfile.objects.create(user=self.invitee, change_reason="invitation")

    def test_browser_csrf_origin_activation_and_login_preserve_access(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("configuracoes:user-invitation-issue", args=[self.invitee.pk]))
        self.assertEqual(response.status_code, 200)
        link = response.context["access_url"]
        self.assertTrue(link.startswith("http://127.0.0.1:8100/ativacao/#"))
        raw = link.split("#")[1]
        token = self.invitee.access_tokens.get()
        groups_before = list(self.invitee.groups.values_list("pk", flat=True))
        scope = self.invitee.groups.get().data_scope
        scope_before = (scope.mode, list(scope.allowed_operators.values_list("pk", flat=True)), list(scope.allowed_projects.values_list("pk", flat=True)))
        browser = Client(enforce_csrf_checks=True, HTTP_HOST="127.0.0.1:8100")
        page = browser.get(reverse("autenticacao:activate"))
        self.assertContains(page, 'name="new_password1"')
        self.assertEqual(page["Referrer-Policy"], "same-origin")
        csrf = browser.cookies["csrftoken"].value
        preview = browser.post(reverse("autenticacao:activate"), {
            "action": "inspect_token", "token": raw, "csrfmiddlewaretoken": csrf,
        }, HTTP_ORIGIN="http://127.0.0.1:8100")
        self.assertEqual(preview.json(), {"display_name": "Lucas"})
        token.refresh_from_db()
        self.assertIsNone(token.consumed_at)
        bad_origin = browser.post(reverse("autenticacao:activate"), {
            "action": "inspect_token", "token": raw, "csrfmiddlewaretoken": csrf,
        }, HTTP_ORIGIN="null")
        self.assertEqual(bad_origin.status_code, 403)
        password = "Garden-Mist-Personal-2026!"
        payload = {"token": raw, "csrfmiddlewaretoken": csrf, "new_password1": password, "new_password2": password}
        completed = browser.post(reverse("autenticacao:activate"), payload, HTTP_ORIGIN="http://127.0.0.1:8100")
        self.assertContains(completed, "Senha definida com sucesso")
        self.assertEqual(browser.post(reverse("autenticacao:activate"), payload, HTTP_ORIGIN="http://127.0.0.1:8100").status_code, 400)
        browser.get(reverse("autenticacao:login"))
        login = browser.post(reverse("autenticacao:login"), {
            "username": self.invitee.username, "password": password,
            "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
        }, HTTP_ORIGIN="http://127.0.0.1:8100")
        self.assertEqual(login.status_code, 302)
        self.assertEqual(int(browser.session["_auth_user_id"]), self.invitee.pk)
        self.assertEqual(list(self.invitee.groups.values_list("pk", flat=True)), groups_before)
        scope.refresh_from_db()
        self.assertEqual((scope.mode, list(scope.allowed_operators.values_list("pk", flat=True)), list(scope.allowed_projects.values_list("pk", flat=True))), scope_before)

    def test_invalid_preview_discloses_no_recipient(self):
        response = self.client.post(reverse("autenticacao:activate"), {"action": "inspect_token", "token": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, "Lucas", status_code=400)

    def test_issued_screen_uses_persisted_theme_and_navigation(self):
        self.client.force_login(self.admin)
        preference = UserPreference.objects.create(user=self.admin, theme="light")
        for theme in ("light", "dark"):
            preference.theme = theme
            preference.save()
            response = self.client.post(reverse("configuracoes:user-invitation-issue", args=[self.invitee.pk]))
            self.assertContains(response, f'data-theme="{theme}"')
            self.assertContains(response, "Copiar link")
            self.assertContains(response, "Voltar aos usuários")
            self.assertContains(response, "Válido por 48 horas")

    def test_legacy_profile_defaults_do_not_treat_existing_password_as_invitation(self):
        profile = get_security_profile(self.admin)
        self.assertEqual(profile.first_activation_completed_at, self.admin.date_joined)
        self.assertIsNone(profile.password_changed_at)
        self.assertEqual(password_access_status(self.admin)[0], "active")
        self.assertEqual(password_access_status(self.invitee)[0], "pending")

    def test_user_list_distinguishes_existing_pending_and_required_accounts(self):
        required = self.User.objects.create_user(username="review-required", password="Review-Required-2026!")
        UserSecurityProfile.objects.create(
            user=required, first_activation_completed_at=timezone.now(),
            must_change_password=True, change_reason="admin",
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("configuracoes:users"))
        self.assertEqual(response.status_code, 200)
        accounts = {user.pk: user for user in response.context["page_obj"]}
        self.assertEqual(accounts[self.admin.pk].access_status_code, "active")
        self.assertEqual(accounts[self.invitee.pk].access_status_code, "pending")
        self.assertEqual(accounts[required.pk].access_status_code, "required")
        self.assertTrue(accounts[self.invitee.pk].can_receive_invitation)
        self.assertFalse(accounts[self.admin.pk].can_receive_invitation)
        self.assertContains(response, "user-scope-summary")

    def test_data_repair_is_idempotent_and_preserves_mandatory_change_and_pending_invites(self):
        self.admin.last_login = timezone.now()
        self.admin.save(update_fields=["last_login"])
        legacy = UserSecurityProfile.objects.create(user=self.admin, change_reason="initial", must_change_password=True)
        issue_access_token(user=self.admin, purpose=AccessToken.Purpose.ACTIVATION)
        original_password = self.admin.password
        migration = import_module("aplicativos.autenticacao.migrations.0002_reconcile_legacy_security_profiles")
        editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))
        for _ in range(2):
            migration.reconcile_legacy_profiles(self.User._meta.apps, editor)
        legacy.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertEqual(legacy.first_activation_completed_at, self.admin.date_joined)
        self.assertTrue(legacy.must_change_password)
        self.assertIsNone(legacy.password_changed_at)
        self.assertEqual(self.admin.password, original_password)
        self.assertIsNone(self.invitee.security_profile.first_activation_completed_at)
