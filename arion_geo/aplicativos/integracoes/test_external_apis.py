from unittest.mock import MagicMock, patch
from io import BytesIO
import json
import smtplib
import ssl
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from aplicativos.auditoria.models import AuditEvent

from .clients.brevo import BrevoSendingDisabled, BrevoTransactionalClient
from .clients.brevo_smtp import SMTPResult, send_brevo_smtp, test_brevo_smtp_auth
from .models import ExternalAPIHealthCheck, ExternalAPIIntegration
from .services.external_apis import get_mapbox_public_token, get_mapbox_runtime_state
from .services.secrets import TestSecretProtector


class ExternalAPITestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="external-api-admin", email="admin@example.com", password="safe-password-1",
        )
        self.client.force_login(self.user)
        self.protector = TestSecretProtector()


class ExternalAPIConfigurationTests(ExternalAPITestCase):
    def test_brevo_page_has_method_tabs_and_compact_test_actions(self):
        response = self.client.get(reverse("configuracoes:integracoes:brevo"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-brevo-method="smtp"')
        self.assertContains(response, 'data-brevo-method="api_rest"')
        self.assertContains(response, "Identidade e destinatários")
        self.assertContains(response, "E-mail em cópia (Cc)")
        self.assertContains(response, "brevo-configuration-grid")
        self.assertContains(response, "integration-field--stacked")

    def test_legacy_health_check_without_method_is_not_inferred(self):
        integration = ExternalAPIIntegration.objects.create(
            provider=ExternalAPIIntegration.Provider.BREVO, name="Brevo",
        )
        ExternalAPIHealthCheck.objects.create(
            integration=integration, status=ExternalAPIHealthCheck.Status.ERROR,
            message="Teste antigo sem método registrado.",
        )
        response = self.client.get(reverse("configuracoes:integracoes:brevo"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Não identificado")

    def test_rest_payload_includes_cc_separately_from_reply_to(self):
        payload = BrevoTransactionalClient.build_transactional_payload(
            sender_email="from@example.com", sender_name="ARION OPS",
            to_email="to@example.com", cc_email="copy@example.com",
            reply_to="reply@example.com", subject="TQ", text_content="body",
        )
        self.assertEqual(payload["to"], [{"email": "to@example.com"}])
        self.assertEqual(payload["cc"], [{"email": "copy@example.com"}])
        self.assertEqual(payload["replyTo"], {"email": "reply@example.com"})

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_brevo_key_is_encrypted_and_never_rendered_or_audited(self, get_protector):
        get_protector.return_value = self.protector
        secret = "brevo-private-key-test-value"
        response = self.client.post(reverse("configuracoes:integracoes:brevo-save"), {
            "send_method": "api_rest", "api_key": secret, "sender_name": "ARION OPS", "sender_email": "noc@example.com",
            "quality_recipient": "quality@example.com", "reply_to": "", "enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        integration = ExternalAPIIntegration.objects.get(provider="brevo")
        self.assertNotEqual(integration.secret_ciphertext, secret)
        self.assertEqual(self.protector.unprotect(integration.secret_ciphertext), secret)
        self.assertFalse(integration.allow_real_sends)
        rendered = self.client.get(reverse("configuracoes:integracoes:brevo"))
        self.assertEqual(rendered.status_code, 200)
        self.assertNotContains(rendered, secret)
        self.assertNotContains(rendered, integration.secret_ciphertext)
        audit_text = " ".join(
            AuditEvent.objects.filter(module="APIs Externas").values_list("description", flat=True)
        ) + str(list(AuditEvent.objects.filter(module="APIs Externas").values_list("metadata", flat=True)))
        self.assertNotIn(secret, audit_text)

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_smtp_key_is_encrypted_separately_and_never_rendered(self, get_protector):
        get_protector.return_value = self.protector
        secret = " fresh-smtp-key-for-test "
        response = self.client.post(reverse("configuracoes:integracoes:brevo-save"), {
            "send_method": "smtp", "smtp_login": "login@example.com", "smtp_key": secret,
            "api_key": "", "sender_name": "ARION OPS", "sender_email": "noc@example.com",
            "quality_recipient": "testbox@example.com", "reply_to": "", "enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        integration = ExternalAPIIntegration.objects.get(provider="brevo")
        self.assertFalse(integration.secret_ciphertext)
        self.assertNotEqual(integration.smtp_secret_ciphertext, secret)
        self.assertEqual(self.protector.unprotect(integration.smtp_secret_ciphertext), secret)
        self.assertEqual(integration.config["smtp_host"], "smtp-relay.brevo.com")
        page = self.client.get(reverse("configuracoes:integracoes:brevo"))
        self.assertNotContains(page, secret)
        self.assertNotContains(page, integration.smtp_secret_ciphertext)

    def test_smtp_test_only_authenticates_and_records_sanitized_result(self):
        secret = "private-smtp-test-key"
        integration = ExternalAPIIntegration.objects.create(
            provider="brevo", name="Brevo", smtp_secret_ciphertext=self.protector.protect(secret),
            config={"smtp_login": "relay-login"},
        )
        with patch("aplicativos.integracoes.views.decrypt_external_api_smtp_secret", return_value=secret), \
             patch("aplicativos.integracoes.views.test_brevo_smtp_auth", return_value=SMTPResult(True, "Autenticação SMTP aceita; nenhum e-mail foi enviado.", 24)) as authenticate, \
             patch("aplicativos.integracoes.clients.brevo_smtp.send_brevo_smtp") as send:
            response = self.client.post(reverse("configuracoes:integracoes:brevo-smtp-test"))
        self.assertEqual(response.status_code, 302)
        authenticate.assert_called_once_with("relay-login", secret)
        send.assert_not_called()
        integration.refresh_from_db()
        self.assertEqual(integration.health, ExternalAPIIntegration.Health.AVAILABLE)
        self.assertEqual(ExternalAPIHealthCheck.objects.get(integration=integration).latency_ms, 24)
        audit = AuditEvent.objects.get(action="BREVO_SMTP_AUTH_TESTED", object_id=str(integration.pk))
        self.assertEqual(audit.metadata["method"], "smtp")
        self.assertNotIn(secret, str(audit.metadata))

    def test_smtp_dpapi_credential_is_recovered_from_its_separate_field(self):
        from .services.external_apis import decrypt_external_api_smtp_secret

        secret = "test-only-smtp-secret"
        integration = ExternalAPIIntegration.objects.create(
            provider="brevo", name="Brevo", smtp_secret_ciphertext=self.protector.protect(secret),
        )
        with patch("aplicativos.integracoes.services.external_apis.get_secret_protector", return_value=self.protector):
            recovered = decrypt_external_api_smtp_secret(integration)
        self.assertEqual(recovered, secret)
        self.assertNotEqual(integration.smtp_secret_ciphertext, integration.secret_ciphertext)

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_saving_blank_smtp_key_preserves_both_existing_secrets(self, get_protector):
        get_protector.return_value = self.protector
        integration = ExternalAPIIntegration.objects.create(
            provider="brevo", name="Brevo", secret_ciphertext=self.protector.protect("rest-test-key"),
            smtp_secret_ciphertext=self.protector.protect("smtp-test-key"), enabled=True,
            config={"send_method": "smtp", "smtp_login": "relay-login", "sender_name": "ARION OPS",
                    "sender_email": "noc@example.com", "quality_recipient": "testbox@example.com", "reply_to": ""},
        )
        previous_rest, previous_smtp = integration.secret_ciphertext, integration.smtp_secret_ciphertext
        response = self.client.post(reverse("configuracoes:integracoes:brevo-save"), {
            "send_method": "smtp", "smtp_login": "relay-login", "smtp_key": "", "api_key": "",
            "sender_name": "ARION OPS", "sender_email": "noc@example.com",
            "quality_recipient": "testbox@example.com", "reply_to": "", "enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        integration.refresh_from_db()
        self.assertEqual(integration.secret_ciphertext, previous_rest)
        self.assertEqual(integration.smtp_secret_ciphertext, previous_smtp)
        self.assertEqual(integration.config["send_method"], "smtp")

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_admin_must_explicitly_authorize_real_tq_sends(self, get_protector):
        get_protector.return_value = self.protector
        url = reverse("configuracoes:integracoes:brevo-save")
        payload = {
            "send_method": "api_rest", "api_key": "brevo-private-key-test-value", "sender_name": "ARION OPS",
            "sender_email": "noc@example.com", "quality_recipient": "quality@example.com",
            "reply_to": "", "enabled": "on",
        }
        rejected = self.client.post(url, {**payload, "quality_recipient_validated": "on", "allow_real_sends": "on"})
        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(ExternalAPIIntegration.objects.filter(provider="brevo").exists())
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        integration = ExternalAPIIntegration.objects.get(provider="brevo")
        self.assertTrue(integration.enabled)
        self.assertFalse(integration.allow_real_sends)
        response = self.client.post(url, {
            **payload, "api_key": "", "quality_recipient_validated": "on", "allow_real_sends": "on",
        })
        self.assertEqual(response.status_code, 302)
        integration.refresh_from_db()
        self.assertTrue(integration.allow_real_sends)
        self.assertTrue(AuditEvent.objects.filter(action="BREVO_REAL_SEND_AUTHORIZATION_CHANGED").exists())

    def test_unprivileged_user_cannot_manage_or_test_apis(self):
        viewer = get_user_model().objects.create_user(username="api-viewer", password="safe-password-2")
        content_type = ContentType.objects.get(app_label="configuracoes", model="groupdatascope")
        view_permission = Permission.objects.get(content_type=content_type, codename="view_integrations")
        group = Group.objects.create(name="API status viewer")
        group.permissions.add(view_permission)
        viewer.groups.add(group)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("configuracoes:integracoes:brevo")).status_code, 200)
        self.assertEqual(self.client.post(reverse("configuracoes:integracoes:brevo-save"), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse("configuracoes:integracoes:brevo-test")).status_code, 403)

    def test_integrations_home_lists_external_api_cards(self):
        response = self.client.get(reverse("configuracoes:integrations"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "APIs externas")
        self.assertContains(response, "Brevo")
        self.assertContains(response, "Mapbox")
        self.assertContains(response, "Abrir integra&ccedil;&atilde;o")

    def test_external_api_admin_pages_return_http_200_for_superuser(self):
        paths = (
            "/configuracoes/administracao/sistema/",
            "/configuracoes/administracao/integracoes/",
            reverse("configuracoes:integracoes:brevo"),
            reverse("configuracoes:integracoes:mapbox"),
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_brevo_connection_test_uses_aggregated_transactional_report_without_storing_stats(self):
        protector = self.protector
        integration = ExternalAPIIntegration.objects.create(
            provider="brevo", name="Brevo", secret_ciphertext=protector.protect(" \tapi-key-value\r\n"),
        )
        response = MagicMock()
        response.__enter__.return_value = response
        response.getcode.return_value = 200
        response.read.return_value = b'{"requests":987654,"delivered":987650}'
        with patch("aplicativos.integracoes.services.external_apis.get_secret_protector", return_value=protector), \
             patch("aplicativos.integracoes.clients.brevo._open_url", return_value=response) as open_url:
            result = self.client.post(reverse("configuracoes:integracoes:brevo-test"))
        self.assertEqual(result.status_code, 302)
        integration.refresh_from_db()
        self.assertEqual(integration.health, ExternalAPIIntegration.Health.AVAILABLE)
        check = ExternalAPIHealthCheck.objects.get(integration=integration)
        self.assertIn("HTTP 200", check.message)
        open_url.assert_called_once()
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url,
                         "https://api.brevo.com/v3/smtp/statistics/aggregatedReport?days=1")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Api-key"), "api-key-value")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        response.read.assert_not_called()
        audit = AuditEvent.objects.get(action="EXTERNAL_API_TESTED", object_id=str(integration.pk))
        self.assertEqual(audit.metadata["http_status"], 200)
        rendered = self.client.get(reverse("configuracoes:integracoes:brevo"))
        self.assertContains(rendered, "Validada")
        self.assertContains(rendered, "API de e-mails transacionais autenticada")
        self.assertNotContains(rendered, "987654")
        self.assertNotIn("987654", check.message + str(audit.metadata))

    def test_brevo_auth_failure_is_sanitized(self):
        integration = ExternalAPIIntegration.objects.create(
            provider="brevo", name="Brevo", secret_ciphertext=self.protector.protect("bad-key"),
        )
        failure = HTTPError("https://api.brevo.com/v3/smtp/statistics/aggregatedReport?days=1", 401, "unauthorized", {},
                            BytesIO(b'{"code":"unauthorized","message":"private response detail"}'))
        with patch("aplicativos.integracoes.services.external_apis.get_secret_protector", return_value=self.protector), \
             patch("aplicativos.integracoes.clients.brevo._open_url", side_effect=failure):
            self.client.post(reverse("configuracoes:integracoes:brevo-test"))
        integration.refresh_from_db()
        self.assertEqual(integration.health, ExternalAPIIntegration.Health.ERROR)
        self.assertEqual(integration.last_error,
                         "HTTP 401 — API Key inválida ou não reconhecida. Código Brevo: unauthorized.")
        self.assertNotIn("bad-key", integration.last_error)
        self.assertNotIn("private response detail", integration.last_error)
        check = ExternalAPIHealthCheck.objects.get(integration=integration)
        self.assertEqual(check.message, integration.last_error)
        audit = AuditEvent.objects.get(action="EXTERNAL_API_TESTED", object_id=str(integration.pk))
        self.assertEqual(audit.metadata["http_status"], 401)
        self.assertEqual(audit.metadata["brevo_error_code"], "unauthorized")
        rendered = self.client.get(reverse("configuracoes:integracoes:brevo"))
        self.assertContains(rendered, "HTTP 401")
        self.assertNotContains(rendered, "private response detail")

    def test_brevo_connection_diagnoses_403_and_429_without_exposing_raw_body(self):
        client = BrevoTransactionalClient("test-key")
        cases = (
            (403, {"code": "permission_denied", "message": "restricted details"},
             "HTTP 403 — Permissão insuficiente para o recurso smtp/statistics/aggregatedReport. Código Brevo: permission_denied.",
             "permission_denied"),
            (403, {"code": "unauthorized", "message": "This IP address is not authorized"},
             "HTTP 403 — Acesso bloqueado pela política de IP no recurso smtp/statistics/aggregatedReport. Código Brevo: unauthorized.",
             "unauthorized"),
            (429, {"code": "account_email_private", "message": "private response details"},
             "HTTP 429 — Limite de requisições atingido; tente novamente mais tarde.", ""),
        )
        for status, body, expected_message, expected_code in cases:
            with self.subTest(status=status, code=body["code"]):
                error = HTTPError(
                    "https://api.brevo.com/v3/smtp/statistics/aggregatedReport?days=1", status, "rejected", {},
                    BytesIO(json.dumps(body).encode("utf-8")),
                )
                with patch("aplicativos.integracoes.clients.brevo._open_url", side_effect=error):
                    result = client.test_connection()
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.error_code, expected_code)
                self.assertEqual(result.message, expected_message)
                self.assertNotIn(body["message"], result.message)
                if not expected_code:
                    self.assertNotIn(body["code"], result.message)

    @override_settings(MAPBOX_PUBLIC_TOKEN="pk.legacy-token-value")
    def test_mapbox_prefers_database_and_uses_legacy_env_only_without_database_record(self):
        self.assertEqual(get_mapbox_public_token(), "pk.legacy-token-value")
        legacy_state = get_mapbox_runtime_state()
        self.assertEqual(legacy_state["source"], "environment")
        self.assertTrue(legacy_state["configured"])
        self.assertIsNone(legacy_state["integration"])
        configured = ExternalAPIIntegration.objects.create(
            provider="mapbox", name="Mapbox", enabled=True, config={"public_token": "pk.database-token"},
        )
        self.assertEqual(get_mapbox_public_token(), "pk.database-token")
        database_state = get_mapbox_runtime_state()
        self.assertEqual(database_state["source"], "database")
        self.assertTrue(database_state["configured"])
        self.assertTrue(database_state["enabled"])
        self.assertEqual(database_state["integration"].pk, configured.pk)
        configured.config = {"public_token": "sk.private-token"}
        configured.save(update_fields=("config",))
        self.assertEqual(get_mapbox_public_token(), "")
        invalid_database_state = get_mapbox_runtime_state()
        self.assertEqual(invalid_database_state["source"], "database")
        self.assertFalse(invalid_database_state["configured"])
        self.assertEqual(invalid_database_state["status"], "Erro: token público inválido")
        self.assertEqual(self.client.post(reverse("configuracoes:integracoes:mapbox-save"), {
            "public_token": "sk.never-accept", "enabled": "on",
        }).status_code, 200)
        self.assertNotContains(self.client.get(reverse("configuracoes:integracoes:mapbox")), "sk.private-token")

    @override_settings(MAPBOX_PUBLIC_TOKEN="")
    def test_mapbox_runtime_state_without_database_or_legacy_configuration(self):
        state = get_mapbox_runtime_state()
        self.assertEqual(state, {
            "configured": False,
            "enabled": False,
            "source": "none",
            "status": "Não configurada",
            "integration": None,
        })

    def test_mapbox_save_and_validation_do_not_expose_public_token_in_api_configuration(self):
        token = "pk.public-token-that-must-not-be-rendered-here"
        saved = self.client.post(reverse("configuracoes:integracoes:mapbox-save"), {
            "public_token": token, "enabled": "on",
        })
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(get_mapbox_public_token(), token)
        page = self.client.get(reverse("configuracoes:integracoes:mapbox"))
        self.assertNotContains(page, token)
        self.assertContains(page, "valor oculto")

    def test_authorized_map_page_receives_database_public_token(self):
        map_permission = Permission.objects.get(content_type__app_label="configuracoes", codename="view_map")
        self.user.user_permissions.add(map_permission)
        token = "pk.public-token-for-authorized-map-page"
        ExternalAPIIntegration.objects.create(
            provider="mapbox", name="Mapbox", enabled=True, config={"public_token": token},
        )
        response = self.client.get(reverse("mapa:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-token="{token}"')


class BrevoClientTests(TestCase):
    def test_smtp_connection_test_authenticates_without_mail_commands(self):
        class FakeSMTP:
            def __init__(self):
                self.calls = []

            def ehlo(self):
                self.calls.append("ehlo")

            def starttls(self, *, context):
                self.calls.append("ehlo")  # smtplib.starttls() performs EHLO before upgrading.
                self.calls.append(("starttls", context.check_hostname, context.verify_mode))
                self.calls.append("ehlo")  # smtplib.starttls() renegotiates capabilities after TLS.

            def login(self, username, password):
                self.calls.append(("login", username, password))

            def quit(self):
                self.calls.append("quit")

        server = FakeSMTP()
        with patch("django.core.mail.backends.smtp.smtplib.SMTP", return_value=server):
            result = test_brevo_smtp_auth("relay-login", "relay-secret")
        self.assertTrue(result.ok)
        self.assertEqual(server.calls[0], "ehlo")
        self.assertEqual(server.calls[1][0], "starttls")
        self.assertEqual([call for call in server.calls if call == "ehlo"], ["ehlo", "ehlo"])
        self.assertIn(("starttls", True, 2), server.calls)
        self.assertIn(("login", "relay-login", "relay-secret"), server.calls)
        self.assertEqual(server.calls[-1], "quit")
        self.assertFalse(any("send" in str(call).casefold() for call in server.calls))

    def test_smtp_connection_test_sanitizes_auth_and_starttls_failures(self):
        class FailedBackend:
            def __init__(self, error):
                self.error = error
                self.closed = False
            def open(self): raise self.error
            def close(self): self.closed = True

        auth_backend = FailedBackend(smtplib.SMTPAuthenticationError(535, b"sensitive auth detail"))
        ip_backend = FailedBackend(smtplib.SMTPAuthenticationError(525, b"private IP policy details"))
        starttls_backend = FailedBackend(smtplib.SMTPNotSupportedError("private STARTTLS detail"))
        tls_backend = FailedBackend(ssl.SSLCertVerificationError("private certificate detail"))
        auth = test_brevo_smtp_auth("user", "private", backend_factory=lambda **kwargs: auth_backend)
        ip_block = test_brevo_smtp_auth("user", "private", backend_factory=lambda **kwargs: ip_backend)
        starttls = test_brevo_smtp_auth("user", "private", backend_factory=lambda **kwargs: starttls_backend)
        tls = test_brevo_smtp_auth("user", "private", backend_factory=lambda **kwargs: tls_backend)
        self.assertFalse(auth.ok)
        self.assertIn("SMTP 535", auth.message)
        self.assertEqual(auth.error_code, 535)
        self.assertEqual(auth.exception_class, "SMTPAuthenticationError")
        self.assertNotIn("private", auth.message)
        self.assertIn("SMTP 525", ip_block.message)
        self.assertEqual(ip_block.error_code, 525)
        self.assertNotIn("private IP policy details", ip_block.message)
        self.assertIn("STARTTLS", starttls.message)
        self.assertNotIn("private STARTTLS detail", starttls.message)
        self.assertFalse(tls.ok)
        self.assertIn("certificado TLS", tls.message)
        self.assertEqual(auth_backend.closed, True)
        self.assertEqual(tls_backend.closed, True)

    def test_smtp_send_builds_text_and_html_alternatives_without_real_transport(self):
        message = MagicMock()
        message.send.return_value = 1
        with patch("aplicativos.integracoes.clients.brevo_smtp.get_connection") as get_connection_mock, \
             patch("aplicativos.integracoes.clients.brevo_smtp.EmailMultiAlternatives", return_value=message) as email_class:
            result = send_brevo_smtp(
                username="login", password="secret", config={"sender_name": "ARION", "sender_email": "from@example.com",
                    "quality_recipient": "test@example.com", "reply_to": "reply@example.com"},
                subject="TQ test", text_content="plain body with ARION-QID", html_content="<p>html</p>",
            )
        self.assertTrue(result.ok)
        get_connection_mock.assert_called_once()
        kwargs = email_class.call_args.kwargs
        self.assertEqual(kwargs["body"], "plain body with ARION-QID")
        self.assertEqual(kwargs["to"], ["test@example.com"])
        self.assertEqual(kwargs["reply_to"], ["reply@example.com"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        message.attach_alternative.assert_called_once_with("<p>html</p>", "text/html")
        message.send.assert_called_once_with(fail_silently=False)

    def test_real_send_is_blocked_before_any_network_request(self):
        client = BrevoTransactionalClient("private-key")
        with patch("aplicativos.integracoes.clients.brevo._open_url") as open_url:
            with self.assertRaises(BrevoSendingDisabled):
                client.send_transactional({"subject": "TQ"}, allow_real_sends=False)
        open_url.assert_not_called()

    def test_send_client_parses_message_id_from_mock_only(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.getcode.return_value = 201
        response.read.return_value = b'{"messageId":"<mock-message-id>"}'
        client = BrevoTransactionalClient("private-key")
        payload = client.build_transactional_payload(
            sender_email="noc@example.com", sender_name="ARION", to_email="quality@example.com",
            subject="TQ test", text_content="mock body",
        )
        with patch("aplicativos.integracoes.clients.brevo._open_url", return_value=response) as open_url:
            result = client.send_transactional(payload, allow_real_sends=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.message_id, "<mock-message-id>")
        open_url.assert_called_once()

    def test_send_client_separates_definitive_rejection_from_uncertain_timeout(self):
        client = BrevoTransactionalClient("private-key")
        payload = {"subject": "TQ", "textContent": "mock"}
        with patch("aplicativos.integracoes.clients.brevo._open_url", side_effect=TimeoutError):
            uncertain = client.send_transactional(payload, allow_real_sends=True)
        self.assertFalse(uncertain.ok)
        self.assertTrue(uncertain.uncertain)

        rejected = HTTPError("https://api.brevo.com/v3/smtp/email", 400, "rejected", {}, BytesIO(b"{}"))
        with patch("aplicativos.integracoes.clients.brevo._open_url", side_effect=rejected):
            confirmed = client.send_transactional(payload, allow_real_sends=True)
        self.assertFalse(confirmed.ok)
        self.assertFalse(confirmed.uncertain)
        self.assertEqual(confirmed.status_code, 400)
