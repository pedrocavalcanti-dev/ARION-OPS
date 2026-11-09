"""Evidence-only name enrichment; imported domain facts remain immutable."""
import hashlib
from io import BytesIO
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from xml.etree.ElementTree import ParseError
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from aplicativos.auditoria.services import AuditService
from ..models import LegacyCancellationReference, LegacyPerson
from .legacy_import import SOURCE, authorize_import
from .identity import normalize_ticket_number

KINDS = {"mensalidades_atrasadas": "Mensalidades Atrasadas", "multa_contrato": "Multa Contrato",
         "multa_equipamento": "Multa Equipamento", "outros": "Outros (digitar)"}
STATUSES = {"ABERTA": "Aberta", "RESOLVIDA": "Resolvida", "DESCARTADA": "Descartada"}
EMPTY_NAMES = {"", "sem responsável", "não atribuído", "não informado", "nome não disponível", "—", "-"}


def text(value):
    return " ".join(str(value or "").split())


def person_name(value):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 150 or any(ord(c) < 32 for c in value):
        raise ValueError
    value = text(value)
    if value.startswith("="):
        raise ValueError
    return "" if value.casefold() in EMPTY_NAMES else value


def sheet_rows(sheet, required):
    # The report has a title and subtitle before the header. No formula evaluation.
    if sheet.max_row is None or sheet.max_row > 5010 or sheet.max_column is None or sheet.max_column > 100:
        raise ValueError
    rows = iter(sheet.iter_rows(values_only=True))
    header = None
    for _ in range(10):
        row = next(rows, ())
        if set(required).issubset(set(row)):
            header = row
            break
    if header is None or any(header.count(key) != 1 for key in required):
        raise ValueError
    result = []
    for number, row in enumerate(rows, start=_ + 2):
        if not any(v is not None for v in row):
            continue
        if len(result) >= 5000:
            raise ValueError
        item = dict(zip(header, row))
        if any(isinstance(item.get(key), str) and item[key].startswith("=") for key in required):
            raise ValueError
        result.append((number, item))
    return result


