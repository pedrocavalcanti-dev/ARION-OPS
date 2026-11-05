"""Staged MySQL candidate preparation and local-data migration."""
import hashlib
import json
import logging
import os
import re
import sqlite3
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connections, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import ConnectionHandler
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .arion_database import (
    CANDIDATE_ALIAS, candidate_config, candidate_test_is_current,
    candidate_database_settings, mark_migration_stage, migration_state,
    test_mysql_connection,
)
from .secrets import get_secret_protector

logger = logging.getLogger(__name__)


class CandidateStageError(ValidationError):
    def __init__(self, message, *, technical_detail):
        self.technical_detail = technical_detail
        super().__init__(message)


def _redact_diagnostic_text(value):
    text = str(value or "")
    secrets = [os.environ.get("DATABASE_URL", "")]
    try:
        protected = candidate_config(decrypt=False)
        if protected:
            secrets.append(protected.get("password_protected", ""))
        candidate = candidate_config(decrypt=True)
        if candidate:
            secrets.append(candidate.get("password", ""))
    except Exception:
        pass
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(password|passwd|pwd|secret|token)(\s*[=:]\s*)[^\s,;]+", r"\1\2[REDACTED]", text)
    text = re.sub(r"(?i)(mysql|mariadb)://[^\s]+", "[REDACTED_DATABASE_URL]", text)
    return text


def safe_stage_error_detail(exception):
    detail = _redact_diagnostic_text(str(exception))
    return f"{type(exception).__name__}: {detail}" if detail else type(exception).__name__


def log_candidate_stage_failure(stage, exception):
    detail = safe_stage_error_detail(exception)
    stack = _redact_diagnostic_text("".join(traceback.format_exception(
        type(exception), exception, exception.__traceback__)))
    logger.error("Banco Arion candidato falhou stage=%s exception=%s detail=%s\n%s",
                 stage, type(exception).__name__, detail, stack)
    return detail


def _require_candidate_test():
    candidate = candidate_config(decrypt=True)
    if not candidate_test_is_current():
        raise ValidationError("Teste a autenticação do MySQL candidato antes de preparar, migrar ou validar.")
    test_mysql_connection(candidate, candidate["password"])
    return candidate


@contextmanager
def _temporary_database(config, prefix):
    alias = f"{prefix}_{uuid.uuid4().hex[:10]}"
    normalized = ConnectionHandler({"default": settings.DATABASES["default"], alias: config}).databases[alias]
    connections.databases[alias] = normalized
    try:
        yield alias
    finally:
        try:
            connection = connections[alias]
            connection.close()
        except Exception:
            # Preserve the operation's original exception if backend creation failed.
            pass
        finally:
            connections.databases.pop(alias, None)
            try:
                delattr(connections._connections, alias)
            except AttributeError:
                pass


def _install_mysql_driver():
    """Register PyMySQL for Django's mysql backend even when default is SQLite."""
    try:
        import pymysql
        pymysql.install_as_MySQLdb()
    except Exception:
        raise ImproperlyConfigured("PyMySQL nao esta disponivel para conectar ao MySQL candidato.") from None


@contextmanager
def candidate_database():
    """Add a unique, short-lived alias; never change DATABASES['default']."""
    _install_mysql_driver()
    with _temporary_database(candidate_database_settings(), CANDIDATE_ALIAS) as alias:
        yield alias


@contextmanager
def sqlite_source_database(path):
    from pathlib import Path
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValidationError("O arquivo SQLite de origem não foi encontrado.")
    config = {"ENGINE": "django.db.backends.sqlite3", "NAME": str(source), "OPTIONS": {}}
    with _temporary_database(config, "arion_source_sqlite") as alias:
        yield alias


def create_sqlite_backup(*, source_alias="default"):
    source_connection = connections[source_alias]
    if source_connection.vendor != "sqlite":
        raise ValidationError("O backup automatico exige uma origem SQLite.")
    source_path = Path(source_connection.settings_dict["NAME"])
    if not source_path.is_file():
        raise ValidationError("O arquivo SQLite ativo nao foi encontrado para backup.")
    backup_dir = Path(settings.BASE_DIR).parent / "var" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"db-before-mysql-{stamp}.sqlite3"
    source = sqlite3.connect(str(source_path), timeout=30)
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    except Exception:
        destination.close()
        source.close()
        backup_path.unlink(missing_ok=True)
        raise ValidationError("Nao foi possivel criar o backup consistente do SQLite.") from None
    destination.close()
    source.close()
    return backup_path


def _migration_status(alias):
    connection = connections[alias]
    applied = set(MigrationRecorder(connection).applied_migrations())
    loader = __import__("django.db.migrations.loader", fromlist=["MigrationLoader"]).MigrationLoader(connection)
    missing = sorted(set(loader.graph.leaf_nodes()) - applied)
    return {"applied_migrations": len(applied), "pending_migrations": len(missing)}


def _assert_all_migrations_applied(alias):
    report = _migration_status(alias)
    if report["pending_migrations"]:
        raise ValidationError("Existem migrations pendentes no MySQL candidato.")
    return report


