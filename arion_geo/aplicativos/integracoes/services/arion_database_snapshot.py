"""Complete snapshot to an empty candidate, using existing DB provisioning.

Includes managed models/M:N with stable PKs, except sessions and live leases.
Credentials must be configured again under the target Windows account.
"""
import hashlib
import json

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connections, transaction

from .arion_database import mark_migration_stage, migration_state
from .arion_database_migration import _assert_all_migrations_applied, _require_candidate_test, candidate_database

OMITTED = {"sessions.session", "integracoes.integrationlease"}
GENERATED = {"contenttypes.contenttype", "auth.permission"}


def assert_empty_before_prepare(target_alias):
    """Refuse business data before migrations can change an existing schema."""
    connection = connections[target_alias]
    allowed = {"django_migrations", "django_content_type", "auth_permission", "auth_group",
               "auth_group_permissions", "configuracoes_groupdatascope"}
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
        for table in tables:
            if table not in allowed:
                cursor.execute(f"SELECT 1 FROM {connection.ops.quote_name(table)} LIMIT 1")
                if cursor.fetchone() is not None:
                    raise ValidationError("O candidato já contém dados; nenhuma migration do snapshot será executada. Use schema dedicado vazio.")


def snapshot_models():
    models = {m._meta.label_lower: m for m in apps.get_models(include_auto_created=True)
              if m._meta.managed and not m._meta.proxy and m._meta.label_lower not in OMITTED}
    ordered, done = [], set()
    while len(done) < len(models):
        ready = [label for label, model in models.items() if label not in done and all(
            f.related_model == model or f.related_model._meta.label_lower in done
            for f in model._meta.concrete_fields if f.is_relation)]
        if not ready:
            raise ValidationError("Dependência cíclica não suportada no snapshot; revise os modelos antes de migrar.")
        for label in sorted(ready):
            ordered.append(models[label])
            done.add(label)
    return ordered


def transferable_row(model, row):
    row = dict(row)
    label = model._meta.label_lower
    if label in {"integracoes.integration", "integracoes.externalapiintegration"}:
        for key in ("secret_ciphertext", "smtp_secret_ciphertext"):
            if key in row:
                row[key] = ""
        row["enabled"] = False
        for key in ("last_test_at", "last_success_at", "last_read_at", "last_error_at", "last_latency_ms"):
            if key in row:
                row[key] = None
        row["last_error"] = ""
        if label == "integracoes.integration":
            row["status"] = model.Status.NOT_CONFIGURED
    return row


def legacy_columns(alias, model):
    """SeparateDatabaseAndState kept the original two scope-through column names.

    Same compatibility rule as the existing curated migrator. No schema repair or
    change to the legacy scope is needed to carry these historical relationships.
    """
    if model._meta.label_lower not in {"configuracoes.legacyuserdatascope_allowed_operators",
                                       "configuracoes.legacyuserdatascope_allowed_projects"}:
        return None
    connection = connections[alias]
    with connection.cursor() as cursor:
        actual = {c.name for c in connection.introspection.get_table_description(cursor, model._meta.db_table)}
    columns = {}
    for field in model._meta.concrete_fields:
        column = field.column
        if column not in actual and column == "legacyuserdatascope_id":
            column = "userdatascope_id"
        if column not in actual:
            raise ValidationError("Relação histórica de escopo incompatível com o schema.")
        columns[field.attname] = column
    return columns


def snapshot_rows(alias, model):
    columns = legacy_columns(alias, model)
    if columns is None:
        yield from model.objects.using(alias).order_by(model._meta.pk.name).values().iterator(chunk_size=1000)
        return
    connection = connections[alias]
    quote = connection.ops.quote_name
    selected = ", ".join(quote(c) for c in columns.values())
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {selected} FROM {quote(model._meta.db_table)} ORDER BY {quote(model._meta.pk.column)}")
        while rows := cursor.fetchmany(1000):
            for values in rows:
                yield dict(zip(columns, values))


def inventory(alias, *, source=False):
    result = {}
    for model in snapshot_models():
        hasher, count = hashlib.sha256(), 0
        for row in snapshot_rows(alias, model):
            if source:
                row = transferable_row(model, row)
            hasher.update(json.dumps(row, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":")).encode())
            hasher.update(b"\n")
            count += 1
        result[model._meta.label_lower] = {"count": count, "sha256": hasher.hexdigest()}
    return result


def _assert_distinct(source_alias, target_alias):
    if connections[source_alias].vendor != "sqlite" or connections[target_alias].vendor != "mysql":
        raise ValidationError("Snapshot completo exige origem SQLite explícita e candidato MySQL real.")


def _nonempty_target(target_alias):
    models = snapshot_models() + [apps.get_model(label) for label in sorted(OMITTED)]
    return [m._meta.label_lower for m in models
            if m._meta.label_lower not in GENERATED and m.objects.using(target_alias).exists()]


