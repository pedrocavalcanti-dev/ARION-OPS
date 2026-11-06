"""Whitelisted Markdown documents and a safe renderer for the internal portal."""
import html
import hashlib
import re
import unicodedata
from pathlib import Path

from django.conf import settings
from django.urls import reverse


DOCS_ROOT = (Path(settings.BASE_DIR).parent / "docs").resolve()
DOCUMENTS = {
    # title, description, category, Markdown path, explicit search terms
    "projeto": ("Projeto", "Objetivo, público e limites do ARION OPS", "Começar", "01-projeto/visao-geral.md", "produto NOC Atacado operação inteligência operacional"),
    "estado-atual": ("Estado Atual", "Matriz verificável de módulos, fontes e capacidades", "Começar", "01-projeto/estado-atual.md", "status feature matrix ativo backlog snapshot"),
    "roadmap": ("Roadmap", "Itens concluídos na versão 1.3.0 e evolução futura", "Começar", "01-projeto/roadmap.md", "release 1.3.0 1.2.0 identidade ARION OPS Cockpit exposição incidência Top 10 provedores Links chamados ANOP ETIT"),
    "arquitetura": ("Arquitetura", "Aplicativos, camadas, fluxos e relações entre fontes", "Começar", "02-arquitetura/arquitetura.md", "apps services views templates worker desenho fluxo"),
    "fontes-de-dados": ("Fontes de Dados", "Fontes oficiais, mirrors, escopo e freshness", "Começar", "02-arquitetura/fontes-de-dados.md", "CMDB OTRS MySQL espelho mirror read-only Data Scope"),
    "autenticacao": ("Autenticação e Sessão", "Login, sessão Django e proteção de rotas", "Funcionalidades", "05-funcionalidades/autenticacao.md", "login logout sessão CSRF senha Django"),
    "dashboard": ("Dashboard", "Indicadores executivos e recortes usados", "Funcionalidades", "05-funcionalidades/dashboard.md", "visão geral KPI painel chamados gráficos links provedores"),
    "chamados": ("Chamados", "Fila NOC e acompanhamento local no ARION", "Funcionalidades", "05-funcionalidades/chamados.md", "TicketTracking ticket status nota evento prioridade recorrência fila NOC"),
    "cockpit": ("Cockpit", "Análise operacional, consultas, views e exportações", "Funcionalidades", "05-funcionalidades/cockpit.md", "AnalyticsQuery Saved Views CSV filtros evidências séries"),
    "relatorios": ("Central de Relatórios", "Claro Multas, cálculos, enriquecimento e tratativa", "Funcionalidades", "05-funcionalidades/relatorios.md", "Claro Multas PenaltyCompetence baseline pausa TMP efetivo ISP tratativa decisão auditoria"),
    "qualidade": ("Qualidade Operacional", "Ordens, reincidências, ciclos RREIP e dashboard", "Funcionalidades", "05-funcionalidades/qualidade.md", "OQ Ordem de Qualidade ocorrência reincidência TQ Ticket de Qualidade RREIP monitoramento links prioritários Data Scope"),
    "mapa": ("Mapa Operacional", "Agregações por UF, modos e critérios geográficos", "Funcionalidades", "05-funcionalidades/mapa.md", "mapa UF chamadas reincidência motivos geografia"),
    "ranking-provedores": ("Ranking de Provedores", "Índice ARION, risco e desempenho por Provider/ISP", "Funcionalidades", "05-funcionalidades/rankings.md", "provedor ISP risco performance exposição incidência"),
    "base": ("Base de Links", "Links e Providers/ISP do espelho CMDB", "Funcionalidades", "05-funcionalidades/base.md", "links provider operadora ISP ConfigItemID CMDB busca exportação"),
    "ari": ("ARI", "Conversas, anexos e artefatos Preview", "Funcionalidades", "05-funcionalidades/ari.md", "assistente IA intelligence artificial preview artifacts LLM"),
    "banco-de-dados": ("Banco de Dados e Modelos", "Banco local, schema, chaves e relacionamentos", "Dados e Integrações", "02-arquitetura/banco-de-dados.md", "banco database MySQL SQLite models migrations schema tabelas ConfigItemID OTRSTicketConfigItem CMDBLinkMirror external_id ticket_number closing_code_raw GroupDataScope IntegrationLease CodigoFechamento FalhaMassiva TELMEX Provider DPAPI source_config_item_id"),
    "otrs": ("OTRS · Visão geral", "Arquitetura, cobertura e limites da integração OTRS", "OTRS", "06-integracoes-e-dados/otrs.md", "OTRS ticket tn ticket.id ticket.tn ticket_number external_id Dynamic Field CodigoFechamento closing_code_raw Falha Massiva status link_relation ConfigItemID FULL FAST"),
    "otrs-origem-dados": ("02 · Origem dos dados", "Fonte externa, escopo de leitura e evidências locais", "OTRS", "06-integracoes-e-dados/otrs/origem-dados.md", "MySQL origem schema information_schema READ ONLY origem externa fotografia schema validado"),
    "otrs-tabelas": ("03 · Catálogo de tabelas", "Inventário de tabelas consultadas pelo código", "OTRS", "06-integracoes-e-dados/otrs/tabelas.md", "tabelas ticket queue ticket_state link_relation dynamic_field article catalog"),
    "otrs-dicionario": ("04 · Dicionário de dados", "Campos, tipos observados, transformações e destinos", "OTRS", "06-integracoes-e-dados/otrs/dicionario-dados.md", "campos colunas tipos field data dictionary ticket id tn article article_data_mime.a_body a_body"),
    "otrs-relacionamentos": ("05 · Relacionamentos", "Chaves, cardinalidade, relação Ticket-CI e JOINs", "OTRS", "06-integracoes-e-dados/otrs/relacionamentos.md", "JOIN cardinalidade source_key target_key ConfigItem ticket ticket_history article"),
    "otrs-consultas-sql": ("06 · Consultas SQL", "Catálogo estável de 48 famílias de consulta", "OTRS", "06-integracoes-e-dados/otrs/consultas-sql.md", "SQL query OTRS-Q001 OTRS-Q101 OTRS-Q201 OTRS-Q301 SELECT filtros parâmetros"),
    "otrs-sincronizacao": ("07 · Sincronização", "FULL, FAST, cursor, worker, leases e falhas", "OTRS", "06-integracoes-e-dados/otrs/sincronizacao.md", "FULL FAST cursor safety window worker lease retry checkpoint IntegrationSyncState"),
    "otrs-espelho-local": ("08 · Espelho local", "Modelos Django, identidade, upsert e reconciliação", "OTRS", "06-integracoes-e-dados/otrs/espelho-local.md", "mirror OTRSTicket OTRSTicketConfigItem OTRSStateMapping external_id"),
    "otrs-estados": ("09 · Estados dos tickets", "Estado bruto, categoria operacional e compatibilidade CLOSED", "OTRS", "06-integracoes-e-dados/otrs/estados.md", "OPEN PENDING SCHEDULED RESOLVED CLOSED CANCELED UNKNOWN status migration 0014"),
    "otrs-datas": ("10 · Datas operacionais", "Criação, mudança, resolução e histórico", "OTRS", "06-integracoes-e-dados/otrs/datas.md", "create_time change_time ticketResolvido StateUpdate terminal_state_at timezone"),
    "otrs-indicadores": ("11 · Indicadores", "Regras temporais, status, universo e deduplicação", "OTRS", "06-integracoes-e-dados/otrs/indicadores.md", "KPI backlog resolvidos incidentes reincidência links impactados distinct Data Scope"),
    "otrs-consumidores": ("12 · Módulos consumidores", "Cockpit, Chamados, Rankings, Mapa e demais módulos", "OTRS", "06-integracoes-e-dados/otrs/consumidores.md", "Cockpit Chamados Rankings Mapa Qualidade Relatórios Base Links Integracoes consumers"),
    "otrs-qualidade-tq": ("13 · Qualidade e TQ", "Envio, QID, artigos, checkpoint e reconciliação", "OTRS", "06-integracoes-e-dados/otrs/qualidade-tq.md", "TQ QID ARION-QID article correlation Brevo checkpoint artigos"),
    "otrs-seguranca": ("14 · Segurança", "TLS, segredos, somente leitura e dados sensíveis", "OTRS", "06-integracoes-e-dados/otrs/otrs-seguranca.md", "TLS certificado DPAPI segredo read-only READ ONLY senha logs a_body"),
    "otrs-diagnostico": ("15 · Diagnóstico", "Procedimentos seguros para falhas e divergências", "OTRS", "06-integracoes-e-dados/otrs/diagnostico.md", "diagnóstico falha conexão TLS FAST cursor duplicidade CI TQ worker"),
    "otrs-glossario": ("16 · Glossário", "Termos OTRS, ARION OPS e semântica de mirror", "OTRS", "06-integracoes-e-dados/otrs/glossario.md", "glossário TN ID CI Dynamic Field mirror cursor ticket"),
    "otrs-rastreabilidade": ("17 · Rastreabilidade", "Da coluna externa ao modelo, serviço e interface", "OTRS", "06-integracoes-e-dados/otrs/rastreabilidade.md", "origem transformação destino interface source traceability"),
    "otrs-limitacoes": ("18 · Limitações e cobertura", "Confirmações, inferências e validações pendentes", "OTRS", "06-integracoes-e-dados/otrs/limitacoes.md", "pendente inferido confirmado cobertura limitação schema validação"),
    "cmdb": ("01 · Visão geral", "Arquitetura, cobertura e percurso dos dados CMDB ServiceUp", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb.md", "CMDB ServiceUp ConfigItemID source_config_item_id Number Links de internet Operadoras Provider ISP TELMEX projeto conta identidade confiança CMDB-API001 CMDB-Q001"),
    "cmdb-fonte-serviceup": ("02 · Fonte ServiceUp", "Origem, autenticação, HTTPS e acesso configurado", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/fonte-serviceup.md", "GenericInterface HTTPS JSON UserLogin Password TLS timeout URL base read-only"),
    "cmdb-endpoints": ("03 · Catálogo de endpoints", "Operações Search/Get, métodos, payloads e erros", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/endpoints.md", "CMDB-API001 CMDB-API002 ConfigItemSearch ConfigItemGet POST body headers lote"),
    "cmdb-respostas": ("04 · Estruturas de resposta", "Envelopes JSON, ConfigItem, CIXMLData e validação", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/respostas.md", "JSON Data ConfigItemID ConfigItemIDs CIXMLData XML sanitização"),
    "cmdb-dicionario": ("05 · Dicionário de dados", "65 campos de origem, derivados e persistidos", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/dicionario-dados.md", "OPERATOR_FIELDS LINK_FIELDS CMDB-F ConfigItemID Number UsuarioPPOE campos tipo coluna"),
    "cmdb-entidades": ("06 · Entidades", "Links, Operadoras, Provider, cliente, projeto e conta", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/entidades.md", "CMDBLinkMirror CMDBOperatorMirror ConfigItem business_project legacy_project"),
    "cmdb-relacionamentos": ("07 · Relacionamentos", "Chaves, cardinalidades e integridade Ticket–ConfigItem", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/relacionamentos.md", "CMDB-Q005 FK ConfigItemID Number SET_NULL OTRSTicketConfigItem"),
    "cmdb-normalizacao": ("08 · Normalização", "Regras de identidade, aliases, projeto e ISP", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/normalizacao.md", "TELMEX Claro Vivo Algar BD BL BusinessIdentity confidence alias ISP"),
    "cmdb-sincronizacao": ("09 · Sincronização", "Worker, lotes, validação, FULL e reconciliação", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/sincronizacao.md", "integration_worker CMDB_CADENCE lease CMDBSyncService stale dry-run apply"),
    "cmdb-espelho-local": ("10 · Espelho local", "Modelos Django, chaves, freshness e migrações", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/espelho-local.md", "CMDBLinkMirror CMDBOperatorMirror migration 0002 source_last_seen_at is_present_in_source"),
    "cmdb-otrs": ("11 · CMDB + OTRS", "Correlação de tickets, ConfigItems e Links", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/cmdb-otrs.md", "OTRS Ticket CI relation ConfigItemID cmdb_link FK Number tickets links afetados"),
    "cmdb-geolocalizacao": ("12 · Geolocalização", "UF, cidade, endereço, mapa e limites espaciais", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/geolocalizacao.md", "city state address latitude longitude geocoding Mapbox GeoJSON UF"),
    "cmdb-indicadores": ("13 · Indicadores", "Universos, fórmulas, filtros e deduplicação", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/indicadores.md", "links_total deployment_state ticket Count distinct Top 10 Links afetados"),
    "cmdb-consumidores": ("14 · Módulos consumidores", "Base, Chamados, Dashboard, Cockpit, Ranking, Mapa e Qualidade", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/consumidores.md", "CMDB-Q001 CMDB-Q008 CMDB-T001 CMDB-T008 Base Chamados Dashboard Cockpit Rankings Mapa Qualidade Relatórios ORM"),
    "cmdb-qualidade": ("15 · Qualidade", "Links prioritários, reincidência e monitoramento", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/cmdb-qualidade.md", "QualityOrder QualityOccurrence RREIP recorrência business_priority FUST Provider circuito"),
    "cmdb-seguranca": ("16 · Segurança", "Segredo, TLS, sanitização, RBAC e Data Scope", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/cmdb-seguranca.md", "DPAPI credential verify_tls senha secret token TLS permission inspect_integrations Data Scope"),
    "cmdb-diagnostico": ("17 · Diagnóstico", "Investigações seguras de conexão, sync e divergências", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/diagnostico.md", "CMDBTimeoutError TLS autenticação UNRESOLVED worker snapshot ISP"),
    "cmdb-rastreabilidade": ("18 · Rastreabilidade", "Da origem ao campo, consulta e interface", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/rastreabilidade.md", "CMDB-R CMDB-T ConfigItemID CIXMLData traceability fonte transformação modelo"),
    "cmdb-glossario": ("19 · Glossário", "Termos e semântica usada na integração", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/glossario.md", "ConfigItem CMDB ServiceUp GenericInterface mirror Business ID Data Scope"),
    "cmdb-limitacoes": ("20 · Limitações e cobertura", "Cobertura confirmada e pendências de validação externa", "CMDB ServiceUp", "06-integracoes-e-dados/cmdb/limitacoes.md", "paginação cobertura pendente inferido schema limite contrato externo"),
    "worker": ("Worker e Sincronização", "Cadências, FAST/FULL, leases e freshness", "Dados e Integrações", "02-arquitetura/worker-e-sincronizacao.md", "worker integration_worker FAST FULL cadência lease heartbeat cursor IntegrationLease IntegrationRun retry SyncState"),
    "dicionario-de-dados": ("Dicionário de Dados", "Campos centrais, origens, chaves e semântica", "Dados e Integrações", "02-arquitetura/dicionario-de-dados.md", "TicketTracking CMDBLinkMirror OTRSTicketConfigItem ConfigItemID external_id ticket_number closing_code_raw source_config_item_id CodigoFechamento GroupDataScope IntegrationLease"),
    "configuracoes": ("Configurações", "Conta, aparência e administração interna", "Administração", "05-funcionalidades/configuracoes.md", "Central Configurações preferências appearance UserPreference"),
    "integracoes": ("Integrações", "Conectores, estado operacional e diagnóstico", "Administração", "05-funcionalidades/integracoes.md", "integrações conector status IntegrationRun CMDB OTRS"),
    "usuarios-grupos-escopos": ("Usuários, Grupos e Escopos", "RBAC Django e Data Scope aplicado no backend", "Administração", "05-funcionalidades/usuarios-grupos-e-escopos.md", "RBAC permissions groups Data Scope ALL CUSTOM Operadora Projeto"),
    "auditoria": ("Auditoria", "Eventos administrativos e sanitização", "Administração", "05-funcionalidades/auditoria.md", "AuditEvent AuditService histórico metadata sanitização"),
    "tratamento-global-erros": ("Tratamento Global de Erros", "Handlers HTTP, APIs e diagnóstico", "Administração", "05-funcionalidades/tratamento-global-de-erros.md", "500 404 JSON CSRF middleware logging"),
    "validacao-de-seguranca": ("Validação de Segurança", "Resumo executivo da avaliação de segurança da V1", "Segurança e operação", "03-seguranca-e-qualidade/validacao-de-seguranca.md", "validação segurança executivo V1.3.0 controles aprovados RBAC Data Scope CSRF"),
    "seguranca": ("Arquitetura de Segurança", "Acesso, escopo, credenciais e limites", "Segurança e operação", "03-seguranca-e-qualidade/seguranca.md", "security segredo credenciais read-only autorização sanitização"),
    "auditoria-v1-3-0": ("Auditoria V1.3.0", "Baseline de segurança versionada da release 1.3.0", "Segurança e operação", "03-seguranca-e-qualidade/auditorias/auditoria-seguranca-v1.3.0-2026-11-02.md", "auditoria baseline 1.3.0 matriz vetores validado"),
    "relatorio-seguranca-v1": ("Relatório Técnico", "Relatório completo da avaliação defensiva da V1", "Segurança e operação", "03-seguranca-e-qualidade/relatorios/relatorio-de-seguranca-v1.md", "relatório técnico pentest achados SEC-001 retestes"),
    "testes-e-qa": ("Testes e QA", "Suítes, verificações e troubleshooting de testes", "Segurança e operação", "03-seguranca-e-qualidade/testes-e-qa.md", "test pytest Django check makemigrations QA SQLite MySQL"),
    "implantacao-e-producao": ("Implantação e Produção", "Configuração web, banco e worker", "Segurança e operação", "02-arquitetura/implantacao-e-producao.md", "deploy produção Windows MySQL worker DPAPI"),
    "diretrizes-de-interface": ("Diretrizes de Interface", "Tokens, componentes, temas e acessibilidade", "Padrões", "04-interface/diretrizes-de-interface.md", "CSS tema claro escuro responsividade acessibilidade"),
}
CATEGORY_ORDER = ("Começar", "Funcionalidades", "Dados e Integrações", "OTRS", "CMDB ServiceUp", "Administração", "Segurança e operação", "Padrões")


def _slugify(value):
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")


def _document_url(target):
    parsed = target.split("#", 1)
    target_path = parsed[0]
    if not target_path:
        return "#" + parsed[1] if len(parsed) == 2 else None
    normalized_target = target_path.replace("\\", "/").strip("/")
    if normalized_target.casefold() in {"funcionalidades", "05-funcionalidades"}:
        return reverse("configuracoes:documentation") + "#home-docs-modulos"
    document = _document_match(normalized_target)
    if document:
        slug, item = document
        url = reverse("configuracoes:documentation-page", kwargs={"slug": slug})
        return url + ("#" + _slugify(parsed[1]) if len(parsed) == 2 else "")
    return None


def _document_match(target_path):
    stem = Path(target_path.replace("\\", "/")).stem.casefold()
    for slug, item in DOCUMENTS.items():
        if slug.casefold() == stem or Path(item[3]).stem.casefold() == stem or (slug == "ranking-provedores" and stem == "ranking-provedores"):
            return slug, item
    return None


def _inline(text):
    """Escape source HTML first, then add only a small safe inline Markdown subset."""
    badge_labels = {
        "OTRS", "CMDB", "ARION", "MIRROR", "RAW", "DERIVADO", "READ-ONLY",
        "LOCAL", "REMOTE", "LEGACY", "OPERATIONAL", "RECONSTRUÍVEL",
        "PERSISTENTE", "SEGURANÇA", "VALIDADO", "VALIDADO NO ESCOPO TESTADO",
    }
    badges = {}

    def stash_badge(match):
        label = match.group(1).strip().upper()
        if label not in badge_labels:
            return match.group(0)
        token = f"DOCSBADGETOKEN{len(badges)}"
        tone = {
            "READ-ONLY": "readonly",
            "LEGACY": "legacy",
            "DERIVADO": "derived",
            "MIRROR": "mirror",
            "REMOTE": "remote",
            "RECONSTRUÍVEL": "reconstructible",
            "PERSISTENTE": "persistent",
            "SEGURANÇA": "security",
            "VALIDADO": "validated",
            "VALIDADO NO ESCOPO TESTADO": "validated",
            "ARION": "local",
            "LOCAL": "local",
            "OPERATIONAL": "local",
        }.get(label, "source")
        badges[token] = f'<span class="docs-badge docs-badge--{tone}">{label}</span>'
        return token

    text = re.sub(r"\{\{badge:([\w -]+)\}\}", stash_badge, text)
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    def replace_link(match):
        label, target = match.groups()
        if target.startswith(("https://", "http://")):
            safe_target = html.escape(target, quote=True)
            return f'<a href="{safe_target}" rel="noopener noreferrer">{label}</a>'
        internal = _document_url(target)
        if internal:
            if label.lower().endswith(".md"):
                target_path = target.split("#", 1)[0].replace("\\", "/").strip("/")
                match = _document_match(target_path)
                if match:
                    label = html.escape(match[1][0], quote=False)
            return f'<a href="{html.escape(internal, quote=True)}">{label}</a>'
        return label

    rendered = pattern.sub(replace_link, escaped)
    for token, badge in badges.items():
        rendered = rendered.replace(token, badge)
    return rendered


def render_markdown(source):
    """Render the Markdown patterns used by docs; raw HTML is always escaped."""
    lines = source.splitlines()
    output, headings, used_ids = [], [], set()
    i = 0
    dictionary_table_pending = False

    def heading(level, title):
        base = _slugify(title) or "secao"
        slug, suffix = base, 2
        while slug in used_ids:
            slug, suffix = f"{base}-{suffix}", suffix + 1
        used_ids.add(slug)
        headings.append((title, slug, level))
        return f'<h{level} id="{slug}">{_inline(title)}</h{level}>'

    def is_table_separator(line):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            language = line[3:].strip()
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            klass = f' class="language-{html.escape(language, quote=True)}"' if re.fullmatch(r"[\w+-]+", language) else ""
            block = f"<pre><code{klass}>{html.escape(chr(10).join(code))}</code></pre>"
            if language.casefold() == "mermaid":
                diagram = _render_diagram(chr(10).join(code))
                toolbar = (
                    '<div class="docs-diagram-toolbar"><button type="button" class="docs-diagram-expand" '
                    'data-docs-diagram-expand aria-haspopup="dialog">'
                    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2H2v4m8-4h4v4M2 10v4h4m8-4v4h-4"/></svg>'
                    '<span>Ampliar</span></button></div>'
                    if diagram else ""
                )
                output.append(
                    '<section class="docs-diagram" aria-label="Diagrama">'
                    + toolbar
                    + (diagram or '<p class="docs-diagram-note">Diagrama textual não renderizado; consulte o código fonte.</p>')
                    + '<details class="docs-diagram-source"><summary>Ver código do diagrama</summary>' + block + "</details></section>"
                )
            else:
                output.append(block)
        elif line.startswith("# ") or line.startswith("## ") or line.startswith("### "):
            level = len(line) - len(line.lstrip("#"))
            title = line[level:].strip()
            output.append(heading(level, title))
            if level == 2 and _slugify(title) == "indice-de-campos":
                dictionary_table_pending = True
        elif line.lstrip().startswith("|") and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
            header = [cell.strip() for cell in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append([cell.strip() for cell in lines[i].strip().strip("|").split("|")])
                i += 1
            table_class = "doc-table-wrap docs-dictionary-index" if dictionary_table_pending else "doc-table-wrap"
            row_attribute = ' data-doc-dictionary-row=""' if dictionary_table_pending else ""
            output.append(f'<div class="{table_class}"><table><thead><tr>' + "".join(f"<th scope=\"col\">{_inline(cell)}</th>" for cell in header) + "</tr></thead><tbody>" + "".join("<tr" + row_attribute + ">" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>" for row in rows) + "</tbody></table></div>")
            dictionary_table_pending = False
            continue
        elif re.match(r"\s*[-*+] ", line):
            items = []
            while i < len(lines) and re.match(r"\s*[-*+] ", lines[i]):
                items.append(f"<li>{_inline(lines[i].strip()[2:])}</li>")
                i += 1
            output.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif re.match(r"\s*\d+[.)] ", line):
            items = []
            while i < len(lines) and re.match(r"\s*\d+[.)] ", lines[i]):
                item_text = re.sub(r"^\s*\d+[.)]\s+", "", lines[i])
                items.append(f"<li>{_inline(item_text)}</li>")
                i += 1
            output.append("<ol>" + "".join(items) + "</ol>")
            continue
        elif line.startswith("> "):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(_inline(lines[i].removeprefix("> ").removeprefix(">")))
                i += 1
            label = re.match(r"<strong>(Fonte da verdade|Informação|Atenção|Importante|Segurança Validada|Segurança|Somente leitura|Backlog):</strong>", quote[0], re.I) if quote else None
            if label:
                kind = {"fonte da verdade": "source-of-truth", "informação": "info", "atenção": "attention", "importante": "important", "segurança validada": "validated", "segurança": "security", "somente leitura": "readonly", "backlog": "backlog"}[label.group(1).casefold()]
                output.append(f'<aside class="docs-callout docs-callout--{kind}" role="note">' + "".join(f"<p>{part}</p>" for part in quote if part) + "</aside>")
            else:
                output.append("<blockquote>" + "".join(f"<p>{part}</p>" for part in quote if part) + "</blockquote>")
            continue
        elif not line.strip():
            pass
        elif re.fullmatch(r"\s*([-*_])\s*\1\s*\1[\s\1]*", line):
            output.append("<hr>")
        else:
            paragraph = [line.strip()]
            i += 1
            while i < len(lines):
                candidate = lines[i]
                starts_block = (
                    candidate.startswith(("# ", "## ", "### ", "```", "> "))
                    or candidate.lstrip().startswith("|")
                    or re.match(r"\s*[-*+] |\s*\d+[.)] ", candidate)
                    or re.fullmatch(r"\s*([-*_])\s*\1\s*\1[\s\1]*", candidate)
                )
                if not candidate.strip() or starts_block:
                    break
                paragraph.append(candidate.strip())
                i += 1
            output.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            continue
        i += 1
    return "\n".join(output), headings


def _render_diagram(source):
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if lines and lines[0].casefold() == "sequencediagram":
        return _render_sequence_diagram(lines)
    return _render_flowchart(source)


def _render_sequence_diagram(lines):
    """Render a small, safe Mermaid sequence-diagram subset used by the docs."""
    participants = []
    aliases = {}
    messages = []
    participant_pattern = re.compile(r"^participant\s+([A-Za-z0-9_]+)(?:\s+as\s+(.+))?$", re.I)
    message_pattern = re.compile(r"^([A-Za-z0-9_]+)(-->>|->>)([A-Za-z0-9_]+):\s*(.+)$")
    for line in lines[1:]:
        participant = participant_pattern.fullmatch(line)
        if participant:
            key, label = participant.groups()
            if key in aliases or len(participants) >= 6:
                return ""
            participants.append(key)
            aliases[key] = (label or key).strip()
            continue
        message = message_pattern.fullmatch(line)
        if not message:
            return ""
        left, arrow, right, label = message.groups()
        messages.append((left, arrow, right, label.strip()))
    if not participants or not messages or any(left not in aliases or right not in aliases for left, _, right, _ in messages):
        return ""

    padding, box_width, box_height, step_x = 24, 132, 46, 156
    width = padding * 2 + (len(participants) - 1) * step_x + box_width
    top, row_step = 82, 58
    height = top + len(messages) * row_step + 24
    positions = {key: padding + index * step_x + box_width / 2 for index, key in enumerate(participants)}

    def wrap(value, limit, maximum=2):
        words, result, current = value.split(), [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > limit:
                result.append(current)
                current = word
            else:
                current = candidate
        if current:
            result.append(current)
        if len(result) > maximum:
            result = result[:maximum]
            result[-1] = result[-1].rstrip(" .…") + "…"
        return result

    marker_id = "docs-sequence-arrow-" + hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:10]
    nodes = []
    for key in participants:
        x = positions[key] - box_width / 2
        label_lines = wrap(aliases[key], 19)
        first_y = 16 + box_height / 2 - (len(label_lines) - 1) * 6
        tspans = "".join(
            f'<tspan x="{positions[key]}" dy="{0 if index == 0 else 13}">{html.escape(part)}</tspan>'
            for index, part in enumerate(label_lines)
        )
        nodes.append(
            f'<g class="docs-diagram-node"><rect x="{x}" y="16" width="{box_width}" height="{box_height}" rx="9"/>'
            f'<text x="{positions[key]}" y="{first_y}" text-anchor="middle">{tspans}</text></g>'
        )

    lifelines = "".join(
        f'<path class="docs-diagram-edge docs-diagram-lifeline" d="M {positions[key]} 62 V {height - 12}"/>'
        for key in participants
    )
    arrows = []
    descriptions = []
    for index, (left, arrow, right, label) in enumerate(messages):
        y = top + index * row_step
        x1, x2 = positions[left], positions[right]
        arrows.append(
            f'<path class="docs-diagram-edge" d="M {x1} {y} H {x2}" marker-end="url(#{marker_id})"'
            + (' stroke-dasharray="4 3"' if arrow == "-->>" else "")
            + "/>"
        )
        label_lines = wrap(label, 22)
        label_y = y - (len(label_lines) - 1) * 11 - 7
        tspans = "".join(
            f'<tspan x="{(x1 + x2) / 2}" dy="{0 if line_index == 0 else 12}">{html.escape(part)}</tspan>'
            for line_index, part in enumerate(label_lines)
        )
        arrows.append(
            f'<text class="docs-diagram-message-label" x="{(x1 + x2) / 2}" y="{label_y}" text-anchor="middle">{tspans}</text>'
        )
        descriptions.append(f"{aliases[left]} to {aliases[right]}: {label}")

    aria = html.escape("; ".join(descriptions), quote=True)
    return (
        f'<svg class="docs-diagram-svg docs-diagram-svg--horizontal" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="{aria}" xmlns="http://www.w3.org/2000/svg">'
        f'<defs><marker id="{marker_id}" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">'
        '<path d="M0,0 L0,6 L7,3 z"/></marker></defs>'
        + lifelines + "".join(arrows) + "".join(nodes) + "</svg>"
    )


def _render_flowchart(source):
    """Render simple Mermaid flowcharts without scaling labels to fit SVG boxes."""
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if not lines or not re.fullmatch(r"(?:flowchart|graph)\s+(?:LR|TD|TB)", lines[0], re.I):
        return ""
    vertical = lines[0].upper().endswith((" TD", " TB"))
    edges = []
    pattern = re.compile(r"^([A-Za-z0-9_-]+)\s*(?:\[([^]]+)\]|\(([^)]+)\)|\{([^}]+)\})?\s*-->(?:\|([^|]+)\|)?\s*([A-Za-z0-9_-]+)\s*(?:\[([^]]+)\]|\(([^)]+)\)|\{([^}]+)\})?$")
    labels = {}
    for line in lines[1:]:
        match = pattern.fullmatch(line)
        if not match:
            return ""
        left, left_box, left_round, left_diamond, edge_label, right, right_box, right_round, right_diamond = match.groups()
        labels[left] = left_box or left_round or left_diamond or labels.get(left, left)
        labels[right] = right_box or right_round or right_diamond or labels.get(right, right)
        edges.append((left, right, edge_label or ""))
    order = []
    for left, right, _ in edges:
        for node in (left, right):
            if node not in order:
                order.append(node)
    if not order:
        return ""

    # Longest-path layers keep siblings side by side and make forks compact.
    depth = {node: 0 for node in order}
    for _ in range(len(order) - 1):
        changed = False
        for left, right, _ in edges:
            candidate = depth[left] + 1
            if candidate > depth[right]:
                depth[right] = candidate
                changed = True
        if not changed:
            break
    layers = {}
    for node in order:
        layers.setdefault(depth[node], []).append(node)

    box_width, box_height = 168, 64
    step_x, step_y = 200, 86
    padding = 16
    max_layer_size = max(map(len, layers.values()))
    max_depth = max(layers)
    if vertical:
        width = padding * 2 + (max_layer_size - 1) * step_x + box_width
        height = padding * 2 + max_depth * step_y + box_height
        positions = {
            node: (padding + (max_layer_size - len(nodes)) * step_x // 2 + row * step_x,
                   padding + layer * step_y)
            for layer, nodes in layers.items()
            for row, node in enumerate(nodes)
        }
    else:
        width = padding * 2 + max_depth * step_x + box_width
        height = padding * 2 + (max_layer_size - 1) * step_y + box_height
        positions = {
            node: (padding + layer * step_x,
                   padding + (max_layer_size - len(nodes)) * step_y // 2 + row * step_y)
            for layer, nodes in layers.items()
            for row, node in enumerate(nodes)
        }

    def wrapped_label(value, limit=21, maximum=3):
        words, output, current = value.split(), [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > limit:
                output.append(current)
                current = word
            else:
                current = candidate
        if current:
            output.append(current)
        if len(output) > maximum:
            output = output[:maximum]
            output[-1] = output[-1].rstrip(" .…") + "…"
        return output

    nodes = []
    for node in order:
        x, y = positions[node]
        label_lines = wrapped_label(labels[node])
        first_y = y + (box_height / 2) + (len(label_lines) - 1) * -7
        tspans = "".join(
            f'<tspan x="{x + box_width / 2}" dy="{0 if index == 0 else 15}">{html.escape(part)}</tspan>'
            for index, part in enumerate(label_lines)
        )
        nodes.append(
            f'<g class="docs-diagram-node"><rect x="{x}" y="{y}" width="{box_width}" height="{box_height}" rx="9"/>'
            f'<text x="{x + box_width / 2}" y="{first_y}" text-anchor="middle">{tspans}</text></g>'
        )
    if vertical:
        arrows = "".join(
            f'<path class="docs-diagram-edge" d="M {positions[left][0] + box_width / 2} {positions[left][1] + box_height} '
            f'V {positions[left][1] + box_height + 12} H {positions[right][0] + box_width / 2} '
            f'V {positions[right][1] - 7}" marker-end="url(#docs-arrow)"/>'
            for left, right, _ in edges
        )
    else:
        arrows = "".join(
            f'<path class="docs-diagram-edge" d="M {positions[left][0] + box_width} {positions[left][1] + box_height / 2} '
            f'H {positions[left][0] + box_width + 12} V {positions[right][1] + box_height / 2} '
            f'H {positions[right][0] - 7}" marker-end="url(#docs-arrow)"/>'
            for left, right, _ in edges
        )
    descriptions = [f"{labels[left]} to {labels[right]}" + (f": {label}" if label else "") for left, right, label in edges]
    aria = html.escape("; ".join(descriptions), quote=True)
    orientation_class = " docs-diagram-svg--vertical" if vertical else " docs-diagram-svg--horizontal"
    marker_id = "docs-arrow-" + hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
    return (
        f'<svg class="docs-diagram-svg{orientation_class}" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" aria-label="{aria}" xmlns="http://www.w3.org/2000/svg">'
        f'<defs><marker id="{marker_id}" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z"/></marker></defs>'
        f'{arrows.replace("docs-arrow", marker_id)}{"".join(nodes)}</svg>'
    )


class DocumentationService:
    """Render versioned Markdown files from ``docs/``, the portal's source of truth."""

    @classmethod
    def navigation(cls):
        return [
            {"slug": slug, "title": data[0], "description": data[1], "category": data[2], "search_terms": data[4]}
            for slug, data in DOCUMENTS.items()
        ]

    @classmethod
    def categories(cls):
        docs = cls.navigation()
        return [{"name": name, "documents": [doc for doc in docs if doc["category"] == name]} for name in CATEGORY_ORDER]

    @classmethod
    def get(cls, slug):
        data = DOCUMENTS.get(slug)
        if not data:
            return None
        path = (DOCS_ROOT / data[3]).resolve()
        if not path.is_relative_to(DOCS_ROOT) or not path.is_file():
            return None
        source = path.read_text(encoding="utf-8-sig")
        body, headings = render_markdown(source)
        # The portal heading uses the first Markdown H1 where one is present.
        title = headings[0][0] if headings and headings[0][2] == 1 else data[0]
        if headings and headings[0][2] == 1:
            body = re.sub(r"<h1\b[^>]*>.*?</h1>\s*", "", body, count=1, flags=re.DOTALL)
            headings = headings[1:]
        return {"slug": slug, "title": title, "description": data[1], "category": data[2], "body": body, "headings": headings}

    @classmethod
    def search(cls, query):
        needle = query.casefold()
        results = []
        for doc in cls.navigation():
            record = cls.get(doc["slug"])
            if record is None:
                continue
            raw = (DOCS_ROOT / DOCUMENTS[doc["slug"]][3]).read_text(encoding="utf-8-sig")
            haystack = f"{doc['title']}\n{doc['description']}\n{doc['search_terms']}\n{raw}"
            position = haystack.casefold().find(needle)
            if position < 0:
                continue
            start, end = max(0, position - 100), min(len(haystack), position + len(query) + 140)
            excerpt = re.sub(r"\s+", " ", haystack[start:end]).strip()
            excerpt = ("…" if start else "") + excerpt + ("…" if end < len(haystack) else "")
            nearby = next((slug for title, slug, _ in record["headings"] if title.casefold() in haystack[max(0, position - 80):position + 80].casefold()), "")
            results.append({**doc, "excerpt": excerpt, "anchor": nearby})
        return results
