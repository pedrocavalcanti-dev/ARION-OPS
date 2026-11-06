"""Versioned, read-only explorer for the documented OTRS and CMDB structures.

The Markdown dictionaries remain the source for external metadata. Django model
fields are introspected locally so the portal does not keep a second copy of
the mirror schema.
"""

import re

from django.apps import apps
from django.urls import reverse

from .documentation import DOCS_ROOT


DOC_LINKS = {
    "otrs": {
        "overview": "otrs", "tables": "otrs-tabelas", "fields": "otrs-dicionario",
        "relations": "otrs-relacionamentos", "queries": "otrs-consultas-sql",
        "mirror": "otrs-espelho-local", "sync": "otrs-sincronizacao",
        "trace": "otrs-rastreabilidade", "consumers": "otrs-consumidores",
        "dates": "otrs-datas", "limits": "otrs-limitacoes",
    },
    "cmdb": {
        "overview": "cmdb", "endpoints": "cmdb-endpoints", "responses": "cmdb-respostas",
        "fields": "cmdb-dicionario", "entities": "cmdb-entidades",
        "relations": "cmdb-relacionamentos", "mirror": "cmdb-espelho-local",
        "sync": "cmdb-sincronizacao", "trace": "cmdb-rastreabilidade",
        "consumers": "cmdb-consumidores", "otrs": "cmdb-otrs", "limits": "cmdb-limitacoes",
    },
}


def _read(relative_path):
    return (DOCS_ROOT / relative_path).read_text(encoding="utf-8-sig")


def _markdown_tables(relative_path):
    """Return Markdown tables as header/row dictionaries without rendering them."""
    tables, current = [], []
    for line in _read(relative_path).splitlines():
        if not line.strip().startswith("|"):
            if current:
                tables.append(current)
                current = []
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        current.append(cells)
    if current:
        tables.append(current)
    result = []
    for table in tables:
        if len(table) < 2:
            continue
        header = table[0]
        result.append([
            {header[index]: row[index] if index < len(row) else "" for index in range(len(header))}
            for row in table[1:]
        ])
    return result


def _doc_url(slug):
    return reverse("configuracoes:documentation-page", kwargs={"slug": slug})


def _table_category(name):
    if name in {"ticket", "queue", "ticket_state", "ticket_state_type", "ticket_priority", "ticket_type", "service", "sla"}:
        return "Tickets e dimensões"
    if name in {"dynamic_field", "dynamic_field_value"}:
        return "Dynamic Fields"
    if name.startswith("ticket_history"):
        return "Histórico"
    if name in {"article", "article_data_mime", "article_sender_type"}:
        return "Artigos"
    if name.startswith("link_") or name in {"configitem", "general_catalog"}:
        return "Relacionamentos e ConfigItems"
    return "Outros"


def _model_field(model, field):
    if field.is_relation and field.many_to_one and field.remote_field:
        field_type = f"ForeignKey → {field.remote_field.model.__name__}"
        key = "FK"
    else:
        field_type = field.get_internal_type()
        key = "PK" if field.primary_key else ("UNIQUE" if field.unique else "—")
    return {
        "id": f"{model._meta.model_name}:{field.name}",
        "name": field.name,
        "type": field_type,
        "key": key,
        "nullable": "Sim" if field.null else "Não",
        "description": str(field.help_text or field.verbose_name or field.name),
        "usage": "Persistido no espelho local ARION OPS.",
        "transformation": f"Coluna local: {field.column or field.name}.",
        "source": "ARION / derivado" if field.name not in {"id"} else "ARION",
        "local_model": model.__name__,
        "relationship": field.remote_field.model.__name__ if field.is_relation and field.remote_field else "",
        "validation": "Modelo Django atual",
        "confidence": "Confirmado no código local",
        "reference": "mirror",
        "reference_url": "",
    }


