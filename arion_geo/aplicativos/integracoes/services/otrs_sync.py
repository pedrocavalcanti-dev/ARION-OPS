"""Queue snapshot synchronization through the dedicated read-only OTRS client."""
from collections import Counter
import logging
import re
import time
import unicodedata

from django.db import transaction
from django.utils import timezone

from aplicativos.integracoes.models import (
    CMDBLinkMirror, OTRSStateMapping, OTRSTicket, OTRSTicketConfigItem,
)

from .sync_common import clean, integer_or_none, normalized_name, source_datetime
from .otrs_status import BUSINESS_OPEN_STATUSES, status_for_external_state

logger = logging.getLogger(__name__)
IDENTIFIER = re.compile(r"^[A-Za-z0-9_$]+$")
OPEN_STATUSES = set(BUSINESS_OPEN_STATUSES)


def normalize_responsibility(value):
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", str(value or "").casefold())
        if not unicodedata.combining(character)
    ).strip()
    if normalized == "procedente":
        return "PROCEDENTE"
    if normalized == "improcedente":
        return "IMPROCEDENTE"
    return "INDETERMINADO"


def _val(row, key, default=None):
    return row.get(key, row.get(key.lower(), default)) if isinstance(row, dict) else default


def _number(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _otrs_naive_local_datetime(value):
    """Convert an internal Django timestamp to OTRS's timezone-naive DATETIME."""
    if value is None:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value, timezone.get_default_timezone())
    return value.replace(tzinfo=None)


