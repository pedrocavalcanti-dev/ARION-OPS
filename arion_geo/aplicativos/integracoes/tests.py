from io import StringIO
from unittest.mock import Mock, patch
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from aplicativos.auditoria.models import AuditEvent
from .models import Integration, IntegrationRun
from .services.errors import (
    OTRSAuthenticationError,
    OTRSCredentialDecryptionError,
    OTRSCredentialMissingError,
    OTRSTLSCertificateError,
    OTRSTLSHandshakeError,
    OTRSTLSRequiredError,
    SecretStorageUnavailable,
)
from .services.secrets import TestSecretProtector


class IntegrationPermissionTests(TestCase):
    def test_regular_user_cannot_open_integration_detail_or_test_endpoint(self):
        user = get_user_model().objects.create_user(username="ordinary", password="safe-password-1")
        self.client.force_login(user)
        response = self.client.get(reverse("configuracoes:integracoes:cmdb"))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("configuracoes:integracoes:cmdb-test"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(reverse("configuracoes:integracoes:arion-save"), {
            "engine": "sqlite", "database": "arion.sqlite3",
        }).status_code, 403)
        self.assertEqual(self.client.post(reverse("configuracoes:integracoes:arion-test"), {
            "engine": "sqlite", "database": "arion.sqlite3",
        }).status_code, 403)

    def test_view_permission_does_not_grant_manage_or_run(self):
        user = get_user_model().objects.create_user(username="integration-viewer", password="safe-password-1")
        content_type = ContentType.objects.get(app_label="configuracoes", model="groupdatascope")
        permission = Permission.objects.get(content_type=content_type, codename="view_integrations")
        group = Group.objects.create(name="Integration Viewer")
        group.permissions.add(permission)
        user.groups.add(group)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("configuracoes:integracoes:cmdb")).status_code, 200)
        self.assertEqual(self.client.post(reverse("configuracoes:integracoes:cmdb-test")).status_code, 403)
        self.assertEqual(self.client.post(reverse("configuracoes:integracoes:save-config", args=["cmdb"])).status_code, 403)
        self.assertEqual(self.client.post(reverse("configuracoes:integracoes:arion-save"), {
            "engine": "sqlite", "database": "arion.sqlite3",
        }).status_code, 403)
        self.assertEqual(self.client.post(reverse("configuracoes:integracoes:arion-test"), {
            "engine": "sqlite", "database": "arion.sqlite3",
        }).status_code, 403)


class IntegrationConfigurationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="integration-admin", email="admin@example.com", password="safe-password-1")
        self.client.force_login(self.user)
        self.protector = TestSecretProtector()

    @patch("aplicativos.integracoes.services.arion_database.config_path")
    def test_arion_configuration_page_offers_the_mysql_candidate_flow(self, database_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_config_path.return_value = Path(temp_dir) / "var" / "database-config.json"
            response = self.client.get(reverse("configuracoes:integracoes:arion"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Banco atualmente ativo")
        self.assertContains(response, "Salvar candidato")
        self.assertContains(response, 'name="database"')
        self.assertContains(response, 'name="host"')
        self.assertContains(response, 'name="user"')
        self.assertNotContains(response, "MariaDB Workbench")

    @patch("aplicativos.integracoes.services.arion_database.config_path")
    def test_arion_candidate_requires_mysql_host_database_and_user(self, database_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "var" / "database-config.json"
            database_config_path.return_value = config_file
            response = self.client.post(reverse("configuracoes:integracoes:arion-save"), {
                "database": "", "host": "", "user": "", "port": "3306", "timeout": "10",
            })
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Este campo")
            self.assertFalse(config_file.exists())

    @patch("aplicativos.integracoes.services.arion_database.get_secret_protector")
    @patch("aplicativos.integracoes.services.arion_database.config_path")
    def test_arion_mysql_config_encrypts_secret_and_test_does_not_save_it(self, database_config_path, get_protector):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "var" / "database-config.json"
            database_config_path.return_value = config_file
            get_protector.return_value = self.protector
            saved = self.client.post(reverse("configuracoes:integracoes:arion-save"), {
                "host": "127.0.0.1", "port": "3306", "database": "arion_geo",
                "user": "arion", "password": "private-db-password", "timeout": "10",
            })
            self.assertEqual(saved.status_code, 302)
            raw = config_file.read_text(encoding="utf-8")
            self.assertNotIn("private-db-password", raw)
            self.assertIn(self.protector.protect("private-db-password"), raw)

            with patch("aplicativos.integracoes.views.password_for_connection_test", return_value="private-db-password") as password, \
                 patch("aplicativos.integracoes.views.test_mysql_connection", return_value={"database": "arion_geo", "version": "8.4.2"}) as test_connection:
                response = self.client.post(reverse("configuracoes:integracoes:arion-test"), {
                    "host": "127.0.0.1", "port": "3306", "database": "arion_geo",
                    "user": "arion", "password": "", "timeout": "10",
                })
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Conexao do candidato OK")
            self.assertContains(response, "MySQL 8.x")
            self.assertNotContains(response, "private-db-password")
            password.assert_called_once()
            test_connection.assert_called_once()

    @patch("aplicativos.integracoes.clients.factory.get_secret_protector")
    def test_otrs_client_receives_decrypted_plaintext_and_normalized_connection_options(self, get_protector):
        get_protector.return_value = self.protector
        integration = Integration.objects.create(
            key="otrs", name="OTRS MySQL", kind="MySQL", username="otrs-reader",
            secret_ciphertext=self.protector.protect("a-private-password"),
            config={"host": "200.174.198.133", "port": "3306", "database": "otrs", "timeout": 10},
        )

        from .views import _otrs_client
        client = _otrs_client(integration)

        self.assertEqual(client.host, "200.174.198.133")
        self.assertEqual(client.port, 3306)
        self.assertIs(type(client.port), int)
        self.assertEqual(client.database, "otrs")
        self.assertEqual(client.username, "otrs-reader")
        self.assertEqual(client.password, "a-private-password")
        self.assertIs(type(client.password), str)
        self.assertEqual(client.timeout, 10)
        self.assertTrue(client.use_tls)
        self.assertTrue(client.ssl_verify)
        self.assertFalse(client.allow_insecure_remote)
        self.assertEqual(client.ssl_ca, "")

    @patch("aplicativos.integracoes.clients.factory.get_secret_protector")
    def test_otrs_factory_preserves_tls_policy_with_legacy_null_config_fields(self, get_protector):
        get_protector.return_value = self.protector
        integration = Integration.objects.create(
            key="otrs", name="OTRS MySQL", kind="MySQL", username="otrs-reader",
            secret_ciphertext=self.protector.protect("a-private-password"),
            config={
                "host": "200.174.198.133",
                "port": 3306,
                "database": "",
                "timeout": 10,
                "use_tls": None,
                "ssl_ca": None,
                "ssl_verify": None,
            },
        )

        from .views import _otrs_client
        client = _otrs_client(integration)

        self.assertTrue(client.use_tls)
        self.assertTrue(client.ssl_verify)
        self.assertFalse(client.allow_insecure_remote)
        self.assertEqual(client.ssl_ca, "")
        self.assertIsNotNone(client._build_ssl_context())

        integration.config["ssl_verify"] = False
        client_unverified = _otrs_client(integration)
        self.assertFalse(client_unverified.ssl_verify)
        with self.assertRaises(OTRSTLSRequiredError):
            client_unverified._build_ssl_context()

        integration.config["ssl_verify"] = True
        integration.config["use_tls"] = False
        client_no_tls = _otrs_client(integration)
        self.assertFalse(client_no_tls.use_tls)
        self.assertFalse(client_no_tls.allow_insecure_remote)
        with self.assertRaises(OTRSTLSRequiredError):
            client_no_tls._build_ssl_context()

        integration.config["allow_insecure_remote"] = True
        client_legacy_allowed = _otrs_client(integration)
        self.assertFalse(client_legacy_allowed.use_tls)
        self.assertTrue(client_legacy_allowed.allow_insecure_remote)
        with self.assertLogs("aplicativos.integracoes.clients.otrs", level="WARNING") as captured:
            self.assertIsNone(client_legacy_allowed._build_ssl_context())
        self.assertIn(
            "OTRS remoto usando transporte MySQL sem TLS por configuração explícita de compatibilidade legada.",
            " ".join(captured.output),
        )
        self.assertNotIn("a-private-password", " ".join(captured.output))
        self.assertNotIn("otrs-reader", " ".join(captured.output))

    @patch("aplicativos.integracoes.management.commands.sincronizar_otrs._otrs_client")
    def test_sincronizar_otrs_reports_sanitized_tls_diagnostic_on_discovery_failure(self, client_factory):
        Integration.objects.create(
            key="otrs", name="OTRS MySQL", kind="MySQL", username="otrs-reader",
            secret_ciphertext=self.protector.protect("secret-otrs-password"),
            config={"host": "200.174.198.133", "port": 3306, "database": ""},
        )
        fake_client = Mock()
        fake_client.database = ""
        fake_client.list_databases.side_effect = OTRSTLSCertificateError(
            error_code="certificate_verify_failed",
            technical_detail="MySQL 2003 / SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate",
        )
        client_factory.return_value = fake_client

        stderr = StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command("sincronizar_otrs", "--full", "--apply", verbosity=2, stdout=StringIO(), stderr=stderr)

        error_text = str(caught.exception)
        self.assertIn("OTRSTLSCertificateError", error_text)
        self.assertIn("certificate_verify_failed", error_text)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", error_text)
        self.assertNotIn("OTRSTLSRequiredError", error_text)
        self.assertNotIn("secret-otrs-password", error_text)
        self.assertIn("[OTRS][DIAGNÓSTICO]", stderr.getvalue())

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_otrs_dpapi_failure_is_reported_as_decryption_failure(self, get_protector):
        get_protector.return_value.unprotect.side_effect = SecretStorageUnavailable()
        integration = Integration.objects.create(
            key="otrs", name="OTRS MySQL", kind="MySQL", username="otrs-reader",
            secret_ciphertext="unreadable-ciphertext", config={"host": "db.example", "database": "otrs"},
        )

        from .views import _otrs_client
        with self.assertRaises(OTRSCredentialDecryptionError) as caught:
            _otrs_client(integration)
        self.assertNotIn("unreadable-ciphertext", caught.exception.safe_message)

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_otrs_missing_credentials_have_a_specific_safe_error(self, get_protector):
        integration = Integration.objects.create(
            key="otrs", name="OTRS MySQL", kind="MySQL", config={"host": "db.example"},
        )
        from .views import _otrs_client
        with self.assertRaises(OTRSCredentialMissingError):
            _otrs_client(integration)
        get_protector.assert_not_called()

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_password_is_encrypted_and_never_returned_or_audited(self, get_protector):
        get_protector.return_value = self.protector
        secret = "cmdb-password-never-rendered"
        response = self.client.post(reverse("configuracoes:integracoes:save-config", args=["cmdb"]), {
            "base_url": "https://cmdb.example/Webservice", "username": "cmdb-reader", "password": secret,
            "timeout": 15, "verify_tls": "on", "batch_size": 50,
        })
        self.assertEqual(response.status_code, 302)
        integration = Integration.objects.get(key="cmdb")
        self.assertNotIn(secret, integration.secret_ciphertext)
        self.assertEqual(self.protector.unprotect(integration.secret_ciphertext), secret)
        self.assertNotIn(secret, response.content.decode())
        self.assertNotIn(secret, str(list(AuditEvent.objects.values("action", "description", "metadata"))))
        detail = self.client.get(reverse("configuracoes:integracoes:cmdb") + "?tab=configuration")
        self.assertContains(detail, "Alterar credenciais")
        self.assertNotContains(detail, secret)

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_blank_password_keeps_current_secret_and_new_password_replaces_it(self, get_protector):
        get_protector.return_value = self.protector
        integration = Integration.objects.create(key="cmdb", name="CMDB ServiceUp", kind="API REST", username="old-user", secret_ciphertext=self.protector.protect("original"), config={"base_url": "https://old.example", "timeout": 12, "verify_tls": True, "batch_size": 50})
        data = {"base_url": "https://cmdb.example", "username": "new-user", "password": "", "timeout": 15, "verify_tls": "on", "batch_size": 50}
        response = self.client.post(reverse("configuracoes:integracoes:save-config", args=["cmdb"]), data)
        self.assertEqual(response.status_code, 302)
        integration.refresh_from_db()
        self.assertEqual(self.protector.unprotect(integration.secret_ciphertext), "original")
        data["password"] = "replacement"
        self.client.post(reverse("configuracoes:integracoes:save-config", args=["cmdb"]), data)
        integration.refresh_from_db()
        self.assertEqual(self.protector.unprotect(integration.secret_ciphertext), "replacement")
        with patch("aplicativos.integracoes.views.get_secret_protector", return_value=self.protector):
            from aplicativos.integracoes.views import _cmdb_client
            client = _cmdb_client(integration)
        self.assertEqual(client.username, "new-user")
        self.assertEqual(client.password, "replacement")

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_storage_failure_is_safe_and_does_not_save_plaintext(self, get_protector):
        get_protector.return_value.protect.side_effect = SecretStorageUnavailable()
        secret = "must-not-be-saved"
        response = self.client.post(reverse("configuracoes:integracoes:save-config", args=["cmdb"]), {
            "base_url": "https://cmdb.example", "username": "reader", "password": secret,
            "timeout": 15, "verify_tls": "on", "batch_size": 50,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "O armazenamento seguro de credenciais não está disponível neste ambiente.")
        self.assertNotContains(response, secret)
        self.assertFalse(Integration.objects.filter(secret_ciphertext__contains=secret).exists())

    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_remove_credentials_requires_explicit_confirmation_and_clears_both_parts(self, get_protector):
        get_protector.return_value = self.protector
        integration = Integration.objects.create(key="otrs", name="OTRS MySQL", kind="MySQL", username="reader", secret_ciphertext=self.protector.protect("db-secret"), config={"host": "db.example"})
        url = reverse("configuracoes:integracoes:remove-credentials", args=["otrs"])
        self.client.post(url, {"confirm": "yes"})
        integration.refresh_from_db()
        self.assertEqual(integration.username, "")
        self.assertEqual(integration.secret_ciphertext, "")
        self.assertEqual(integration.status, Integration.Status.NOT_CONFIGURED)
        audit = AuditEvent.objects.filter(action="INTEGRATION_CREDENTIALS_UPDATED").latest("timestamp")
        self.assertNotIn("db-secret", audit.description + str(audit.metadata))

    def test_toggle_preserves_configuration_and_audits_action(self):
        integration = Integration.objects.create(key="otrs", name="OTRS MySQL", kind="MySQL", config={"host": "db.example"})
        response = self.client.post(reverse("configuracoes:integracoes:toggle", args=["otrs"]))
        self.assertEqual(response.status_code, 302)
        integration.refresh_from_db()
        self.assertTrue(integration.enabled)
        self.assertEqual(integration.config["host"], "db.example")
        self.assertTrue(AuditEvent.objects.filter(action="INTEGRATION_ENABLED").exists())

    def test_home_and_detail_pages_render_without_exposing_database_path(self):
        home = self.client.get(reverse("configuracoes:integrations"))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "NOC - ATACADO")
        self.assertContains(home, "Central de Integrações")
        self.assertContains(home, "Voltar para Configura&ccedil;&otilde;es")
        self.assertContains(home, "Pesquisar integração")
        self.assertNotContains(home, 'class="settings-sidebar"')
        self.assertContains(home, "CMDB ServiceUp")
        self.assertContains(home, "Banco Arion")
        self.assertContains(home, "Espelhos operacionais locais")
        self.assertNotContains(home, "FONTE ATUAL")
        self.assertNotContains(home, "Base CMDB XLSX")
        self.assertNotContains(home, "Desktop\\ARION-ATACADO-GEO")
        arion = self.client.get(reverse("configuracoes:integracoes:arion"))
        self.assertEqual(arion.status_code, 200)
        self.assertContains(arion, "Conex&atilde;o")
        for name in ("configuracoes:integracoes:cmdb", "configuracoes:integracoes:otrs"):
            with self.subTest(route=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    @patch("aplicativos.integracoes.views._otrs_client")
    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_schema_explorer_renders_metadata_and_sanitized_sample(self, get_protector, client_factory):
        get_protector.return_value = self.protector
        integration = Integration.objects.create(key="otrs", name="OTRS MySQL", kind="MySQL", username="reader", secret_ciphertext=self.protector.protect("password"), config={"host": "db.example", "port": 3306, "database": "otrs", "timeout": 10})
        fake = Mock()
        fake.list_tables.return_value = [{"name": "users", "type": "BASE TABLE", "estimated_rows": 7}]
        fake.get_table_schema.return_value = [{"column": "pw", "type": "varchar(64)", "nullable": "NO", "key": "", "default": None, "extra": ""}]
        fake.get_table_indexes.return_value = [{"name": "PRIMARY", "unique": True, "sequence": 1, "column": "id", "type": "BTREE"}]
        fake.get_foreign_keys.return_value = []
        fake.get_sample_rows.return_value = [{"id": 1, "pw": "••••••"}]
        client_factory.return_value = fake

        schema = self.client.get(reverse("configuracoes:integracoes:otrs-schema"))
        self.assertEqual(schema.status_code, 200)
        self.assertContains(schema, "users")
        detail = self.client.get(reverse("configuracoes:integracoes:otrs-table", args=["users"]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Amostra sanitizada")
        self.assertContains(detail, "••••••")
        self.assertNotContains(detail, "password")

    def test_integrations_document_is_registered_in_the_documentation_portal(self):
        from aplicativos.configuracoes.documentation import DocumentationService

        record = DocumentationService.get("integracoes")
        self.assertIsNotNone(record)
        self.assertEqual(record["title"], "Central de Integra\u00e7\u00f5es")
        self.assertIn("DPAPI", record["body"])
        self.assertIn("integracoes", {item["slug"] for category in DocumentationService.categories() for item in category["documents"]})

    @patch("aplicativos.integracoes.views._cmdb_client")
    @patch("aplicativos.integracoes.views.get_secret_protector")
    def test_cmdb_test_connection_is_mocked_and_creates_safe_run(self, get_protector, client_factory):
        get_protector.return_value = self.protector
        integration = Integration.objects.create(key="cmdb", name="CMDB ServiceUp", kind="API REST", username="reader", secret_ciphertext=self.protector.protect("secret"), config={"base_url": "https://cmdb.example", "timeout": 15, "verify_tls": True, "batch_size": 50})
        self.assertFalse(integration.enabled)
        client_factory.return_value.test_connection.return_value = True
        response = self.client.post(reverse("configuracoes:integracoes:cmdb-test"))
        self.assertEqual(response.status_code, 302)
        run = IntegrationRun.objects.get(operation=IntegrationRun.Operation.TEST)
        self.assertEqual(run.status, IntegrationRun.Status.SUCCESS)
        integration.refresh_from_db()
        self.assertEqual(integration.status, Integration.Status.CONNECTED)
        self.assertFalse(integration.enabled)
        self.assertNotIn("secret", run.message)
        self.assertTrue(AuditEvent.objects.filter(action="INTEGRATION_TESTED", result="SUCCESS").exists())

    @patch("aplicativos.integracoes.views._otrs_client")
    def test_otrs_test_connection_runs_even_when_integration_is_disabled(self, client_factory):
        integration = Integration.objects.create(
            key="otrs", name="OTRS MySQL", kind="MySQL", username="reader",
            secret_ciphertext=self.protector.protect("secret"),
            config={"host": "db.example", "port": 3306, "database": "otrs"}, enabled=False,
        )
        client_factory.return_value.test_connection.return_value = True
        client_factory.return_value.list_databases.return_value = ["otrs"]

        response = self.client.post(reverse("configuracoes:integracoes:otrs-test"))

        self.assertEqual(response.status_code, 302)
        run = IntegrationRun.objects.get(integration=integration, operation=IntegrationRun.Operation.TEST)
        self.assertEqual(run.status, IntegrationRun.Status.SUCCESS)
        integration.refresh_from_db()
        self.assertFalse(integration.enabled)
        self.assertEqual(integration.accessible_databases, ["otrs"])

    @patch("aplicativos.integracoes.views._otrs_client")
    def test_otrs_access_denied_keeps_specific_safe_message(self, client_factory):
        integration = Integration.objects.create(
            key="otrs", name="OTRS MySQL", kind="MySQL", username="reader",
            secret_ciphertext=self.protector.protect("secret"), config={"host": "db.example"},
        )
        client_factory.return_value.test_connection.side_effect = OTRSAuthenticationError(
            "Access denied; password=must-not-leak",
        )

        self.client.post(reverse("configuracoes:integracoes:otrs-test"))

        run = IntegrationRun.objects.get(integration=integration, operation=IntegrationRun.Operation.TEST)
        integration.refresh_from_db()
        self.assertEqual(run.status, IntegrationRun.Status.FAILED)
        self.assertEqual(run.message, OTRSAuthenticationError.safe_message)
        self.assertNotIn("must-not-leak", run.message)
        self.assertNotIn("must-not-leak", integration.last_error)