def prepare_candidate_schema():
    _require_candidate_test()
    with candidate_database() as alias:
        try:
            recorder = MigrationRecorder(connections[alias])
            before = len(recorder.applied_migrations())
            call_command("migrate", database=alias, interactive=False, verbosity=0)
            _ensure_current_model_permissions(alias)
            report = _assert_all_migrations_applied(alias)
            report["newly_applied"] = max(0, report["applied_migrations"] - before)
        except ValidationError:
            raise
        except Exception as exc:
            detail = log_candidate_stage_failure("prepare", exc)
            raise CandidateStageError(
                "Falha ao preparar schema MySQL candidato. Confira os detalhes tecnicos autorizados.",
                technical_detail=detail,
            ) from None
    mark_migration_stage("schema_prepared")
    return report


def _model_specs():
    return [
        (get_user_model(), ("username",), ()),
        (Group, ("name",), ()),
        (apps.get_model("base_links", "Operadora"), ("normalized_name",), ()),
        (apps.get_model("base_links", "Projeto"), ("normalized_name",), ()),
        (apps.get_model("configuracoes", "GroupDataScope"), ("group_id",), ("group_id",)),
        (apps.get_model("configuracoes", "LegacyUserDataScope"), ("user_id",), ("user_id",)),
        (apps.get_model("configuracoes", "UserPreference"), ("user_id",), ("user_id",)),
        (apps.get_model("painel", "SavedAnalyticsView"), ("user_id", "name"), ("user_id",)),
        (apps.get_model("integracoes", "Integration"), ("key",), ()),
        (apps.get_model("integracoes", "IntegrationRun"), ("id",), ()),
        (apps.get_model("integracoes", "IntegrationSyncState"), ("key",), ()),
        (apps.get_model("integracoes", "ExternalAPIIntegration"), ("provider",), ()),
        (apps.get_model("integracoes", "ExternalAPIHealthCheck"), ("id",), ()),
        (apps.get_model("integracoes", "OTRSStateMapping"), ("external_state_id",), ()),
        (apps.get_model("integracoes", "CMDBOperatorMirror"), ("source_config_item_id",), ()),
        (apps.get_model("integracoes", "CMDBLinkMirror"), ("source_config_item_id",), ()),
        (apps.get_model("integracoes", "OTRSTicket"), ("external_id",), ()),
        (apps.get_model("qualidade", "QualityOrder"), ("code",), ()),
        (apps.get_model("qualidade", "QualityTreatmentSequence"), ("key",), ()),
        (apps.get_model("qualidade", "QualityTreatmentIdentity"), ("code",), ()),
        (apps.get_model("qualidade", "QualityOccurrence"), ("id",), ()),
        (apps.get_model("qualidade", "QualityTicket"), ("id",), ()),
        (apps.get_model("qualidade", "QualityTreatment"), ("code",), ()),
        (apps.get_model("qualidade", "QualityCorrelationCheckpoint"), ("queue_name",), ()),
        (apps.get_model("qualidade", "QualityMonitoringCycle"), ("quality_order_id", "cycle_number"), ()),
        (apps.get_model("qualidade", "QualityContact"), ("id",), ()),
        (apps.get_model("qualidade", "QualityAction"), ("id",), ()),
        (apps.get_model("qualidade", "QualityAuditEvent"), (), ()),
        (apps.get_model("auditoria", "AuditEvent"), (), ("user_id",)),
    ]


def _map_fk_values(model, row, id_maps):
    mapped = dict(row)
    for field in model._meta.concrete_fields:
        if not field.is_relation or not field.many_to_one:
            continue
        attname = field.attname
        value = mapped.get(attname)
        if value is None:
            continue
        related_label = field.remote_field.model._meta.label_lower
        if field.target_field.primary_key:
            related_map = id_maps.get(related_label)
        else:
            related_map = id_maps.get(f"{related_label}:{field.target_field.attname}")
        if related_map is not None:
            mapped[attname] = related_map.get(value, value)
    return mapped


def _natural_key(model, row, key_fields):
    return tuple(row.get(field) for field in key_fields)


def _field_for_key(model, name):
    return next(field for field in model._meta.concrete_fields
                if field.name == name or field.attname == name)


def _find_target(model, target_alias, key_fields, row):
    if not key_fields:
        return None
    lookup = {field: row[field] for field in key_fields}
    if any(value is None for value in lookup.values()):
        return None
    return model.objects.using(target_alias).filter(**lookup).first()


def _audit_fingerprint(model, row):
    excluded = {model._meta.pk.attname}
    if model._meta.label_lower == "auditoria.auditevent":
        excluded.add("timestamp")
    values = [(field.attname, row.get(field.attname)) for field in model._meta.concrete_fields
              if field.attname not in excluded]
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _copy_integration_secret(model, row, protector):
    label = model._meta.label_lower
    if label == "integracoes.integration":
        secret_fields = ("secret_ciphertext",)
    elif label == "integracoes.externalapiintegration":
        secret_fields = ("secret_ciphertext", "smtp_secret_ciphertext")
    else:
        return row
    try:
        for field_name in secret_fields:
            ciphertext = row.get(field_name)
            if not ciphertext:
                continue
            plaintext = protector.unprotect(ciphertext)
            if not isinstance(plaintext, str) or not plaintext:
                raise ValueError
            row[field_name] = protector.protect(plaintext)
            del plaintext
    except Exception:
        raise ValidationError("Uma credencial de integração não pode ser reaberta com DPAPI nesta conta Windows. Reconfigure-a antes da migração.") from None
    return row


