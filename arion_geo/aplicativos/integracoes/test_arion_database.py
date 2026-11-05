import json
import tempfile
from datetime import timedelta
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import MagicMock, Mock, patch, sentinel

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import SimpleTestCase
from django.conf import settings
from unittest import TestCase as UnitTestCase

from .services.arion_database import (
    activate_candidate, candidate_config, candidate_database_settings, django_database_settings, migration_state,
    record_candidate_test, rollback_to_previous, save_candidate_config, test_mysql_connection,
)
from .services.secrets import TestSecretProtector
from .services import arion_database_migration as migration_service


class CandidateDatabaseFlowTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "database-config.json"
        self.protector = TestSecretProtector()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, **overrides):
        data = {"host": "127.0.0.1", "port": 3306, "database": "candidate_db",
                "user": "arion", "password": "candidate-secret", "timeout": 10}
        data.update(overrides)
        return save_candidate_config(data, path=self.path, protector=self.protector)

    @patch("aplicativos.integracoes.services.arion_database._verified_mysql_ssl_context")
    def test_remote_database_url_uses_env_password_and_verified_ca(self, build_context):
        build_context.return_value = sentinel.verified_tls
        config = django_database_settings(
            base_dir=self.path.parent,
            environ={
                "DATABASE_URL": (
                    "mysql://avnadmin@arionops-arionops.c.aivencloud.com:17384/defaultdb"
                    "?connect_timeout=8&ssl_mode=VERIFY_IDENTITY&ssl_ca=var%2Fcerts%2Faiven-ca.pem"
                ),
                "DATABASE_PASSWORD": "local-test-secret",
            },
            path=self.path,
            protector=self.protector,
        )
        self.assertEqual(config["ENGINE"], "django.db.backends.mysql")
        self.assertEqual(config["PASSWORD"], "local-test-secret")
        self.assertEqual(config["OPTIONS"]["connect_timeout"], 8)
        self.assertEqual(config["OPTIONS"]["charset"], "utf8mb4")
        self.assertIs(config["OPTIONS"]["ssl"], sentinel.verified_tls)
        self.assertEqual(config["CONN_MAX_AGE"], 0)
        self.assertTrue(config["CONN_HEALTH_CHECKS"])
        build_context.assert_called_once_with("var/certs/aiven-ca.pem")

    def test_remote_database_url_rejects_missing_ca_or_password(self):
        base = "mysql://avnadmin@arionops-arionops.c.aivencloud.com:17384/defaultdb"
        with self.assertRaisesRegex(ImproperlyConfigured, "DATABASE_PASSWORD"):
            django_database_settings(
                base_dir=self.path.parent, environ={"DATABASE_URL": base}, path=self.path,
                protector=self.protector,
            )
        with self.assertRaisesRegex(ImproperlyConfigured, "CA validada"):
            django_database_settings(
                base_dir=self.path.parent,
                environ={"DATABASE_URL": base, "DATABASE_PASSWORD": "local-test-secret"},
                path=self.path, protector=self.protector,
            )

    @patch("aplicativos.integracoes.services.arion_database._verified_mysql_ssl_context")
    def test_connection_probe_requires_tls_cipher_when_ca_is_configured(self, build_context):
        build_context.return_value = sentinel.verified_tls
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            ("defaultdb", "8.4.0", "2026-11-05 12:00:00"),
            ("Ssl_cipher", "TLS_AES_256_GCM_SHA384"),
        ]
        connector = Mock(return_value=connection)
        result = test_mysql_connection(
            {"engine": "mysql", "host": "aiven.example", "port": 17384,
             "database": "defaultdb", "user": "avnadmin", "timeout": 8,
             "ssl_ca": "var/certs/aiven-ca.pem"},
            "local-test-secret", connection_factory=connector,
        )
        self.assertTrue(result["tls_verified"])
        self.assertEqual(result["version"], "8.4.0")
        self.assertIs(connector.call_args.kwargs["ssl"], sentinel.verified_tls)
        self.assertEqual(cursor.execute.call_args_list[-1].args[0], "SHOW SESSION STATUS LIKE 'Ssl_cipher'")

    @patch("aplicativos.integracoes.services.arion_database._verified_mysql_ssl_context")
    def test_candidate_uses_environment_password_and_ca_without_persisting_password(self, build_context):
        build_context.return_value = sentinel.verified_tls
        with patch.dict("os.environ", {
            "DATABASE_PASSWORD": "local-test-secret",
            "DB_SSL_CA": "var/certs/aiven-ca.pem",
        }):
            save_candidate_config({
                "host": "arionops-arionops.c.aivencloud.com", "port": 17384,
                "database": "defaultdb", "user": "avnadmin", "timeout": 8,
            }, path=self.path, protector=self.protector)
            candidate = candidate_config(path=self.path, protector=self.protector)
            database = candidate_database_settings(path=self.path, protector=self.protector)

        self.assertEqual(candidate["password"], "local-test-secret")
        self.assertEqual(candidate["password_source"], "environment")
        self.assertIs(database["OPTIONS"]["ssl"], sentinel.verified_tls)
        self.assertNotIn("local-test-secret", self.path.read_text(encoding="utf-8"))

    def test_candidate_dpapi_failure_is_reported_without_secret(self):
        self.save()

        class BrokenProtector(TestSecretProtector):
            def unprotect(self, ciphertext):
                raise ValueError("candidate-secret")

        with self.assertRaises(ValidationError) as raised:
            candidate_config(path=self.path, protector=BrokenProtector())
        self.assertIn("DPAPI", str(raised.exception))
        self.assertNotIn("candidate-secret", str(raised.exception))

    def test_saving_candidate_does_not_change_active_sqlite(self):
        self.save()
        active = django_database_settings(base_dir=self.path.parent, environ={}, path=self.path,
                                          protector=self.protector)
        self.assertEqual(active["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(candidate_config(path=self.path, protector=self.protector)["password"], "candidate-secret")
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("candidate-secret", raw)

    def test_candidate_requires_successful_test_before_activation(self):
        self.save()
        with self.assertRaises(ValidationError):
            activate_candidate(path=self.path)
        self.assertEqual(django_database_settings(base_dir=self.path.parent, environ={}, path=self.path,
                                                  protector=self.protector)["ENGINE"], "django.db.backends.sqlite3")

    def test_1045_is_safe_and_connection_error_is_classified(self):
        config = {"engine": "mysql", "host": "localhost", "port": 3306, "database": "db",
                  "user": "arion", "timeout": 1}
        for exception, expected in (
            (Exception(1045, "Access denied; password=do-not-leak"), "Acesso negado"),
            (Exception(2003, "connection refused"), "recusada"),
            (TimeoutError("timed out"), "tempo limite"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(ValidationError) as raised:
                    test_mysql_connection(config, "secret", connection_factory=Mock(side_effect=exception))
                self.assertIn(expected.casefold(), str(raised.exception).casefold())
                self.assertNotIn("do-not-leak", str(raised.exception))
                self.assertNotIn("secret", str(raised.exception))

    def test_cutover_requires_every_stage_and_preserves_rollback_sqlite(self):
        self.save()
        record_candidate_test(path=self.path)
        with self.assertRaises(ValidationError):
            activate_candidate(path=self.path)
        bootstrap = json.loads(self.path.read_text(encoding="utf-8"))
        bootstrap["migration"].update(schema_prepared=True, data_copied=True,
                                      validation={"passed": True, "checks": {"users": True}})
        self.path.write_text(json.dumps(bootstrap), encoding="utf-8")
        activate_candidate(path=self.path, environ={})
        active = django_database_settings(base_dir=self.path.parent, environ={}, path=self.path,
                                          protector=self.protector)
        self.assertEqual(active["ENGINE"], "django.db.backends.mysql")
        self.assertEqual(migration_state(path=self.path)["validated"], False)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["rollback"]["engine"], "sqlite")

    def test_rollback_restores_sqlite_without_deleting_mysql_candidate_data(self):
        self.save()
        record_candidate_test(path=self.path)
        bootstrap = json.loads(self.path.read_text(encoding="utf-8"))
        bootstrap["migration"].update(schema_prepared=True, data_copied=True,
                                      validation={"passed": True})
        self.path.write_text(json.dumps(bootstrap), encoding="utf-8")
        activate_candidate(path=self.path, environ={})
        rollback_to_previous(path=self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["active"]["engine"], "sqlite")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["rollback"]["engine"], "mysql")



class LocalDataCopyTests(UnitTestCase):
    def _databases(self, directory):
        from .services.arion_database_migration import _temporary_database
        from django.core.management import call_command

        contexts = (
            _temporary_database({"ENGINE": "django.db.backends.sqlite3", "NAME": str(Path(directory) / "source.sqlite3")}, "perm_source"),
            _temporary_database({"ENGINE": "django.db.backends.sqlite3", "NAME": str(Path(directory) / "target.sqlite3")}, "perm_target"),
        )
        return contexts

    def _migrated_databases(self, directory):
        from contextlib import ExitStack
        from django.core.management import call_command

        source_context, target_context = self._databases(directory)
        stack = ExitStack()
        source = stack.enter_context(source_context)
        target = stack.enter_context(target_context)
        call_command("migrate", database=source, interactive=False, verbosity=0)
        call_command("migrate", database=target, interactive=False, verbosity=0)
        return stack, source, target

    def test_copy_preserves_user_hash_group_permissions_and_group_scope(self):
        from django.apps import apps
        from django.contrib.auth import get_user_model
        from django.contrib.auth.hashers import make_password
        from django.contrib.auth.models import Group, Permission
        from aplicativos.base_links.models import Operadora
        from django.core.management import call_command
        from .services.arion_database_migration import _copy_models, _temporary_database

        user_model = get_user_model()
        with tempfile.TemporaryDirectory() as directory:
            source_file = Path(directory) / "source.sqlite3"
            target_file = Path(directory) / "target.sqlite3"
            with _temporary_database({"ENGINE": "django.db.backends.sqlite3", "NAME": str(source_file)}, "test_source") as source:
                with _temporary_database({"ENGINE": "django.db.backends.sqlite3", "NAME": str(target_file)}, "test_target") as target:
                    call_command("migrate", database=source, interactive=False, verbosity=0)
                    call_command("migrate", database=target, interactive=False, verbosity=0)
                    user = user_model.objects.using(source).create(username="migration-admin", password=make_password("known-local-pass"), is_staff=True, is_superuser=True)
                    group = Group.objects.using(source).create(name="NOC migration test")
                    permission = Permission.objects.using(source).get(codename="view_integrations")
                    group.permissions.add(permission)
                    user.groups.add(group)
                    apps.get_model("configuracoes", "GroupDataScope").objects.using(source).create(group=group, mode="all")
                    legacy_scope_model = apps.get_model("configuracoes", "LegacyUserDataScope")
                    legacy_scope = legacy_scope_model.objects.using(source).create(user=user, mode="custom")
                    operator = Operadora.objects.using(source).create(name="Migration ISP", normalized_name="migration-isp")
                    with __import__("django.db", fromlist=["connections"]).connections[source].cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO configuracoes_userdatascope_allowed_operators (userdatascope_id, operadora_id) VALUES (%s, %s)",
                            [legacy_scope.pk, operator.pk],
                        )
                    expected_hash = user.password
                    _copy_models(source, target)
                    migrated_user = user_model.objects.using(target).get(username="migration-admin")
                    migrated_group = Group.objects.using(target).get(name="NOC migration test")
                    self.assertEqual(migrated_user.password, expected_hash)
                    self.assertTrue(migrated_user.is_staff and migrated_user.is_superuser)
                    self.assertIn(migrated_group.pk, migrated_user.groups.values_list("pk", flat=True))
                    self.assertTrue(migrated_group.permissions.filter(codename="view_integrations").exists())
                    scope = apps.get_model("configuracoes", "GroupDataScope").objects.using(target).get(group=migrated_group)
                    self.assertEqual(scope.mode, "all")
                    migrated_operator = Operadora.objects.using(target).get(normalized_name="migration-isp")
                    migrated_legacy_scope = legacy_scope_model.objects.using(target).get(user=migrated_user)
                    with __import__("django.db", fromlist=["connections"]).connections[target].cursor() as cursor:
                        cursor.execute(
                            "SELECT COUNT(*) FROM configuracoes_userdatascope_allowed_operators WHERE userdatascope_id = %s AND operadora_id = %s",
                            [migrated_legacy_scope.pk, migrated_operator.pk],
                        )
                        self.assertEqual(cursor.fetchone()[0], 1)

    def test_copy_preserves_quality_qid_ledger_checkpoints_and_ticket_links(self):
        from django.apps import apps
        from django.utils import timezone

        with tempfile.TemporaryDirectory() as directory:
            stack, source, target = self._migrated_databases(directory)
            with stack:
                QualityOrder = apps.get_model("qualidade", "QualityOrder")
                QualityOccurrence = apps.get_model("qualidade", "QualityOccurrence")
                QualityTreatment = apps.get_model("qualidade", "QualityTreatment")
                QualityTreatmentSequence = apps.get_model("qualidade", "QualityTreatmentSequence")
                QualityTreatmentIdentity = apps.get_model("qualidade", "QualityTreatmentIdentity")
                QualityCorrelationCheckpoint = apps.get_model("qualidade", "QualityCorrelationCheckpoint")
                QualityMonitoringCycle = apps.get_model("qualidade", "QualityMonitoringCycle")
                QualityAuditEvent = apps.get_model("qualidade", "QualityAuditEvent")
                CMDBLinkMirror = apps.get_model("integracoes", "CMDBLinkMirror")
                OTRSTicket = apps.get_model("integracoes", "OTRSTicket")

                QualityTreatmentSequence.objects.using(source).create(key="tq", last_value=0)
                link = CMDBLinkMirror.objects.using(source).create(source_config_item_id="CI-QUALITY-1")
                order = QualityOrder.objects.using(source).create(code="OQ-2026-000321")
                QualityOrder.objects.using(source).filter(pk=order.pk).update(link_id=link.source_config_item_id)
                ticket = OTRSTicket.objects.using(source).create(
                    external_id=987654321, ticket_number="2026110500000123", queue_external_id=12,
                    queue_name="NOC - QUALIDADE ATACADO", external_state_id=1, external_state_name="open",
                )
                occurred_at = timezone.now()
                QualityOccurrence.objects.using(source).create(
                    quality_order=order, otrs_ticket=ticket, ticket_external_id_snapshot=ticket.external_id,
                    ticket_number=ticket.ticket_number, occurred_at=occurred_at,
                )
                treatment = QualityTreatment.objects.using(source).create(
                    quality_order=order, support_ticket=ticket, reason="Migração controlada",
                )
                QualityCorrelationCheckpoint.objects.using(source).create(
                    queue_name="NOC - QUALIDADE ATACADO", last_article_id=12345, records_processed=17,
                )
                QualityMonitoringCycle.objects.using(source).create(
                    quality_order=order, cycle_number=1, expected_end_at=occurred_at + timedelta(days=30),
                )
                QualityAuditEvent.objects.using(source).create(
                    quality_order=order, event_type="TREATMENT_CREATED", description="TQ de migração",
                )
                expected_code = treatment.code
                expected_qid = treatment.correlation_id
                expected_sequence = QualityTreatmentSequence.objects.using(source).get(key="tq").last_value

                migration_service._copy_models(source, target)

                migrated = QualityTreatment.objects.using(target).get(code=expected_code)
                self.assertEqual(migrated.correlation_id, expected_qid)
                self.assertRegex(migrated.correlation_id, r"^ARION-QID-[0-9A-F]{32}$")
                self.assertEqual(
                    QualityTreatmentIdentity.objects.using(target).get(code=expected_code).correlation_id,
                    expected_qid,
                )
                self.assertGreaterEqual(
                    QualityTreatmentSequence.objects.using(target).get(key="tq").last_value,
                    expected_sequence,
                )
                self.assertEqual(migrated.support_ticket_id, ticket.external_id)
                migrated_order = QualityOrder.objects.using(target).get(code="OQ-2026-000321")
                self.assertEqual(migrated_order.link_id, link.source_config_item_id)
                self.assertEqual(
                    QualityCorrelationCheckpoint.objects.using(target).get(
                        queue_name="NOC - QUALIDADE ATACADO").last_article_id,
                    12345,
                )
                self.assertTrue(QualityMonitoringCycle.objects.using(target).filter(
                    quality_order=migrated_order, cycle_number=1).exists())
                self.assertTrue(QualityAuditEvent.objects.using(target).filter(
                    quality_order=migrated_order, event_type="TREATMENT_CREATED").exists())

    def test_permission_natural_keys_ignore_unused_history_and_copy_custom_assignments_idempotently(self):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType
        from django.contrib.auth import get_user_model

        with tempfile.TemporaryDirectory() as directory:
            stack, source, target = self._migrated_databases(directory)
            with stack:
                User = get_user_model()
                group = Group.objects.using(source).create(name="custom-permission-group")
                user = User.objects.using(source).create(username="permission-user", password="!unset")
                AuditEvent = __import__("django.apps", fromlist=["apps"]).apps.get_model("auditoria", "AuditEvent")
                source_event = AuditEvent.objects.using(source).create(
                    user=user, category="settings", action="copy-test", result="SUCCESS",
                    module="integrations", object_type="Integration", object_id="7",
                    description="source event", metadata={"safe": True})
                AuditEvent.objects.using(target).create(
                    category="settings", action="candidate-only", result="SUCCESS",
                    module="integrations", description="preserve candidate event")
                source_event_timestamp = source_event.timestamp
                user_ct = ContentType.objects.using(source).get(app_label="auth", model="user")
                custom = Permission.objects.using(source).create(
                    content_type=user_ct, codename="export_user_report", name="Can export user report")
                group.permissions.add(custom)
                user.user_permissions.add(custom)
                old_ct = ContentType.objects.using(source).create(app_label="removed_app", model="removed_model")
                Permission.objects.using(source).create(content_type=old_ct, codename="view_removed_model",
                                                        name="Can view removed model")

                first = migration_service._copy_models(source, target)
                migrated_permission = Permission.objects.using(target).get(
                    content_type__app_label="auth", content_type__model="user", codename="export_user_report")
                self.assertTrue(Group.objects.using(target).get(name=group.name).permissions.filter(pk=migrated_permission.pk).exists())
                self.assertTrue(User.objects.using(target).get(username=user.username).user_permissions.filter(pk=migrated_permission.pk).exists())
                self.assertEqual(first["permission_report"]["missing_unused"], [("removed_app", "removed_model", "view_removed_model")])
                self.assertEqual(first["permission_report"]["created_custom"], [("auth", "user", "export_user_report")])
                self.assertEqual(AuditEvent.objects.using(target).count(), 2)
                self.assertEqual(AuditEvent.objects.using(target).get(action="copy-test").timestamp,
                                 source_event_timestamp)

                before = {model._meta.label_lower: model.objects.using(target).count()
                          for model, _, _ in migration_service._model_specs()}
                migration_service._copy_models(source, target)
                after = {model._meta.label_lower: model.objects.using(target).count()
                         for model, _, _ in migration_service._model_specs()}
                self.assertEqual(after, before)
                self.assertEqual(AuditEvent.objects.using(target).count(), 2)
                self.assertTrue(AuditEvent.objects.using(target).filter(action="candidate-only").exists())

    def test_prepare_recreates_missing_default_permission_without_duplicates(self):
        from django.contrib.auth.models import Permission

        with tempfile.TemporaryDirectory() as directory:
            stack, _source, target = self._migrated_databases(directory)
            with stack:
                permission_filter = {"content_type__app_label": "auth", "content_type__model": "group",
                                     "codename": "view_group"}
                Permission.objects.using(target).filter(**permission_filter).delete()
                migration_service._ensure_current_model_permissions(target)
                self.assertEqual(Permission.objects.using(target).filter(**permission_filter).count(), 1)

    def test_used_permission_for_removed_model_blocks_copy_with_safe_identity(self):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType

        with tempfile.TemporaryDirectory() as directory:
            stack, source, target = self._migrated_databases(directory)
            with stack:
                group = Group.objects.using(source).create(name="legacy-permission-group")
                old_ct = ContentType.objects.using(source).create(app_label="removed_app", model="removed_model")
                permission = Permission.objects.using(source).create(
                    content_type=old_ct, codename="manage_removed_model", name="Manage removed model")
                group.permissions.add(permission)
                baseline_groups = Group.objects.using(target).count()
                with self.assertRaises(ValidationError) as raised:
                    migration_service._copy_models(source, target)
                message = str(raised.exception)
                self.assertIn("removed_app", message)
                self.assertIn("removed_model", message)
                self.assertIn("manage_removed_model", message)
                self.assertIn("group_assigned=sim", message)
                self.assertEqual(Group.objects.using(target).count(), baseline_groups)


class CandidateMigrationStageTests(SimpleTestCase):
    @patch.object(migration_service, "connections")
    @patch.object(migration_service, "mark_migration_stage")
    @patch.object(migration_service, "_assert_all_migrations_applied", return_value={"applied_migrations": 39, "pending_migrations": 0})
    @patch.object(migration_service, "_ensure_current_model_permissions")
    @patch.object(migration_service, "call_command")
    @patch.object(migration_service, "MigrationRecorder")
    @patch.object(migration_service, "candidate_database", return_value=nullcontext("candidate_alias"))
    @patch.object(migration_service, "_require_candidate_test")
    def test_prepare_migrates_candidate_alias_only_and_is_idempotent(
        self, require_test, candidate_context, recorder_class, call_command, ensure_permissions,
        assert_migrations, mark_stage, connections_mock
    ):
        recorder_class.return_value.applied_migrations.return_value = {( "auth", "0001_initial"): object()}
        connections_mock.__getitem__.return_value = Mock()
        default_engine = settings.DATABASES["default"]["ENGINE"]
        result = migration_service.prepare_candidate_schema()
        call_command.assert_called_once_with("migrate", database="candidate_alias", interactive=False, verbosity=0)
        ensure_permissions.assert_called_once_with("candidate_alias")
        self.assertEqual(result["newly_applied"], 38)
        mark_stage.assert_called_once_with("schema_prepared")
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], default_engine)

    @patch.object(migration_service, "_permission_id_map", return_value=({}, {
        "source_count": 0, "target_count": 0, "missing_count": 0, "missing_unused": [],
        "missing_assigned": [], "missing_user_assigned": [], "missing_group_assigned": [],
        "missing_current_required": [], "created_custom": [], "unavailable_models": [],
        "missing_content_types": [],
    }))
    @patch.object(migration_service, "_required_privilege_report", return_value={"known": True, "sufficient": True, "missing": []})
    @patch.object(migration_service, "_model_specs", return_value=[])
    @patch.object(migration_service, "_migration_status", return_value={"applied_migrations": 0, "pending_migrations": 10})
    @patch.object(migration_service, "candidate_database", return_value=nullcontext("candidate_alias"))
    @patch.object(migration_service, "_require_candidate_test")
    @patch.object(migration_service, "migration_state", return_value={"schema_prepared": False})
    def test_dry_run_is_read_only_with_sqlite_default_and_candidate_alias(
        self, state, require_test, candidate_context, migration_status, model_specs, privilege_report, permission_map
    ):
        target_cursor = Mock()
        target_cursor.fetchone.return_value = ("candidate_db",)
        cursor_context = Mock()
        cursor_context.__enter__ = Mock(return_value=target_cursor)
        cursor_context.__exit__ = Mock(return_value=False)
        target = Mock()
        target.cursor.return_value = cursor_context
        target.settings_dict = {"NAME": "candidate_db"}
        target.introspection.table_names.return_value = []
        handler = MagicMock()
        handler.__getitem__.return_value = target
        class EmptyQuerySet:
            def using(self, alias):
                return self

            def values_list(self, *args, **kwargs):
                return []

        class EmptyUser:
            objects = EmptyQuerySet()

        default_engine = settings.DATABASES["default"]["ENGINE"]
        with patch.object(migration_service, "connections", handler), \
                patch.object(migration_service, "get_user_model", return_value=EmptyUser):
            report = migration_service.dry_run_migration()
        self.assertTrue(report["connection"])
        self.assertFalse(report["migration_table_exists"])
        self.assertEqual(report["migrations"]["pending_migrations"], 10)
        self.assertEqual(report["username_conflict_count"], 0)
        state.assert_called_once()
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], default_engine)

    @patch.object(migration_service, "mark_migration_stage")
    @patch.object(migration_service, "log_candidate_stage_failure", return_value="RuntimeError: sanitized")
    @patch.object(migration_service, "call_command", side_effect=RuntimeError("password=do-not-log"))
    @patch.object(migration_service, "MigrationRecorder")
    @patch.object(migration_service, "candidate_database", return_value=nullcontext("candidate_alias"))
    @patch.object(migration_service, "_require_candidate_test")
    def test_failed_prepare_is_sanitized_and_does_not_mark_schema_ready(
        self, require_test, candidate_context, recorder_class, call_command, log_failure, mark_stage
    ):
        recorder_class.return_value.applied_migrations.return_value = {}
        with self.assertRaises(migration_service.CandidateStageError) as raised:
            migration_service.prepare_candidate_schema()
        self.assertNotIn("do-not-log", str(raised.exception))
        self.assertEqual(raised.exception.technical_detail, "RuntimeError: sanitized")
        mark_stage.assert_not_called()

    def test_technical_diagnostics_redact_candidate_and_database_url_secrets(self):
        secrets = ("candidate-password", "dpapi-blob", "mysql://app:db-password@localhost/arion_geo")
        with patch.object(migration_service, "candidate_config", return_value={
            "password": secrets[0], "password_protected": secrets[1],
        }), patch.dict("os.environ", {"DATABASE_URL": secrets[2]}), \
                patch.object(migration_service.logger, "error") as log_error:
            try:
                raise RuntimeError(f"password={secrets[0]} blob={secrets[1]} {secrets[2]}")
            except RuntimeError as exc:
                detail = migration_service.log_candidate_stage_failure("dry-run", exc)
        self.assertIn("RuntimeError", detail)
        for secret in secrets:
            self.assertNotIn(secret, detail)
            self.assertNotIn(secret, str(log_error.call_args))

    def test_copy_and_activate_remain_blocked_until_prior_stages_validate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database-config.json"
            save_candidate_config({"host": "127.0.0.1", "port": 3306, "database": "candidate_db",
                                   "user": "arion", "password": "candidate-secret", "timeout": 10},
                                  path=path, protector=TestSecretProtector())
            record_candidate_test(path=path)
            with patch.object(migration_service, "migration_state", return_value={"schema_prepared": False}), \
                    patch.object(migration_service, "candidate_config", return_value={"password": "safe"}), \
                    patch.object(migration_service, "candidate_test_is_current", return_value=True), \
                    patch.object(migration_service, "test_mysql_connection", return_value={"database": "candidate_db"}):
                with self.assertRaises(ValidationError):
                    migration_service.copy_local_data()
            with self.assertRaises(ValidationError):
                activate_candidate(path=path, environ={})

    def test_permission_and_migration_errors_keep_utf8_text(self):
        report = {
            "missing_assigned": [("old_app", "old_model", "manage_old_model")],
            "missing_current_required": [], "unavailable_models": [("old_app", "old_model", "manage_old_model")],
            "missing_content_types": [], "missing_user_assigned": [],
            "missing_group_assigned": [("old_app", "old_model", "manage_old_model")],
        }
        with self.assertRaises(ValidationError) as permission_error:
            migration_service._raise_for_unmapped_required_permissions(report)
        self.assertIn("Permissões", str(permission_error.exception))
        self.assertIn("group_assigned=sim", str(permission_error.exception))
        self.assertIn("user_assigned=não", str(permission_error.exception))

        with patch.object(migration_service, "_require_candidate_test"), \
                patch.object(migration_service, "migration_state", return_value={"schema_prepared": False, "data_copied": False}):
            with self.assertRaises(ValidationError) as migration_error:
                migration_service.validate_candidate_migration()
        self.assertIn("migração", str(migration_error.exception))