class OTRSSyncService:
    TICKET_TABLE = "ticket"
    QUEUE_TABLE = "queue"
    RELATION_TABLE = "link_relation"

    def __init__(self, client, *, progress=lambda _: None):
        self.client = client
        self.progress = progress
        self.tables = set()
        self.columns = {}

    def _discover(self):
        rows = self.client.get_all_columns()
        self.tables = {str(_val(row, "TABLE_NAME")) for row in rows if _val(row, "TABLE_NAME")}
        for row in rows:
            table = str(_val(row, "TABLE_NAME") or "")
            column = str(_val(row, "COLUMN_NAME") or "")
            self.columns.setdefault(table.casefold(), set()).add(column)
        required = (self.TICKET_TABLE, self.QUEUE_TABLE, "ticket_state", "ticket_state_type", "ticket_priority", "ticket_type", "service", "sla", self.RELATION_TABLE, "link_object", "link_type", "link_state", "dynamic_field", "dynamic_field_value", "ticket_history", "ticket_history_type")
        missing = [name for name in required if name.casefold() not in self.columns]
        if missing:
            raise ValueError("Schema OTRS incompleto para sync: " + ", ".join(missing))

    def _table(self, name):
        actual = next((table for table in self.tables if table.casefold() == name.casefold()), None)
        if not actual or not IDENTIFIER.fullmatch(actual):
            raise ValueError("Tabela OTRS fora do allowlist do schema.")
        return f"`{actual}`"

    def _column(self, table, name, *, required=False):
        columns = self.columns.get(table.casefold(), set())
        actual = next((column for column in columns if column.casefold() == name.casefold()), None)
        if required and not actual:
            raise ValueError(f"Coluna necessária não encontrada: {table}.{name}.")
        if actual and not IDENTIFIER.fullmatch(actual):
            raise ValueError("Coluna OTRS fora do allowlist do schema.")
        return f"`{actual}`" if actual else None

    def _select(self, table, mapping, *, where="", params=()):
        projection = []
        for alias, column in mapping:
            quoted = self._column(table, column)
            if quoted:
                projection.append(f"{quoted} AS `{alias}`")
        if not projection:
            raise ValueError(f"Nenhuma coluna segura disponível em {table}.")
        sql = f"SELECT {', '.join(projection)} FROM {self._table(table)}"
        if where:
            sql += " WHERE " + where
        return self.client.execute_select(sql, params)

    def _dimension(self, table, *, with_type=False):
        mapping = [("id", "id"), ("name", "name")]
        if with_type:
            mapping.append(("type_id", "type_id"))
        return self._select(table, mapping)

    @staticmethod
    def _dimension_map(rows):
        return {_number(_val(row, "id")): clean(_val(row, "name")) for row in rows if _number(_val(row, "id")) is not None}

    def _queue(self, queue_name):
        rows = self._select("queue", (("id", "id"), ("name", "name")), where=f"{self._column('queue', 'name', required=True)} = %s", params=(queue_name,))
        exact = [row for row in rows if clean(_val(row, "name")) == queue_name]
        if len(exact) != 1:
            raise ValueError(f"A fila exata '{queue_name}' não foi encontrada de forma única.")
        return _number(_val(exact[0], "id")), clean(_val(exact[0], "name"))

    def _tickets(self, queue_ids, *, changed_since=None, known_ids=()):
        queue_ids = tuple(dict.fromkeys(_number(value) for value in queue_ids if _number(value) is not None))
        if not queue_ids:
            return []
        fields = (
            ("id", "id", True), ("tn", "tn", True), ("title", "title", False),
            ("queue_id", "queue_id", True), ("state_id", "ticket_state_id", True),
            ("priority_id", "ticket_priority_id", False), ("type_id", "type_id", False),
            ("service_id", "service_id", False), ("sla_id", "sla_id", False),
            ("responsible_user_id", "responsible_user_id", False), ("customer_id", "customer_id", False),
            ("timeout", "timeout", False), ("until_time", "until_time", False),
            ("escalation_time", "escalation_time", False), ("escalation_update_time", "escalation_update_time", False),
            ("escalation_response_time", "escalation_response_time", False), ("escalation_solution_time", "escalation_solution_time", False),
            ("create_time", "create_time", False), ("change_time", "change_time", False),
        )
        selected = []
        for alias, column, required in fields:
            quoted = self._column("ticket", column, required=required)
            if quoted:
                selected.append(f"{quoted} AS `{alias}`")
        queue_column = self._column("ticket", "queue_id", required=True)
        queue_marks = ",".join(["%s"] * len(queue_ids))
        queue_filter = f"{queue_column} IN ({queue_marks})"
        where = queue_filter
        params = list(queue_ids)
        if changed_since is not None:
            change_column = self._column("ticket", "change_time", required=True)
            id_column = self._column("ticket", "id", required=True)
            known_ids = tuple(known_ids)
            marks = ",".join(["%s"] * len(known_ids)) or "NULL"
            where = f"(({queue_filter}) OR ({id_column} IN ({marks}))) AND {change_column} >= %s"
            params = [*queue_ids, *known_ids, changed_since]
        sql = f"SELECT {', '.join(selected)} FROM {self._table('ticket')} WHERE {where}"
        rows = self.client.execute_select(sql, tuple(params))
        if any(_number(_val(row, "id")) is None or not clean(_val(row, "tn")) for row in rows):
            raise ValueError("Snapshot de ticket contém identificador ou número vazio.")
        ids = [str(_val(row, "id")) for row in rows]
        numbers = [clean(_val(row, "tn")) for row in rows]
        if len(set(ids)) != len(ids) or len(set(numbers)) != len(numbers):
            raise ValueError("IDs/números de ticket duplicados no snapshot; reconciliação cancelada.")
        return rows

    def _relation_ids(self, table, names):
        rows = self._select(table, (("id", "id"), ("name", "name")))
        matches = [_number(_val(row, "id")) for row in rows if clean(_val(row, "name")) in names]
        if len(matches) != 1:
            raise ValueError(f"Valores OTRS esperados não identificados em {table}.")
        return matches[0]

    def _object_ids(self):
        rows = self._select("link_object", (("id", "id"), ("name", "name")))
        found = {}
        for row in rows:
            name = clean(_val(row, "name"))
            if name in {"Ticket", "ITSMConfigItem"}:
                found[name] = _number(_val(row, "id"))
        if not found.get("Ticket") or not found.get("ITSMConfigItem"):
            raise ValueError("Objetos OTRS Ticket/ITSMConfigItem não foram resolvidos por nome.")
        return found

    def _relations(self, ticket_rows, objects, relation_type_id, relation_state_id):
        ticket_ids = [str(_val(row, "id")) for row in ticket_rows]
        if not ticket_ids:
            return []
        table = "link_relation"
        cols = (("source_object_id", "source_object_id"), ("source_key", "source_key"), ("target_object_id", "target_object_id"), ("target_key", "target_key"), ("type_id", "type_id"), ("state_id", "state_id"), ("create_time", "create_time"))
        projection = []
        for alias, field in cols:
            quoted = self._column(table, field)
            if quoted:
                projection.append(f"{quoted} AS `{alias}`")
        source_obj = self._column(table, "source_object_id", required=True)
        source_key = self._column(table, "source_key", required=True)
        target_obj = self._column(table, "target_object_id", required=True)
        target_key = self._column(table, "target_key", required=True)
        result = []
        for offset in range(0, len(ticket_ids), 400):
            batch = ticket_ids[offset:offset + 400]
            marks = ",".join(["%s"] * len(batch))
            sql = (f"SELECT {', '.join(projection)} FROM {self._table(table)} WHERE "
                   f"({source_obj} = %s AND {source_key} IN ({marks})) OR "
                   f"({target_obj} = %s AND {target_key} IN ({marks}))")
            rows = self.client.execute_select(sql, (objects["Ticket"], *batch, objects["Ticket"], *batch))
            for row in rows:
                if _number(_val(row, "type_id")) != relation_type_id or _number(_val(row, "state_id")) != relation_state_id:
                    continue
                source_object = _number(_val(row, "source_object_id"))
                target_object = _number(_val(row, "target_object_id"))
                if source_object == objects["ITSMConfigItem"] and target_object == objects["Ticket"]:
                    ci_id, ticket_id = clean(_val(row, "source_key")), _number(_val(row, "target_key"))
                elif source_object == objects["Ticket"] and target_object == objects["ITSMConfigItem"]:
                    ticket_id, ci_id = _number(_val(row, "source_key")), clean(_val(row, "target_key"))
                else:
                    continue
                if ticket_id is not None and ci_id:
                    result.append({"ticket_id": ticket_id, "config_item_id": ci_id, "relation_type": "DependsOn", "relation_state": "Valid", "source_created_at": source_datetime(_val(row, "create_time"))})
        return result

    @staticmethod
    def _state_status(name):
        return status_for_external_state(name)

    def _normalize_tickets(self, ticket_rows, queue_names_by_id, selected_queue_ids, dimensions, state_type_map, now):
        statuses = Counter()
        mappings = {}
        warnings = []
        for row in dimensions["states"]:
            state_id = _number(_val(row, "id"))
            if state_id is None:
                continue
            name = clean(_val(row, "name"))
            state_type = state_type_map.get(_number(_val(row, "type_id")), "")
            status = self._state_status(name)
            mappings[state_id] = {"external_state_id": state_id, "external_name": name, "external_type": state_type, "business_status": status, "is_configured": status != OTRSStateMapping.BusinessStatus.UNKNOWN, "source_last_seen_at": now}
        normalized = []
        dimension_maps = {key: self._dimension_map(dimensions[key]) for key in ("priorities", "types", "services", "slas")}
        selected_queue_ids = set(selected_queue_ids)
        for row in ticket_rows:
            current_queue_id = _number(_val(row, "queue_id"))
            queue_name = queue_names_by_id.get(current_queue_id, "")
            if not queue_name:
                raise ValueError(f"Fila OTRS atual sem nome resolvido para ticket {_number(_val(row, 'id'))}.")
            state_id = _number(_val(row, "state_id"))
            state_map = mappings.get(state_id)
            state_name = state_map["external_name"] if state_map else ""
            state_type = state_map["external_type"] if state_map else ""
            status = state_map["business_status"] if state_map else OTRSStateMapping.BusinessStatus.UNKNOWN
            if status == OTRSStateMapping.BusinessStatus.UNKNOWN:
                warnings.append(f"Estado sem mapeamento explícito: {state_name or state_id}")
            statuses[status] += 1
            def dim_value(field, map_key):
                identifier = _number(_val(row, field))
                return identifier, dimension_maps[map_key].get(identifier, "")
            priority_id, priority_name = dim_value("priority_id", "priorities")
            type_id, type_name = dim_value("type_id", "types")
            service_id, service_name = dim_value("service_id", "services")
            sla_id, sla_name = dim_value("sla_id", "slas")
            normalized.append({
                "external_id": _number(_val(row, "id")), "ticket_number": clean(_val(row, "tn"), 80),
                "title": clean(_val(row, "title"), 500), "queue_external_id": current_queue_id or 0, "queue_name": queue_name,
                "external_state_id": state_id or 0, "external_state_name": state_name, "external_state_type": state_type,
                "business_status": status, "is_business_open": status in OPEN_STATUSES,
                "external_priority_id": priority_id, "priority_name": priority_name,
                "external_type_id": type_id, "type_name": type_name,
                "external_service_id": service_id, "service_name": service_name,
                "external_sla_id": sla_id, "sla_name": sla_name,
                "responsible_user_id": _number(_val(row, "responsible_user_id")),
                "customer_id": clean(_val(row, "customer_id"), 250),
                "timeout": _number(_val(row, "timeout")), "until_time": _number(_val(row, "until_time")),
                "escalation_time": _number(_val(row, "escalation_time")),
                "escalation_update_time": _number(_val(row, "escalation_update_time")),
                "escalation_response_time": _number(_val(row, "escalation_response_time")),
                "escalation_solution_time": _number(_val(row, "escalation_solution_time")),
                "source_created_at": source_datetime(_val(row, "create_time")),
                "source_changed_at": source_datetime(_val(row, "change_time")),
                "is_present_in_queue": current_queue_id in selected_queue_ids,
            })
        # Log only state names and counts; never titles or customer details.
        for warning in sorted(set(warnings)):
            logger.warning("OTRS sync: %s", warning)
        return normalized, mappings, statuses, sorted(set(warnings))

    def _ticket_terminal_fields(self, ticket_rows, dimensions):
        """Load approved closing fields and an optional ticket validation field in batches."""
        ticket_ids = [_number(_val(row, "id")) for row in ticket_rows]
        if not ticket_ids:
            return {}, {"closing_code_filled": 0, "resolved_at_filled": 0, "terminal_at_filled": 0}
        dynamic_fields = self._select(
            "dynamic_field", (("id", "id"), ("name", "name"), ("field_type", "field_type"), ("object_type", "object_type")),
            where=f"{self._column('dynamic_field', 'name', required=True)} IN (%s, %s, %s)",
            params=("CodigoFechamento", "ticketResolvido", "RvalidacaoProcedenteImprocedente"),
        )
        definitions = {clean(_val(row, "name")): row for row in dynamic_fields}
        required_fields = {"CodigoFechamento", "ticketResolvido"}
        if not required_fields.issubset(definitions) or any(clean(_val(definitions[name], "object_type")) != "Ticket" for name in required_fields):
            raise ValueError("Dynamic Fields de fechamento do tipo Ticket não foram encontrados.")
        responsibility_definition = definitions.get("RvalidacaoProcedenteImprocedente")
        if responsibility_definition and (
            clean(_val(responsibility_definition, "object_type")) != "Ticket"
            or clean(_val(responsibility_definition, "field_type")).casefold() != "dropdown"
        ):
            responsibility_definition = None
        field_ids = {name: _number(_val(definitions[name], "id")) for name in required_fields}
        if responsibility_definition:
            field_ids["RvalidacaoProcedenteImprocedente"] = _number(_val(responsibility_definition, "id"))
        if any(value is None for value in field_ids.values()):
            raise ValueError("IDs dos Dynamic Fields de fechamento inválidos.")
        source_states = {
            _number(_val(row, "id")): self._state_status(clean(_val(row, "name")))
            for row in dimensions["states"]
        }
        terminal_state_ids = {
            _number(_val(row, "id")) for row in dimensions["states"]
            if self._state_status(clean(_val(row, "name"))) in {
                OTRSStateMapping.BusinessStatus.RESOLVED,
                OTRSStateMapping.BusinessStatus.CANCELED,
            }
        }
        current_state_by_id = {
            _number(_val(row, "id")): _number(_val(row, "state_id")) for row in ticket_rows
        }
        result = {ticket_id: {"closing_code_raw": "", "responsibility_raw": "", "responsibility_normalized": "INDETERMINADO",
                              "resolved_at": None, "terminal_state_at": None, "canceled_at": None}
                  for ticket_id in ticket_ids if ticket_id is not None}
        object_id = self._column("dynamic_field_value", "object_id", required=True)
        field_id = self._column("dynamic_field_value", "field_id", required=True)
        value_text = self._column("dynamic_field_value", "value_text", required=True)
        value_date = self._column("dynamic_field_value", "value_date", required=True)
        for offset in range(0, len(ticket_ids), 400):
            batch = ticket_ids[offset:offset + 400]
            marks = ",".join(["%s"] * len(batch))
            field_marks = ", ".join(["%s"] * len(field_ids))
            sql = (f"SELECT {object_id} AS `object_id`, {field_id} AS `field_id`, "
                   f"{value_text} AS `value_text`, {value_date} AS `value_date` "
                   f"FROM {self._table('dynamic_field_value')} WHERE {field_id} IN ({field_marks}) "
                   f"AND {object_id} IN ({marks})")
            ordered_field_ids = list(field_ids.values())
            for row in self.client.execute_select(sql, (*ordered_field_ids, *batch)):
                target = result.get(_number(_val(row, "object_id")))
                if not target:
                    continue
                source_field = _number(_val(row, "field_id"))
                if source_field == field_ids["CodigoFechamento"]:
                    target["closing_code_raw"] = clean(_val(row, "value_text"), 250)
                elif source_field == field_ids["ticketResolvido"]:
                    target["resolved_at"] = source_datetime(_val(row, "value_date"))
                elif field_ids.get("RvalidacaoProcedenteImprocedente") == source_field:
                    target["responsibility_raw"] = clean(_val(row, "value_text"), 120)
                    target["responsibility_normalized"] = normalize_responsibility(target["responsibility_raw"])

        current_terminal = {ticket_id: state_id for ticket_id, state_id in current_state_by_id.items()
                            if state_id in terminal_state_ids}
        if current_terminal:
            history_type = self._column("ticket_history", "history_type_id", required=True)
            history_ticket = self._column("ticket_history", "ticket_id", required=True)
            history_state = self._column("ticket_history", "state_id", required=True)
            history_time = self._column("ticket_history", "create_time", required=True)
            type_id_rows = self._select("ticket_history_type", (("id", "id"), ("name", "name")),
                                        where=f"{self._column('ticket_history_type', 'name', required=True)} = %s", params=("StateUpdate",))
            if len(type_id_rows) != 1:
                raise ValueError("Tipo de histórico StateUpdate não foi encontrado de forma única.")
            state_ids = sorted(terminal_state_ids)
            state_marks = ",".join(["%s"] * len(state_ids))
            for offset in range(0, len(ticket_ids), 400):
                batch = ticket_ids[offset:offset + 400]
                marks = ",".join(["%s"] * len(batch))
                sql = (f"SELECT {history_ticket} AS `ticket_id`, {history_state} AS `state_id`, MAX({history_time}) AS `terminal_at` "
                       f"FROM {self._table('ticket_history')} WHERE {history_type} = %s "
                       f"AND {history_state} IN ({state_marks}) AND {history_ticket} IN ({marks}) "
                       f"GROUP BY {history_ticket}, {history_state}")
                params = (_number(_val(type_id_rows[0], "id")), *state_ids, *batch)
                for row in self.client.execute_select(sql, params):
                    ticket_id = _number(_val(row, "ticket_id"))
                    state_id = _number(_val(row, "state_id"))
                    terminal_at = source_datetime(_val(row, "terminal_at"))
                    target = result.get(ticket_id)
                    if target and current_terminal.get(ticket_id) == state_id and terminal_at:
                        target["terminal_state_at"] = terminal_at
                        if source_states.get(state_id) == OTRSStateMapping.BusinessStatus.CANCELED:
                            target["canceled_at"] = terminal_at
        return result, {
            "closing_code_filled": sum(bool(row["closing_code_raw"]) for row in result.values()),
            "responsibility_filled": sum(bool(row["responsibility_raw"]) for row in result.values()),
            "resolved_at_filled": sum(row["resolved_at"] is not None for row in result.values()),
            "terminal_at_filled": sum(row["terminal_state_at"] is not None for row in result.values()),
        }

    @staticmethod
    def _upsert_dimension(model, rows, *, apply):
        existing = {obj.external_state_id: obj for obj in model.objects.all()}
        fields = ("external_name", "external_type", "business_status", "is_configured", "source_last_seen_at")
        create, update, created, updated, unchanged = [], [], 0, 0, 0
        for row in rows.values():
            current = existing.get(row["external_state_id"])
            if current is None:
                create.append(model(**row))
                created += 1
            else:
                changed = any(getattr(current, field) != row[field] for field in fields[:-1])
                if changed:
                    updated += 1
                else:
                    unchanged += 1
                for key, value in row.items():
                    setattr(current, key, value)
                update.append(current)
        if apply:
            model.objects.bulk_create(create, batch_size=500)
            model.objects.bulk_update(update, fields=fields, batch_size=500)
        return {"found": len(rows), "processed": len(rows), "created": created, "updated": updated, "unchanged": unchanged, "failed": 0}

    @staticmethod
    def _upsert_tickets(rows, *, now, apply, reconcile=True, reconcile_queue_ids=None):
        source_ids = {row["external_id"] for row in rows}
        if reconcile and reconcile_queue_ids is not None:
            existing_qs = (
                OTRSTicket.objects.filter(queue_external_id__in=reconcile_queue_ids)
                | OTRSTicket.objects.filter(external_id__in=source_ids)
            )
        else:
            existing_qs = OTRSTicket.objects.all() if reconcile else OTRSTicket.objects.filter(external_id__in=source_ids)
        existing = {obj.external_id: obj for obj in existing_qs}
        fields = tuple(rows[0].keys()) if rows else ()
        create, update, created, updated, unchanged = [], [], 0, 0, 0
        for row in rows:
            values = {**row, "source_last_seen_at": now, "source_synced_at": now, "is_present_in_queue": row.get("is_present_in_queue", True)}
            current = existing.get(row["external_id"])
            if current is None:
                create.append(OTRSTicket(**values))
                created += 1
                continue
            changed = any(getattr(current, field) != row[field] for field in fields) or not current.is_present_in_queue
            if changed:
                updated += 1
            else:
                unchanged += 1
            for key, value in values.items():
                setattr(current, key, value)
            update.append(current)
        reconcile_queue_ids = None if reconcile_queue_ids is None else set(reconcile_queue_ids)
        stale_ids = [
            obj.pk for obj in existing.values()
            if reconcile and obj.is_present_in_queue and obj.external_id not in source_ids
            and (reconcile_queue_ids is None or obj.queue_external_id in reconcile_queue_ids)
        ]
        if apply:
            OTRSTicket.objects.bulk_create(create, batch_size=500)
            OTRSTicket.objects.bulk_update(update, fields=[*fields, "source_last_seen_at", "source_synced_at", "is_present_in_queue"], batch_size=500)
            if stale_ids:
                OTRSTicket.objects.filter(pk__in=stale_ids).update(is_present_in_queue=False)
        return {"found": len(rows), "processed": len(rows), "created": created, "updated": updated, "unchanged": unchanged, "stale": len(stale_ids), "failed": 0}

    @staticmethod
    def _canonical_relation_key(ticket_id, config_item_id, relation_type):
        external_ticket_id = _number(ticket_id)
        canonical_ci_id = normalized_name(config_item_id)
        canonical_relation_type = normalized_name(relation_type)
        if external_ticket_id is None or not canonical_ci_id or not canonical_relation_type:
            raise ValueError("Relação OTRS sem ticket, ConfigItem ou tipo válido.")
        return external_ticket_id, canonical_ci_id, canonical_relation_type

    @classmethod
    def _deduplicate_relation_rows(cls, relations):
        unique = {}
        for relation in relations:
            key = cls._canonical_relation_key(
                relation.get("ticket_id"), relation.get("config_item_id"), relation.get("relation_type"),
            )
            # Keep the first source row deterministically; later copies of the
            # same logical relation must not affect the mirror or its metrics.
            unique.setdefault(key, relation)
        return list(unique.values())

    @classmethod
    def _upsert_relations(cls, relations, *, ticket_map, now, apply, affected_ticket_ids=None):
        found_count = len(relations)
        relations = cls._deduplicate_relation_rows(relations)
        tickets_by_external = {ticket.external_id: ticket for ticket in ticket_map.values()}
        config_item_ids = {relation["config_item_id"] for relation in relations}
        links_by_ci = {
            normalized_name(link.source_config_item_id): link
            for link in CMDBLinkMirror.objects.filter(source_config_item_id__in=config_item_ids)
        }
        existing = {}
        existing_rows = OTRSTicketConfigItem.objects.select_related("ticket").filter(
            **({} if affected_ticket_ids is None else {"ticket__external_id__in": affected_ticket_ids})
        )
        for row in existing_rows:
            key = cls._canonical_relation_key(
                row.ticket.external_id, row.config_item_id, row.relation_type,
            )
            existing.setdefault(key, row)
        create, update, created, updated, unchanged = [], [], 0, 0, 0
        seen = set()
        fields = ("ticket_id", "config_item_id", "cmdb_link_id", "relation_type", "relation_state", "source_created_at", "source_last_seen_at", "source_synced_at", "is_present_in_source")
        for relation in relations:
            ticket = tickets_by_external.get(relation["ticket_id"])
            if not ticket:
                raise ValueError("Relação recebida para ticket fora da fila; reconciliação cancelada.")
            key = cls._canonical_relation_key(
                ticket.external_id, relation["config_item_id"], relation["relation_type"],
            )
            link = links_by_ci.get(normalized_name(relation["config_item_id"]))
            values = {
                "ticket_id": ticket.pk, "config_item_id": relation["config_item_id"],
                "cmdb_link_id": link.pk if link else None, "relation_type": relation["relation_type"],
                "relation_state": relation["relation_state"], "source_created_at": relation["source_created_at"],
                "source_last_seen_at": now, "source_synced_at": now, "is_present_in_source": True,
            }
            seen.add(key)
            current = existing.get(key)
            if current is None:
                create.append(OTRSTicketConfigItem(**values))
                created += 1
            else:
                changed = any(getattr(current, field) != values[field] for field in fields[:-3]) or not current.is_present_in_source
                if changed:
                    updated += 1
                else:
                    unchanged += 1
                for name, value in values.items():
                    setattr(current, name, value)
                update.append(current)
        stale_ids = [row.pk for key, row in existing.items() if row.is_present_in_source and key not in seen]
        if apply:
            OTRSTicketConfigItem.objects.bulk_create(create, batch_size=500)
            OTRSTicketConfigItem.objects.bulk_update(update, fields=fields, batch_size=500)
            if stale_ids:
                OTRSTicketConfigItem.objects.filter(pk__in=stale_ids).update(is_present_in_source=False)
        return {"found": found_count, "processed": len(relations), "created": created, "updated": updated, "unchanged": unchanged, "stale": len(stale_ids), "failed": 0, "unresolved_link": sum(1 for row in relations if normalized_name(row["config_item_id"]) not in links_by_ci)}

    def run(self, queue_name="NOC - ATACADO", *, queue_names=None, apply=False, changed_since=None, safety_window=120):
        started = time.perf_counter()
        queries_before = getattr(self.client, "select_queries_count", 0)
        self._discover()
        if queue_names is not None:
            requested_queue_names = tuple(queue_names)
        elif isinstance(queue_name, str):
            requested_queue_names = (queue_name,)
        else:
            requested_queue_names = tuple(queue_name or ())
        requested_queue_names = tuple(dict.fromkeys(clean(name) for name in requested_queue_names if clean(name)))
        if not requested_queue_names:
            raise ValueError("Informe ao menos uma fila OTRS para sincronizar.")

        queues = [self._queue(name) for name in requested_queue_names]
        queue_ids = tuple(queue_id for queue_id, _name in queues)
        if len(set(queue_ids)) != len(queue_ids):
            raise ValueError("Filas OTRS diferentes resolveram para o mesmo identificador.")
        queue_names_by_id = {queue_id: name for queue_id, name in queues}
        for queue_id, resolved_queue in queues:
            if resolved_queue == "NOC - ATACADO" and queue_id != 107:
                self.progress(f"[OTRS] Aviso: fila resolvida por nome com id={queue_id}; estudo anterior observou 107.")
            self.progress(f"[OTRS] Fila {resolved_queue} encontrada: id={queue_id}.")

        dimensions = {
            "states": self._dimension("ticket_state", with_type=True),
            "priorities": self._dimension("ticket_priority"),
            "types": self._dimension("ticket_type"),
            "services": self._dimension("service"),
            "slas": self._dimension("sla"),
        }
        type_rows = self._dimension("ticket_state_type")
        state_type_map = self._dimension_map(type_rows)
        now = timezone.now()
        is_incremental = changed_since is not None
        known_ids = tuple(
            OTRSTicket.objects.filter(is_present_in_queue=True, queue_external_id__in=queue_ids)
            .values_list("external_id", flat=True)
        ) if is_incremental else ()
        query_changed_since = changed_since
        if is_incremental:
            from datetime import timedelta
            safety_cursor = changed_since - timedelta(seconds=safety_window)
            query_changed_since = _otrs_naive_local_datetime(safety_cursor)
            logger.info(
                "OTRS FAST cursor_internal=%s changed_since_otrs=%s safety_window=%ss timezone=%s",
                changed_since.isoformat(), query_changed_since.isoformat(),
                safety_window, timezone.get_default_timezone_name(),
            )
        ticket_source_rows = self._tickets(queue_ids, changed_since=query_changed_since, known_ids=known_ids)
        self.progress(f"[OTRS] Tickets atuais encontrados: {len(ticket_source_rows)}.")
        source_queue_ids = {_number(_val(row, "queue_id")) for row in ticket_source_rows}
        unmapped_queue_ids = source_queue_ids - set(queue_names_by_id)
        if unmapped_queue_ids:
            queue_id_column = self._column("queue", "id", required=True)
            marks = ",".join(["%s"] * len(unmapped_queue_ids))
            queue_rows = self._select(
                "queue", (("id", "id"), ("name", "name")),
                where=f"{queue_id_column} IN ({marks})", params=tuple(sorted(unmapped_queue_ids)),
            )
            queue_names_by_id.update({
                _number(_val(row, "id")): clean(_val(row, "name"))
                for row in queue_rows if _number(_val(row, "id")) is not None and clean(_val(row, "name"))
            })
        if source_queue_ids - set(queue_names_by_id):
            raise ValueError("A fila atual de um ticket não pôde ser resolvida pelo nome.")
        queue_ticket_counts = Counter(
            queue_names_by_id[_number(_val(row, "queue_id"))]
            for row in ticket_source_rows
        )
        queue_ticket_summary = [
            {"name": name, "id": queue_id, "tickets": queue_ticket_counts.get(name, 0)}
            for queue_id, name in queues
        ]
        for item in queue_ticket_summary:
            self.progress(f"[OTRS] Fila {item['name']}: {item['tickets']} tickets retornados.")
        tickets, state_mappings, status_counts, warnings = self._normalize_tickets(
            ticket_source_rows, queue_names_by_id, queue_ids, dimensions, state_type_map, now,
        )
        ticket_terminal_fields, terminal_field_counts = self._ticket_terminal_fields(ticket_source_rows, dimensions)
        for ticket in tickets:
            ticket.update(ticket_terminal_fields.get(ticket["external_id"], {}))
        objects = self._object_ids()
        relation_type_id = self._relation_ids("link_type", {"DependsOn"})
        relation_state_id = self._relation_ids("link_state", {"Valid"})
        relation_rows = self._relations(ticket_source_rows, objects, relation_type_id, relation_state_id)
        unique_relation_rows = self._deduplicate_relation_rows(relation_rows)
        self.progress(f"[OTRS] Relações válidas ticket–ConfigItem: {len(unique_relation_rows)} únicas em {len(relation_rows)} linhas de origem.")
        ticket_ci_counts = Counter(row["ticket_id"] for row in unique_relation_rows)
        ci_ticket_counts = Counter(normalized_name(row["config_item_id"]) for row in unique_relation_rows)
        relation_ci_ids = {row["config_item_id"] for row in unique_relation_rows}
        local_ci_ids = set(CMDBLinkMirror.objects.filter(source_config_item_id__in=relation_ci_ids).values_list("source_config_item_id", flat=True))
        canonical_local_ci_ids = {normalized_name(config_item_id) for config_item_id in local_ci_ids}
        relation_stats = {
            "tickets_with_ci": len(ticket_ci_counts),
            "tickets_without_ci": max(0, len(tickets) - len(ticket_ci_counts)),
            "tickets_one_ci": sum(1 for count in ticket_ci_counts.values() if count == 1),
            "tickets_multiple_ci": sum(1 for count in ticket_ci_counts.values() if count > 1),
            "unique_config_items": len(ci_ticket_counts),
            "configitems_multiple_tickets": sum(1 for count in ci_ticket_counts.values() if count > 1),
            "resolved_local_links": sum(1 for row in unique_relation_rows if normalized_name(row["config_item_id"]) in canonical_local_ci_ids),
            "unresolved_local_links": sum(1 for row in unique_relation_rows if normalized_name(row["config_item_id"]) not in canonical_local_ci_ids),
        }

        # Validate exact TN uniqueness before any local reconciliation.
        ticket_counts = Counter(row["ticket_number"] for row in tickets)
        if any(count > 1 for count in ticket_counts.values()):
            raise ValueError("Números de ticket duplicados; reconciliação cancelada.")
        number_owners = {ticket.ticket_number: ticket.external_id for ticket in OTRSTicket.objects.filter(ticket_number__in=[row["ticket_number"] for row in tickets])}
        if any(number_owners.get(row["ticket_number"], row["external_id"]) != row["external_id"] for row in tickets):
            raise ValueError("TN já pertence a outro ID externo local; reconciliação cancelada.")
        affected_ticket_ids = {item["external_id"] for item in tickets}
        if not is_incremental:
            affected_ticket_ids.update(
                OTRSTicket.objects.filter(is_present_in_queue=True, queue_external_id__in=queue_ids)
                .values_list("external_id", flat=True)
            )
        with transaction.atomic() if apply else _NullContext():
            mapping_counts = self._upsert_dimension(OTRSStateMapping, state_mappings, apply=apply)
            ticket_counts_summary = self._upsert_tickets(
                tickets, now=now, apply=apply, reconcile=not is_incremental,
                reconcile_queue_ids=queue_ids if not is_incremental else None,
            )
            if apply:
                ticket_map = {row.external_id: row for row in OTRSTicket.objects.filter(external_id__in=[row["external_id"] for row in tickets])}
            else:
                existing_tickets = {row.external_id: row for row in OTRSTicket.objects.filter(external_id__in=[item["external_id"] for item in tickets])}
                ticket_map = {
                    item["external_id"]: existing_tickets.get(item["external_id"]) or OTRSTicket(external_id=item["external_id"], **{key: value for key, value in item.items() if key != "external_id"})
                    for item in tickets
                }
            relation_counts_summary = self._upsert_relations(
                relation_rows, ticket_map=ticket_map, now=now, apply=apply,
                affected_ticket_ids=sorted(affected_ticket_ids),
            )

        status_counts = {key: status_counts.get(key, 0) for key in OTRSStateMapping.BusinessStatus.values}
        result = {
            "status": "APPLIED" if apply else "DRY_RUN",
            "queue": {"name": queues[0][1], "id": queues[0][0]},
            "queues": [{"name": name, "id": queue_id} for queue_id, name in queues],
            "tickets_by_queue": queue_ticket_summary,
            "tickets": ticket_counts_summary, "relations": relation_counts_summary, "state_mappings": mapping_counts,
            "ticket_terminal_fields": terminal_field_counts,
            "relation_stats": relation_stats,
            "business_status": status_counts, "business_open": sum(status_counts[key] for key in OPEN_STATUSES),
            # Advance the watermark to this successful read's start time, even when the delta is empty.
            # The command persists it only after the local transaction commits; FAST reapplies its
            # safety window on the next query, so delayed/second-precision changes remain covered.
            "cursor": max(
                now,
                max((item["source_changed_at"] for item in tickets if item.get("source_changed_at")), default=now),
            ),
            "warnings": warnings, "performance": {"duration_ms": int((time.perf_counter() - started) * 1000), "select_queries": getattr(self.client, "select_queries_count", queries_before) - queries_before, "ticket_query_rows": len(ticket_source_rows), "relation_rows": len(relation_rows), "relation_batches": (len(ticket_source_rows) + 399) // 400},
        }
        return result


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
