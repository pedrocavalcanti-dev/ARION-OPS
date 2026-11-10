"""Production guards and MySQL-only transaction tests, using synthetic exports."""
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.integracoes.models import OTRSTicket, OTRSTicketConfigItem, CMDBLinkMirror
from .models import CancellationIntake, CancellationProcess, CancellationEvent, CancellationPending, FinancialObligation, LegacyImportRun
from .services import legacy_import as importer
from .services.queries import query_module
from .services.legacy_import_policy import production_source, file_sha256, PRODUCTION_CONFIRMATION
from .test_legacy_import import synthetic_export, literal


def export_many(folder, count=2):
    original = synthetic_export(folder)
    sql = "/*!40103 SET TIME_ZONE='+00:00' */;\n"
    for table, rows in original.items():
        columns = importer.TABLE_COLUMNS[table]
        values = []
        for pk in range(1, count + 1):
            row = {**rows[0], "id": pk}
            if table == importer.MAIN:
                row["ticket_arion"] = f"SYNTHETIC-{pk:03}"
            else:
                row["cancelamento_id"] = pk
            values.append("(" + ",".join(literal(row[c]) for c in columns) + ")")
        sql += f"INSERT INTO `{table}` (" + ",".join(f"`{c}`" for c in columns) + ") VALUES " + ",".join(values) + ";\n"
    (folder / "dados.sql").write_text(sql, encoding="utf-8")
    return importer.load_source(folder, "dados.sql", "UTC")


class ProductionImportGuardTests(SimpleTestCase):
    @override_settings(ARION_ENV="production", ARION_PRODUCTION=True, DEBUG=False)
    def test_explicit_mode_wrong_database_sqlite_and_debug_fail_before_connection(self):
        with self.assertRaises(ValidationError):
            importer.destination_status()
        for vendor, name, user, host in (("sqlite", "arion_geo", "test", "127.0.0.1"),
                ("mysql", "arion_geo_dev", "test", "127.0.0.1"),
                ("mysql", "arion_geo", "root", "127.0.0.1"),
                ("mysql", "arion_geo", "test", "remote.invalid")):
            conn = SimpleNamespace(vendor=vendor, settings_dict={"NAME":name,"USER":user,"HOST":host})
            with patch.object(importer, "connection", conn), self.assertRaises(ValidationError):
                importer.destination_status(production=True)
        for overrides in ({"DEBUG":True}, {"ARION_ENV":"development"}, {"ARION_PRODUCTION":False}):
            with override_settings(**overrides), self.assertRaises(ValidationError):
                importer.destination_status(production=True)
        with self.assertRaises(ValidationError):
            importer.destination_status(production=True, allow_sqlite_dev=True)

    def test_snapshot_hash_source_changes_and_public_paths_fail_closed(self):
        with TemporaryDirectory() as temporary:
            source = export_many(Path(temporary), 1)
            checksum = source["snapshot_checksum"]
            self.assertEqual(production_source(source, checksum)["snapshot_sha256"], checksum)
            for bad in (None, "bad", "0" * 64):
                with self.assertRaises(ValidationError):
                    production_source(source, bad)
            with override_settings(MEDIA_ROOT=temporary), self.assertRaises(ValidationError):
                production_source(source, checksum)
            (Path(temporary)/"dados.sql").write_text("changed")
            with self.assertRaises(ValidationError):
                production_source(source, checksum)


