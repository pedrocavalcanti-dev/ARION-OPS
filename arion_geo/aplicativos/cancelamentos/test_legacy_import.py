"""Synthetic fixtures only. These unit tests do not homologate a MySQL migration."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from aplicativos.auditoria.models import AuditEvent

from . import tests as operational_tests
from .models import (CancellationEvent, CancellationPending, CancellationProcess, FinancialObligation,
                     LegacyCancellationReference, LegacyImportRun)
from .services import legacy_dump, legacy_import
from .services.data_scope import CancellationScope
from .services.legacy_history import legacy_context
from .services.queries import query_module
from .services.workflow import act_on_process, decide_intake, Conflict


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r").replace("\0", "\\0") + "'"


def synthetic_export(directory, queue="CONCLUIDO", number="2026-123"):
    datetime_fields = {"criado_em", "atualizado_em", "apropriado_em", "concluido_em", "movido_em", "resolvida_em"}
    date_fields = {"agendamento", "data_criacao_chamado", "data_baixa_financeira", "data_cancelamento_confirmado",
                   "data_primeiro_contato", "data_proxima_acao", "data_ultima_interacao", "data_prevista"}
    amount_fields = set(legacy_import.MONEY_FIELDS) | {"valor"}
    tables, schema = {}, []
    for table, columns in legacy_dump.TABLE_COLUMNS.items():
        row, fields = {}, []
        for column in columns:
            if column == "id" or column.endswith("_id"):
                kind, value = "bigint", 1 if column in ("id", "cancelamento_id") else 99
            elif column in datetime_fields:
                kind, value = "datetime(6)", "2026-05-02 12:34:56.123456" if column in ("criado_em", "atualizado_em", "movido_em") else None
            elif column in date_fields:
                kind, value = "date", None
            elif column in amount_fields:
                kind, value = "decimal(12,2)", None
            elif column == "confirmacao_sem_nova_cobranca":
                kind, value = "tinyint(1)", 0
            else:
                kind, value = "longtext", ""
            row[column] = value
            fields.append(f"  `{column}` {kind} DEFAULT NULL")
        tables[table] = [row]
        schema.append(f"CREATE TABLE `{table}` (\n" + ",\n".join(fields) + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
    main = tables[legacy_dump.MAIN][0]
    main.update(ticket_arion=number, designacao_circuito="DES-A", cliente="Cliente A", isp="ISP A", fila=queue,
        status={"PEDIDO": "NOVO", "SOLICITACAO": "NOVO", "PENDENCIA": "PENDENCIA_IDENTIFICADA", "CONCLUIDO": "CANCELAMENTO_CONFIRMADO"}[queue],
        data_criacao_chamado="0026-10-02", valor_multa_rescisao="0.00", valor_equipamento="125.90",
        observacao="Texto livre reservado à revisão financeira", observacao_financeira="informação financeira privada")
    tables[legacy_dump.PREFIX + "historicocancelamento"][0].update(tipo="EDICAO", descricao="Aspas ' e vírgula, barra \\; linha\nUnicode çã — <script>unsafe</script>")
    tables[legacy_dump.PREFIX + "historicofila"][0].update(fila_origem="PEDIDO", fila_destino=queue)
    tables[legacy_dump.PREFIX + "observacaocancelamento"][0].update(texto="Observação histórica original")
    tables[legacy_dump.PREFIX + "pendenciacancelamento"][0].update(tipo="multa_equipamento", descricao="Cobrança informada, não paga", valor="125.90", status="DESCARTADA")
    directory = Path(directory)
    directory.joinpath("cancelamentos-schema.sql").write_text("\n".join(schema), encoding="utf-8")
    sql = "/*!40103 SET TIME_ZONE='+00:00' */;\n"
    for table, rows in tables.items():
        columns = legacy_dump.TABLE_COLUMNS[table]
        sql += f"INSERT INTO `{table}` (" + ",".join(f"`{column}`" for column in columns) + ") VALUES "
        sql += ",".join("(" + ",".join(literal(row[column]) for column in columns) + ")" for row in rows) + ";\n"
    directory.joinpath("dados.sql").write_text(sql, encoding="utf-8")
    return tables


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class LegacyImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        operational_tests.CancellationTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.original = synthetic_export(self.directory)
        self.source = legacy_import.load_source(self.directory, "dados.sql", "UTC")
        self.client.force_login(self.admin)

    def run_record(self, source=None):
        source = source or self.source
        plan = legacy_import.build_plan(source)
        run = LegacyImportRun.objects.create(batch="synthetic-unit", actor=self.admin, mapping_version=legacy_import.MAPPING_VERSION,
            snapshot_checksum=source["snapshot_checksum"], schema_checksum=source["schema_checksum"])
        process_id = legacy_import._apply_record(source, plan["records"][0], run)
        return CancellationProcess.objects.get(pk=process_id)

    def test_lexer_preserves_quotes_escapes_unicode_semicolons_and_decimal(self):
        self.assertEqual(self.source["tables"], self.original)
        self.assertEqual(self.source["tables"][legacy_dump.MAIN][0]["valor_equipamento"], "125.90")
        self.assertEqual(legacy_import.normalize_ticket_number(" #000123 "), "000123")
        self.assertNotEqual(legacy_import.normalize_ticket_number("000123"), "123")
        self.assertEqual(legacy_import.normalize_ticket_number("20-123"), "20-123")
        self.assertEqual(list(legacy_dump.value_rows(r"('a\%b\_c','it''s',NULL,0,1.20)")), [[r"a\%b\_c", "it's", None, 0, "1.20"]])

    def test_parser_refuses_executable_sql_unknown_columns_and_orphans(self):
        path = self.directory / "dados.sql"
        text = path.read_text(encoding="utf-8")
        for bad in (text + "DROP TABLE auth_user;", text.replace("`ticket_arion`", "`password`"),
                    text.replace("'EDICAO'", "NOW()"), text + "/*!40101 DELETE FROM auth_user */;", text[:-2],
                    text.replace(",1,99)", ",999,99)")):
            path.write_text(bad, encoding="utf-8")
            with self.assertRaises(legacy_dump.DumpError):
                legacy_dump.read_dump(path)
        self.assertEqual(CancellationProcess.objects.count(), 0)

    def test_schema_mismatch_and_source_data_mutation_are_errors(self):
        schema = self.directory / "cancelamentos-schema.sql"
        schema.write_text(schema.read_text().replace("`ticket_arion` longtext", "`ticket_arion` varchar(2)"))
        with self.assertRaises(legacy_dump.DumpError):
            legacy_import.load_source(self.directory, "dados.sql", "UTC")

    def test_preview_and_dry_run_write_only_private_reports(self):
        before = {model: model.objects.count() for model in (CancellationProcess, LegacyCancellationReference, AuditEvent, LegacyImportRun)}
        report = self.directory / "preview.json"
        call_command("importar_cancelamentos_legado", source_dir=str(self.directory), dump="dados.sql", source_timezone="UTC", preview=True, report=str(report), verbosity=0)
        self.assertEqual(json.loads(report.read_text())["total"], 5)
        call_command("importar_cancelamentos_legado", source_dir=str(self.directory), dump="dados.sql", source_timezone="UTC", dry_run=True,
                     actor_id=self.admin.pk, report=str(report), verbosity=0)
        result = json.loads(report.read_text())
        self.assertFalse(result["ready_for_apply"])
        self.assertIn("destination_requires_mysql8_dev", result["destination"]["issues"])
        for model, count in before.items():
            self.assertEqual(model.objects.count(), count)

    def test_apply_is_blocked_on_sqlite_and_production_before_any_import(self):
        with self.assertRaises(ValidationError):
            legacy_import.apply_plan(self.source, self.admin, {"ready_for_apply": True}, "unavailable", "bad", True)
        with override_settings(ARION_PRODUCTION=True, ARION_ENV="production"):
            with self.assertRaises(ValidationError):
                legacy_import.build_plan(self.source)
        self.assertFalse(CancellationProcess.objects.exists())

    def test_mysql_wrong_database_or_remote_host_is_rejected_before_connection(self):
        for config in ({"NAME": "arion_geo", "HOST": "127.0.0.1", "USER": "dev"},
                       {"NAME": "arion_geo_dev", "HOST": "production.invalid", "USER": "dev"},
                       {"NAME": "arion_geo_dev", "HOST": "127.0.0.1", "USER": "root"}):
            with patch.object(legacy_import, "connection", SimpleNamespace(vendor="mysql", settings_dict=config)):
                with self.assertRaises(ValidationError):
                    legacy_import.destination_status()

    def test_mapping_preserves_raw_dates_null_zero_discarded_users_and_no_payment(self):
        process = self.run_record()
        self.assertEqual(process.stage, "COMPLETED")
        self.assertIsNone(process.intake_id)
        self.assertIsNone(process.completed_at)
        self.assertIsNone(process.owner_id)
        self.assertIsNone(process.completed_by_id)
        self.assertIsNone(process.ticket_opened_on)
        self.assertEqual(process.obligations.count(), 2)
        self.assertEqual(str(process.obligations.get(kind="CONTRACT").estimated_amount), "0.00")
        self.assertIsNone(process.obligations.get(kind="CONTRACT").charged_amount)
        self.assertEqual(process.obligations.get(kind="EQUIPMENT").reconciliation_state, "POSSIBLE_DUPLICATE")
        self.assertFalse(process.obligations.exclude(financial_status="UNKNOWN").exists())
        self.assertFalse(process.treatment.service_confirmed)
        self.assertFalse(process.treatment.billing_closed)
        pending = process.pendings.get()
        self.assertEqual(pending.status, "DISCARDED")
        self.assertIsNone(pending.resolved_at)
        refs = process.legacy_references
        self.assertEqual(refs.count(), 5)
        self.assertEqual(refs.get(entity="cancelamento").source_data["data_criacao_chamado"], "0026-10-02")
        self.assertEqual(process.events.count(), 3)
        self.assertFalse(process.events.exclude(actor_id=None).exists())
        self.assertTrue(all(event.created_at.year == 2026 and event.created_at.month == 5 for event in process.events.all()))
        self.assertEqual(AuditEvent.objects.filter(action="legacy_import").count(), 1)
        self.assertFalse(AuditEvent.objects.filter(action="triage_approve").exists())

    def test_mapping_uses_queue_not_shared_novo_status(self):
        for queue, stage in legacy_import.STAGES.items():
            tables = synthetic_export(self.directory, queue=queue)
            source = legacy_import.load_source(self.directory, "dados.sql", "UTC")
            self.assertEqual(legacy_import.STAGES[tables[legacy_dump.MAIN][0]["fila"]], stage)
            self.assertFalse(source["errors"])

    def test_pending_stage_and_open_pending_on_completed_record_remain_visible(self):
        process = self.run_record()
        process.pendings.update(status="OPEN")
        self.assertEqual(query_module(self.admin, {"tab": "pending"})["metrics"]["pending"], 0)
        self.assertFalse(query_module(self.admin, {"tab": "pending"})["queryset"].exists())
        expanded = query_module(self.admin, {"tab": "pending", "with_open_pending": "on"})
        self.assertEqual(expanded["queryset"].get().pk, process.pk)
        self.assertEqual(expanded["metrics"]["open_pending"], 1)
        process.pendings.update(status="DISCARDED")
        process.stage = "PENDING"
        process.save()
        self.assertEqual(query_module(self.admin, {"tab": "pending"})["queryset"].get().pk, process.pk)

    def test_imported_process_continues_operation_without_changing_source_facts(self):
        from django.test import RequestFactory
        from .services.workflow import act_on_process
        process = self.run_record()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("cancelamentos:index"), {"tab": "completed"})
        self.assertContains(response, "Data a revisar")
        original = list(process.legacy_references.order_by("pk").values())
        request = RequestFactory().post("/cancelamentos/")
        request.user = self.admin
        for action, data in (("reopen", {"stage": "ORDER", "reason": "Conferência autorizada"}),
                             ("assume", {}), ("status", {"status": "ANALYSIS", "reason": "Retomada"}),
                             ("move", {"stage": "REQUEST", "reason": "Contato ISP"}),
                             ("pending_add", {"kind": "DOCUMENT", "description": "Documento novo"})):
            process.refresh_from_db()
            act_on_process(request=request, pk=process.pk, action=action, data={"revision": process.revision, **data})
        process.refresh_from_db()
        self.assertEqual(process.owner_id, self.admin.pk)
        self.assertEqual(process.stage, "REQUEST")
        self.assertEqual(process.operational_status, "ANALYSIS")
        self.assertEqual(list(process.legacy_references.order_by("pk").values()), original)
        self.assertEqual(process.events.filter(action__startswith="legacy_").count(), 3)
        self.assertEqual(process.events.filter(action__in=("reopen", "assume", "status", "move", "pending_add"), audit_event__isnull=False).count(), 5)

    def test_replay_does_not_duplicate_or_overwrite_current_owner_and_changed_source_conflicts(self):
        process = self.run_record()
        process.owner = self.admin
        process.save()
        plan = legacy_import.build_plan(self.source)
        self.assertEqual(plan["reconciliation"], {"existing": 1})
        self.assertTrue(all(r["extracted"] == r["existing"] for r in plan["entities"].values()))
        self.source["tables"][legacy_dump.MAIN][0]["observacao"] = "Outra versão da fonte"
        self.assertEqual(legacy_import.build_plan(self.source)["reconciliation"], {"conflict": 1})
        process.refresh_from_db()
        self.assertEqual(process.owner_id, self.admin.pk)
        self.assertEqual(CancellationProcess.objects.count(), 1)

    def test_reconciliation_detects_missing_derived_financial_value(self):
        process = self.run_record()
        self.assertEqual(legacy_import.build_plan(self.source)["derived_financial_values"]["existing"], 2)
        process.obligations.get(kind="EQUIPMENT").delete()
        result = legacy_import.build_plan(self.source)
        self.assertEqual(result["reconciliation"], {"conflict": 1})
        self.assertEqual(result["records"][0]["reason"], "incomplete_financial_mapping")
        self.assertEqual(process.legacy_references.get(entity="cancelamento").source_data["valor_equipamento"], "125.90")

    def test_parent_transaction_rolls_back_all_children_on_failure(self):
        with patch.object(legacy_import.AuditService, "record", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.run_record()
        self.assertFalse(CancellationProcess.objects.exists())
        self.assertFalse(CancellationEvent.objects.exists())
        self.assertFalse(CancellationPending.objects.exists())
        self.assertFalse(LegacyCancellationReference.objects.exists())

    def test_number_matches_real_ticket_id_without_changing_human_triage(self):
        entry = operational_tests.CancellationTests.entry(self)
        process = self.run_record()
        entry.refresh_from_db()
        self.assertEqual(process.intake_id, entry.pk)
        self.assertEqual(process.source_ticket_id, entry.ticket_id)
        self.assertEqual(process.external_ticket_id, 123)
        self.assertEqual(entry.status, "WAITING")
        self.assertIsNone(entry.decided_by_id)
        self.assertEqual(query_module(self.admin, {})["metrics"]["intake"], 0)
        response = self.client.get(reverse("cancelamentos:intake_detail", args=(entry.pk,)))
        self.assertContains(response, "não é necessária nova aprovação")
        self.assertNotContains(response, "Avaliar e decidir")

    def test_late_otrs_projection_does_not_offer_another_official_process(self):
        process = self.run_record()
        entry = operational_tests.CancellationTests.entry(self)
        self.assertIsNone(process.intake_id)
        self.assertEqual(query_module(self.admin, {})["metrics"]["intake"], 0)
        response = self.client.get(reverse("cancelamentos:intake_detail", args=(entry.pk,)))
        self.assertContains(response, process.code)
        self.assertNotContains(response, "Avaliar e decidir")
        entry.ticket_number = " #2026-123 "
        entry.save(update_fields=("ticket_number",))
        with self.assertRaises(Conflict):
            decide_intake(request=operational_tests.CancellationTests.request(self), pk=entry.pk,
                action="approve", data={"revision": entry.revision, "link": self.link.pk})

    # Reuse only fixture helpers, without inheriting/rerunning the operational suite.
    ticket = operational_tests.CancellationTests.ticket

    def test_scoped_legacy_process_without_intake_and_financial_text_redaction(self):
        process = self.run_record()
        user = operational_tests.CancellationTests.restricted(self, permissions=("view_cancellations",), client="Cliente A", project="Projeto A")
        self.assertTrue(CancellationScope.processes(user).filter(pk=process.pk).exists())
        self.assertFalse(any(group["title"] == "Informações e observações históricas restritas" for group in legacy_context(process, user)["groups"]))
        self.client.force_login(user)
        response = self.client.get(reverse("cancelamentos:detail", args=(process.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "informação financeira privada")
        self.assertNotContains(response, "Observação histórica original")
        self.assertContains(response, "Movimentação ArionBroker")
        process.link = None
        process.save()
        self.assertFalse(CancellationScope.processes(user).filter(pk=process.pk).exists())
        self.assertEqual(self.client.get(reverse("cancelamentos:detail", args=(process.pk,))).status_code, 404)

    def test_legacy_ticket_with_mixed_links_stays_restricted_to_global_scope(self):
        from aplicativos.integracoes.models import OTRSTicketConfigItem
        process = self.run_record()
        ticket = self.ticket()
        process.source_ticket = ticket
        process.save()
        user = operational_tests.CancellationTests.restricted(self, permissions=("view_cancellations",))
        self.assertTrue(CancellationScope.processes(user).filter(pk=process.pk).exists())
        OTRSTicketConfigItem.objects.create(ticket=ticket, config_item_id=self.other_link.source_config_item_id,
            cmdb_link=self.other_link, relation_type="DependsOn", relation_state="Valid")
        self.assertFalse(CancellationScope.processes(user).filter(pk=process.pk).exists())
        self.assertTrue(CancellationScope.processes(self.admin).filter(pk=process.pk).exists())

    def test_detail_renders_historical_warning_escaped_text_and_all_tabs(self):
        process = self.run_record()
        response = self.client.get(reverse("cancelamentos:detail", args=(process.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data de conclusão a revisar")
        self.assertContains(response, "0026-10-02")
        self.assertContains(response, "&lt;script&gt;unsafe&lt;/script&gt;")
        self.assertContains(response, "Usuário legado #99")
        self.assertNotContains(response, "<script>unsafe</script>")
        for tab in ("order", "request", "pending", "completed"):
            self.assertEqual(self.client.get(reverse("cancelamentos:index"), {"tab": tab}).status_code, 200)
        self.assertEqual(self.client.get(reverse("cancelamentos:export"), {"tab": "completed"}).status_code, 200)

    def test_completed_legacy_assignment_is_local_and_does_not_change_original_owner(self):
        process = self.run_record()
        req = operational_tests.CancellationTests.request(self)
        act_on_process(request=req, pk=process.pk, action="assign", data={"revision": process.revision, "owner": self.admin.pk})
        process.refresh_from_db()
        self.assertEqual(process.owner_id, self.admin.pk)
        self.assertEqual(process.legacy_references.get(entity="cancelamento").source_data["responsavel_id"], 99)

    def test_import_requires_permission_and_global_scope(self):
        user = operational_tests.CancellationTests.restricted(self, permissions=("view_cancellations", "import_cancellation_history"))
        with self.assertRaises(PermissionDenied):
            legacy_import.authorize_import(user)

    def test_legacy_other_pending_with_null_amount_still_requires_financial_access(self):
        process = self.run_record()
        process.stage = "REQUEST"
        process.save()
        pending = process.pendings.get()
        pending.kind, pending.amount, pending.status = "OTHER", None, "OPEN"
        pending.description = "Texto financeiro histórico reservado"
        pending.save()
        user = operational_tests.CancellationTests.restricted(self, permissions=("view_cancellations", "manage_cancellation_pending"))
        self.client.force_login(user)
        self.assertNotContains(self.client.get(reverse("cancelamentos:detail", args=(process.pk,))), pending.description)
        with self.assertRaises(PermissionDenied):
            act_on_process(request=operational_tests.CancellationTests.request(self, user), pk=process.pk,
                action="pending_resolve", data={"revision": process.revision, "child_id": pending.pk,
                    "status": "RESOLVED", "resolution": "Tentativa sem permissão", "evidence_reference": "teste"})

    def test_dry_run_with_allow_sqlite_dev_is_ready_for_apply(self):
        report = self.directory / "dry-run-sqlite.json"
        call_command("importar_cancelamentos_legado", source_dir=str(self.directory), dump="dados.sql",
                     source_timezone="UTC", dry_run=True, allow_sqlite_dev=True,
                     actor_id=self.admin.pk, report=str(report), verbosity=0)
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertTrue(result["ready_for_apply"])
        self.assertEqual(result["destination"]["issues"], [])
        self.assertEqual(result["destination"]["engine"], "sqlite")

    def test_allow_sqlite_dev_strictly_blocked_in_production(self):
        with override_settings(ARION_PRODUCTION=True, ARION_ENV="production"):
            with self.assertRaises(ValidationError):
                legacy_import.destination_status(allow_sqlite_dev=True)
            report = self.directory / "dry-run-prod.json"
            from django.core.management.base import CommandError
            with self.assertRaises(CommandError):
                call_command("importar_cancelamentos_legado", source_dir=str(self.directory), dump="dados.sql",
                             source_timezone="UTC", dry_run=True, allow_sqlite_dev=True,
                             actor_id=self.admin.pk, report=str(report), verbosity=0)

    def test_apply_with_allow_sqlite_dev_end_to_end(self):
        import hashlib
        import sqlite3
        backup_path = self.directory / "backup_test.sqlite3"
        conn = sqlite3.connect(str(backup_path))
        try:
            conn.execute("CREATE TABLE test_tab (id integer primary key)")
            conn.execute("INSERT INTO test_tab VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        hasher = hashlib.sha256()
        with backup_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        backup_sha = hasher.hexdigest()

        plan = legacy_import.build_plan(self.source, allow_sqlite_dev=True)
        self.assertTrue(plan["ready_for_apply"])
        result = legacy_import.apply_plan(self.source, self.admin, plan, str(backup_path),
                                          backup_sha, restore_verified=True, allow_sqlite_dev=True)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(CancellationProcess.objects.count(), 1)
        self.assertEqual(LegacyImportRun.objects.filter(status="SUCCESS").count(), 1)

        # Re-plan should see 1 existing, 0 new
        plan_after = legacy_import.build_plan(self.source, allow_sqlite_dev=True)
        self.assertEqual(plan_after["reconciliation"]["existing"], 1)
        self.assertEqual(plan_after["reconciliation"].get("new", 0), 0)