def _local_models(names, source, field_docs=None):
    field_docs = field_docs or {}
    models = []
    for model_name in names:
        model = apps.get_model("integracoes", model_name)
        fields = []
        for field in model._meta.get_fields():
            if not getattr(field, "concrete", False):
                continue
            item = _model_field(model, field)
            documented = field_docs.get((model_name, field.name))
            if documented:
                item.update({
                    "description": documented.get("description") or item["description"],
                    "transformation": documented.get("transformation") or item["transformation"],
                    "source": documented.get("source") or item["source"],
                    "validation": "Campo do modelo mapeado pela sincronização" if documented.get("source_path") else "Campo derivado ou metadado local",
                    "confidence": "Confirmado pelo mapeamento versionado" if documented.get("source_path") else "Confirmado no código local",
                })
            fields.append(item)
        fields.sort(key=lambda item: (item["key"] not in {"PK", "FK"}, item["name"].casefold()))
        models.append({
            "id": f"local:{model._meta.model_name}", "name": model.__name__,
            "label": str(model._meta.verbose_name), "description": str(model._meta.verbose_name_plural),
            "category": "Espelho local", "type": "modelo Django", "source": source,
            "origin": "Banco local ARION OPS", "purpose": str(model._meta.verbose_name_plural),
            "validation": "Modelo Django atual", "confidence": "Confirmado no código local",
            "field_count": len(fields), "fields": fields,
            "reference": "mirror", "reference_url": _doc_url(DOC_LINKS[source]["mirror"]),
            "consumers": [], "tags": ["local", "modelo", model.__name__],
        })
    return models


