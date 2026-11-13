"""ArionBroker facts -> existing CancellationProcess, without workflow side effects."""
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import date, datetime, timezone as utc_timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror, IntegrationLease, OTRSTicket, OTRSTicketConfigItem
from aplicativos.integracoes.services.sync_lock import Lease

from ..models import (CancellationEvent, CancellationIntake, CancellationPending, CancellationProcess,
                      FinancialObligation, ISPTreatment, LegacyCancellationReference, LegacyImportRun)
from .legacy_dump import DumpError, MAIN, PREFIX, TABLE_COLUMNS, read_dump, validate_schema
from .identity import normalize_ticket_number
from .legacy_import_policy import (PRODUCTION_DATABASE, production_environment, production_source,
                                  production_restore_evidence, external_private_file)


SOURCE = "arionbroker"
MAPPING_VERSION = "2026-11-09.1"
STAGES = {"PEDIDO": "ORDER", "SOLICITACAO": "REQUEST", "PENDENCIA": "PENDING", "CONCLUIDO": "COMPLETED"}
STATUSES = {"PEDIDO": {"NOVO"}, "SOLICITACAO": {"NOVO"}, "PENDENCIA": {"PENDENCIA_IDENTIFICADA"},
            "CONCLUIDO": {"CONCLUIDO_CANCELAMENTO", "CANCELAMENTO_CONFIRMADO"}}
PENDING_KINDS = {"mensalidades_atrasadas": "OVERDUE", "multa_contrato": "CONTRACT_FINE",
                 "multa_equipamento": "EQUIPMENT_FINE", "outros": "OTHER"}
PENDING_STATUSES = {"ABERTA": "OPEN", "RESOLVIDA": "RESOLVED", "DESCARTADA": "DISCARDED"}
MONEY_FIELDS = {"valor_multa_rescisao": ("CONTRACT", ("multa_contrato",)),
                "valor_equipamento": ("EQUIPMENT", ("multa_equipamento",)),
                "valor_outros": ("OTHER", ("outros", "mensalidades_atrasadas"))}