def _source_rows_for_copy(model, source_alias):
    label = model._meta.label_lower
    quality_order = apps.get_model("qualidade", "QualityOrder")
    cmdb_link = apps.get_model("integracoes", "CMDBLinkMirror")
    if label == "integracoes.cmdblinkmirror":
        linked_ids = quality_order.objects.using(source_alias).exclude(link_id__isnull=True).values_list("link_id", flat=True)
        return model.objects.using(source_alias).filter(source_config_item_id__in=linked_ids)
    if label == "integracoes.cmdboperatormirror":
        linked_ids = quality_order.objects.using(source_alias).exclude(link_id__isnull=True).values_list("link_id", flat=True)
        operator_ids = cmdb_link.objects.using(source_alias).filter(
            source_config_item_id__in=linked_ids,
        ).exclude(operator_id__isnull=True).values_list("operator_id", flat=True)
        return model.objects.using(source_alias).filter(pk__in=operator_ids)
    if label == "integracoes.otrsticket":
        external_ids = set()
        for model_label, field_name in (
            ("qualidade.QualityOccurrence", "otrs_ticket_id"),
            ("qualidade.QualityTicket", "otrs_ticket_id"),
            ("qualidade.QualityTreatment", "support_ticket_id"),
        ):
            related_model = apps.get_model(model_label)
            external_ids.update(
                related_model.objects.using(source_alias).exclude(**{f"{field_name}__isnull": True})
                .values_list(field_name, flat=True)
            )
        return model.objects.using(source_alias).filter(external_id__in=external_ids)
    return model.objects.using(source_alias).all()


def _copy_models(source_alias, target_alias):
    protector = get_secret_protector()
    id_maps = {}
    source_counts = {}
    copied_counts = {}
    permission_report = None
    audit_labels = {"auditoria.auditevent", "qualidade.qualityauditevent"}
    preserve_auto_pk_labels = {
        "integracoes.integrationrun", "integracoes.externalapihealthcheck",
        "qualidade.qualityoccurrence", "qualidade.qualityticket",
        "qualidade.qualitycontact", "qualidade.qualityaction",
    }
    with transaction.atomic(using=target_alias):
        _clear_candidate_mirrors_and_runtime_state(target_alias)
        for model, key_fields, _fk_fields in _model_specs():
            label = model._meta.label_lower
            rows = list(_source_rows_for_copy(model, source_alias).order_by(model._meta.pk.name).values())
            source_counts[label] = len(rows)
            id_map = {}
            field_maps = {field_name: {} for field_name in key_fields}
            audit_candidates = {}
            audit_fingerprints = set()
            if label in audit_labels:
                for target_row in model.objects.using(target_alias).order_by(model._meta.pk.name).values():
                    fingerprint = _audit_fingerprint(model, target_row)
                    audit_candidates.setdefault(fingerprint, []).append(target_row[model._meta.pk.attname])
            for original in rows:
                row = _map_fk_values(model, original, id_maps)
                source_pk = original[model._meta.pk.attname]
                if label in {"qualidade.qualitytreatment", "qualidade.qualitymonitoringcycle"}:
                    active_order_key = row.get("active_order_key")
                    if active_order_key is not None:
                        row["active_order_key"] = id_maps.get("qualidade.qualityorder", {}).get(
                            active_order_key, active_order_key)
                _copy_integration_secret(model, row, protector)
                existing = _find_target(model, target_alias, key_fields, row)
                if model is apps.get_model("integracoes", "Integration"):
                    for field_name, reset_value in (("status", model.Status.NOT_CONFIGURED), ("last_error", ""),
                            ("last_test_at", None), ("last_success_at", None), ("last_read_at", None),
                            ("last_error_at", None), ("last_latency_ms", None), ("accessible_databases", [])):
                        row[field_name] = reset_value
                if label == "qualidade.qualitytreatmentsequence" and existing is not None:
                    row["last_value"] = max(existing.last_value, row["last_value"])
                if label in audit_labels:
                    fingerprint = _audit_fingerprint(model, row)
                    audit_fingerprints.add(fingerprint)
                    candidate_ids = audit_candidates.get(fingerprint, [])
                    existing = (model.objects.using(target_alias).filter(pk=candidate_ids.pop(0)).first()
                                if candidate_ids else None)
                if label == "qualidade.qualitytreatment" and existing is not None:
                    if existing.correlation_id != row["correlation_id"]:
                        raise ValidationError("Um código TQ existente está associado a outro QID no banco candidato.")
                if label == "qualidade.qualitytreatmentidentity" and existing is not None:
                    if existing.correlation_id != row["correlation_id"]:
                        raise ValidationError("Um identificador TQ existente está associado a outro QID no banco candidato.")
                if existing is not None:
                    if model is get_user_model() and existing.password != row["password"]:
                        raise ValidationError("Já existe um usuário com o mesmo nome no candidato e o hash de senha diverge. Resolva o conflito antes de copiar.")
                    for field in model._meta.concrete_fields:
                        if field.primary_key:
                            continue
                        attname = field.attname
                        if attname in row:
                            setattr(existing, attname, row[attname])
                    existing.save_base(raw=True, using=target_alias)
                    target = existing
                    target_pk = existing.pk
                else:
                    data = {}
                    for field in model._meta.concrete_fields:
                        if field.attname not in row:
                            continue
                        if (field.primary_key and field.get_internal_type() in {"AutoField", "BigAutoField"}
                                and label not in preserve_auto_pk_labels):
                            continue
                        data[field.attname] = row[field.attname]
                    created = model(**data)
                    created.save_base(raw=True, using=target_alias, force_insert=True)
                    target = created
                    target_pk = created.pk
                if label == "auditoria.auditevent":
                    # auto_now_add overwrites the imported timestamp in save/create.
                    model.objects.using(target_alias).filter(pk=target_pk).update(timestamp=row["timestamp"])
                id_map[source_pk] = target_pk
                for field_name, field_map in field_maps.items():
                    source_value = original.get(field_name)
                    target_field = _field_for_key(model, field_name)
                    target_value = getattr(target, target_field.attname)
                    field_map[source_value] = target_value
            if label in audit_labels:
                # Remove only excess target rows matching source event payloads; preserve
                # candidate-only audit entries whose fingerprints are not in the source.
                duplicates = [pk for fingerprint in audit_fingerprints
                              for pk in audit_candidates.get(fingerprint, [])]
                if duplicates:
                    model.objects.using(target_alias).filter(pk__in=duplicates).delete()
            id_maps[label] = id_map
            for field_name, field_map in field_maps.items():
                model_field = _field_for_key(model, field_name)
                id_maps[f"{label}:{model_field.attname}"] = field_map
            copied_counts[label] = len(rows)
        permission_report = _copy_permissions_and_memberships(source_alias, target_alias, id_maps)
    return {"source_counts": source_counts, "copied_counts": copied_counts,
            "relationships": _relationship_counts(source_alias), "permission_report": permission_report}