class DocumentationExplorer:
    """Build schema-only catalog payloads from versioned docs and Django models."""

    OTRS_MODEL_NAMES = ("OTRSTicket", "OTRSStateMapping", "OTRSTicketConfigItem")
    CMDB_MODEL_NAMES = ("CMDBLinkMirror", "CMDBOperatorMirror")

    @classmethod
    def _otrs(cls):
        table_rows = _markdown_tables("06-integracoes-e-dados/otrs/tabelas.md")
        field_rows = _markdown_tables("06-integracoes-e-dados/otrs/dicionario-dados.md")
        tables = next((rows for rows in table_rows if rows and "ID" in rows[0] and rows[0]["ID"].startswith("T")), [])
        fields = next((rows for rows in field_rows if rows and "ID" in rows[0] and rows[0]["ID"].startswith("OTRS-F")), [])
        fields_by_table = {}
        field_docs = {}
        external_nodes = []
        for row in fields:
            table = row.get("Fonte", "").strip("`")
            field_name = row.get("Campo", "").strip("`")
            type_name = row.get("Tipo observado", "").strip("`")
            type_name = type_name.replace(" PK", "").replace(" unique", "")
            key = "PK" if " PK" in row.get("Tipo observado", "") else ("UNIQUE" if "unique" in row.get("Tipo observado", "") else "—")
            item = {
                "id": row.get("ID", ""), "name": field_name, "type": type_name or "Não documentado",
                "key": key, "nullable": "Não documentado",
                "description": row.get("Significado e papel", ""),
                "transformation": row.get("Transformação ou destino", ""),
                "usage": row.get("Transformação ou destino", ""), "source": "OTRS",
                "local_model": "", "relationship": "", "validation": "Campo identificado no código; tipo conforme snapshot quando disponível",
                "confidence": "Uso confirmado pelo código; tipo pode vir do snapshot local de schema",
                "reference": "fields", "reference_url": _doc_url(DOC_LINKS["otrs"]["fields"]),
            }
            target = re.search(r"\b(OTRSTicket|OTRSStateMapping|OTRSTicketConfigItem)\.([A-Za-z_][A-Za-z0-9_]*)", item["transformation"])
            if target:
                item["local_model"] = target.group(1)
                field_docs[(target.group(1), target.group(2))] = {
                    "description": item["description"], "transformation": item["transformation"], "source": "OTRS",
                    "source_path": field_name,
                }
            fields_by_table.setdefault(table, []).append(item)

        for row in tables:
            name = row.get("Tabela OTRS", "").strip("`")
            table_fields = fields_by_table.get(name, [])
            external_nodes.append({
                "id": f"otrs:{name}", "name": name, "label": name,
                "description": row.get("Colunas e finalidade comprovada", ""),
                "category": _table_category(name), "type": "tabela externa",
                "source": "OTRS", "origin": "Banco externo OTRS · somente leitura",
                "purpose": row.get("Caminho que lê conteúdo", ""),
                "validation": "Estrutura parcial documentada; esquema completo não é afirmado",
                "confidence": "Identificada nos SELECTs, filtros ou leitura de estudo do código",
                "field_count": len(table_fields), "fields": table_fields,
                "reference": "tables", "reference_url": _doc_url(DOC_LINKS["otrs"]["tables"]),
                "consumers": [part.strip() for part in row.get("Caminho que lê conteúdo", "").split(",")],
                "tags": [name, *[field["name"] for field in table_fields]],
            })

        local = _local_models(cls.OTRS_MODEL_NAMES, "otrs", field_docs)
        return {"items": external_nodes + local, "external_count": len(external_nodes),
                "external_fields": len(fields), "local_count": len(local),
                "local_fields": sum(model["field_count"] for model in local)}

    @classmethod
    def _cmdb(cls):
        sections = _read("06-integracoes-e-dados/cmdb/dicionario-dados.md").split("## CMDBLinkMirror", 1)
        field_docs = {}
        entity_nodes = []
        source_field_total = 0
        mapped_field_total = 0
        model_data = (("CMDBOperatorMirror", sections[0]), ("CMDBLinkMirror", "## CMDBLinkMirror" + sections[1] if len(sections) > 1 else ""))
        for model_name, section in model_data:
            model = apps.get_model("integracoes", model_name)
            rows = []
            for table in _markdown_tables_from_text(section):
                rows.extend(table)
            external_entries = []
            for row in rows:
                field_name = row.get("Campo local", "").strip("`")
                if not field_name:
                    continue
                mapped_field_total += 1
                rule = row.get("Entrada / regra", "")
                source_path = _cmdb_source_path(rule)
                if source_path:
                    source_field_total += 1
                field_docs[(model_name, field_name)] = {
                    "description": f"Mapeado a partir de {source_path}" if source_path else rule,
                    "transformation": rule,
                    "source": "CMDB ServiceUp" if source_path else ("Derivado / legado" if "legado" in rule.casefold() or "classific" in rule.casefold() else "ARION / derivado"),
                    "source_path": source_path,
                }
                if source_path:
                    external_entries.append({"source_path": source_path, "field_name": field_name, "rule": rule})
            external_fields = []
            seen = set()
            for row in external_entries:
                source_path = row["source_path"]
                if not source_path or source_path in seen:
                    continue
                seen.add(source_path)
                external_fields.append({
                    "id": f"serviceup:{model._meta.model_name}:{source_path}", "name": source_path,
                    "type": "Tipo JSON não fixado pelo contrato documentado", "key": "—",
                    "nullable": "Não documentado", "description": f"Mapeado para {model_name}.{row['field_name']}.",
                    "transformation": row["rule"], "usage": f"Destino local: {model_name}.{row['field_name']}.",
                    "source": "ServiceUp JSON", "local_model": model_name, "relationship": "",
                    "validation": "Campo identificado pelo mapeamento do código",
                    "confidence": "Confirmado pelo whitelist da sincronização; contrato externo não declara tipo",
                    "reference": "fields", "reference_url": _doc_url(DOC_LINKS["cmdb"]["fields"]),
                })
            entity_nodes.append({
                "id": f"serviceup:{model._meta.model_name}", "name": model_name,
                "label": "Links de internet" if model_name == "CMDBLinkMirror" else "Operadoras (Provider/ISP)",
                "description": "Classe ConfigItem ServiceUp documentada pelo mapeamento atual.",
                "category": "ConfigItem externo", "type": "entidade externa (JSON)",
                "source": "CMDB ServiceUp", "origin": "ServiceUp · classe ConfigItem; não é tabela SQL ARION",
                "purpose": "Campos externos da classe que possuem mapeamento local conhecido.",
                "validation": "Estrutura parcial documentada; somente campos mapeados são listados",
                "confidence": "Classe e campos confirmados pelo sync da CMDB",
                "field_count": len(external_fields), "fields": external_fields,
                "reference": "entities", "reference_url": _doc_url(DOC_LINKS["cmdb"]["entities"]),
                "consumers": [model_name], "tags": [model_name, "ConfigItem", "CIXMLData", *[field["name"] for field in external_fields]],
            })

        local = _local_models(cls.CMDB_MODEL_NAMES, "cmdb", field_docs)
        endpoints = [
            {"id": "serviceup:search", "name": "CMDB-API001 · ConfigItemSearch", "label": "ConfigItemSearch",
             "description": "Busca IDs (e, em pesquisas interativas, pode retornar itens completos).",
             "category": "Operações ServiceUp", "type": "endpoint POST JSON", "source": "CMDB ServiceUp",
             "origin": "ServiceUp · Webservice/CMDB/ConfigItemSearch", "purpose": "Busca Links de internet e Operadoras antes de obter os ConfigItems.",
             "validation": "Operação implementada no CMDBClient", "confidence": "Confirmada pelo código",
             "field_count": 2, "fields": [
                 _api_field("ConfigItem.Class", "Busca por classe ConfigItem; valores documentados: Links de internet e Operadoras.", "endpoints"),
                 _api_field("ConfigItemID / ConfigItemIDs", "IDs retornados e deduplicados pelo client.", "endpoints"),
             ], "reference": "endpoints", "reference_url": _doc_url(DOC_LINKS["cmdb"]["endpoints"]),
             "consumers": ["CMDBSyncService", "CMDBClient"], "tags": ["search", "CMDB-API001", "ConfigItem.Class"]},
            {"id": "serviceup:get", "name": "CMDB-API002 · ConfigItemGet", "label": "ConfigItemGet",
             "description": "Obtém detalhes em lotes de ConfigItemID.", "category": "Operações ServiceUp",
             "type": "endpoint POST JSON", "source": "CMDB ServiceUp", "origin": "ServiceUp · Webservice/CMDB/ConfigItemGet",
             "purpose": "Retorna ConfigItemID, Name, Number, Class e CIXMLData para o sync.",
             "validation": "Operação implementada no CMDBClient", "confidence": "Confirmada pelo código",
             "field_count": 5, "fields": [
                 _api_field("ConfigItemID", "IDs de entrada separados por vírgula e enviados por lotes.", "endpoints"),
                 _api_field("ConfigItemID", "Identidade externa do item retornado.", "responses"),
                 _api_field("Name", "Nome literal recebido da classe.", "responses"),
                 _api_field("Number", "Identificador de apresentação; não substitui ConfigItemID.", "responses"),
                 _api_field("CIXMLData", "Atributos dinâmicos sanitizados e mapeados pela whitelist.", "responses"),
             ], "reference": "endpoints", "reference_url": _doc_url(DOC_LINKS["cmdb"]["endpoints"]),
             "consumers": ["CMDBSyncService", "CMDBClient"], "tags": ["get", "CMDB-API002", "ConfigItemID", "CIXMLData"]},
        ]
        return {"items": endpoints + entity_nodes + local, "external_count": len(endpoints) + len(entity_nodes),
                "external_fields": mapped_field_total, "mapped_fields": mapped_field_total,
                "serviceup_fields": source_field_total, "local_count": len(local),
                "local_fields": sum(model["field_count"] for model in local)}

    @classmethod
    def build(cls):
        otrs, cmdb = cls._otrs(), cls._cmdb()
        return {
            "otrs": {**otrs, "title": "OTRS", "links": _source_links("otrs")},
            "cmdb": {**cmdb, "title": "CMDB ServiceUp", "links": _source_links("cmdb")},
            "diagrams": _diagrams(otrs["items"], cmdb["items"]),
        }