HISTORY_TYPES = {"CRIACAO", "APROPRIACAO", "ALTERACAO_STATUS", "OBSERVACAO", "MUDANCA_FILA", "LIBERACAO",
                 "PENDENCIA_CRIADA", "VALOR_ALTERADO", "PENDENCIA_RESOLVIDA", "CONCLUSAO", "EDICAO"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def source_datetime(value, source_timezone):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    zone = ZoneInfo(source_timezone)
    # A duplicated/missing wall time needs explicit correction instead of a guessed offset.
    first, second = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise DumpError("Timestamp legado ambíguo no fuso informado.")
    return first.astimezone(utc_timezone.utc)


def source_date(value):
    if value is None:
        return None
    parsed = date.fromisoformat(value)
    return parsed if parsed.year >= 1000 else None


def load_source(directory, dump_name, source_timezone):
    directory = Path(directory or os.environ.get("ARION_CANCELAMENTOS_MIGRATION_DIR", "")).expanduser()
    if not str(directory) or str(directory) == ".":
        raise DumpError("Informe --source-dir ou ARION_CANCELAMENTOS_MIGRATION_DIR.")
    directory = directory.resolve(strict=True)
    dump_path = (directory / dump_name).resolve(strict=True)
    if dump_path.parent != directory:
        raise DumpError("O dump precisa estar dentro do diretório de origem informado.")
    tables, checksum = read_dump(dump_path)
    schema_checksum = validate_schema(directory / "cancelamentos-schema.sql", tables)
    ZoneInfo(source_timezone)
    errors, warnings, numbers = [], [], set()
    for row in tables[MAIN]:
        pk = row["id"]
        number = normalize_ticket_number(row["ticket_arion"])
        if not number or len(number) > 80 or number in numbers:
            errors.append({"entity": "cancelamento", "id": pk, "reason": "ticket_number_empty_or_duplicate"})
        numbers.add(number)
        if row["fila"] not in STAGES or row["status"] not in STATUSES.get(row["fila"], ()):
            errors.append({"entity": "cancelamento", "id": pk, "reason": "unmapped_queue_or_status"})
        if datetime.fromisoformat(row["criado_em"]).year != 2026:
            errors.append({"entity": "cancelamento", "id": pk, "reason": "registration_outside_2026"})
        if row["fila"] == "CONCLUIDO" and not row["concluido_em"]:
            warnings.append({"entity": "cancelamento", "id": pk, "reason": "historical_completion_without_date"})
        for field, value in row.items():
            if value and (field.startswith("data_") or field == "agendamento") and len(str(value)) == 10 and source_date(value) is None:
                warnings.append({"entity": "cancelamento", "id": pk, "field": field, "reason": "date_year_below_1000_preserved_raw"})
        for field in MONEY_FIELDS:
            if row[field] is not None and Decimal(str(row[field])) < 0:
                errors.append({"entity": "cancelamento", "id": pk, "field": field, "reason": "negative_amount"})
        for field in ("criado_em", "atualizado_em", "apropriado_em", "concluido_em"):
            if row[field]:
                source_datetime(row[field], source_timezone)
        created = source_datetime(row["criado_em"], source_timezone)
        if any(row[field] and source_datetime(row[field], source_timezone) < created for field in ("atualizado_em", "concluido_em")):
            warnings.append({"entity": "cancelamento", "id": pk, "reason": "historical_timestamp_before_registration_preserved"})
    for table, rows in tables.items():
        for row in rows:
            if table != MAIN:
                source_datetime(row["movido_em" if table.endswith("historicofila") else "criado_em"], source_timezone)
            if table.endswith("historicocancelamento") and row["tipo"] not in HISTORY_TYPES:
                errors.append({"entity": "historicocancelamento", "id": row["id"], "reason": "unmapped_history_type"})
            if table.endswith("pendenciacancelamento"):
                if row["tipo"] not in PENDING_KINDS or row["status"] not in PENDING_STATUSES:
                    errors.append({"entity": "pendenciacancelamento", "id": row["id"], "reason": "unmapped_pending_type_or_status"})
                if row["valor"] is not None and Decimal(str(row["valor"])) < 0:
                    errors.append({"entity": "pendenciacancelamento", "id": row["id"], "reason": "negative_amount"})
                source_datetime(row["resolvida_em"], source_timezone)
                if row["status"] == "RESOLVIDA" and not row["resolvida_em"]:
                    warnings.append({"entity": "pendenciacancelamento", "id": row["id"], "reason": "resolved_without_source_timestamp"})
                if row["resolvida_em"] and source_datetime(row["resolvida_em"], source_timezone) < source_datetime(row["criado_em"], source_timezone):
                    warnings.append({"entity": "pendenciacancelamento", "id": row["id"], "reason": "resolution_before_creation_preserved"})
    users = sorted({row[field] for rows in tables.values() for row in rows for field in row
                    if field.endswith("_por_id") or field in ("responsavel_id", "atendente_id") if row[field] is not None})
    return {"tables": tables, "snapshot_checksum": checksum, "schema_checksum": schema_checksum,
            "source_timezone": source_timezone, "errors": errors, "warnings": warnings, "legacy_user_ids": users,
            "source_paths": {"dump": str(dump_path), "schema": str(directory / "cancelamentos-schema.sql")}}


def source_summary(source):
    tables = source["tables"]
    principal = tables[MAIN]
    pendings = tables[PREFIX + "pendenciacancelamento"]
    return {"mapping_version": MAPPING_VERSION, "snapshot_checksum": source["snapshot_checksum"],
            "schema_checksum": source["schema_checksum"], "source_timezone": source["source_timezone"],
            "counts": {name.removeprefix(PREFIX): len(rows) for name, rows in tables.items()},
            "total": sum(len(rows) for rows in tables.values()),
            "stages": dict(Counter(row["fila"] for row in principal)),
            "queue_status": [{"queue": queue, "status": status, "count": count} for (queue, status), count in Counter((r["fila"], r["status"]) for r in principal).items()],
            "pending_status": dict(Counter(row["status"] for row in pendings)),
            "opening_years": dict(Counter(str(row["data_criacao_chamado"] or "")[:4] for row in principal)),
            "financial_fields": {field: {"non_null": sum(r[field] is not None for r in principal),
                                         "zero": sum(r[field] is not None and Decimal(str(r[field])) == 0 for r in principal)} for field in MONEY_FIELDS},
            "legacy_user_ids_without_names": source["legacy_user_ids"], "warnings": source["warnings"], "errors": source["errors"]}


def destination_status(allow_sqlite_dev=False, *, production=False):
    config = connection.settings_dict
    issues = []
    production_environment(production, allow_sqlite_dev)
    expected_database = PRODUCTION_DATABASE if production else "arion_geo_dev"
    if production and connection.vendor != "mysql":
        raise ValidationError("Importação produtiva aceita somente MySQL 8 local no schema arion_geo.")
    db_name = str(config["NAME"])
    if connection.vendor == "sqlite":
        db_name = str(config["NAME"])
        is_memory = db_name == ":memory:" or "memory" in db_name or getattr(connection, "is_in_memory_db", lambda: False)()
        if not allow_sqlite_dev:
            issues.append("destination_requires_mysql8_dev")
        else:
            if not is_memory:
                db_path = Path(config["NAME"]).resolve()
                if not db_path.exists():
                    issues.append("sqlite_database_not_found")
                elif "prod" in str(db_path).lower() or getattr(settings, "ARION_PRODUCTION", False):
                    raise ValidationError("Destino SQLite identificado como produção; bloqueado.")
            try:
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA integrity_check")
                    row = cursor.fetchone()
                    if not row or row[0] != "ok":
                        issues.append("sqlite_integrity_check_failed")
            except Exception:
                issues.append("sqlite_integrity_check_failed")
    elif connection.vendor != "mysql":
        issues.append("destination_requires_mysql8_dev")
    elif (config["NAME"] != expected_database or config.get("HOST", "").lower() not in ("127.0.0.1", "localhost", "::1")
          or not config.get("USER") or config["USER"].casefold() == "root"):
        raise ValidationError(f"Destino recusado: exige schema {expected_database}, loopback e usuário exclusivo sem root.")
    if connection.vendor == "mysql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE(), VERSION(), @@read_only, @@character_set_database, @@sql_mode")
            name, version, read_only, charset, sql_mode = cursor.fetchone()
            expected_tables = [model._meta.db_table for model in (CancellationProcess, CancellationIntake, CancellationEvent,
                CancellationPending, FinancialObligation, ISPTreatment, LegacyImportRun, LegacyCancellationReference,
                AuditEvent, IntegrationLease, CMDBLinkMirror, OTRSTicket, OTRSTicketConfigItem)]
            placeholders = ",".join(["%s"] * len(expected_tables))
            cursor.execute(f"SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN ({placeholders})", [name, *expected_tables])
            engines = dict(cursor.fetchall())
        if name != expected_database or not str(version).startswith("8.") or read_only:
            issues.append("mysql8_dev_identity_or_write_mode_invalid")
        if charset != "utf8mb4" or not {"STRICT_TRANS_TABLES", "STRICT_ALL_TABLES"}.intersection(str(sql_mode).split(",")):
            issues.append("mysql_utf8mb4_or_strict_mode_required")
        if any(engines.get(table) != "InnoDB" for table in expected_tables):
            issues.append("mysql_transactional_tables_missing_or_invalid")
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if pending:
        issues.append("destination_has_pending_migrations")
    return {"engine": connection.vendor, "database": db_name, "issues": issues,
            "pending_migrations": [f"{migration.app_label}.{migration.name}" for migration, _ in pending]}