def prepare_empty_snapshot_candidate(target_alias):
    """Remove only bootstrap profiles after migrate, before any data is copied."""
    from aplicativos.configuracoes.access_control import ROLE_PERMISSIONS, GROUP_SYSTEM_ADMIN, managed_permissions
    seeds = {"auth.group", "auth.group_permissions", "configuracoes.groupdatascope"}
    conflicts = set(_nonempty_target(target_alias)) - seeds
    group = apps.get_model("auth.Group")
    scope = apps.get_model("configuracoes.GroupDataScope")
    if set(group.objects.using(target_alias).values_list("name", flat=True)) - set(ROLE_PERMISSIONS):
        conflicts.add("custom_groups")
    for row in scope.objects.using(target_alias).select_related("group"):
        expected = scope.Mode.ALL if row.group.name == GROUP_SYSTEM_ADMIN else scope.Mode.CUSTOM
        if row.mode != expected:
            conflicts.add("custom_scopes")
    available = {p.codename: p.pk for p in managed_permissions(target_alias)}
    for row in group.objects.using(target_alias).all():
        expected = {available[code] for code in ROLE_PERMISSIONS.get(row.name, ()) if code in available}
        if set(row.permissions.using(target_alias).values_list("pk", flat=True)) != expected:
            conflicts.add("custom_group_permissions")
    if conflicts:
        raise ValidationError("O candidato contém dados próprios; preparação completa exige schema dedicado vazio.")
    with transaction.atomic(using=target_alias):
        group.objects.using(target_alias).all().delete()


def snapshot_preview(source_alias, target_alias):
    _assert_distinct(source_alias, target_alias)
    _assert_all_migrations_applied(source_alias)
    _assert_all_migrations_applied(target_alias)
    payload = {"version": 1, "mode": "full_snapshot", "source": inventory(source_alias, source=True),
               "omitted": sorted(OMITTED), "credentials": "reconfigure_on_target"}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {**payload, "fingerprint": fingerprint, "conflicts": _nonempty_target(target_alias)}


def _copy_snapshot(source_alias, target_alias, *, approved_source):
    """Only a freshly prepared empty MySQL candidate, never a live database."""
    _assert_distinct(source_alias, target_alias)
    with transaction.atomic(using=target_alias):
        if _nonempty_target(target_alias):
            raise ValidationError("Snapshot exige candidato vazio; não mescla nem substitui dados do servidor.")
        # Only migrate-generated tables may contain rows. Restore source PKs to
        # preserve JSON IDs, audit object IDs and GenericFK as well as normal FKs.
        apps.get_model("auth.Permission").objects.using(target_alias).all().delete()
        apps.get_model("contenttypes.ContentType").objects.using(target_alias).all().delete()
        for model in snapshot_models():
            target_columns = legacy_columns(target_alias, model)
            automatic = [f.attname for f in model._meta.concrete_fields
                         if getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False)]
            batch, timestamps = [], []

            def flush():
                if not batch:
                    return
                if target_columns:
                    connection = connections[target_alias]
                    quote = connection.ops.quote_name
                    columns = ", ".join(quote(c) for c in target_columns.values())
                    parameters = ", ".join(["%s"] * len(target_columns))
                    with connection.cursor() as cursor:
                        cursor.executemany(f"INSERT INTO {quote(model._meta.db_table)} ({columns}) VALUES ({parameters})",
                                           [[getattr(obj, name) for name in target_columns] for obj in batch])
                else:
                    model.objects.using(target_alias).bulk_create(batch, batch_size=500)
                if automatic:
                    for obj, original in zip(batch, timestamps):
                        for name in automatic:
                            setattr(obj, name, original[name])
                    model.objects.using(target_alias).bulk_update(batch, automatic, batch_size=500)
                batch.clear()
                timestamps.clear()

            for row in snapshot_rows(source_alias, model):
                row = transferable_row(model, row)
                batch.append(model(**row))
                timestamps.append({key: row[key] for key in automatic})
                if len(batch) == 500:
                    flush()
            flush()
        connections[target_alias].check_constraints()
        expected, actual = inventory(source_alias, source=True), inventory(target_alias)
        if expected != actual or expected != approved_source:
            raise ValidationError("Conteúdo/contagens divergem após a cópia; transação revertida.")
    return actual


def run_snapshot_phase(source_alias, phase, *, approved=None):
    _require_candidate_test()
    if not migration_state()["schema_prepared"]:
        raise ValidationError("Prepare o schema candidato antes do snapshot completo.")
    with candidate_database() as target_alias:
        if phase == "validate":
            _assert_distinct(source_alias, target_alias)
            _assert_all_migrations_applied(source_alias)
            _assert_all_migrations_applied(target_alias)
            expected, actual = inventory(source_alias, source=True), inventory(target_alias)
            if expected != actual:
                raise ValidationError("Validação integral encontrou diferença de conteúdo ou contagem.")
            report = {"passed": True, "mode": "full_snapshot", "models": actual,
                      "checks": {"content_and_counts": True, "credentials_disabled": True}}
            mark_migration_stage("data_copied", validation=report)
            return report
        report = snapshot_preview(source_alias, target_alias)
        if phase == "copy_local_data":
            if report["conflicts"] or not approved or approved.get("conflicts") or approved.get("fingerprint") != report["fingerprint"]:
                raise ValidationError("Cópia exige preview atual aprovado e candidato vazio.")
            actual = _copy_snapshot(source_alias, target_alias, approved_source=report["source"])
            if report["source"] != actual:
                raise ValidationError("Origem mudou durante a cópia; não ative o candidato.")
            mark_migration_stage("data_copied")
            report["copied_models"] = len(actual)
        return report
