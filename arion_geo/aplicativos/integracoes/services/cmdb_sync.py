"""Fetch, validate, and mirror safe CMDB fields for operational consumers."""
from collections import Counter, defaultdict
import logging
import time
import xml.etree.ElementTree as ET

from django.db import transaction
from django.utils import timezone

from aplicativos.integracoes.clients.cmdb import CMDBClient
from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror

from .sync_common import clean, source_datetime

logger = logging.getLogger(__name__)


def _flatten_attributes(value):
    if isinstance(value, dict):
        result = {}
        def walk(node):
            if isinstance(node, dict):
                for key, child in node.items():
                    if isinstance(child, (dict, list)):
                        walk(child)
                    else:
                        result[str(key)] = child
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(value)
        return result
    if isinstance(value, str) and value.lstrip().startswith("<"):
        try:
            root = ET.fromstring(value)
        except ET.ParseError:
            return {}
        return {
            element.tag.split("}")[-1]: (element.text or "").strip()
            for element in root.iter()
            if element.tag and not list(element)
        }
    return {}


def _item_fields(item):
    attributes = {}
    direct = {}
    for key, value in item.items():
        if str(key).casefold() == "cixmldata":
            attributes = _flatten_attributes(value)
        else:
            direct[str(key).casefold()] = value
    return direct, {str(key).casefold(): value for key, value in attributes.items()}


def _value(direct, attrs, *names):
    for name in names:
        key = name.casefold()
        value = direct.get(key)
        if value in (None, ""):
            value = attrs.get(key)
        if value not in (None, ""):
            return value
    return ""


def _safe_text(value, max_length):
    return clean(value, max_length)


OPERATOR_FIELDS = (
    "source_config_item_id", "source_number", "name", "category", "cnpj",
    "business_hours", "phone", "whatsapp", "opening_email", "followup_email",
    "portal_url", "escalation_1", "escalation_2", "escalation_3",
    "escalation_4", "escalation_5", "deployment_state", "deployment_state_type",
    "incident_state", "incident_state_type", "definition_id", "version_id",
    "last_version_id", "source_created_at",
)
LINK_FIELDS = (
    "source_config_item_id", "source_number", "name", "city", "state", "address",
    "contracting_client_raw", "contracting_client", "business_project", "business_account",
    "business_id", "business_identity_confidence", "business_identity_source",
    "circuit", "designation", "operator_designation", "identification", "link_type",
    "speed_raw", "ip_range", "equipment", "mac_address", "cnpj", "operator_cnpj",
    "contract_number", "business_hours", "isp_notes", "customer_id", "isp_raw",
    "operator_id", "operator_resolution_method", "legacy_project", "project_source",
    "deployment_state", "deployment_state_type", "incident_state", "incident_state_type",
    "definition_id", "version_id", "last_version_id", "source_created_at",
)