def build_plan(source, allow_sqlite_dev=False, *, production=False, snapshot_checksum=None):
    """Read-only reconciliation. Works against the pre-migration DEV schema too."""
    destination = destination_status(allow_sqlite_dev=allow_sqlite_dev, production=production)
    source_identity = production_source(source, snapshot_checksum) if production else None
    mirrors = defaultdict(list)
    for row in OTRSTicket.objects.values("pk", "external_id", "ticket_number"):
        mirrors[normalize_ticket_number(row["ticket_number"])].append(row)
    intakes = {row["ticket_external_id"]: row for row in CancellationIntake.objects.values("pk", "ticket_external_id", "status", "revision")}
    process_by_intake = {row["intake_id"]: row for row in CancellationProcess.objects.values("pk", "intake_id", "revision") if row["intake_id"]}
    schema_ready = not destination["pending_migrations"]
    reference_fields = ["entity", "legacy_pk", "checksum", "process_id", "mapping_version"]
    if schema_ready:
        reference_fields += ["source_data", "event_id", "pending_id"]
    references = {(r["entity"], r["legacy_pk"]): r for r in LegacyCancellationReference.objects.filter(source=SOURCE).values(*reference_fields)}
    process_by_number = {}
    if schema_ready:
        process_by_number = {r["ticket_number"]: r for r in CancellationProcess.objects.exclude(ticket_number=None).values("pk", "intake_id", "ticket_number", "revision")}
    mapped_amounts = set(FinancialObligation.objects.exclude(legacy_field=None).values_list("process_id", "legacy_field")) if schema_ready else set()
    links = {r["pk"]: r for r in CMDBLinkMirror.objects.filter(is_present_in_source=True).values(
        "pk", "source_config_item_id", "designation", "circuit", "contracting_client", "business_project", "legacy_project")}
    by_designation = defaultdict(set)
    for pk, link in links.items():
        for field in ("designation", "circuit"):
            if link[field]:
                by_designation[link[field]].add(pk)
    relations = defaultdict(list)
    matched_ids = {r["pk"] for row in source["tables"][MAIN] for r in mirrors[normalize_ticket_number(row["ticket_arion"])]}
    for row in OTRSTicketConfigItem.objects.filter(ticket_id__in=matched_ids, is_present_in_source=True).values("ticket_id", "cmdb_link_id"):
        relations[row["ticket_id"]].append(row["cmdb_link_id"])
    children = defaultdict(list)
    for table, rows in source["tables"].items():
        if table != MAIN:
            for row in rows:
                children[row["cancelamento_id"]].append((table.removeprefix(PREFIX), row))
    decisions = []
    for row in source["tables"][MAIN]:
        number = normalize_ticket_number(row["ticket_arion"])
        matches = mirrors[number]
        ticket = matches[0] if len(matches) == 1 else None
        entry = intakes.get(ticket["external_id"]) if ticket else None
        official = (process_by_intake.get(entry["pk"]) if entry else None) or process_by_number.get(number)
        ref = references.get(("cancelamento", str(row["id"])))
        item = {"id": row["id"], "result": "new", "reason": "", "ticket_pk": ticket["pk"] if ticket else None,
                "ticket_external_id": ticket["external_id"] if ticket else None,
                "intake_id": entry["pk"] if entry else None, "intake_revision": entry["revision"] if entry else None,
                "link_id": None, "link_method": "unresolved", "process_id": ref["process_id"] if ref else None,
                "child_count": len(children[row["id"]])}
        if ticket:
            candidates = relations[ticket["pk"]]
            if len(candidates) == 1 and candidates[0] in links:
                item["link_id"], item["link_method"] = candidates[0], "ticket_config_item"
            elif candidates:
                item["link_method"] = "ambiguous_ticket_config_items"
        if not item["link_id"] and item["link_method"] == "unresolved":
            candidates = by_designation.get(row["designacao_circuito"], set())
            if len(candidates) == 1:
                link_id = next(iter(candidates))
                if links[link_id]["contracting_client"].strip().casefold() == row["cliente"].strip().casefold() and row["cliente"].strip():
                    item["link_id"], item["link_method"] = link_id, "exact_circuit_and_client"
        expected = [("cancelamento", row), *children[row["id"]]]
        if ref:
            if schema_ready and all((r := references.get((entity, str(data["id"])))) and r["checksum"] == digest(data)
                   and digest(r["source_data"]) == r["checksum"] and r["mapping_version"] == MAPPING_VERSION
                   and r["process_id"] == ref["process_id"] and (entity == "cancelamento" or
                        bool(r["pending_id"] if entity == "pendenciacancelamento" else r["event_id"])) for entity, data in expected):
                item["result"] = "existing"
                if any(row[field] is not None and (ref["process_id"], field) not in mapped_amounts for field in MONEY_FIELDS):
                    item.update(result="conflict", reason="incomplete_financial_mapping")
            else:
                item.update(result="conflict", reason="source_changed_or_incomplete_previous_mapping")
        elif len(matches) > 1:
            item.update(result="conflict", reason="ambiguous_ticket_number")
        elif official:
            item.update(result="conflict", reason="existing_official_process", process_id=official["pk"])
        elif entry and entry["status"] != "WAITING":
            item.update(result="conflict", reason="existing_human_decision")
        source_errors = source["errors"]
        child_keys = {(entity, data["id"]) for entity, data in expected}
        if any((error["entity"], error["id"]) in child_keys for error in source_errors):
            item.update(result="invalid", reason="source_validation_error")
        decisions.append(item)
    outcomes = {row["id"]: row["result"] for row in decisions}
    entity_counts = {}
    for table, rows in source["tables"].items():
        counts = Counter(outcomes[r["id"] if table == MAIN else r["cancelamento_id"]] for r in rows)
        entity_counts[table.removeprefix(PREFIX)] = {"extracted": len(rows), **{key: counts[key] for key in ("new", "existing", "conflict", "invalid")}}
    result = {**source_summary(source), "destination": destination, "records": decisions,
              "reconciliation": dict(Counter(item["result"] for item in decisions)),
              "entities": entity_counts,
              "otrs_matched": sum(bool(r["ticket_pk"]) for r in decisions),
              "cmdb_matched": sum(bool(r["link_id"]) for r in decisions)}
    financial_counts = Counter(outcomes[row["id"]] for row in source["tables"][MAIN] for field in MONEY_FIELDS if row[field] is not None)
    result["derived_financial_values"] = {"expected": sum(financial_counts.values()),
        **{key: financial_counts[key] for key in ("new", "existing", "conflict", "invalid")}}
    if production:
        result.update(production_import=True, source_identity=source_identity,
            intakes_matched=sum(bool(r["intake_id"]) for r in decisions),
            ambiguous_links=sum(r["link_method"].startswith("ambiguous") for r in decisions),
            blocking_conflicts=[{"id": r["id"], "reason": r["reason"]} for r in decisions if r["result"] in {"invalid", "conflict"}],
            financial_totals={field: str(sum((Decimal(str(r[field])) for r in source["tables"][MAIN]
                                           if r[field] is not None), Decimal("0.00"))) for field in MONEY_FIELDS},
            financial_unknown_counts={field: sum(r[field] is None for r in source["tables"][MAIN]) for field in MONEY_FIELDS})
    result["ready_for_apply"] = not source["errors"] and not destination["issues"] and not any(r["result"] == "conflict" for r in decisions)
    result["plan_fingerprint"] = digest(result)
    return result