def workbook_rows(path):
    path = Path(path)
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError
        data = path.read_bytes()
        if len(data) > 10 * 1024 * 1024:
            raise ValueError
        with ZipFile(BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 40 * 1024 * 1024:
                raise ValueError
        checksum = hashlib.sha256(data).hexdigest()
        # In-memory input also releases the OS file handle if parsing a sheet fails.
        book = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
        try:
            main = sheet_rows(book["Cancelamentos"], ("Ticket Arion", "Responsável"))
            pending = sheet_rows(book["Pendências"], ("Ticket Arion", "Tipo", "Descrição", "Valor", "Status",
                                "Data Prevista", "Criada em", "Criada por", "Responsável"))
            return checksum, main, pending
        finally:
            book.close()
    except (OSError, ValueError, KeyError, TypeError, BadZipFile, InvalidOperation, InvalidFileException, ParseError):
        raise ValidationError("Excel inválido: confira as abas, colunas, tamanho e valores do relatório original.") from None


def signature(ticket, kind, description, amount, status, expected, created):
    def day(value):
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        return date.fromisoformat(str(value)).isoformat()
    if not isinstance(created, datetime):
        created = datetime.fromisoformat(str(created))
    if created.tzinfo is not None:
        raise ValueError
    return (normalize_ticket_number(ticket), text(kind), text(description),
            None if amount is None else Decimal(str(amount)), text(status), day(expected),
            created.replace(microsecond=0).isoformat())


@transaction.atomic
def import_workbook(path, actor, *, apply=False, report_timezone="America/Sao_Paulo"):
    authorize_import(actor)
    if settings.ARION_PRODUCTION or settings.ARION_ENV != "development":
        raise ValidationError("Enriquecimento autorizado somente no DEV.")
    try:
        zone = ZoneInfo(report_timezone)
    except (KeyError, ValueError):
        raise ValidationError("Fuso do relatório inválido.") from None
    checksum, main_rows, pending_rows = workbook_rows(path)
    main_refs = list(LegacyCancellationReference.objects.filter(source=SOURCE, entity="cancelamento").select_related("import_run", "process"))
    by_ticket = defaultdict(list)
    process_refs = defaultdict(list)
    for ref in main_refs:
        if normalize_ticket_number(ref.process.ticket_number) != normalize_ticket_number(ref.source_data.get("ticket_arion")):
            continue
        by_ticket[normalize_ticket_number(ref.source_data.get("ticket_arion"))].append(ref)
        process_refs[ref.process_id].append(ref)
    by_process = {pk: refs[0] for pk, refs in process_refs.items() if len(refs) == 1}
    counts = {"processes": Counter(), "pendings": Counter()}
    issues, evidence, roles = [], defaultdict(list), defaultdict(list)

    def observe(pk, value, sheet, row):
        try:
            name = person_name(value)
        except ValueError:
            issues.append({"sheet": sheet, "row": row, "reason": "invalid_name"})
            return
        if name and pk is not None and type(pk) is int and pk > 0:
            evidence[pk].append((name, sheet, row))
        elif name:
            issues.append({"sheet": sheet, "row": row, "reason": "missing_legacy_user_id"})

    tickets = Counter(normalize_ticket_number(row.get("Ticket Arion")) for _, row in main_rows)
    for number, row in main_rows:
        ticket = normalize_ticket_number(row.get("Ticket Arion"))
        matches = by_ticket[ticket]
        if not ticket or len(matches) != 1 or tickets[ticket] != 1:
            counts["processes"]["unidentified"] += 1
            issues.append({"sheet": "Cancelamentos", "row": number, "reason": "ambiguous_or_unknown_ticket"})
            continue
        counts["processes"]["matched"] += 1
        roles["process_responsible"].append(matches[0].source_data.get("responsavel_id"))
        observe(matches[0].source_data.get("responsavel_id"), row["Responsável"], "Cancelamentos", number)

    candidates = defaultdict(list)
    for ref in LegacyCancellationReference.objects.filter(source=SOURCE, entity="pendenciacancelamento", pending__isnull=False):
        parent = by_process.get(ref.process_id)
        if parent is None:
            continue
        data = ref.source_data
        try:
            # The dump stores naive timestamps; the original import records their timezone.
            if not parent.import_run_id:
                continue
            source_zone = ZoneInfo(parent.import_run.counts["source_timezone"])
            created = datetime.fromisoformat(data["criado_em"]).replace(tzinfo=source_zone).astimezone(zone).replace(tzinfo=None)
            key = signature(parent.source_data.get("ticket_arion"), KINDS[data["tipo"]], data["descricao"],
                            data["valor"], STATUSES[data["status"]], data["data_prevista"], created)
            candidates[key].append(ref)
        except (ValueError, KeyError, TypeError, InvalidOperation):
            continue
    keys = []
    for number, row in pending_rows:
        try:
            key = signature(*(row[k] for k in ("Ticket Arion", "Tipo", "Descrição", "Valor", "Status", "Data Prevista", "Criada em")))
        except (ValueError, TypeError, InvalidOperation):
            key = None
        keys.append((number, row, key))
    repeated = Counter(key for _, _, key in keys)
    for number, row, key in keys:
        matches = candidates.get(key, [])
        if key is None or len(matches) != 1 or repeated[key] != 1:
            status = "ambiguous" if len(matches) > 1 or repeated[key] > 1 and key is not None else "unidentified"
            counts["pendings"][status] += 1
            issues.append({"sheet": "Pendências", "row": number, "reason": status})
            continue
        counts["pendings"]["matched"] += 1
        ref = matches[0]
        roles["pending_creator"].append(ref.source_data.get("criada_por_id"))
        roles["pending_process_responsible"].append(by_process[ref.process_id].source_data.get("responsavel_id"))
        observe(ref.source_data.get("criada_por_id"), row["Criada por"], "Pendências", number)
        # This export's Responsável is the parent process's historical responsible,
        # not a pending assignee (the legacy pending table has no such field).
        observe(by_process[ref.process_id].source_data.get("responsavel_id"), row["Responsável"], "Pendências", number)

    existing = {p.external_id: p for p in LegacyPerson.objects.select_for_update().filter(source=SOURCE)}
    people = Counter()
    changed_ids = []
    for pk, observations in evidence.items():
        variants = {name.casefold() for name, _, _ in observations}
        name = observations[0][0]
        current = existing.get(pk)
        if len(variants) != 1 or current and text(current.display_name).casefold() != name.casefold():
            people["conflicts"] += 1
            issues.append({"legacy_user_id": pk, "reason": "conflicting_names_preserved"})
            continue
        people["unchanged" if current else "created"] += 1
        if not current:
            changed_ids.append(pk)
            if apply:
                LegacyPerson.objects.create(source=SOURCE, external_id=pk, first_name=name)
    known = set(existing) | set(changed_ids)
    coverage = {role: {"matched": len(ids), "identified": sum(pk in known for pk in ids),
                       "newly_identified": sum(pk in changed_ids for pk in ids),
                       "unidentified": sum(pk not in known for pk in ids)} for role, ids in roles.items()}
    result = {"apply": apply, "checksum": checksum, "report_timezone": report_timezone, "coverage": coverage,
              "processes": {"rows": len(main_rows), **counts["processes"]},
              "pendings": {"rows": len(pending_rows), **counts["pendings"]},
              "people": dict(people), "issues": issues}
    if apply and changed_ids:
        AuditService.record(user=actor, category="cancellation", module="cancelamentos", action="legacy_workbook_names",
                            description="Nomes históricos conciliados com evidência do Excel, sem alterar responsáveis atuais.",
                            metadata={"source": SOURCE, "checksum": checksum, "report_timezone": report_timezone,
                                      "legacy_user_ids": sorted(changed_ids), "people": dict(people),
                                      "processes": dict(counts["processes"]), "pendings": dict(counts["pendings"])})
    return result