@skipUnless(getattr(settings, "ARION_IMPORT_DISPOSABLE_MYSQL", False), "Requires explicitly configured disposable MySQL; never substitutes SQLite.")
@override_settings(ARION_ENV="production", ARION_PRODUCTION=True, DEBUG=False)
class ProductionImportMySQLTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "mysql")
        self.assertEqual(connection.settings_dict["NAME"], "arion_geo")
        self.actor = get_user_model().objects.create_superuser("synthetic-production-import", password="synthetic-only")
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = export_many(self.folder)
        self.options = {"production":True, "snapshot_checksum":self.source["snapshot_checksum"]}
        self.backup = self.folder / "backup.sql"
        self.backup.write_text("-- MySQL dump\n-- Host: localhost Database: arion_geo\n" + "-- synthetic guard fixture\n"*10)
        self.backup_hash = file_sha256(self.backup)
        self.evidence = self.folder / "restore.json"
        self.evidence.write_text(json.dumps({"database":"arion_geo","backup_sha256":self.backup_hash,
            "restore_verified":True,"verified_at":timezone.now().isoformat(),"validated_by":"synthetic-test",
            "verification_reference":"synthetic-guard-fixture"}))
        for i in range(1,14):
            ticket = OTRSTicket.objects.create(external_id=8000+i, ticket_number=f"SYNTHETIC-{i:03}",
                queue_external_id=164, external_state_id=7,
                queue_name="NOC - CANCELAMENTOS NGO", external_state_name="em atendimento", business_status="OPEN", is_present_in_queue=True)
            CancellationIntake.objects.create(ticket=ticket, ticket_external_id=ticket.external_id,
                ticket_number=ticket.ticket_number, queue_external_id=164, queue_name=ticket.queue_name,
                status="REJECTED" if i == 13 else "WAITING", assigned_to=self.actor)
        link = CMDBLinkMirror.objects.create(source_config_item_id="synthetic-ci", contracting_client="Cliente A", business_project="Projeto A")
        OTRSTicketConfigItem.objects.create(ticket=OTRSTicket.objects.get(external_id=8001),config_item_id=link.source_config_item_id,cmdb_link=link)

    def plan(self):
        return {**importer.build_plan(self.source, **self.options), "mode":"dry-run"}

    def apply(self, plan=None, **overrides):
        kwargs = {**self.options,"confirm_production_apply":True,"restore_evidence":str(self.evidence),**overrides}
        return importer.apply_plan(self.source,self.actor,plan or self.plan(),str(self.backup),self.backup_hash,True,**kwargs)

    def test_apply_preserves_intakes_users_mirrors_and_replay(self):
        before = list(CancellationIntake.objects.values("pk","status","revision","assigned_to_id"))
        tickets = list(OTRSTicket.objects.values())
        links = list(CMDBLinkMirror.objects.values())
        relations = list(OTRSTicketConfigItem.objects.values())
        users = get_user_model().objects.count()
        result = self.apply()
        self.assertEqual(result["applied"],2)
        self.assertEqual(CancellationProcess.objects.count(),2)
        self.assertEqual(CancellationPending.objects.count(),2)
        self.assertEqual(CancellationEvent.objects.count(),6)
        self.assertEqual(FinancialObligation.objects.count(),4)
        self.assertFalse(CancellationProcess.objects.exclude(owner=None).exists())
        self.assertEqual(list(CancellationIntake.objects.values("pk","status","revision","assigned_to_id")),before)
        self.assertEqual(list(OTRSTicket.objects.values()),tickets)
        self.assertEqual(list(CMDBLinkMirror.objects.values()),links)
        self.assertEqual(list(OTRSTicketConfigItem.objects.values()),relations)
        self.assertEqual(get_user_model().objects.count(),users)
        self.assertEqual(CancellationProcess.objects.filter(intake__isnull=False).count(),2)
        self.assertFalse(query_module(self.actor,{"tab":"intake","presence":"all"})["queryset"].filter(ticket_external_id__in=(8001,8002)).exists())
        self.assertEqual(result["otrs_matched"],2)
        self.assertEqual(result["cmdb_matched"],1)
        self.assertEqual(self.apply()["applied"],0)
        self.assertEqual(AuditEvent.objects.filter(action="legacy_import").count(),2)
        self.assertFalse(AuditEvent.objects.filter(action="triage_approve").exists())

    def test_failure_on_second_tree_rolls_back_first_tree_and_audit(self):
        original = importer._apply_record
        def fail_second(source,item,run):
            if item["id"] == 2:
                raise ValidationError("Synthetic transactional failure")
            return original(source,item,run)
        with patch.object(importer,"_apply_record",side_effect=fail_second), self.assertRaises(ValidationError):
            self.apply()
        for model in (CancellationProcess,CancellationPending,CancellationEvent,FinancialObligation):
            self.assertEqual(model.objects.count(),0)
        self.assertFalse(AuditEvent.objects.filter(action="legacy_import").exists())
        self.assertTrue(LegacyImportRun.objects.filter(status="FAILED").exists())
        self.assertEqual(CancellationIntake.objects.count(),13)

    def test_conflict_human_decision_and_changed_source_do_not_overwrite(self):
        entry=CancellationIntake.objects.get(ticket_external_id=8001)
        entry.status="REJECTED"; entry.save()
        self.assertFalse(self.plan()["ready_for_apply"])
        self.assertIn("existing_human_decision", [r["reason"] for r in self.plan()["blocking_conflicts"]])
        with self.assertRaises(ValidationError): self.apply()
        entry.status="WAITING"; entry.save()
        self.apply()
        changed={**self.source,"tables":{**self.source["tables"],importer.MAIN:[dict(r) for r in self.source["tables"][importer.MAIN]]}}
        changed["tables"][importer.MAIN][0]["isp"]="changed"
        result=importer.build_plan(changed,**self.options)
        self.assertFalse(result["ready_for_apply"])
        self.assertEqual(CancellationProcess.objects.first().isp,"ISP A")

    def test_apply_requires_confirmation_backup_hash_restore_and_approved_state(self):
        plan=self.plan()
        for overrides in ({"confirm_production_apply":False},{"restore_evidence":None}):
            with self.assertRaises(ValidationError): self.apply(plan,**overrides)
        with self.assertRaises(ValidationError):
            importer.apply_plan(self.source,self.actor,plan,str(self.backup),"0"*64,True,
                **self.options,confirm_production_apply=True,restore_evidence=str(self.evidence))
        for approved in ({**plan,"mode":"preview"}, {**plan,"plan_fingerprint":"changed"}):
            with self.assertRaises(ValidationError): self.apply(approved)
        with self.assertRaises(PermissionDenied):
            importer.apply_plan(self.source,None,plan,str(self.backup),self.backup_hash,True,
                **self.options,confirm_production_apply=True,restore_evidence=str(self.evidence))
        self.backup.unlink()
        with self.assertRaises(FileNotFoundError): self.apply(plan)
        self.assertFalse(CancellationProcess.objects.exists())

    def test_command_dry_run_validate_and_explicit_apply(self):
        common={"source_dir":str(self.folder),"dump":"dados.sql","source_timezone":"UTC",
            "production_import":True,"snapshot_sha256":self.source["snapshot_checksum"],"actor_id":self.actor.pk,"stdout":StringIO()}
        report=self.folder/"dry.json"
        before=(CancellationProcess.objects.count(),LegacyImportRun.objects.count(),AuditEvent.objects.count())
        call_command("importar_cancelamentos_legado",dry_run=True,report=str(report),**common)
        self.assertEqual(before,(CancellationProcess.objects.count(),LegacyImportRun.objects.count(),AuditEvent.objects.count()))
        with self.assertRaises(CommandError):
            call_command("importar_cancelamentos_legado",validate=True,report=str(self.folder/"before.json"),**common)
        call_command("importar_cancelamentos_legado",apply=True,report=str(self.folder/"apply.json"),approved_report=str(report),
            backup_file=str(self.backup),backup_sha256=self.backup_hash,restore_verified=True,restore_evidence=str(self.evidence),
            confirm_production_apply=PRODUCTION_CONFIRMATION,**common)
        call_command("importar_cancelamentos_legado",validate=True,report=str(self.folder/"after.json"),**common)
        self.assertTrue(json.loads((self.folder/"after.json").read_text())["validated"])
