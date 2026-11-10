"""Structural/guard tests only; these do not validate a MySQL data migration."""
from unittest.mock import MagicMock, Mock, patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from .services import arion_database_snapshot as snapshot


class FullSnapshotContractTests(SimpleTestCase):
    def test_inventory_covers_every_managed_model_and_automatic_relationship(self):
        models = snapshot.snapshot_models()
        labels = [m._meta.label_lower for m in models]
        expected = {m._meta.label_lower for m in apps.get_models(include_auto_created=True)
                    if m._meta.managed and not m._meta.proxy} - snapshot.OMITTED
        self.assertEqual(set(labels), expected)
        self.assertIn("cancelamentos.legacycancellationreference", labels)
        self.assertIn("cancelamentos.handoffobligation", labels)
        self.assertIn("integracoes.otrsbackloginterval", labels)
        self.assertIn("auth.user_groups", labels)
        for model in models:
            for field in model._meta.concrete_fields:
                if field.is_relation and field.related_model != model:
                    self.assertLess(labels.index(field.related_model._meta.label_lower), labels.index(model._meta.label_lower))

    def test_credentials_are_disabled_without_mutating_source_or_user_hashes(self):
        integration = apps.get_model("integracoes.Integration")
        original = {"id": 9, "enabled": True, "secret_ciphertext": "synthetic-private", "status": "CONNECTED", "name": "OTRS"}
        target = snapshot.transferable_row(integration, original)
        self.assertEqual(original["secret_ciphertext"], "synthetic-private")
        self.assertEqual(target["secret_ciphertext"], "")
        self.assertFalse(target["enabled"])
        self.assertEqual(target["status"], integration.Status.NOT_CONFIGURED)
        user = apps.get_model("auth.User")
        self.assertEqual(snapshot.transferable_row(user, {"password": "synthetic-hash"}), {"password": "synthetic-hash"})

    @patch.object(snapshot, "connections")
    def test_sqlite_target_is_rejected_before_any_copy(self, connections):
        connections.__getitem__.return_value.vendor = "sqlite"
        with self.assertRaises(ValidationError):
            snapshot._copy_snapshot("source", "target", approved_source={})

    @patch.object(snapshot, "_require_candidate_test")
    @patch.object(snapshot, "migration_state", return_value={"schema_prepared": False})
    def test_unprepared_candidate_blocks_transfer(self, state, require):
        with self.assertRaises(ValidationError):
            snapshot.run_snapshot_phase("source", "copy_local_data")

    @patch.object(snapshot, "connections")
    def test_prepare_refuses_existing_business_data_before_migrations(self, connections):
        connection = connections.__getitem__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        connection.introspection.table_names.return_value = ["auth_user"]
        connection.ops.quote_name.return_value = "`auth_user`"
        cursor.fetchone.return_value = (1,)
        with self.assertRaises(ValidationError):
            snapshot.assert_empty_before_prepare("target")
        cursor.execute.assert_called_once_with("SELECT 1 FROM `auth_user` LIMIT 1")

    @patch.object(snapshot, "connections")
    def test_legacy_scope_relationship_column_is_preserved_without_schema_changes(self, connections):
        model = apps.get_model("configuracoes.LegacyUserDataScope").allowed_operators.through
        connection = connections.__getitem__.return_value
        connection.introspection.get_table_description.return_value = [
            type("Column", (), {"name": name})() for name in ("id", "userdatascope_id", "operadora_id")]
        mapping = snapshot.legacy_columns("source", model)
        self.assertEqual(mapping["legacyuserdatascope_id"], "userdatascope_id")
        self.assertEqual(mapping["operadora_id"], "operadora_id")

    @patch("aplicativos.configuracoes.access_control.managed_permissions", return_value=[])
    @patch.object(snapshot, "_nonempty_target", return_value=[])
    @patch.object(snapshot.apps, "get_model")
    def test_custom_grants_in_bootstrap_named_group_are_not_deleted(self, get_model, nonempty, managed):
        from aplicativos.configuracoes.access_control import GROUP_ANALYST
        group, scope = MagicMock(), MagicMock()
        get_model.side_effect = [group, scope]
        group.objects.using.return_value.values_list.return_value = [GROUP_ANALYST]
        scope.objects.using.return_value.select_related.return_value = []
        row = Mock()
        row.name = GROUP_ANALYST
        row.permissions.using.return_value.values_list.return_value = [123]
        group.objects.using.return_value.all.return_value = [row]
        with self.assertRaises(ValidationError):
            snapshot.prepare_empty_snapshot_candidate("candidate")

    @patch.object(snapshot, "_require_candidate_test")
    @patch.object(snapshot, "migration_state", return_value={"schema_prepared": True})
    @patch.object(snapshot, "candidate_database")
    @patch.object(snapshot, "snapshot_preview")
    @patch.object(snapshot, "_copy_snapshot")
    def test_existing_destination_or_stale_preview_blocks_copy(self, copy, preview, candidate, state, require):
        candidate.return_value.__enter__.return_value = "target"
        for conflicts, approved in [(["auth.user"], {"fingerprint": "same"}), ([], {"fingerprint": "old"})]:
            preview.return_value = {"fingerprint": "same", "conflicts": conflicts}
            with self.assertRaises(ValidationError):
                snapshot.run_snapshot_phase("source", "copy_local_data", approved=approved)
        copy.assert_not_called()