def authorize_import(actor):
    if not actor or not actor.is_active or not actor.has_perm("cancelamentos.import_cancellation_history") or not actor.has_perm("cancelamentos.view_cancellations") or not DataScopeService.is_global(actor):
        raise PermissionDenied("Importação exige conta ativa, permission de importação/consulta e escopo global.")


def _reference(run, entity, row, process, **destinations):
    return LegacyCancellationReference.objects.create(source=SOURCE, entity=entity, legacy_pk=str(row["id"]),
        process=process, batch=run.batch, mapping_version=MAPPING_VERSION, checksum=digest(row),
        source_data=row, import_run=run, **destinations)


@transaction.atomic
def _apply_record(source, item, run):
    """One complete parent tree per transaction. Called only behind apply_plan's guards."""
    row = next(r for r in source["tables"][MAIN] if r["id"] == item["id"])
    if LegacyCancellationReference.objects.filter(source=SOURCE, entity="cancelamento", legacy_pk=str(row["id"])).exists():
        raise ValidationError("A identidade legada mudou durante o Apply; refaça o dry-run.")
    entry = CancellationIntake.objects.select_for_update().filter(pk=item["intake_id"]).first() if item["intake_id"] else None
    if item["intake_id"] and not entry:
        raise ValidationError("Entrada removida após o dry-run; refaça a reconciliação.")
    if entry and (entry.revision != item["intake_revision"] or entry.status != "WAITING" or CancellationProcess.objects.filter(intake=entry).exists()):
        raise ValidationError("Entrada alterada após o dry-run; nenhuma decisão será sobrescrita.")
    ticket = OTRSTicket.objects.select_for_update().filter(pk=item["ticket_pk"]).first() if item["ticket_pk"] else None
    if item["ticket_pk"] and (not ticket or normalize_ticket_number(ticket.ticket_number) != normalize_ticket_number(row["ticket_arion"])):
        raise ValidationError("Identidade OTRS alterada após o dry-run.")
    link = CMDBLinkMirror.objects.select_for_update().filter(pk=item["link_id"], is_present_in_source=True).first() if item["link_id"] else None
    if item["link_id"] and not link:
        raise ValidationError("Vínculo CMDB deixou de existir; refaça a reconciliação.")
    if link and item["link_method"] == "ticket_config_item":
        candidates = list(OTRSTicketConfigItem.objects.select_for_update().filter(ticket=ticket, is_present_in_source=True).values_list("cmdb_link_id", flat=True))
        if candidates != [link.pk]:
            raise ValidationError("Relações Ticket–CI mudaram após o dry-run.")
    if link and item["link_method"] == "exact_circuit_and_client" and (row["designacao_circuito"] not in (link.designation, link.circuit) or row["cliente"].strip().casefold() != link.contracting_client.strip().casefold()):
        raise ValidationError("Identidade do circuito mudou após o dry-run.")
    zone = source["source_timezone"]
    process = CancellationProcess.objects.create(intake=entry, origin="ARIONBROKER", source_ticket=ticket,
        ticket_number=normalize_ticket_number(row["ticket_arion"]), ticket_opened_on=source_date(row["data_criacao_chamado"]),
        stage=STAGES[row["fila"]], link=link, config_item_id=link.source_config_item_id if link else "",
        contracting_client=row["cliente"], final_client=row["cliente_final"], project=(link.business_project or link.legacy_project) if link else "",
        designation=row["designacao_circuito"], isp=row["isp"], city=row["cidade"], state=row["uf"],
        created_at=source_datetime(row["criado_em"], zone), completed_at=source_datetime(row["concluido_em"], zone))
    CancellationProcess.objects.filter(pk=process.pk).update(updated_at=source_datetime(row["atualizado_em"], zone))
    ISPTreatment.objects.create(process=process, protocol=row["protocolo"], contact=row["contato_isp"])
    parent = _reference(run, "cancelamento", row, process, reconciliation={
        "link_method": item["link_method"], "otrs_matched": bool(ticket),
        "historical_completion_needs_review": row["fila"] == "CONCLUIDO" and not row["concluido_em"],
        "opening_date_needs_review": bool(row["data_criacao_chamado"] and not process.ticket_opened_on)})
    pending_rows = [r for r in source["tables"][PREFIX + "pendenciacancelamento"] if r["cancelamento_id"] == row["id"]]
    for field, (kind, pending_kinds) in MONEY_FIELDS.items():
        if row[field] is not None:
            FinancialObligation.objects.create(process=process, kind=kind, description=f"Valor histórico ArionBroker — {field}",
                estimated_amount=Decimal(str(row[field])), legacy_field=field,
                reconciliation_state="POSSIBLE_DUPLICATE" if any(p["tipo"] in pending_kinds for p in pending_rows) else "UNRECONCILED",
                provider=link.operator if link else None, provider_snapshot=row["isp"],
                reference_number=f"ArionBroker:{row['id']}:{field}", created_at=process.created_at)
    for table, rows in source["tables"].items():
        if table == MAIN:
            continue
        entity = table.removeprefix(PREFIX)
        for child in rows:
            if child["cancelamento_id"] != row["id"]:
                continue
            if entity == "pendenciacancelamento":
                pending = CancellationPending.objects.create(process=process, kind=PENDING_KINDS[child["tipo"]],
                    description=child["descricao"], amount=Decimal(str(child["valor"])) if child["valor"] is not None else None,
                    status=PENDING_STATUSES[child["status"]], expected_date=source_date(child["data_prevista"]),
                    created_at=source_datetime(child["criado_em"], zone), resolved_at=source_datetime(child["resolvida_em"], zone))
                _reference(run, entity, child, process, pending=pending)
            else:
                movement = entity == "historicofila"
                description = (f"{child['fila_origem'] or 'Sem fila anterior'} → {child['fila_destino']}" if movement
                               else child["texto"] if entity == "observacaocancelamento" else child["descricao"])
                actor_key = "movido_por_id" if movement else "criado_por_id" if entity == "observacaocancelamento" else "realizado_por_id"
                event = CancellationEvent.objects.create(intake=entry, process=process, actor=None,
                    created_at=source_datetime(child["movido_em" if movement else "criado_em"], zone),
                    action="legacy_" + entity, description=description,
                    # Unstructured imported text has not been reviewed for financial disclosure.
                    financial=not movement, details={"source": "ArionBroker", "legacy_id": child["id"],
                        "legacy_actor_id": child[actor_key], "legacy_type": child.get("tipo", ""),
                        **({"from_queue": child["fila_origem"], "to_queue": child["fila_destino"]} if movement else {})})
                _reference(run, entity, child, process, event=event)
    AuditService.record(user=run.actor, category="cancellation", module="cancelamentos", action="legacy_import",
        obj=process, description="Processo importado do ArionBroker; fatos anteriores preservados na origem histórica.",
        metadata={"batch": run.batch, "legacy_id": row["id"], "mapping_version": MAPPING_VERSION})
    return parent.process_id