def _markdown_tables_from_text(text):
    tables, current = [], []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            if current:
                tables.append(current)
                current = []
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        current.append(cells)
    if current:
        tables.append(current)
    output = []
    for table in tables:
        if len(table) < 2:
            continue
        header = table[0]
        output.append([{header[i]: row[i] if i < len(row) else "" for i in range(len(header))} for row in table[1:]])
    return output


def _cmdb_source_path(rule):
    """Extract only an externally sourced attribute; derived/local values stay local."""
    match = re.match(r"\s*((?:CIXMLData\.)?[A-Za-z0-9_]+)(?:\s*;|\s*$)", rule)
    if not match:
        return ""
    value = match.group(1)
    if value in {"operator_id", "operator_resolution_method", "legacy_project", "project_source", "contracting_client", "contracting_client_raw", "business_project", "business_account", "business_id", "business_identity_confidence", "business_identity_source"}:
        return ""
    if value in {"ConfigItemID", "Number", "Name", "DefinitionID", "VersionID", "LastVersionID", "CreateTime"}:
        return value
    if value.startswith("CIXMLData.") or value.startswith("Cur") or value in {"DeplState", "DeplStateType", "InciState", "InciStateType"}:
        return value
    return ""


def _api_field(name, description, reference):
    return {"id": f"api:{name}", "name": name, "type": "Estrutura JSON",
            "key": "—", "nullable": "Não especificado", "description": description,
            "transformation": "Contrato e parsing documentados pelo CMDBClient.", "usage": "CMDBSyncService",
            "source": "ServiceUp JSON", "local_model": "", "relationship": "",
            "validation": "Estrutura documentada; sem introspecção da instância", "confidence": "Confirmado pelo código",
            "reference": reference, "reference_url": _doc_url(DOC_LINKS["cmdb"][reference])}


