"""Minimal, idempotent enrichment, independent from immutable imported facts."""
import csv
import io
import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from aplicativos.auditoria.services import AuditService
from ..models import LegacyCancellationReference, LegacyPerson
from .legacy_import import SOURCE, authorize_import

ACTOR_KEYS = ("responsavel_id", "atendente_id", "movido_por_id", "criado_por_id", "realizado_por_id", "criada_por_id")


def referenced_ids():
    ids = set()
    for data in LegacyCancellationReference.objects.filter(source=SOURCE).values_list("source_data", flat=True).iterator():
        for key in ACTOR_KEYS:
            if data.get(key) is not None:
                ids.add(int(data[key]))
    return ids


def names_for(ids):
    return {person.external_id: person.display_name for person in LegacyPerson.objects.filter(source=SOURCE, external_id__in=ids)}


def read_people(path):
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValidationError("Arquivo de pessoas acima do limite de 2 MB.")
    content = path.read_bytes()
    text = content.decode("utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    try:
        rows = json.loads(text) if path.suffix.lower() == ".json" else list(csv.DictReader(
            io.StringIO(text), dialect=csv.Sniffer().sniff(text[:2048], delimiters=",;\t")))
        if not isinstance(rows, list):
            raise ValueError
        result = {}
        for row in rows:
            if isinstance(row, dict) and set(row) == {"legacy_user_id", "username", "first_name", "last_name"}:
                row = {"id": row["legacy_user_id"], **{k: row[k] for k in ("username", "first_name", "last_name")}}
            if not isinstance(row, dict) or set(row) != {"id", "username", "first_name", "last_name"}:
                raise ValueError
            raw_id = row["id"]
            if not (type(raw_id) is int or isinstance(raw_id, str) and raw_id.strip().isdecimal()):
                raise ValueError
            pk = int(raw_id)
            if not 0 < pk <= 9223372036854775807 or pk in result:
                raise ValueError
            values = {}
            for key in ("username", "first_name", "last_name"):
                value = row[key]
                if not isinstance(value, str) or len(value) > 150 or any(ord(c) < 32 for c in value):
                    raise ValueError
                values[key] = value.strip()
            if not any(values.values()):
                raise ValueError
            result[pk] = values
        return result
    except (ValueError, TypeError, KeyError, csv.Error):
        raise ValidationError("Formato inválido. Use legacy_user_id (ou id), username, first_name e last_name, com IDs únicos positivos.") from None


@transaction.atomic
def import_people(path, actor, *, apply=False):
    authorize_import(actor)
    if settings.ARION_PRODUCTION or settings.ARION_ENV != "development":
        raise ValidationError("Enriquecimento autorizado somente no DEV.")
    rows, referenced = read_people(path), referenced_ids()
    if set(rows) - referenced:
        raise ValidationError("O arquivo contém pessoas não referenciadas pelos cancelamentos importados.")
    existing = {p.external_id: p for p in LegacyPerson.objects.filter(source=SOURCE, external_id__in=referenced)}
    counts = {"new": 0, "updated": 0, "unchanged": 0, "missing": len(referenced - set(rows) - set(existing))}
    for pk, values in rows.items():
        person = existing.get(pk)
        kind = "new" if person is None else "updated" if any(getattr(person, k) != v for k, v in values.items()) else "unchanged"
        counts[kind] += 1
        if apply and kind != "unchanged":
            LegacyPerson.objects.update_or_create(source=SOURCE, external_id=pk, defaults=values)
    if apply and (counts["new"] or counts["updated"]):
        AuditService.record(user=actor, category="cancellation", module="cancelamentos", action="legacy_people_import",
                            description="Nomes históricos enriquecidos sem criação de contas.",
                            metadata={**counts, "source": SOURCE, "legacy_user_ids": sorted(rows)})
    return counts