def _clear_candidate_mirrors_and_runtime_state(alias):
    """A candidate starts with no stale mirrors, leases, runs, or sync cursors."""
    for model_name in ("OTRSTicketConfigItem", "CMDBLinkMirror", "OTRSTicket",
                       "CMDBOperatorMirror", "IntegrationRun", "IntegrationLease",
                       "IntegrationSyncState"):
        apps.get_model("integracoes", model_name).objects.using(alias).all().delete()


def _current_model_permission_keys():
    required = set()
    model_labels = set()
    for model in apps.get_models():
        app_label = model._meta.app_label
        model_name = model._meta.model_name
        model_labels.add((app_label, model_name))
        required.update((app_label, model_name, f"{action}_{model_name}")
                        for action in model._meta.default_permissions)
        required.update((app_label, model_name, codename)
                        for codename, _name in model._meta.permissions)
    return model_labels, required


def _ensure_current_model_permissions(alias):
    for app_config in apps.get_app_configs():
        create_permissions(app_config, verbosity=0, interactive=False, using=alias, apps=apps)


def _permission_assignment_ids(source_alias):
    user_through = get_user_model().user_permissions.through
    group_through = Group.permissions.through
    user_ids = set(user_through.objects.using(source_alias).values_list("permission_id", flat=True).distinct())
    group_ids = set(group_through.objects.using(source_alias).values_list("permission_id", flat=True).distinct())
    return user_ids, group_ids


