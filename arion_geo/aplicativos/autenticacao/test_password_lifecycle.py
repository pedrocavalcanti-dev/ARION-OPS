from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.configuracoes.access_control import GROUP_ANALYST, GROUP_SYSTEM_ADMIN, is_system_admin
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import ExternalAPIIntegration
from aplicativos.integracoes.clients.brevo_smtp import SMTPResult

from .forms import PasswordSetForm
from .models import AccessToken, PasswordPolicy, UserSecurityProfile, VerificationChallenge
from .security import (
    OTP_MAX_ATTEMPTS,
    apply_access_token_password,
    apply_verified_password_change,
    issue_access_token,
    password_access_status,
    password_change_required,
    send_password_verification_code,
    valid_access_token,
)


GOOD_PASSWORD = "Aurora-Violet-Garden-Forest-2026!"


class PasswordLifecycleTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.now = timezone.now()
        self.group = Group.objects.get(name=GROUP_ANALYST)
        self.user = self.User.objects.create_user(
            username="operador-lifecycle", password="Legacy-Operator-Password-2024!",
            email="operador@example.test",
        )
        self.user.groups.set([self.group])
        self.profile = UserSecurityProfile.objects.create(
            user=self.user,
            first_activation_completed_at=self.now - timedelta(days=10),
            password_changed_at=self.now - timedelta(days=10),
        )
        self.policy = PasswordPolicy.objects.create(pk=1, active=False, max_age_days=90)
        self.admin = self.User.objects.create_user(
            username="admin-lifecycle", password="Legacy-Admin-Password-2024!",
            email="admin@example.test",
        )
        self.admin.groups.set([Group.objects.get(name=GROUP_SYSTEM_ADMIN)])
        self.analyst = self.user

    def make_pending(self, user=None, *, reason=UserSecurityProfile.ChangeReason.INITIAL):
        user = user or self.user
        profile, _ = UserSecurityProfile.objects.get_or_create(user=user)
        profile.first_activation_completed_at = profile.first_activation_completed_at or self.now - timedelta(days=2)
        profile.must_change_password = True
        profile.change_reason = reason
        profile.save()
        return profile

    def make_invitee(self, username="invitee", email="invitee@example.test"):
        invitee = self.User.objects.create_user(username=username, password=None, email=email)
        UserSecurityProfile.objects.create(
            user=invitee, change_reason=UserSecurityProfile.ChangeReason.INVITATION,
        )
        return invitee

    def activate_policy(self):
        self.policy.active = True
        self.policy.activated_at = self.now
        self.policy.save()

    def test_user_creation_form_stores_no_password_and_requires_email_and_group(self):
        self.client.force_login(self.admin)
        get_response = self.client.get(reverse("configuracoes:user-create"))
        self.assertNotContains(get_response, "password1")
        response = self.client.post(reverse("configuracoes:user-create"), {
            "username": "invitee", "first_name": "Nina", "email": "nina@example.test",
            "group": self.group.pk,
        })
        self.assertEqual(response.status_code, 302)
        invitee = self.User.objects.get(username="invitee")
        self.assertFalse(invitee.has_usable_password())
        self.assertEqual(list(invitee.groups.all()), [self.group])
        self.assertFalse(invitee.access_tokens.exists())
        self.assertIsNone(invitee.security_profile.first_activation_completed_at)

    def test_user_creation_rejects_missing_email_or_group(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("configuracoes:user-create"), {"username": "no-email", "group": self.group.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.User.objects.filter(username="no-email").exists())
        response = self.client.post(reverse("configuracoes:user-create"), {"username": "no-group", "email": "g@example.test"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.User.objects.filter(username="no-group").exists())

    def test_invitation_link_is_shown_once_and_expires_after_48_hours(self):
        invitee = self.make_invitee()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("configuracoes:user-invitation-issue", args=(invitee.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Copiar")
        link = response.context["access_url"]
        self.assertTrue(link.startswith(settings.ARION_PUBLIC_BASE_URL + "/ativacao/#"))
        raw = link.split("#", 1)[1]
        token = invitee.access_tokens.get(purpose=AccessToken.Purpose.ACTIVATION)
        self.assertLessEqual(token.expires_at - token.created_at, timedelta(hours=48))
        self.assertGreater(token.expires_at - token.created_at, timedelta(hours=48) - timedelta(seconds=2))
        self.assertNotEqual(token.token_hash, raw)
        self.assertNotIn("access_link_once", self.client.session)
        detail_page = self.client.get(reverse("configuracoes:user-detail", args=(invitee.pk,)))
        self.assertNotContains(detail_page, raw)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_new_invitation_revokes_previous_token_and_revoke_endpoint_is_authorized(self):
        invitee = self.make_invitee()
        first, _ = issue_access_token(user=invitee, purpose=AccessToken.Purpose.ACTIVATION, actor=self.admin)
        self.client.force_login(self.admin)
        issue_response = self.client.post(reverse("configuracoes:user-invitation-issue", args=(invitee.pk,)))
        self.assertEqual(issue_response.status_code, 200)
        first.refresh_from_db()
        self.assertIsNotNone(first.revoked_at)
        latest = invitee.access_tokens.filter(purpose=AccessToken.Purpose.ACTIVATION).first()
        response = self.client.post(reverse("configuracoes:user-invitation-revoke", args=(invitee.pk,)))
        self.assertEqual(response.status_code, 302)
        latest.refresh_from_db()
        self.assertIsNotNone(latest.revoked_at)

    def test_invitation_cannot_be_issued_to_inactive_user(self):
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))
        self.client.force_login(self.admin)
        self.client.post(reverse("configuracoes:user-invitation-issue", args=(self.user.pk,)))
        self.assertFalse(self.user.access_tokens.exists())

    def test_activation_sets_django_password_and_consumes_token_once(self):
        invitee = self.User.objects.create_user(username="activation-new", email="new@example.test", password=None)
        profile = UserSecurityProfile.objects.create(user=invitee, change_reason=UserSecurityProfile.ChangeReason.INVITATION)
        token, link = issue_access_token(user=invitee, purpose=AccessToken.Purpose.ACTIVATION, actor=self.admin)
        raw = link.split("#", 1)[1]
        page = self.client.get(reverse("autenticacao:activate"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["Referrer-Policy"], "no-referrer")
        self.assertNotContains(page, raw)
        response = self.client.post(reverse("autenticacao:activate"), {
            "token": raw, "new_password1": GOOD_PASSWORD, "new_password2": GOOD_PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Senha definida com sucesso")
        invitee.refresh_from_db()
        profile.refresh_from_db()
        token.refresh_from_db()
        self.assertTrue(invitee.check_password(GOOD_PASSWORD))
        self.assertTrue(profile.first_activation_completed_at)
        self.assertEqual(profile.password_changed_at, profile.first_activation_completed_at)
        self.assertIsNotNone(token.consumed_at)
        self.assertFalse(profile.must_change_password)
        actions = set(AuditEvent.objects.filter(object_id=str(invitee.pk)).values_list("action", flat=True))
        self.assertIn("ACCOUNT_ACTIVATED", actions)
        self.assertIn("ACCESS_INVITATION_CONSUMED", actions)
        self.assertNotIn(raw, str(list(AuditEvent.objects.values_list("metadata", flat=True))))
        second = self.client.post(reverse("autenticacao:activate"), {
            "token": raw, "new_password1": GOOD_PASSWORD, "new_password2": GOOD_PASSWORD,
        })
        self.assertEqual(second.status_code, 400)

    def test_invalid_password_submission_keeps_link_and_form_available_for_correction(self):
        invitee = self.make_invitee("activation-correction", "correction@example.test")
        _, link = issue_access_token(user=invitee, purpose=AccessToken.Purpose.ACTIVATION, actor=self.admin)
        raw = link.split("#", 1)[1]
        response = self.client.post(reverse("autenticacao:activate"), {
            "token": raw, "new_password1": "short", "new_password2": "different",
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, f'name="token" value="{raw}"', status_code=400)
        self.assertContains(response, "data-password-form", status_code=400)
        self.assertContains(response, "data-link-token", status_code=400)

    def test_expired_and_revoked_links_cannot_be_used(self):
        expired, expired_url = issue_access_token(
            user=self.user, purpose=AccessToken.Purpose.PASSWORD_RESET,
            actor=self.admin, now=self.now - timedelta(hours=49),
        )
        self.assertIsNone(valid_access_token(expired_url.split("#", 1)[1], AccessToken.Purpose.PASSWORD_RESET))
        active, active_url = issue_access_token(user=self.user, purpose=AccessToken.Purpose.PASSWORD_RESET, actor=self.admin)
        active.revoked_at = self.now
        active.save(update_fields=("revoked_at",))
        self.assertIsNone(valid_access_token(active_url.split("#", 1)[1], AccessToken.Purpose.PASSWORD_RESET))

    def test_inactive_account_cannot_be_activated_by_a_valid_link(self):
        invitee = self.User.objects.create_user(username="inactive-invite", email="inactive@example.test", password=None, is_active=False)
        UserSecurityProfile.objects.create(user=invitee)
        _, link = issue_access_token(user=invitee, purpose=AccessToken.Purpose.ACTIVATION)
        response = self.client.post(reverse("autenticacao:activate"), {
            "token": link.split("#", 1)[1], "new_password1": GOOD_PASSWORD, "new_password2": GOOD_PASSWORD,
        })
        self.assertEqual(response.status_code, 400)
        invitee.refresh_from_db()
        self.assertFalse(invitee.is_active)
        self.assertFalse(invitee.has_usable_password())

    def test_access_links_use_canonical_host_not_request_host(self):
        invitee = self.make_invitee()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("configuracoes:user-invitation-issue", args=(invitee.pk,)),
            HTTP_HOST="localhost", HTTP_X_FORWARDED_HOST="attacker.invalid",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["access_url"].startswith(settings.ARION_PUBLIC_BASE_URL))
        self.assertNotContains(response, "attacker.invalid")

    def test_administrator_reset_preserves_groups_and_scope(self):
        scope, _ = GroupDataScope.objects.get_or_create(group=self.group)
        scope.mode = GroupDataScope.Mode.ALL
        scope.save()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("configuracoes:user-password-reset-issue", args=(self.user.pk,)))
        self.assertEqual(response.status_code, 200)
        raw = response.context["access_url"].split("#", 1)[1]
        response = self.client.post(reverse("autenticacao:password-reset"), {
            "token": raw, "new_password1": GOOD_PASSWORD, "new_password2": GOOD_PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(GOOD_PASSWORD))
        self.assertEqual(list(self.user.groups.all()), [self.group])
        self.assertEqual(self.group.data_scope.mode, GroupDataScope.Mode.ALL)

    def test_admin_link_cannot_bypass_first_activation(self):
        invitee = self.User.objects.create_user(username="not-activated", email="new@example.test", password=None)
        UserSecurityProfile.objects.create(user=invitee)
        _, link = issue_access_token(user=invitee, purpose=AccessToken.Purpose.PASSWORD_RESET, actor=self.admin)
        self.assertIsNone(apply_access_token_password(
            raw_token=link.split("#", 1)[1], purpose=AccessToken.Purpose.PASSWORD_RESET, new_password=GOOD_PASSWORD,
        ))

    def test_password_policy_requires_12_characters_common_password_check_and_no_immediate_reuse(self):
        short = PasswordSetForm(self.user, {"new_password1": "short", "new_password2": "short"})
        self.assertFalse(short.is_valid())
        common = PasswordSetForm(self.user, {"new_password1": "q1w2e3r4t5y6", "new_password2": "q1w2e3r4t5y6"})
        self.assertFalse(common.is_valid())
        reused = PasswordSetForm(self.user, {
            "new_password1": "Legacy-Operator-Password-2024!",
            "new_password2": "Legacy-Operator-Password-2024!",
        })
        self.assertFalse(reused.is_valid())
        valid = PasswordSetForm(self.user, {"new_password1": GOOD_PASSWORD, "new_password2": GOOD_PASSWORD})
        self.assertTrue(valid.is_valid(), valid.errors)

    def test_policy_activation_blocks_old_sessions_for_modules_admin_and_apis(self):
        self.make_pending()
        self.activate_policy()
        self.client.force_login(self.user)
        response = self.client.get(reverse("painel:index"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("autenticacao:required-password"))
        api = self.client.get("/api/preferences/theme/")
        self.assertEqual(api.status_code, 403)
        self.assertTrue(api["Content-Type"].startswith("application/json"))
        self.assertEqual(api.json()["error"]["code"], "PASSWORD_CHANGE_REQUIRED")
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_existing_admins_and_superusers_are_not_exempt(self):
        self.admin.is_superuser = True
        self.admin.is_staff = True
        self.admin.save(update_fields=("is_superuser", "is_staff"))
        profile = UserSecurityProfile.objects.create(
            user=self.admin, must_change_password=True,
            change_reason=UserSecurityProfile.ChangeReason.INITIAL,
            first_activation_completed_at=self.admin.date_joined,
        )
        self.activate_policy()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("painel:index"))["Location"], reverse("autenticacao:required-password"))
        self.assertTrue(password_change_required(self.admin))

    def test_login_with_old_credentials_does_not_release_pending_user(self):
        self.make_pending()
        self.activate_policy()
        response = self.client.post(reverse("autenticacao:login"), {
            "username": self.user.username, "password": "Legacy-Operator-Password-2024!",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], reverse("autenticacao:required-password"))
        self.assertContains(response, "Atualização obrigatória de senha")

    def test_password_expires_exactly_at_90_days(self):
        self.activate_policy()
        profile = self.user.security_profile
        profile.must_change_password = False
        profile.password_changed_at = self.now - timedelta(days=90) + timedelta(microseconds=1)
        profile.save()
        self.assertFalse(password_change_required(self.user, now=self.now))
        self.assertTrue(password_change_required(self.user, now=self.now + timedelta(microseconds=1)))

    def test_policy_flag_stages_initial_migration_without_blocking_until_activated(self):
        self.make_pending(reason=UserSecurityProfile.ChangeReason.INITIAL)
        self.policy.active = False
        self.policy.save()
        self.assertFalse(password_change_required(self.user, now=self.now))
        profile = self.make_pending(reason=UserSecurityProfile.ChangeReason.ADMIN)
        self.assertTrue(password_change_required(self.user, now=self.now))
        profile.refresh_from_db()

    def test_service_account_exclusion_is_respected_even_when_password_is_old(self):
        self.activate_policy()
        profile = self.user.security_profile
        profile.is_service_account = True
        profile.must_change_password = True
        profile.password_changed_at = self.now - timedelta(days=200)
        profile.save()
        self.assertFalse(password_change_required(self.user, now=self.now))

    def test_brevo_verification_send_is_rate_limited_and_failures_do_not_leave_valid_code(self):
        with patch("aplicativos.autenticacao.security.send_verification_code", return_value=True) as send:
            sent, _ = send_password_verification_code(self.user, now=self.now)
            self.assertTrue(sent)
            code = send.call_args.args[1]
            challenge = VerificationChallenge.objects.get(user=self.user)
            self.assertNotEqual(challenge.code_hash, code)
            self.assertEqual(challenge.expires_at - self.now, timedelta(minutes=10))
            self.assertEqual(send_password_verification_code(self.user, now=self.now + timedelta(seconds=30))[1], "aguarde")
        with patch("aplicativos.autenticacao.security.send_verification_code", return_value=False):
            sent, result = send_password_verification_code(self.user, now=self.now + timedelta(minutes=2))
            self.assertFalse(sent)
            self.assertEqual(result, "indisponivel")
        challenge = VerificationChallenge.objects.get(user=self.user)
        self.assertEqual(challenge.code_hash, "")

    def test_brevo_smtp_delivery_uses_security_recipient_without_quality_copies(self):
        integration = ExternalAPIIntegration.objects.create(
            provider=ExternalAPIIntegration.Provider.BREVO, name="Brevo", enabled=True,
            allow_real_sends=True, smtp_secret_ciphertext="test-ciphertext-placeholder",
            config={"send_method": "smtp", "smtp_login": "smtp-login", "sender_name": "ARION OPS",
                    "sender_email": "security@example.test", "quality_recipient": "ignored@example.test",
                    "quality_cc": "copy@example.test", "reply_to": "reply@example.test"},
        )
        with patch("aplicativos.integracoes.services.external_apis.decrypt_external_api_smtp_secret", return_value="smtp-password"), \
             patch("aplicativos.integracoes.clients.brevo_smtp.send_brevo_smtp", return_value=SMTPResult(True, "accepted")) as send:
            from .security import send_verification_code

            self.assertTrue(send_verification_code(self.user, "01234567"))
        self.assertEqual(send.call_args.kwargs["config"]["quality_recipient"], self.user.email)
        self.assertNotIn("quality_cc", send.call_args.kwargs["config"])
        self.assertNotIn("reply_to", send.call_args.kwargs["config"])
        self.assertEqual(integration.provider, ExternalAPIIntegration.Provider.BREVO)

    def test_otp_send_limit_is_five_per_hour(self):
        with patch("aplicativos.autenticacao.security.send_verification_code", return_value=True):
            for index in range(5):
                sent, result = send_password_verification_code(
                    self.user, now=self.now + timedelta(seconds=61 * index),
                )
                self.assertTrue(sent, result)
            sent, result = send_password_verification_code(self.user, now=self.now + timedelta(seconds=61 * 5))
        self.assertFalse(sent)
        self.assertEqual(result, "limite")

    def test_otp_is_single_use_and_limits_invalid_attempts(self):
        code = "12345678"
        VerificationChallenge.objects.create(
            user=self.user, code_hash=make_password(code),
            expires_at=self.now + timedelta(minutes=10),
        )
        for _ in range(OTP_MAX_ATTEMPTS):
            changed, result = apply_verified_password_change(
                self.user, code="00000000", new_password=GOOD_PASSWORD, now=self.now,
            )
            self.assertFalse(changed)
            self.assertEqual(result, "invalido")
        self.assertEqual(apply_verified_password_change(
            self.user, code=code, new_password=GOOD_PASSWORD, now=self.now,
        ), (False, "limite"))
        self.assertFalse(self.user.check_password(GOOD_PASSWORD))

    def test_expired_otp_is_rejected(self):
        VerificationChallenge.objects.create(
            user=self.user, code_hash=make_password("12345678"),
            expires_at=self.now - timedelta(seconds=1),
        )
        self.assertEqual(apply_verified_password_change(
            self.user, code="12345678", new_password=GOOD_PASSWORD, now=self.now,
        ), (False, "expirado"))

    def test_valid_otp_changes_password_preserves_access_and_ends_old_session(self):
        self.make_pending()
        self.activate_policy()
        scope, _ = GroupDataScope.objects.get_or_create(group=self.group)
        scope.mode = GroupDataScope.Mode.ALL
        scope.save()
        code = "12345678"
        VerificationChallenge.objects.create(
            user=self.user, code_hash=make_password(code), expires_at=self.now + timedelta(minutes=10),
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse("autenticacao:required-password"), {
            "action": "change_password", "verification_code": code,
            "new_password1": GOOD_PASSWORD, "new_password2": GOOD_PASSWORD,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("autenticacao:login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.user.refresh_from_db()
        profile = self.user.security_profile
        self.assertTrue(self.user.check_password(GOOD_PASSWORD))
        self.assertFalse(profile.must_change_password)
        self.assertLessEqual((timezone.now() - profile.password_changed_at).total_seconds(), 5)
        self.assertEqual(list(self.user.groups.all()), [self.group])
        self.assertEqual(self.group.data_scope.mode, GroupDataScope.Mode.ALL)
        self.assertTrue(VerificationChallenge.objects.get(user=self.user).consumed_at)
        audit = list(AuditEvent.objects.filter(object_id=str(self.user.pk)).values_list("action", "metadata"))
        self.assertIn("PASSWORD_VERIFICATION_CODE_VALIDATED", {row[0] for row in audit})
        self.assertNotIn(code, str(audit))

    def test_management_activation_is_dry_run_idempotent_and_keeps_admin_in_scope(self):
        service = self.User.objects.create_user(username="svc_worker", password="service-pass")
        service_profile = UserSecurityProfile.objects.create(user=service)
        UserSecurityProfile.objects.create(
            user=self.admin, first_activation_completed_at=self.admin.date_joined,
        )
        self.client.logout()
        call_command("ativar_politica_senhas", exclude_usernames=["svc_worker"], stdout=None)
        self.policy.refresh_from_db()
        self.assertFalse(self.policy.active)
        self.assertFalse(UserSecurityProfile.objects.get(user=self.admin).must_change_password)
        call_command(
            "ativar_politica_senhas", confirm=True, actor_username=self.admin.username,
            exclude_usernames=["svc_worker"], max_age_days=90,
        )
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.active)
        activated_at = self.policy.activated_at
        self.assertEqual(self.policy.max_age_days, 90)
        self.assertTrue(UserSecurityProfile.objects.get(user=self.admin).must_change_password)
        service_profile.refresh_from_db()
        self.assertTrue(service_profile.is_service_account)
        self.assertFalse(service_profile.must_change_password)
        rotated_profile = UserSecurityProfile.objects.get(user=self.user)
        rotated_profile.must_change_password = False
        rotated_profile.change_reason = ""
        rotated_profile.password_changed_at = activated_at + timedelta(days=1)
        rotated_profile.save()
        call_command("ativar_politica_senhas", confirm=True, actor_username=self.admin.username)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.activated_at, activated_at)
        self.assertEqual(self.policy.max_age_days, 90)
        rotated_profile.refresh_from_db()
        self.assertFalse(rotated_profile.must_change_password)

    def test_management_group_has_read_only_matrix_and_restrictive_scope(self):
        management = Group.objects.get(name="Gestão")
        self.assertFalse(management.permissions.filter(codename__in=(
            "manage_users", "create_users", "manage_groups", "manage_data_scopes",
            "generate_access_invites", "generate_password_reset_links", "manage_integrations",
            "manage_operational_tickets", "manage_quality_orders",
        )).exists())
        self.assertTrue(management.permissions.filter(codename="view_dashboard").exists())
        self.assertTrue(management.permissions.filter(codename="view_reports").exists())
        executive = self.User.objects.create_user(username="gestao-user", password="non-secret-password")
        executive.groups.set([management])
        self.assertFalse(executive.is_staff)
        self.assertFalse(executive.is_superuser)
        self.assertEqual(management.data_scope.mode, GroupDataScope.Mode.CUSTOM)

    def test_security_actions_require_explicit_django_permissions(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("configuracoes:user-invitation-issue", args=(self.user.pk,)))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.user.access_tokens.exists())
        response = self.client.post(reverse("configuracoes:user-password-reset-issue", args=(self.user.pk,)))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("configuracoes:user-require-password-change", args=(self.user.pk,)))
        self.assertEqual(response.status_code, 403)

    def test_existing_user_data_migration_is_idempotent(self):
        from importlib import import_module
        from types import SimpleNamespace

        migration = import_module("aplicativos.autenticacao.migrations.0001_initial")
        user = self.User.objects.create_user(username="legacy-migration", password="Legacy-Migration-Password-2024!")
        apps = self.User._meta.apps
        editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))
        migration.mark_existing_accounts_for_initial_password_change(apps, editor)
        profile = UserSecurityProfile.objects.get(user=user)
        self.assertTrue(profile.must_change_password)
        self.assertEqual(profile.change_reason, UserSecurityProfile.ChangeReason.INITIAL)
        original_created_at = profile.created_at
        migration.mark_existing_accounts_for_initial_password_change(apps, editor)
        profile.refresh_from_db()
        self.assertEqual(profile.created_at, original_created_at)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Legacy-Migration-Password-2024!"))

    def test_local_admin_recovery_confirms_identity_and_audits_one_time_link(self):
        profile, _ = UserSecurityProfile.objects.get_or_create(user=self.admin)
        profile.first_activation_completed_at = self.admin.date_joined
        profile.save(update_fields=("first_activation_completed_at", "updated_at"))
        args = {
            "username": self.admin.username,
            "actor_username": self.admin.username,
        }
        with self.assertRaises(CommandError):
            call_command("emitir_link_redefinicao_admin", **args, confirm_email="wrong@example.test")
        self.assertFalse(self.admin.access_tokens.exists())

        output = StringIO()
        call_command("emitir_link_redefinicao_admin", **args, confirm_email=self.admin.email, stdout=output)
        token = self.admin.access_tokens.get(purpose=AccessToken.Purpose.PASSWORD_RESET)
        raw_token = output.getvalue().split("#", 1)[1].splitlines()[0]
        self.assertEqual(token.created_by, self.admin)
        self.assertNotIn(raw_token, token.token_hash)
        event = AuditEvent.objects.get(action="ADMIN_PASSWORD_RESET_LINK_ISSUED", object_id=str(self.admin.pk))
        self.assertEqual(event.user, self.admin)
        self.assertTrue(event.metadata["local_recovery"])

    def test_user_access_status_distinguishes_inactive_pending_expired_and_active(self):
        self.assertEqual(password_access_status(self.user)[0], "active")
        profile = self.user.security_profile
        profile.first_activation_completed_at = None
        profile.save()
        self.assertEqual(password_access_status(self.user)[0], "pending")
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))
        self.assertEqual(password_access_status(self.user)[0], "inactive")

    def test_old_link_is_not_recoverable_from_database(self):
        _, url = issue_access_token(user=self.user, purpose=AccessToken.Purpose.PASSWORD_RESET, actor=self.admin)
        raw = url.split("#", 1)[1]
        token = AccessToken.objects.get(user=self.user, purpose=AccessToken.Purpose.PASSWORD_RESET)
        self.assertNotIn(raw, token.token_hash)
        self.assertNotIn(raw, str(token.__dict__))