class CMDBSyncService:
    LIMIT = None  # Full class search without Limit was confirmed in the real CMDB study.
    CLASSES = (("Operadoras", CMDBOperatorMirror), ("Links de internet", CMDBLinkMirror))

    def __init__(self, client, *, progress=lambda _: None):
        self.client = client
        self.progress = progress

    def fetch(self):
        started = time.perf_counter()
        request_start = getattr(self.client, "requests_count", 0)
        found = {}
        unmapped = set()
        direct_fields = {"configitemid", "number", "name", "class", "curdeplstate", "curdeplstatetype", "curincistate", "curincistatetype", "deplstate", "deplstatetype", "incistate", "incistatetype", "definitionid", "versionid", "lastversionid", "createtime"}
        attribute_fields = {
            "operadoras": {"categoriaoperadora", "cnpjoperadora", "horariooperadora", "telefoneoperadora", "operadorawhatsapp", "emailabertura", "emailacompanhamento", "portaloperadora", *(f"{i}escalonamentooperadora" for i in range(1, 6))},
            "linksdeinternet": {"cidade", "estado", "endereco", "circuito", "designacao", "designacaooperadora", "identificacao", "tipo", "velocidade", "rangeip", "equipamento", "macaddress", "cnpj", "cnpjoperadora", "numerocontrato", "horariofuncionamento", "observacaoisp", "customerid", "isp", "usuarioppoe"},
        }
        for class_name, model in self.CLASSES:
            self.progress(f"[CMDB] Buscando IDs: {class_name}...")
            ids = self.client.search_item_ids(class_name=class_name, limit=self.LIMIT)
            if not isinstance(ids, list) or len(ids) != len(set(map(str, ids))):
                raise ValueError(f"Resposta incompleta ou duplicada na busca CMDB: {class_name}.")
            items = self.client.get_items(ids)
            safe_items = CMDBClient.sanitize_study_items(items)
            id_to_item = {clean(item.get("ConfigItemID")): item for item in safe_items if clean(item.get("ConfigItemID"))}
            if len(id_to_item) != len(ids) or any(str(value) not in id_to_item for value in ids):
                raise ValueError(f"Get CMDB não retornou todos os itens de {class_name}; reconciliação cancelada.")
            if any(clean(item.get("Class")) != class_name for item in id_to_item.values()):
                raise ValueError(f"Search/Get retornou classe inesperada em {class_name}; reconciliação cancelada.")
            allowed_attributes = attribute_fields["operadoras" if model is CMDBOperatorMirror else "linksdeinternet"]
            for item in id_to_item.values():
                direct, attrs = _item_fields(item)
                for field in direct:
                    if field not in direct_fields and not CMDBClient._is_sensitive(field):
                        unmapped.add(field)
                for field in attrs:
                    if field not in allowed_attributes and not CMDBClient._is_sensitive(field):
                        unmapped.add(field)
            found[model] = [id_to_item[str(value)] for value in ids]
            self.progress(f"[CMDB] {class_name}: {len(ids)} encontrados e carregados.")
        for field in sorted(unmapped, key=str.casefold):
            logger.warning("CMDB sync ignorou campo não mapeado: %s", field)
        return found, {
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "http_requests": getattr(self.client, "requests_count", request_start) - request_start,
            "get_batches": sum((len(rows) + max(1, self.client.batch_size) - 1) // max(1, self.client.batch_size) for rows in found.values()),
        }

    @staticmethod
    def _operator(item):
        direct, attrs = _item_fields(item)
        config_id = clean(_value(direct, attrs, "ConfigItemID"), 40)
        number = clean(_value(direct, attrs, "Number"), 40) or None
        if not config_id or not clean(_value(direct, attrs, "Name")):
            raise ValueError("ConfigItem de Operadora sem ConfigItemID/Name; sync cancelado.")
        return {
            "source_config_item_id": config_id, "source_number": number,
            "name": _safe_text(_value(direct, attrs, "Name"), 300),
            "category": _safe_text(_value(direct, attrs, "CategoriaOperadora"), 160),
            "cnpj": _safe_text(_value(direct, attrs, "CnpjOperadora"), 80),
            "business_hours": _safe_text(_value(direct, attrs, "HorarioOperadora"), 300),
            "phone": _safe_text(_value(direct, attrs, "TelefoneOperadora"), 300),
            "whatsapp": _safe_text(_value(direct, attrs, "OperadoraWhatsApp"), 300),
            "opening_email": _safe_text(_value(direct, attrs, "EmailAbertura"), 300),
            "followup_email": _safe_text(_value(direct, attrs, "EmailAcompanhamento"), 300),
            "portal_url": _safe_text(_value(direct, attrs, "PortalOperadora"), 500),
            **{f"escalation_{i}": _safe_text(_value(direct, attrs, f"{i}EscalonamentoOperadora"), 500) for i in range(1, 6)},
            "deployment_state": _safe_text(_value(direct, attrs, "CurDeplState", "DeplState"), 160),
            "deployment_state_type": _safe_text(_value(direct, attrs, "CurDeplStateType", "DeplStateType"), 100),
            "incident_state": _safe_text(_value(direct, attrs, "CurInciState", "InciState"), 160),
            "incident_state_type": _safe_text(_value(direct, attrs, "CurInciStateType", "InciStateType"), 100),
            "definition_id": clean(_value(direct, attrs, "DefinitionID"), 40),
            "version_id": clean(_value(direct, attrs, "VersionID"), 40),
            "last_version_id": clean(_value(direct, attrs, "LastVersionID"), 40),
            "source_created_at": source_datetime(_value(direct, attrs, "CreateTime")),
        }

    @staticmethod
    def _link(item, legacy_projects, operators_by_id, operators_by_name):
        from .link_business_identity import classify_link_name

        direct, attrs = _item_fields(item)
        config_id = clean(_value(direct, attrs, "ConfigItemID"), 40)
        number = clean(_value(direct, attrs, "Number"), 40) or None
        if not config_id:
            raise ValueError("ConfigItem Link sem ConfigItemID; sync cancelado.")
        isp = clean(_value(direct, attrs, "ISP"), 300)
        operator = None
        if not isp:
            method = CMDBLinkMirror.OperatorResolution.EMPTY
        elif isp in operators_by_id:
            method, operator = CMDBLinkMirror.OperatorResolution.CONFIG_ITEM_ID, operators_by_id[isp]
        else:
            matches = operators_by_name.get(isp.casefold(), [])
            if len(matches) == 1:
                method, operator = CMDBLinkMirror.OperatorResolution.NAME, matches[0]
            else:
                method = CMDBLinkMirror.OperatorResolution.UNRESOLVED
        project = legacy_projects.get(config_id)
        name = _safe_text(_value(direct, attrs, "Name"), 500)
        designation = _safe_text(_value(direct, attrs, "Designacao"), 300)
        business_identity = classify_link_name(name, designation=designation)
        return {
            "source_config_item_id": config_id, "source_number": number,
            "name": name, **business_identity,
            "city": _safe_text(_value(direct, attrs, "Cidade"), 200),
            "state": _safe_text(_value(direct, attrs, "Estado"), 100),
            "address": _safe_text(_value(direct, attrs, "Endereco"), 500),
            "circuit": _safe_text(_value(direct, attrs, "Circuito"), 300),
            "designation": designation,
            "operator_designation": _safe_text(_value(direct, attrs, "DesignacaoOperadora"), 300),
            "identification": _safe_text(_value(direct, attrs, "Identificacao"), 300),
            "link_type": _safe_text(_value(direct, attrs, "Tipo"), 160),
            "speed_raw": _safe_text(_value(direct, attrs, "Velocidade"), 160),
            "ip_range": _safe_text(_value(direct, attrs, "RangeIP"), 300),
            "equipment": _safe_text(_value(direct, attrs, "Equipamento"), 300),
            "mac_address": _safe_text(_value(direct, attrs, "MACAddress"), 160),
            "cnpj": _safe_text(_value(direct, attrs, "CNPJ"), 100),
            "operator_cnpj": _safe_text(_value(direct, attrs, "CnpjOperadora"), 100),
            "contract_number": _safe_text(_value(direct, attrs, "NumeroContrato"), 200),
            "business_hours": _safe_text(_value(direct, attrs, "HorarioFuncionamento"), 300),
            "isp_notes": clean(_value(direct, attrs, "ObservacaoISP"))[:10000],
            "customer_id": _safe_text(_value(direct, attrs, "CustomerID"), 200),
            "isp_raw": isp,
            "operator_id": operator["source_config_item_id"] if operator else None,
            "operator_resolution_method": method,
            "legacy_project": clean(project, 300) if project else "",
            "project_source": CMDBLinkMirror.ProjectSource.LEGACY_XLSX if project else "",
            "deployment_state": _safe_text(_value(direct, attrs, "CurDeplState", "DeplState"), 160),
            "deployment_state_type": _safe_text(_value(direct, attrs, "CurDeplStateType", "DeplStateType"), 100),
            "incident_state": _safe_text(_value(direct, attrs, "CurInciState", "InciState"), 160),
            "incident_state_type": _safe_text(_value(direct, attrs, "CurInciStateType", "InciStateType"), 100),
            "definition_id": clean(_value(direct, attrs, "DefinitionID"), 40),
            "version_id": clean(_value(direct, attrs, "VersionID"), 40),
            "last_version_id": clean(_value(direct, attrs, "LastVersionID"), 40),
            "source_created_at": source_datetime(_value(direct, attrs, "CreateTime")),
        }

    @staticmethod
    def _duplicates(rows, field):
        seen = set()
        duplicates = set()
        for row in rows:
            value = row.get(field)
            if value and value in seen:
                duplicates.add(value)
            if value:
                seen.add(value)
        return duplicates

    @staticmethod
    def _upsert(model, rows, value_fields, *, now, apply):
        existing = {obj.source_config_item_id: obj for obj in model.objects.all()}
        created, updated, unchanged, to_create, to_update = 0, 0, 0, [], []
        compare_fields = tuple(field for field in value_fields if field not in {"source_config_item_id"})
        incoming_ids = {row["source_config_item_id"] for row in rows}
        incoming_numbers = {row["source_number"]: row["source_config_item_id"] for row in rows if row.get("source_number")}
        number_owners = {obj.source_number: obj.source_config_item_id for obj in model.objects.filter(source_number__in=incoming_numbers)}
        if any(number_owners.get(number, source_id) != source_id for number, source_id in incoming_numbers.items()):
            raise ValueError(f"Colisão de Number com outro ConfigItemID em {model.__name__}; reconciliação cancelada.")
        for row in rows:
            values = dict(row)
            values.update(source_last_seen_at=now, source_synced_at=now, is_present_in_source=True)
            current = existing.get(values["source_config_item_id"])
            if current is None:
                created += 1
                to_create.append(model(**values))
                continue
            changed = any(getattr(current, field) != values[field] for field in compare_fields)
            if not current.is_present_in_source:
                changed = True
            for field, value in values.items():
                setattr(current, field, value)
            if changed:
                updated += 1
                to_update.append(current)
            else:
                unchanged += 1
                to_update.append(current)
        stale_ids = [obj.pk for obj in existing.values() if obj.is_present_in_source and obj.source_config_item_id not in incoming_ids]
        if apply:
            model.objects.bulk_create(to_create, batch_size=500)
            if to_update:
                model.objects.bulk_update(to_update, fields=[*compare_fields, "source_last_seen_at", "source_synced_at", "is_present_in_source"], batch_size=500)
            if stale_ids:
                model.objects.filter(pk__in=stale_ids).update(is_present_in_source=False)
        return {"found": len(rows), "processed": len(rows), "created": created, "updated": updated, "unchanged": unchanged, "stale": len(stale_ids), "failed": 0}

    def run(self, *, apply=False):
        payloads, performance = self.fetch()
        now = timezone.now()
        operator_rows = [self._operator(item) for item in payloads[CMDBOperatorMirror]]
        duplicate_ids = self._duplicates(operator_rows, "source_config_item_id")
        duplicate_numbers = self._duplicates(operator_rows, "source_number")
        link_rows_seed = payloads[CMDBLinkMirror]
        link_numbers = [clean(_item_fields(item)[0].get("number") or _item_fields(item)[1].get("number")) for item in link_rows_seed]
        duplicates = {
            "operator_config_item_id": duplicate_ids,
            "operator_number": duplicate_numbers,
            "link_config_item_id": self._duplicates([{"source_config_item_id": clean(_value(*_item_fields(item), "ConfigItemID"))} for item in link_rows_seed], "source_config_item_id"),
            "link_number": self._duplicates([{"source_number": number} for number in link_numbers], "source_number"),
        }
        if any(duplicates.values()):
            raise ValueError("IDs/Números CMDB duplicados; nenhum espelho foi alterado.")

        for item in operator_rows:
            item["source_created_at"] = item["source_created_at"]
        if len({row["source_config_item_id"] for row in operator_rows}) != len(operator_rows):
            raise ValueError("ConfigItemID de Operadora ausente ou duplicado.")
        # Build the exact operator lookup from the remote snapshot, independent of existing local rows.
        operator_refs = [
            {"pk": row["source_config_item_id"], "source_config_item_id": row["source_config_item_id"], "name": row["name"]}
            for row in operator_rows
        ]
        by_id = {row["source_config_item_id"]: row for row in operator_refs}
        by_name = defaultdict(list)
        for row in operator_refs:
            by_name[row["name"].casefold()].append(row)
        legacy_ids = [clean(_value(*_item_fields(item), "ConfigItemID"), 40) for item in link_rows_seed]
        legacy_projects = {
            row["source_config_item_id"]: row["legacy_project"]
            for row in CMDBLinkMirror.objects.filter(source_config_item_id__in=legacy_ids).values("source_config_item_id", "legacy_project")
            if row["legacy_project"]
        }
        # Match operators locally by source ID so new and existing rows can share the FK.
        current_ops = {op.source_config_item_id: op for op in CMDBOperatorMirror.objects.filter(source_config_item_id__in=by_id)}
        operator_rows = [row for row in operator_rows]
        operator_model_by_id = {key: value for key, value in current_ops.items()}
        link_rows = [self._link(item, legacy_projects, by_id, by_name) for item in link_rows_seed]
        operator_resolution = Counter(row["operator_resolution_method"] for row in link_rows)
        cmdb_numbers = {row["source_number"] for row in link_rows if row["source_number"]}

        summaries = {}
        transaction_context = transaction.atomic() if apply else _NullContext()
        with transaction_context:
            summaries["operators"] = self._upsert(CMDBOperatorMirror, operator_rows, OPERATOR_FIELDS, now=now, apply=apply)
            if apply:
                operator_model_by_id = {op.source_config_item_id: op for op in CMDBOperatorMirror.objects.filter(source_config_item_id__in=by_id)}
            for row in link_rows:
                external_id = row.pop("operator_id")
                row["operator_id"] = operator_model_by_id.get(external_id).pk if external_id and operator_model_by_id.get(external_id) else None
            summaries["links"] = self._upsert(CMDBLinkMirror, link_rows, LINK_FIELDS, now=now, apply=apply)
            if apply:
                from .operational_identity import OperationalIdentityService
                summaries["operational_identity"] = OperationalIdentityService.reconcile_all(apply=True)
        return {
            "status": "APPLIED" if apply else "DRY_RUN", "operators": summaries["operators"], "links": summaries["links"],
            "operational_identity": summaries.get("operational_identity"),
            "isp_resolution": {key: operator_resolution.get(key, 0) for key in CMDBLinkMirror.OperatorResolution.values},
            "performance": performance, "duration_ms": performance["duration_ms"],
            "links_with_number": len(cmdb_numbers),
        }


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