def _permission_id_map(source_alias, target_alias, *, ensure_current_permissions=False):
    if ensure_current_permissions:
        _ensure_current_model_permissions(target_alias)
    current_models, current_required = _current_model_permission_keys()
    source_rows = list(Permission.objects.using(source_alias).select_related("content_type").values_list(
        "pk", "codename", "name", "content_type__app_label", "content_type__model"))
    target_rows = Permission.objects.using(target_alias).select_related("content_type").values_list(
        "pk", "codename", "content_type__app_label", "content_type__model")
    target = {(app, model, codename): pk for pk, codename, app, model in target_rows}
    user_ids, group_ids = _permission_assignment_ids(source_alias)
    assigned_ids = user_ids | group_ids
    target_content_types = {
        (app, model): pk for pk, app, model in ContentType.objects.using(target_alias).values_list(
            "pk", "app_label", "model")
    }
    source_keys = {(app, model, codename) for _pk, codename, _name, app, model in source_rows}
    missing_keys = source_keys - set(target)
    created_keys = set()
    if ensure_current_permissions:
        for source_pk, codename, name, app, model in source_rows:
            key = (app, model, codename)
            if key not in missing_keys or source_pk not in assigned_ids or (app, model) not in current_models:
                continue
            content_type_id = target_content_types.get((app, model))
            if not content_type_id:
                continue
            permission, created = Permission.objects.using(target_alias).get_or_create(
                content_type_id=content_type_id, codename=codename, defaults={"name": name})
            target[key] = permission.pk
            if created:
                created_keys.add(key)

    missing = source_keys - set(target)
    source_by_key = {(app, model, codename): (pk, name) for pk, codename, name, app, model in source_rows}
    user_missing = {key for key in missing if source_by_key[key][0] in user_ids}
    group_missing = {key for key in missing if source_by_key[key][0] in group_ids}
    assigned_missing = user_missing | group_missing
    unused_missing = missing - assigned_missing
    missing_current = current_required - set(target)
    model_unavailable = {key for key in assigned_missing if key[:2] not in current_models}
    no_content_type = {key for key in assigned_missing
                       if key[:2] in current_models and key[:2] not in target_content_types}
    report = {
        "source_count": len(source_rows),
        "target_count": Permission.objects.using(target_alias).count(),
        "missing_count": len(missing_keys), "missing_unused": sorted(unused_missing),
        "missing_assigned": sorted(assigned_missing), "missing_user_assigned": sorted(user_missing),
        "missing_group_assigned": sorted(group_missing), "missing_current_required": sorted(missing_current),
        "created_custom": sorted(created_keys), "unavailable_models": sorted(model_unavailable),
        "missing_content_types": sorted(no_content_type),
    }
    logger.info("Permissions source=%s target=%s missing=%s missing_assigned=%s missing_unused=%s created_custom=%s",
                report["source_count"], report["target_count"], report["missing_count"],
                len(assigned_missing), len(unused_missing), len(created_keys))
    if unused_missing:
        logger.warning("Permissões históricas não utilizadas ignoradas count=%s keys=%s",
                       len(unused_missing), sorted(unused_missing))
    result = {pk: target[(app, model, codename)] for pk, codename, _name, app, model in source_rows
              if (app, model, codename) in target}
    return result, report


def _raise_for_unmapped_required_permissions(report):
    blocking = set(report["missing_assigned"]) | set(report["missing_current_required"])
    blocking |= set(report["unavailable_models"]) | set(report["missing_content_types"])
    if not blocking:
        return
    details = "; ".join(
        f"app_label={app}, model={model}, codename={codename}, "
        f"user_assigned={'sim' if (app, model, codename) in report['missing_user_assigned'] else 'não'}, "
        f"group_assigned={'sim' if (app, model, codename) in report['missing_group_assigned'] else 'não'}"
        for app, model, codename in sorted(blocking)
    )
    raise ValidationError("Permissões obrigatórias ausentes no candidato: " + details)


def _copy_through(source_alias, target_alias, through, id_maps, permission_ids=None):
    source_connection = connections[source_alias]
    target_connection = connections[target_alias]
    with source_connection.cursor() as cursor:
        source_columns = {column.name for column in source_connection.introspection.get_table_description(cursor, through._meta.db_table)}
    column_map = {}
    for field in through._meta.concrete_fields:
        if field.primary_key:
            continue
        column = field.column
        if column not in source_columns and field.is_relation and field.remote_field.model._meta.label_lower == "configuracoes.legacyuserdatascope":
            column = "userdatascope_id"
        column_map[field.attname] = column
    if any(column not in source_columns for column in column_map.values()):
        raise ValidationError("Uma tabela de relacao de escopo nao corresponde ao schema esperado.")
    if any(field.column != column_map.get(field.attname, field.column)
           for field in through._meta.concrete_fields if not field.primary_key):
        return _copy_legacy_through_raw(source_alias, target_alias, through, id_maps,
                                        permission_ids, column_map)
    source_rows = through.objects.using(source_alias).values()
    created = 0
    for raw in source_rows.iterator(chunk_size=1000):
        row = dict(raw)
        kwargs = {}
        for field in through._meta.concrete_fields:
            if field.primary_key:
                continue
            value = row.get(field.attname)
            if value is None:
                kwargs[field.attname] = None
                continue
            related = field.remote_field.model if field.is_relation else None
            if related is Permission and permission_ids is not None:
                value = permission_ids.get(value)
                if value is None:
                    raise ValidationError("Uma permissão da origem não existe no MySQL candidato.")
            elif related is not None:
                related_map = id_maps.get(related._meta.label_lower)
                if related_map is not None:
                    value = related_map.get(value)
                    if value is None:
                        raise ValidationError("Uma relação de usuário, grupo ou escopo não pode ser resolvida no candidato.")
            kwargs[field.attname] = value
        if not through.objects.using(target_alias).filter(**kwargs).exists():
            through.objects.using(target_alias).create(**kwargs)
            created += 1
    return created


