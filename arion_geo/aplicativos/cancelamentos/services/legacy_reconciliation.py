"""Explicit DEV reconciliation of later legacy reports, never OTRS admission."""
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from aplicativos.auditoria.services import AuditService
from ..models import (CancellationEvent, CancellationPending, CancellationProcess,
                      FinancialObligation, ISPTreatment, LegacyCancellationReference)
from .financial_totals import money_columns
from .identity import normalize_ticket_number
from .legacy_import import SOURCE, MONEY_FIELDS, PENDING_KINDS, authorize_import, digest, source_datetime
from .legacy_workbook import KINDS, STATUSES, person_name, signature, text, workbook_rows

VERSION = "workbook-delta-1"
QUEUES = {"Pedido de Cancelamento": "ORDER", "Solicitação de Cancelamento": "REQUEST",
          "Pendências de Cancelamento": "PENDING", "Concluídos": "COMPLETED"}
STATES = {"ORDER": {"Novo"}, "REQUEST": {"Novo"}, "PENDING": {"Pendência identificada"},
          "COMPLETED": {"Concluído Cancelamento", "Cancelamento confirmado"}}


def money(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result >= Decimal("1000000000000") or result != result.quantize(Decimal(".01")):
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise ValidationError("Valor financeiro inválido no relatório.") from None


def stamp(value, zone):
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is not None or not 1990 <= value.year <= 2100:
        raise ValidationError("Data/hora inválida no relatório.")
    return source_datetime(value.isoformat(), zone)


def day(value, *, historical=False):
    if value is None:
        return None
    if not isinstance(value, (date, datetime)) or (not historical and not 1990 <= value.year <= 2100):
        raise ValidationError("Data inválida no relatório.")
    return value.date() if isinstance(value, datetime) else value


def principal_evidence(path):
    """Optional separately reviewed principal fields; never derive them from pendings."""
    if not path:
        return {}
    try:
        path = Path(path)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(rows, list):
            raise ValueError
        result = {}
        for row in rows:
            if set(row) != {"ticket", "evidence", *MONEY_FIELDS} or not text(row["evidence"]):
                raise ValueError
            ticket = normalize_ticket_number(row["ticket"])
            if not ticket or ticket in result or len(str(row["evidence"])) > 500:
                raise ValueError
            result[ticket] = {"evidence": text(row["evidence"]),
                              **{key: money(row[key]) for key in MONEY_FIELDS}}
        return result
    except (OSError, ValueError, TypeError, KeyError):
        raise ValidationError("Evidência de valores principais inválida.") from None


def _pending_key(ticket, row):
    return _key(signature(ticket, row["Tipo"], row["Descrição"], money(row["Valor"]),
                          "", day(row["Data Prevista"], historical=True), row["Criada em"]))


def _key(values):
    return tuple(format(v, ".2f") if isinstance(v, Decimal) else v for v in values)


@transaction.atomic
def reconcile_workbook(path, actor, *, apply=False, approved=None, principal_path=None,
                       report_timezone="America/Sao_Paulo"):
    authorize_import(actor)
    if settings.ARION_PRODUCTION or settings.ARION_ENV != "development":
        raise ValidationError("Reconciliação histórica autorizada somente no DEV.")
    try:
        ZoneInfo(report_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("Fuso do relatório inválido.") from None
    checksum, main, pending_rows = workbook_rows(path)
    evidence = principal_evidence(principal_path)
    refs = list(LegacyCancellationReference.objects.filter(source=SOURCE, entity="cancelamento")
                .select_related("process", "import_run"))
    by_ticket = defaultdict(list)
    for ref in refs:
        by_ticket[normalize_ticket_number(ref.source_data.get("ticket_arion"))].append(ref)
    duplicates = Counter(normalize_ticket_number(row.get("Ticket Arion")) for _, row in main)
    issues, changes, found = [], [], {}
    for number, row in main:
        ticket = normalize_ticket_number(row["Ticket Arion"])
        candidates = by_ticket.get(ticket, [])
        if duplicates[ticket] != 1 or len(candidates) != 1:
            issues.append({"sheet": "Cancelamentos", "row": number, "reason": "ambiguous_or_missing_ticket"})
            continue
        ref = candidates[0]
        process = CancellationProcess.objects.select_for_update().get(pk=ref.process_id)
        if process.origin != "ARIONBROKER" or normalize_ticket_number(process.ticket_number) != ticket:
            issues.append({"row": number, "reason": "not_original_imported_identity"})
            continue
        stage = QUEUES.get(row.get("Fila Atual"))
        if stage is None or row.get("Status Atual") not in STATES[stage]:
            raise ValidationError("Fila/status não homologado no relatório.")
        total = money(row.get("Valor Final"))
        if total is None:
            raise ValidationError("Relatório sem total final informado.")
        completed = stamp(row.get("Conclusão"), report_timezone)
        effective = day(row.get("Data Cancelamento"))
        if stage != "COMPLETED" and completed is not None:
            raise ValidationError("Conclusão incompatível com a etapa do relatório.")
        obligations = list(process.obligations.all())
        known = money_columns(obligations)["total"]
        principal = evidence.get(ticket)
        financial_changes = []
        if principal:
            values = {key: principal[key] for key in MONEY_FIELDS}
            if sum((v for v in values.values() if v is not None), Decimal(0)) != total:
                issues.append({"row": number, "reason": "principal_total_mismatch"})
            for key, value in values.items():
                existing = next((o for o in obligations if o.legacy_field == key), None)
                if existing and existing.estimated_amount != value:
                    issues.append({"row": number, "reason": "confirmed_principal_conflict"})
                elif existing is None and value is not None:
                    financial_changes.append({"field": key, "amount": str(value)})
        elif (known or Decimal(0)) != total:
            issues.append({"row": number, "reason": "principal_evidence_required"})
        found[ticket] = (process, ref)
        # Only untouched imports may follow a later workbook automatically. A replay
        # is recognized by an evidence row, not by overwriting the original snapshot.
        prior = process.legacy_references.filter(source=SOURCE, entity="workbook_delta").order_by("-pk").first()
        target = {"stage": stage, "completed_at": completed.isoformat() if completed else None,
                  "effective_date": effective.isoformat() if effective else None,
                  "status": row["Status Atual"], "total": str(total)}
        if prior and prior.source_data.get("observation") == target and not financial_changes:
            if process.revision != prior.reconciliation.get("revision"):
                issues.append({"row": number, "reason": "local_edit_after_reconciliation"})
            continue
        # Preserve already imported completion timestamps and local treatment data.
        treatment = ISPTreatment.objects.filter(process=process).first()
        change_stage = process.stage != stage
        change_completed = stage == "COMPLETED" and process.completed_at is None and completed is not None
        original_effective = str(ref.source_data.get("data_cancelamento_confirmado") or "")[:10]
        change_effective = (effective is not None and (not treatment or treatment.effective_date is None)
                            and (change_stage or effective.isoformat() != original_effective))
        if not (change_stage or change_completed or change_effective or financial_changes):
            continue
        if process.revision != (prior.reconciliation.get("revision") if prior else 0):
            issues.append({"row": number, "reason": "local_revision_conflict"})
        changes.append({"process_id": process.pk, "ticket": ticket, "revision": process.revision,
                        "from_stage": process.stage, "observation": target,
                        "change_stage": change_stage, "change_completed": change_completed,
                        "change_effective": change_effective, "financial": financial_changes,
                        "evidence": principal["evidence"] if principal else "Excel original ArionBroker"})
    if set(evidence) - set(found):
        issues.append({"reason": "principal_without_unique_process", "count": len(set(evidence) - set(found))})

    # Existing rows match by timestamp/type/description/value/date, never row order.
    existing_keys = defaultdict(list)
    for ref in LegacyCancellationReference.objects.filter(source=SOURCE, pending__isnull=False).select_related("pending", "process", "import_run"):
        raw = ref.source_data
        if ref.entity == "workbook_pending":
            key = tuple(raw["match_key"])
        elif ref.entity == "pendenciacancelamento":
            parent = by_ticket.get(normalize_ticket_number(ref.process.ticket_number), [])
            zone = parent[0].import_run.counts.get("source_timezone") if len(parent) == 1 and parent[0].import_run_id else None
            if not zone:
                raise ValidationError("Pendência original sem fuso conhecido.")
            created = source_datetime(raw["criado_em"], zone).astimezone(ZoneInfo(report_timezone)).replace(tzinfo=None)
            key = signature(normalize_ticket_number(ref.process.ticket_number), KINDS[raw["tipo"]],
                            raw["descricao"], raw["valor"], "", date.fromisoformat(raw["data_prevista"]) if raw["data_prevista"] else None, created)
        else:
            day(row["Data Prevista"])
            continue
        existing_keys[_key(key)].append(ref)
    keyed_rows = [(_pending_key(normalize_ticket_number(row["Ticket Arion"]), row), number, row)
                  for number, row in pending_rows]
    occurrences = Counter(key for key, _, _ in keyed_rows)
    pending_new, matched = [], 0
    kinds = {label: PENDING_KINDS[key] for key, label in KINDS.items()}
    statuses = {"Aberta": "OPEN", "Resolvida": "RESOLVED", "Descartada": "DISCARDED"}
    for key, number, row in keyed_rows:
        ticket = normalize_ticket_number(row["Ticket Arion"])
        matches = existing_keys.get(key, [])
        if ticket not in found or occurrences[key] != 1 or len(matches) > 1:
            issues.append({"sheet": "Pendências", "row": number, "reason": "ambiguous_pending"})
            continue
        if row["Tipo"] not in kinds or row["Status"] not in statuses:
            raise ValidationError("Tipo/status de pendência não homologado.")
        if matches:
            if matches[0].pending.status != statuses[row["Status"]]:
                issues.append({"sheet": "Pendências", "row": number, "reason": "pending_status_conflict"})
            matched += 1
        else:
            pending_new.append({"process_id": found[ticket][0].pk, "key": list(key),
                                "kind": kinds[row["Tipo"]], "status": statuses[row["Status"]],
                                "created_at": stamp(row["Criada em"], report_timezone).isoformat(),
                                "creator_name": person_name(row["Criada por"])})
    payload = {"checksum": checksum, "version": VERSION, "timezone": report_timezone,
               "changes": changes, "new_pendings": pending_new}
    fingerprint = digest(payload)
    result = {"checksum": checksum, "fingerprint": fingerprint, "mode": "apply" if apply else "preview",
              "found": len(found), "stage_moves": sum(c["change_stage"] for c in changes),
              "process_updates": len(changes), "pending_matched": matched, "pending_new": len(pending_new),
              "principal_new": sum(len(c["financial"]) for c in changes),
              "target_stages": dict(Counter(QUEUES[row["Fila Atual"]] for _, row in main)),
              "issues": issues, "plan": payload}
    if not apply:
        return result
    if issues or not approved or approved.get("fingerprint") != fingerprint or approved.get("issues"):
        raise ValidationError("Apply exige preview sem conflitos e fingerprint atual; refaça a revisão.")
    for item in changes:
        process, parent = found[item["ticket"]]
        target = item["observation"]
        before = process.stage
        if item["change_stage"]:
            process.stage = target["stage"]
            process.operational_status = "BLOCKED" if process.stage == "PENDING" else ""
        if item["change_completed"]:
            process.completed_at = datetime.fromisoformat(target["completed_at"])
            # The importer is not the historical person who concluded the ticket.
        process.revision += 1
        process.save(update_fields=["stage", "operational_status", "completed_at", "revision", "updated_at"])
        if item["change_effective"]:
            treatment, _ = ISPTreatment.objects.get_or_create(process=process)
            treatment.effective_date = date.fromisoformat(target["effective_date"])
            treatment.save(update_fields=["effective_date"])
        for amount in item["financial"]:
            FinancialObligation.objects.create(process=process, kind=MONEY_FIELDS[amount["field"]][0],
                legacy_field=amount["field"], estimated_amount=Decimal(amount["amount"]),
                description="Valor principal histórico confirmado por evidência complementar",
                reconciliation_state="POSSIBLE_DUPLICATE", provider_snapshot=process.isp)
        audit = AuditService.record(user=actor, category="cancellation", module="cancelamentos",
            action="legacy_reconcile", obj=process, description="Estado histórico reconciliado com relatório posterior do ArionBroker.",
            metadata={"checksum": checksum, "from_stage": before, "to_stage": process.stage, "revision": process.revision})
        event = CancellationEvent.objects.create(process=process, action="legacy_reconcile", actor=None,
            description="Relatório posterior do ArionBroker reconciliado; não comprova pagamento nem confirmação operacional do ISP.",
            audit_event=audit, details={"source": "ArionBroker", "checksum": checksum,
                "from_stage": before, "to_stage": process.stage, "historical_completed_at": target["completed_at"]})
        source_data = {"observation": target, "evidence": item["evidence"]}
        LegacyCancellationReference.objects.create(source=SOURCE, entity="workbook_delta",
            legacy_pk=f"{checksum}:{process.pk}", process=process, event=event, batch=checksum,
            mapping_version=VERSION, checksum=digest(source_data), source_data=source_data,
            reconciliation={"revision": process.revision})
    for item in pending_new:
        key = item["key"]
        pending = CancellationPending.objects.create(process_id=item["process_id"], kind=item["kind"],
            description=key[2], amount=money(key[3]), status=item["status"],
            expected_date=date.fromisoformat(key[5]) if key[5] else None,
            created_at=datetime.fromisoformat(item["created_at"]))
        source_data = {"match_key": key, "creator_name": item["creator_name"], "individual_id_unavailable": True}
        LegacyCancellationReference.objects.create(source=SOURCE, entity="workbook_pending",
            legacy_pk=digest(key), process_id=item["process_id"], pending=pending, batch=checksum,
            mapping_version=VERSION, checksum=digest(source_data), source_data=source_data)
        AuditService.record(user=actor, category="cancellation", module="cancelamentos", action="legacy_pending_reconcile",
            obj=pending, description="Pendência histórica adicional identificada no Excel por assinatura única.",
            metadata={"checksum": checksum, "process_id": item["process_id"]})
    return result