def _source_links(source):
    keys = DOC_LINKS[source]
    ordered = ("overview", "tables", "endpoints", "fields", "relations", "mirror", "queries", "sync", "trace", "consumers", "dates", "otrs", "limits")
    labels = {"overview": "Visão geral", "tables": "Explorador de tabelas", "endpoints": "Endpoints e estruturas JSON",
              "fields": "Dicionário de campos", "relations": "Relacionamentos", "mirror": "Modelos do espelho local",
              "queries": "Consultas SQL", "sync": "Sincronização", "trace": "Rastreabilidade", "consumers": "Módulos consumidores",
              "dates": "Datas operacionais", "otrs": "Correlação OTRS + CMDB", "limits": "Limitações e cobertura"}
    return [{"label": labels[key], "slug": keys[key], "url": _doc_url(keys[key])} for key in ordered if key in keys]


def _diagrams(otrs_items, cmdb_items):
    otrs_ids = {item["name"]: item["id"] for item in otrs_items if item["type"] == "tabela externa"}
    def edge(source, target, label, source_field="", target_field="", cardinality="", query="", multiplication="", deduplication="", confidence="Confirmado pelo código"):
        return {"source": source, "target": target, "label": label, "source_field": source_field,
                "target_field": target_field, "cardinality": cardinality, "query": query,
                "multiplication": multiplication, "deduplication": deduplication, "confidence": confidence}
    otrs_edges = [
        edge(otrs_ids["ticket"], otrs_ids["queue"], "queue_id → id", "ticket.queue_id", "queue.id", "N:1", "Sync e estudo; dimensão resolvida em memória.", "JOIN de consulta não é executado no sync principal.", "Resolve fila pelo nome; preserva queue_name no espelho."),
        edge(otrs_ids["ticket"], otrs_ids["ticket_state"], "ticket_state_id → id", "ticket.ticket_state_id", "ticket_state.id", "N:1", "Sync FULL/FAST.", "JOIN externo não é necessário; dimensões são lidas separadamente.", "Upsert pelo external_state_id."),
        edge(otrs_ids["ticket_state"], otrs_ids["ticket_state_type"], "type_id → id", "ticket_state.type_id", "ticket_state_type.id", "N:1", "Sync e estudo.", "JOIN externo não é necessário; resolvido por mapa.", "Estado atual fica no ticket; tipo é contexto."),
        *[edge(otrs_ids["ticket"], otrs_ids[table], f"{field} → id", f"ticket.{field}", f"{table}.id", "N:1", "Dimensão resolvida em memória no sync.", "Sem JOIN no sync principal.", f"Nome copiado para OTRSTicket.{target}.") for table, field, target in (("ticket_priority", "ticket_priority_id", "priority_name"), ("ticket_type", "type_id", "type_name"), ("service", "service_id", "service_name"), ("sla", "sla_id", "sla_name"))],
        edge(otrs_ids["ticket"], otrs_ids["dynamic_field_value"], "id → object_id", "ticket.id", "dynamic_field_value.object_id", "1:N lógico", "Leitura em lotes de Dynamic Fields.", "Vários campos por ticket; agrupados por ticket.", "Seleciona nomes aprovados por field_id."),
        edge(otrs_ids["dynamic_field_value"], otrs_ids["dynamic_field"], "field_id → id", "dynamic_field_value.field_id", "dynamic_field.id", "N:1", "Resolve definições por nome antes da leitura.", "Valores de vários campos são reunidos.", "Usa CodigoFechamento e ticketResolvido aprovados."),
        edge(otrs_ids["ticket"], otrs_ids["ticket_history"], "id → ticket_id", "ticket.id", "ticket_history.ticket_id", "1:N", "StateUpdate por ID e estado atual.", "Histórico tem vários eventos por ticket.", "MAX(create_time) por ticket/estado; somente estado atual correspondente."),
        edge(otrs_ids["ticket_history"], otrs_ids["ticket_history_type"], "history_type_id → id", "ticket_history.history_type_id", "ticket_history_type.id", "N:1", "Seleciona StateUpdate por nome.", "Agrega apenas o tipo de evento necessário.", "Exige resolução única de StateUpdate."),
        edge(otrs_ids["ticket"], otrs_ids["link_relation"], "ticket ↔ chave polimórfica", "ticket.id", "source_key / target_key", "N:M", "Ticket–ConfigItem: DependsOn e Valid.", "Ticket pode vincular vários ConfigItems.", "Dedup por ticket, ConfigItemID e tipo; aceita ambas orientações.", "Relação lógica por chave polimórfica; não é FK declarada."),
        edge(otrs_ids["link_relation"], otrs_ids["link_object"], "object_id → id", "source_object_id / target_object_id", "link_object.id", "N:1", "Resolve Ticket e ITSMConfigItem pelo nome.", "Um vínculo tem duas pontas tipadas.", "Normaliza orientação em memória."),
        edge(otrs_ids["link_relation"], otrs_ids["link_type"], "type_id → id", "link_relation.type_id", "link_type.id", "N:1", "Filtra DependsOn.", "Não materializa JOIN direto.", "Tipo resolvido por nome, sem ID fixo."),
        edge(otrs_ids["link_relation"], otrs_ids["link_state"], "state_id → id", "link_relation.state_id", "link_state.id", "N:1", "Filtra Valid.", "Não materializa JOIN direto.", "Relações inválidas são ignoradas."),
        edge(otrs_ids["ticket"], otrs_ids["article"], "id → ticket_id", "ticket.id", "article.ticket_id", "1:N", "Estudo e reconciliação TQ.", "Vários artigos por ticket.", "Artigos processados por ID/cursor conforme tarefa."),
        edge(otrs_ids["article"], otrs_ids["article_data_mime"], "id → article_id", "article.id", "article_data_mime.article_id", "1:0..1+", "Reconciliação lê a_body para localizar QID.", "Corpos MIME não são espelhados.", "a_body permanece transitório e não é registrado."),
        edge(otrs_ids["article"], otrs_ids["article_sender_type"], "article_sender_type_id → id", "article.article_sender_type_id", "article_sender_type.id", "N:1", "Estudo agregado de canais/remetentes.", "Dimensão usada em agregação.", "Rótulo é agregado, não espelhado."),
        edge(otrs_ids["configitem"], otrs_ids["general_catalog"], "class_id → id", "configitem.class_id", "general_catalog.id", "N:1", "Estudo de classe ConfigItem.", "JOIN de classificação no estudo.", "Filtra namespace ITSM::ConfigItem::Class."),
    ]
    cmdb_edges = [
        edge("serviceup:search", "serviceup:get", "IDs deduplicados → lote de Get", "ConfigItemID(s)", "ConfigItemID", "1:N por lote", "CMDBSyncService chama Search e depois Get.", "Resposta Search pode conter vários IDs.", "Client remove IDs vazios e duplicados."),
        edge("serviceup:get", "serviceup:response", "ConfigItems JSON", "ConfigItemID", "ConfigItemID, Name, Number, Class, CIXMLData", "1:N", "CMDBClient.get_items e parser de resposta.", "Lista de ConfigItems por chamada.", "Valida estrutura e completude antes do upsert."),
        edge("serviceup:response", "serviceup:cmdblinkmirror", "Classe Links de internet → transformação", "Name / CIXMLData", "whitelist LINK_FIELDS", "1:0..1 por ConfigItemID", "CMDBSyncService._normalize_links.", "Itens de outras classes não viram Link.", "Upsert por source_config_item_id."),
        edge("serviceup:response", "serviceup:cmdboperatormirror", "Classe Operadoras → transformação", "Name / CIXMLData", "whitelist OPERATOR_FIELDS", "1:0..1 por ConfigItemID", "CMDBSyncService._normalize_operators.", "Itens de outras classes não viram Provider.", "Upsert por source_config_item_id."),
        edge("serviceup:cmdblinkmirror", "local:cmdblinkmirror", "ConfigItemID → identidade do espelho", "ConfigItemID", "source_config_item_id", "1:1 por ID externo", "CMDBSyncService faz upsert da classe Links de internet.", "Um ConfigItem da classe é representado por um registro do mirror.", "Upsert e reconciliação usam source_config_item_id."),
        edge("serviceup:cmdboperatormirror", "local:cmdboperatormirror", "ConfigItemID → identidade do espelho", "ConfigItemID", "source_config_item_id", "1:1 por ID externo", "CMDBSyncService faz upsert da classe Operadoras.", "Um ConfigItem da classe é representado por um registro do mirror.", "Upsert e reconciliação usam source_config_item_id."),
        edge("local:cmdblinkmirror", "local:cmdboperatormirror", "operator FK", "CMDBLinkMirror.operator_id", "CMDBOperatorMirror.id", "N:0..1", "CMDBSyncService resolve ISP.", "Um Link tem zero ou um Provider local.", "ConfigItemID primeiro; fallback por nome exato único."),
    ]
    cross_edges = [
        edge(otrs_ids["ticket"], otrs_ids["link_relation"], "TicketID na chave polimórfica", "ticket.id", "source_key / target_key", "1:N", "OTRS-Q família Ticket–ConfigItem; DependsOn + Valid.", "Um ticket pode ter múltiplos CIs.", "Normaliza orientação e deduplica a tripla Ticket/CI/tipo.", "Relação lógica OTRS; sem FK SQL direta."),
        edge(otrs_ids["link_relation"], "otrs:configitem", "ITSMConfigItem key", "source_key / target_key", "configitem.id / ConfigItemID", "N:1", "link_object identifica o tipo de objeto; associação por chave.", "ConfigItem pode aparecer em vários tickets.", "Mantém ConfigItemID da fonte; não usa Number."),
        edge("otrs:configitem", "local:otrsticketconfigitem", "ConfigItemID → config_item_id", "ConfigItemID externo", "OTRSTicketConfigItem.config_item_id", "N:M local", "OTRSSyncService persiste a relação normalizada.", "Múltiplos CIs e tickets são permitidos.", "UniqueConstraint(ticket, config_item_id, relation_type).", "Mapeamento lógico por ConfigItemID."),
        edge("local:otrsticketconfigitem", "local:cmdblinkmirror", "ConfigItemID → source_config_item_id", "config_item_id", "source_config_item_id", "N:0..1", "Upsert associa FK se o Link já estiver no espelho.", "Ticket com vários ConfigItems pode se relacionar a vários Links.", "A relação permanece mesmo sem Link local; reconciliação pode resolvê-la."),
        edge("local:cmdblinkmirror", "local:cmdboperatormirror", "ISP resolvido → Provider", "operator_id", "id", "N:0..1", "Resolução por ConfigItemID; fallback nome exato único.", "Vários Links podem apontar ao Provider.", "FK nullable SET_NULL; isp_raw é preservado."),
        *[edge("local:cmdblinkmirror", module, "modelo consumido", "CMDBLinkMirror", module.rsplit(":", 1)[-1], "1:N leitura", "Consumidor local identificado nos documentos de rastreabilidade.", "Agregações variam conforme módulo.", "TicketID/ConfigItemID únicos antes de agregar.") for module in ("module:base", "module:mapa", "module:rankings", "module:cockpit")],
    ]
    response_node = {"id": "serviceup:response", "name": "ConfigItem response", "label": "ConfigItem + CIXMLData",
                     "description": "Envelope JSON com os atributos dinâmicos reconhecidos pelo parser.",
                     "category": "Resposta JSON", "type": "estrutura JSON", "source": "CMDB ServiceUp",
                     "origin": "Resposta ConfigItemGet", "purpose": "Validação, sanitização e transformação para os modelos locais.",
                     "validation": "Estrutura implementada no parser; campos da whitelist documentados",
                     "confidence": "Confirmado pelo código", "field_count": 5,
                     "fields": [_api_field(name, description, "responses") for name, description in (
                         ("ConfigItemID", "Identidade externa estável."), ("Class", "Define Links de internet ou Operadoras."),
                         ("Name", "Nome literal do item."), ("Number", "Identificador de apresentação."),
                         ("CIXMLData", "Atributos externos tratados pela whitelist."))],
                     "reference": "responses", "reference_url": _doc_url(DOC_LINKS["cmdb"]["responses"]),
                     "consumers": ["CMDBClient", "CMDBSyncService"], "tags": ["JSON", "ConfigItem", "CIXMLData"]}
    cmdb_items.append(response_node)
    cross_nodes = [
        item for item in (*otrs_items, *cmdb_items)
        if item["id"] in {
            "otrs:ticket", "otrs:link_relation", "otrs:configitem",
            "local:otrsticketconfigitem", "local:cmdblinkmirror", "local:cmdboperatormirror",
        }
    ]
    cross_nodes.extend([
        {"id": "module:base", "name": "Base", "label": "Base de Links", "type": "módulo consumidor", "category": "Consumidores", "fields": [], "field_count": 0},
        {"id": "module:mapa", "name": "Mapa", "label": "Mapa Operacional", "type": "módulo consumidor", "category": "Consumidores", "fields": [], "field_count": 0},
        {"id": "module:rankings", "name": "Rankings", "label": "Rankings", "type": "módulo consumidor", "category": "Consumidores", "fields": [], "field_count": 0},
        {"id": "module:cockpit", "name": "Cockpit", "label": "Cockpit", "type": "módulo consumidor", "category": "Consumidores", "fields": [], "field_count": 0},
    ])
    return {
        "otrs": {"title": "Relacionamentos OTRS", "nodes": [item for item in otrs_items if item["type"] == "tabela externa"], "edges": otrs_edges},
        "cmdb": {"title": "Estrutura ServiceUp → espelho local", "nodes": cmdb_items, "edges": cmdb_edges},
        "cross": {"title": "Correlação Ticket → Link → Provedor", "nodes": cross_nodes, "edges": cross_edges},
    }