def _copy_legacy_through_raw(source_alias, target_alias, through, id_maps, permission_ids, column_map):
    source_connection = connections[source_alias]
    target_connection = connections[target_alias]
    table = through._meta.db_table
    source_table = source_connection.ops.quote_name(table)
    target_table = target_connection.ops.quote_name(table)
    created = 0
    with source_connection.cursor() as source_cursor:
        source_cursor.execute(f"SELECT * FROM {source_table}")
        columns = [item[0] for item in source_cursor.description]
        for values in source_cursor.fetchall():
            raw = dict(zip(columns, values))
            mapped = {}
            for field in through._meta.concrete_fields:
                if field.primary_key:
                    continue
                column = column_map[field.attname]
                value = raw[column]
                if value is not None and field.is_relation:
                    related = field.remote_field.model
                    if related is Permission and permission_ids is not None:
                        value = permission_ids.get(value)
                        if value is None:
                            raise ValidationError("Uma permissao da origem nao existe no MySQL candidato.")
                    else:
                        related_map = id_maps.get(related._meta.label_lower)
                        if related_map is not None:
                            value = related_map.get(value)
                            if value is None:
                                raise ValidationError("Uma relacao de escopo nao pode ser resolvida no candidato.")
                mapped[column] = value
            quoted_columns = [target_connection.ops.quote_name(column) for column in mapped]
            where = " AND ".join(f"{target_connection.ops.quote_name(column)} = %s" for column in mapped)
            with target_connection.cursor() as target_cursor:
                target_cursor.execute(f"SELECT 1 FROM {target_table} WHERE {where}", list(mapped.values()))
                if target_cursor.fetchone():
                    continue
                placeholders = ", ".join(["%s"] * len(mapped))
                target_cursor.execute(
                    f"INSERT INTO {target_table} ({', '.join(quoted_columns)}) VALUES ({placeholders})",
                    list(mapped.values()),
                )
                created += 1
    return created


def _copy_permissions_and_memberships(source_alias, target_alias, id_maps):
    permission_ids, report = _permission_id_map(source_alias, target_alias, ensure_current_permissions=True)
    _raise_for_unmapped_required_permissions(report)
    for model in (Group, get_user_model()):
        for field in model._meta.many_to_many:
            through = field.remote_field.through
            _copy_through(source_alias, target_alias, through, id_maps, permission_ids)
    for label, field_name in (
        ("configuracoes.GroupDataScope", "allowed_operators"),
        ("configuracoes.GroupDataScope", "allowed_projects"),
        ("configuracoes.LegacyUserDataScope", "allowed_operators"),
        ("configuracoes.LegacyUserDataScope", "allowed_projects"),
    ):
        model = apps.get_model(label)
        _copy_through(source_alias, target_alias, model._meta.get_field(field_name).remote_field.through,
                      id_maps, permission_ids)
    return report


def _relationship_counts(alias):
    counts = {}
    for model in (Group, get_user_model()):
        for field in model._meta.many_to_many:
            through = field.remote_field.through
            counts[through._meta.label_lower] = through.objects.using(alias).count()
    for label, field_name in (
        ("configuracoes.GroupDataScope", "allowed_operators"),
        ("configuracoes.GroupDataScope", "allowed_projects"),
        ("configuracoes.LegacyUserDataScope", "allowed_operators"),
        ("configuracoes.LegacyUserDataScope", "allowed_projects"),
    ):
        through = apps.get_model(label)._meta.get_field(field_name).remote_field.through
        counts[through._meta.label_lower] = through.objects.using(alias).count()
    return counts


def copy_local_data(*, source_alias="default"):
    candidate = _require_candidate_test()
    state = migration_state()
    if not state["schema_prepared"]:
        raise ValidationError("Prepare o schema MySQL candidato antes de copiar dados locais.")
    backup_path = None
    if connections[source_alias].vendor == "sqlite":
        backup_path = create_sqlite_backup(source_alias=source_alias)
        source_context = sqlite_source_database(backup_path)
    else:
        source_context = _null_context(source_alias)
    with source_context as stable_source, candidate_database() as target_alias:
        try:
            report = _copy_models(stable_source, target_alias)
            report["target_counts"] = {label: apps.get_model(label).objects.using(target_alias).count()
                                       for label in report["source_counts"]}
        except ValidationError:
            raise
        except Exception:
            raise ValidationError("A cópia local foi interrompida e revertida. Confira os conflitos e o log técnico.") from None
    mark_migration_stage("data_copied")
    report["sqlite_backup"] = str(backup_path) if backup_path else None
    return report


@contextmanager
def _null_context(alias):
    yield alias


def _model_count_report(source_alias, target_alias):
    report = {}
    for model, _keys, _fks in _model_specs():
        label = model._meta.label_lower
        report[label] = {"source": _source_rows_for_copy(model, source_alias).count(),
                         "target": model.objects.using(target_alias).count()}
    report["relationships"] = {"source": _relationship_counts(source_alias),
                               "target": _relationship_counts(target_alias)}
    report["permissions"] = {"source": Permission.objects.using(source_alias).count(),
                            "target": Permission.objects.using(target_alias).count()}
    _permission_map, report["permission_details"] = _permission_id_map(source_alias, target_alias)
    return report


def _permissions_match(source_alias, target_alias):
    _permission_map, report = _permission_id_map(source_alias, target_alias)
    missing_required = set(report["missing_assigned"]) | set(report["missing_current_required"])
    missing_required |= set(report["unavailable_models"]) | set(report["missing_content_types"])
    return not missing_required


