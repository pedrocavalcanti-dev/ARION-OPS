"""Additional production guards for the existing dump importer, never a DB switch."""
import hashlib
import json
import re
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

PRODUCTION_DATABASE = "arion_geo"
PRODUCTION_CONFIRMATION = "IMPORTAR_CANCELAMENTOS_ARION_GEO"


def production_environment(production=False, allow_sqlite_dev=False):
    if production:
        if (settings.ARION_ENV != "production" or not settings.ARION_PRODUCTION
                or settings.DEBUG is not False or allow_sqlite_dev):
            raise ValidationError("Modo produtivo exige ARION_ENV=production, DEBUG=False e proíbe SQLite DEV.")
    elif settings.ARION_PRODUCTION or settings.ARION_ENV != "development":
        raise ValidationError("Produção bloqueada sem --production-import explícito.")


def external_private_file(value, *, must_exist=True):
    path = Path(value).expanduser().resolve(strict=must_exist)
    root = Path(settings.BASE_DIR).parent.resolve()
    forbidden = [root, Path(settings.STATIC_ROOT).resolve(), Path(settings.MEDIA_ROOT).resolve()]
    forbidden.extend(Path(p).resolve() for p in settings.STATICFILES_DIRS)
    if ((must_exist and not path.is_file()) or any(path.is_relative_to(p) for p in forbidden)
            or any((p / ".git").exists() for p in path.parents)):
        raise ValidationError("Arquivo produtivo deve ficar fora de checkouts Git, static e media.")
    return path


def file_sha256(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def production_source(source, expected_checksum):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_checksum or ""):
        raise ValidationError("Modo produtivo exige --snapshot-sha256 com 64 caracteres hexadecimais.")
    paths = source.get("source_paths", {})
    if not paths.get("dump") or not paths.get("schema"):
        raise ValidationError("Identidade dos arquivos do snapshot não está disponível; releia a origem.")
    dump = external_private_file(paths["dump"])
    schema = external_private_file(paths["schema"])
    if (source["snapshot_checksum"] != expected_checksum.lower()
            or file_sha256(dump) != expected_checksum.lower()
            or file_sha256(schema) != source["schema_checksum"]):
        raise ValidationError("SHA256 do snapshot/schema divergente; nenhuma importação autorizada.")
    return {"dump_name": dump.name, "schema_name": schema.name, "bytes": dump.stat().st_size,
            "snapshot_sha256": source["snapshot_checksum"], "schema_sha256": source["schema_checksum"]}


def production_restore_evidence(path, backup_checksum):
    evidence = external_private_file(path)
    record = json.loads(evidence.read_text(encoding="utf-8-sig"))
    if not isinstance(record, dict):
        raise ValidationError("Evidência de restore deve ser um objeto JSON.")
    verified_at = parse_datetime(str(record.get("verified_at", "")))
    if (record.get("database") != PRODUCTION_DATABASE or record.get("backup_sha256") != backup_checksum
            or record.get("restore_verified") is not True or not str(record.get("validated_by", "")).strip()
            or not str(record.get("verification_reference", "")).strip()
            or not verified_at or timezone.is_naive(verified_at) or verified_at > timezone.now()):
        raise ValidationError("Evidência de restore inválida: exige schema, hash do backup, data com fuso, responsável e referência.")
    return file_sha256(evidence)