def apply_plan(source, actor, approved_report, backup_path, backup_checksum, restore_verified, allow_sqlite_dev=False,
               *, production=False, snapshot_checksum=None, confirm_production_apply=False, restore_evidence=None):
    authorize_import(actor)
    status = destination_status(allow_sqlite_dev=allow_sqlite_dev, production=production)
    evidence_checksum = None
    if production:
        production_source(source, snapshot_checksum)
        if not confirm_production_apply:
            raise ValidationError("Apply produtivo exige confirmação explícita específica.")
        if not restore_evidence:
            raise ValidationError("Apply produtivo exige arquivo privado de evidência do restore testado.")
        evidence_checksum = production_restore_evidence(restore_evidence, backup_checksum)
    if status["issues"]:
        if "destination_requires_mysql8_dev" in status["issues"]:
            raise ValidationError("Apply bloqueado: exige MySQL 8 DEV validado ou autorização expressa SQLite DEV e migrations aplicadas.")
        raise ValidationError(f"Apply bloqueado por pendências de destino: {', '.join(status['issues'])}.")
    if not restore_verified:
        raise ValidationError("Valide a restauração do backup antes do Apply.")
    backup = Path(backup_path).expanduser().resolve(strict=True)
    if production:
        external_private_file(backup)
    hasher = hashlib.sha256()
    with backup.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    if backup.stat().st_size < 100 or hasher.hexdigest() != backup_checksum:
        raise ValidationError("Backup ausente ou checksum divergente.")
    if connection.vendor == "mysql":
        with backup.open("r", encoding="utf-8-sig") as stream:
            header = stream.read(8192)
        expected_database = PRODUCTION_DATABASE if production else "arion_geo_dev"
        if not re.search(r"Database:\s+" + re.escape(expected_database) + r"(?:\s|$)", header) or "MySQL dump" not in header:
            raise ValidationError(f"O backup precisa identificar o schema MySQL {expected_database}.")
    elif connection.vendor == "sqlite":
        with backup.open("rb") as stream:
            header = stream.read(16)
        if header != b"SQLite format 3\x00":
            raise ValidationError("O backup precisa ser um arquivo SQLite válido.")
        import sqlite3
        check_conn = None
        try:
            check_conn = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
            row = check_conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise ValidationError("Falha de integridade no arquivo de backup SQLite.")
        except Exception as exc:
            if isinstance(exc, ValidationError):
                raise
            raise ValidationError(f"Falha ao validar integridade do backup SQLite: {exc}")
        finally:
            if check_conn:
                check_conn.close()
    else:
        raise ValidationError("Vendor de banco não suportado para importação.")
    if not approved_report.get("ready_for_apply"):
        raise ValidationError("O dry-run aprovado não está apto ao Apply.")
    if production and (approved_report.get("mode") != "dry-run" or not approved_report.get("production_import")):
        raise ValidationError("Apply produtivo exige relatório Dry-run emitido no mesmo modo produtivo.")
    lease = Lease("cancellation_legacy_import")
    if not lease.acquire():
        raise ValidationError("Outra importação histórica está em execução.")
    run = None
    try:
        plan = build_plan(source, allow_sqlite_dev=allow_sqlite_dev, production=production, snapshot_checksum=snapshot_checksum)
        if plan["plan_fingerprint"] != approved_report.get("plan_fingerprint") or not plan["ready_for_apply"]:
            raise ValidationError("A origem ou o destino mudaram; gere um novo dry-run.")
        run = LegacyImportRun.objects.create(batch=uuid4().hex, snapshot_checksum=source["snapshot_checksum"],
            schema_checksum=source["schema_checksum"], mapping_version=MAPPING_VERSION, actor=actor,
            counts={"source_timezone": source["source_timezone"]})
        with transaction.atomic() if production else nullcontext():
            authorize_import(actor)
            # Recheck the approved universe within the production transaction.
            current = build_plan(source, allow_sqlite_dev=allow_sqlite_dev, production=production, snapshot_checksum=snapshot_checksum)
            if current["plan_fingerprint"] != plan["plan_fingerprint"]:
                raise ValidationError("Destino alterado ao iniciar transação; refaça o Dry-run.")
            imported = 0
            for item in plan["records"]:
                if not lease.heartbeat():
                    raise ValidationError("Lease perdida; reexecute preview e dry-run para retomar.")
                if item["result"] == "new":
                    _apply_record(source, item, run)
                    imported += 1
            final = build_plan(source, allow_sqlite_dev=allow_sqlite_dev, production=production, snapshot_checksum=snapshot_checksum)
            if final["reconciliation"].get("existing", 0) != len(source["tables"][MAIN]):
                raise ValidationError("Reconciliação final incompleta; examine o relatório privado.")
            run.status, run.finished_at = "SUCCESS", timezone.now()
            run.counts = {"imported_processes": imported, "source_counts": final["counts"], "existing": final["reconciliation"]["existing"],
                         "source_timezone": source["source_timezone"], "backup_sha256": backup_checksum,
                         **({"production_import": True, "restore_evidence_sha256": evidence_checksum,
                             "source_identity": final["source_identity"]} if production else {})}
            run.save()
        return {**final, "batch": run.batch, "applied": imported}
    except Exception:
        if run:
            run.status, run.finished_at = "FAILED", timezone.now()
            run.save(update_fields=("status", "finished_at"))
            if production:
                AuditService.record(user=actor, category="cancellation", module="cancelamentos", action="legacy_import_failed",
                    result="FAILED", obj=run, description="Importação produtiva revertida integralmente.",
                    metadata={"batch": run.batch, "snapshot_sha256": source["snapshot_checksum"]})
        raise
    finally:
        lease.release()


def private_report_path(path):
    path = Path(path).expanduser().resolve()
    root = Path(settings.BASE_DIR).parent.resolve()
    if path.suffix.lower() != ".json":
        raise ValidationError("O relatório privado precisa ter extensão .json.")
    if path.is_relative_to(root) and not path.is_relative_to(root / "var"):
        raise ValidationError("Relatórios no checkout devem ficar exclusivamente em var/, fora de static/media e do Git.")
    return path


def write_private_report(path, report):
    path = private_report_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