def _user_hashes_match(source_alias, target_alias):
    user_model = get_user_model()
    for username, password_hash, is_staff, is_superuser, is_active in user_model.objects.using(source_alias).values_list(
            "username", "password", "is_staff", "is_superuser", "is_active"):
        target = user_model.objects.using(target_alias).filter(username=username).values_list(
            "password", "is_staff", "is_superuser", "is_active").first()
        if target != (password_hash, is_staff, is_superuser, is_active):
            return False
    return True


def _group_names_match(source_alias, target_alias):
    source = set(Group.objects.using(source_alias).values_list("name", flat=True))
    target = set(Group.objects.using(target_alias).values_list("name", flat=True))
    return source <= target


def _user_group_memberships_match(source_alias, target_alias):
    user_model = get_user_model()
    source = set(user_model.objects.using(source_alias).filter(groups__isnull=False).values_list(
        "username", "groups__name"))
    target = set(user_model.objects.using(target_alias).filter(groups__isnull=False).values_list(
        "username", "groups__name"))
    return source <= target


def _permission_assignment_keys(alias):
    user_model = get_user_model()
    user_permissions = set(user_model.objects.using(alias).filter(user_permissions__isnull=False).values_list(
        "username", "user_permissions__content_type__app_label", "user_permissions__content_type__model",
        "user_permissions__codename"))
    group_permissions = set(Group.objects.using(alias).filter(permissions__isnull=False).values_list(
        "name", "permissions__content_type__app_label", "permissions__content_type__model",
        "permissions__codename"))
    return user_permissions, group_permissions


def _permission_assignments_match(source_alias, target_alias):
    source_users, source_groups = _permission_assignment_keys(source_alias)
    target_users, target_groups = _permission_assignment_keys(target_alias)
    return source_users <= target_users and source_groups <= target_groups


def _integration_secrets_open(alias):
    Integration = apps.get_model("integracoes", "Integration")
    ExternalAPIIntegration = apps.get_model("integracoes", "ExternalAPIIntegration")
    protector = get_secret_protector()
    try:
        for ciphertext in Integration.objects.using(alias).exclude(secret_ciphertext="").values_list("secret_ciphertext", flat=True):
            value = protector.unprotect(ciphertext)
            if not isinstance(value, str) or not value:
                return False
            del value
        for field_name in ("secret_ciphertext", "smtp_secret_ciphertext"):
            ciphertexts = ExternalAPIIntegration.objects.using(alias).exclude(
                **{f"{field_name}__in": ["", None]}
            ).values_list(field_name, flat=True)
            for ciphertext in ciphertexts:
                value = protector.unprotect(ciphertext)
                if not isinstance(value, str) or not value:
                    return False
                del value
    except Exception:
        return False
    return True


def _quality_identifiers_match(source_alias, target_alias):
    QualityOrder = apps.get_model("qualidade", "QualityOrder")
    QualityTreatment = apps.get_model("qualidade", "QualityTreatment")
    QualityTreatmentIdentity = apps.get_model("qualidade", "QualityTreatmentIdentity")
    QualityTreatmentSequence = apps.get_model("qualidade", "QualityTreatmentSequence")
    source_orders = set(QualityOrder.objects.using(source_alias).exclude(code__isnull=True).values_list("code", flat=True))
    target_orders = set(QualityOrder.objects.using(target_alias).exclude(code__isnull=True).values_list("code", flat=True))
    source_treatments = set(QualityTreatment.objects.using(source_alias).values_list("code", "correlation_id"))
    target_treatments = set(QualityTreatment.objects.using(target_alias).values_list("code", "correlation_id"))
    source_ledger = set(QualityTreatmentIdentity.objects.using(source_alias).values_list(
        "sequence_number", "code", "correlation_id"))
    target_ledger = set(QualityTreatmentIdentity.objects.using(target_alias).values_list(
        "sequence_number", "code", "correlation_id"))
    source_sequences = dict(QualityTreatmentSequence.objects.using(source_alias).values_list("key", "last_value"))
    target_sequences = dict(QualityTreatmentSequence.objects.using(target_alias).values_list("key", "last_value"))
    return (source_orders <= target_orders and source_treatments <= target_treatments
            and source_ledger <= target_ledger
            and all(target_sequences.get(key, -1) >= value for key, value in source_sequences.items()))


def _required_privilege_report(alias):
    candidate = candidate_config(decrypt=False) or {}
    required = {"CREATE", "ALTER", "INDEX", "INSERT", "UPDATE", "DELETE", "SELECT", "REFERENCES"}
    try:
        with connections[alias].cursor() as cursor:
            cursor.execute("SHOW GRANTS")
            grants = [str(row[0]) for row in cursor.fetchall()]
    except Exception:
        return {"known": False, "sufficient": None, "missing": sorted(required)}
    available = set()
    schema = str(candidate.get("database", "")).casefold()
    for grant in grants:
        normalized = grant.casefold()
        if " on *.* " not in normalized and f" on `{schema}`.* " not in normalized and f" on {schema}.* " not in normalized:
            continue
        privilege_text = normalized.split("grant ", 1)[-1].split(" on ", 1)[0]
        privileges = {item.strip().strip("`").upper() for item in privilege_text.split(",")}
        if "ALL PRIVILEGES" in privileges or "ALL" in privileges:
            available.update(required)
        else:
            available.update(privileges)
    missing = sorted(required - available)
    return {"known": True, "sufficient": not missing, "missing": missing}


def validate_candidate_migration(*, source_alias="default"):
    candidate = _require_candidate_test()
    state = migration_state()
    if not state["schema_prepared"] or not state["data_copied"]:
        raise ValidationError("Prepare o schema e copie os dados locais antes de validar a migração.")
    with candidate_database() as target_alias:
        migrations = _assert_all_migrations_applied(target_alias)
        counts = _model_count_report(source_alias, target_alias)
        checks = {"connection": True, "migrations": migrations["pending_migrations"] == 0,
                  "users": counts["auth.user"]["target"] >= counts["auth.user"]["source"],
                  "groups": counts["auth.group"]["target"] >= counts["auth.group"]["source"],
                  "scopes": counts["configuracoes.groupdatascope"]["target"] >= counts["configuracoes.groupdatascope"]["source"],
                  "legacy_scopes": counts["configuracoes.legacyuserdatascope"]["target"] >= counts["configuracoes.legacyuserdatascope"]["source"],
                  "saved_queries": counts["painel.savedanalyticsview"]["target"] >= counts["painel.savedanalyticsview"]["source"],
                  "preferences": counts["configuracoes.userpreference"]["target"] >= counts["configuracoes.userpreference"]["source"],
                  "audit": counts["auditoria.auditevent"]["target"] >= counts["auditoria.auditevent"]["source"],
                  "integrations": counts["integracoes.integration"]["target"] >= counts["integracoes.integration"]["source"],
                  "external_apis": counts["integracoes.externalapiintegration"]["target"] >= counts["integracoes.externalapiintegration"]["source"],
                  "quality_records": all(
                      values["target"] >= values["source"]
                      for label, values in counts.items() if label.startswith("qualidade.")
                  ),
                  "quality_identifiers": _quality_identifiers_match(source_alias, target_alias),
                  "quality_linked_mirrors": all(
                      counts[label]["target"] >= counts[label]["source"]
                      for label in ("integracoes.cmdblinkmirror", "integracoes.otrsticket")
                  ),
                  "worker_checkpoints": all(
                      counts[label]["target"] >= counts[label]["source"]
                      for label in ("integracoes.integrationsyncstate", "qualidade.qualitycorrelationcheckpoint")
                  ),
                  "relationships": all(counts["relationships"]["target"].get(key, 0) >= count
                                        for key, count in counts["relationships"]["source"].items()),
                  "permissions": _permissions_match(source_alias, target_alias),
                  "permission_assignments": _permission_assignments_match(source_alias, target_alias),
                  "group_names": _group_names_match(source_alias, target_alias),
                  "user_group_memberships": _user_group_memberships_match(source_alias, target_alias),
                  "user_hashes": _user_hashes_match(source_alias, target_alias),
                  "integration_secrets": _integration_secrets_open(target_alias)}
        if not all(checks.values()):
            raise ValidationError("A validação encontrou contagens locais divergentes; o candidato não pode ser ativado.")
        report = {"passed": True, "checks": checks, "counts": counts,
                  "migrations": migrations, "candidate_database": candidate["database"]}
    mark_migration_stage("data_copied", validation=report)
    return report


def dry_run_migration(*, source_alias="default"):
    _require_candidate_test()
    state = migration_state()
    with candidate_database() as target_alias:
        target_connection = connections[target_alias]
        with target_connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            selected_database = cursor.fetchone()[0]
        tables = set(target_connection.introspection.table_names())
        migration_table_exists = "django_migrations" in tables
        migrations = _migration_status(target_alias)
        _permission_map, permission_report = _permission_id_map(source_alias, target_alias)
        try:
            counts = _model_count_report(source_alias, target_alias) if migration_table_exists else None
        except Exception:
            counts = None
        users = get_user_model()
        source_usernames = set(users.objects.using(source_alias).values_list("username", flat=True))
        try:
            target_usernames = set(users.objects.using(target_alias).values_list("username", flat=True)) if state["schema_prepared"] else set()
        except Exception:
            target_usernames = set()
        conflicts = sorted(source_usernames & target_usernames)
        target_counts = {key: value["target"] for key, value in counts.items()
                         if key not in {"relationships", "permissions"}} if counts else {}
        if counts:
            target_counts["permissions"] = counts["permissions"]["target"]
        return {"connection": True, "schema_prepared": bool(state["schema_prepared"]),
                "schema_exists": selected_database == target_connection.settings_dict.get("NAME"),
                "migration_table_exists": migration_table_exists,
                "source_counts": {model._meta.label_lower: model.objects.using(source_alias).count() for model, _, _ in _model_specs()},
                "target_counts": target_counts,
                "relationship_counts": (counts["relationships"] if counts else None), "username_conflict_count": len(conflicts),
                "username_conflict_hashes": [hashlib.sha256(name.casefold().encode()).hexdigest() for name in conflicts],
                "migrations": migrations, "required_privileges": _required_privilege_report(target_alias),
                "permissions": permission_report}
