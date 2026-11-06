(() => {
  const page = document.querySelector("[data-cockpit]");
  if (!page) return;

  const form = page.querySelector("[data-filters]");
  const FILTER_KEYS = ["provider", "uf", "status", "source_state", "service", "sla", "priority", "termination_reason", "stage", "origin", "project", "responsibility", "responsible", "queue", "age_bucket"];
  const ADVANCED_KEYS = ["source_state", "uf", "sla", "priority", "service", "stage", "origin", "project", "responsibility", "responsible", "queue", "age_bucket"];
  const DEFAULT_START = page.dataset.defaultStart;
  const DEFAULT_END = page.dataset.defaultEnd;
  const DEFAULTS = { scope: "support", metric: "tickets", start: DEFAULT_START, end: DEFAULT_END, compare_to: "", selected_month: "", group_by: "provider", chart_view: "all", chart_origin: "consolidated", include_cancelled: true, include_improcedente: true, detail_page: 1, ticket_page: 1, ticket_page_size: 25, ticket_search: "", filters: Object.fromEntries(FILTER_KEYS.map(key => [key, "all"])) };
  const METRICS = { tickets: "Tickets criados", open_tickets: "Em atendimento no per\u00edodo", pending_tickets: "Pendentes no per\u00edodo", scheduled_tickets: "Agendados no per\u00edodo", resolved_tickets: "Resolvidos no per\u00edodo", closed_tickets: "Resolvidos no per\u00edodo", resolved_closed_tickets: "Resolvidos no per\u00edodo", canceled_tickets: "Cancelados no per\u00edodo", links_affected: "Links afetados", recurrences: "Links reincidentes", backlog: "Backlog atual" };
  const DIMENSIONS = { provider: "Provedor", uf: "UF", status: "Status", source_state: "Status de origem", sla: "SLA", priority: "Prioridade", termination_reason: "Motivo de encerramento", cancellation_stage: "Etapa do cancelamento", operational_origin: "Origem", project: "Projeto / cliente", month: "Per\u00edodo" };
  const FILTER_LABELS = { ...DIMENSIONS, service: "Servi\u00e7o", stage: "Etapa", origin: "Origem", responsibility: "Proced\u00eancia", responsible: "Respons\u00e1vel", queue: "Fila", age_bucket: "Aging" };
  const MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
  const STATE_CODES = new Set(["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]);
  const STATE_NAMES = { AC: "acre", AL: "alagoas", AP: "amapa", AM: "amazonas", BA: "bahia", CE: "ceara", DF: "distrito federal", ES: "espirito santo", GO: "goias", MA: "maranhao", MT: "mato grosso", MS: "mato grosso do sul", MG: "minas gerais", PA: "para", PB: "paraiba", PR: "parana", PE: "pernambuco", PI: "piaui", RJ: "rio de janeiro", RN: "rio grande do norte", RS: "rio grande do sul", RO: "rondonia", RR: "roraima", SC: "santa catarina", SP: "sao paulo", SE: "sergipe", TO: "tocantins" };
  function stateCode(value) {
    const raw = String(value || "").trim().toUpperCase();
    if (STATE_CODES.has(raw)) return raw;
    const normalized = String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim().toLowerCase();
    return Object.keys(STATE_NAMES).find(code => STATE_NAMES[code] === normalized) || "";
  }
  const number = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
  const percentage = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const monthLabel = value => `${MONTHS[Number(value.slice(5)) - 1]}/${value.slice(2, 4)}`;
  const dateLabel = value => { const [year, month, day] = value.split("-"); return `${day}/${month}/${year}`; };
  const csrf = page.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
  let queryState = readURLState();
  let monthExplicitlySelected = Boolean(queryState.selected_month);
  let activePreset = null;
  let latestRequest = 0;
  let controller = null;
  let temporaryTicketDay = "";
  let selectedDayForDialog = null;
  const DETAIL_COLLAPSE_DEFAULTS = { aging: false, dimensions: false, distribution: true, tickets: false };
  const DETAIL_COLLAPSE_STORAGE_KEY = "arion.cockpit.detail-collapse.v1";
  let detailCollapseState = readDetailCollapseState();
  let sourceStateLabels = new Map();
  let latestAnalytics = null;

  function readDetailCollapseState() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(DETAIL_COLLAPSE_STORAGE_KEY) || "{}");
      return Object.fromEntries(Object.entries(DETAIL_COLLAPSE_DEFAULTS).map(([key, fallback]) => [key, typeof saved[key] === "boolean" ? saved[key] : fallback]));
    } catch (_) { return { ...DETAIL_COLLAPSE_DEFAULTS }; }
  }
  function writeDetailCollapseState() {
    try { sessionStorage.setItem(DETAIL_COLLAPSE_STORAGE_KEY, JSON.stringify(detailCollapseState)); } catch (_) { /* session storage can be unavailable in private contexts */ }
  }
  function updateGlobalCollapseControl() {
    const button = page.querySelector("[data-details-global]");
    if (!button) return;
    const allCollapsed = Object.keys(DETAIL_COLLAPSE_DEFAULTS).every(key => detailCollapseState[key]);
    const label = button.querySelector("[data-details-global-label]");
    if (label) label.textContent = allCollapsed ? "Expandir detalhes" : "Recolher detalhes";
    button.setAttribute("aria-expanded", String(!allCollapsed));
  }
  function setDetailCollapsed(key, collapsed, persist = true) {
    const panel = page.querySelector(`[data-collapse-panel="${key}"]`), button = page.querySelector(`[data-collapse-toggle="${key}"]`);
    if (!panel || !button) return;
    detailCollapseState[key] = Boolean(collapsed);
    panel.classList.toggle("is-collapsed", detailCollapseState[key]);
    panel.setAttribute("aria-hidden", String(detailCollapseState[key]));
    panel.inert = detailCollapseState[key];
    button.setAttribute("aria-expanded", String(!detailCollapseState[key]));
    if (persist) writeDetailCollapseState();
    updateGlobalCollapseControl();
  }
  function initDetailCollapses() {
    Object.keys(DETAIL_COLLAPSE_DEFAULTS).forEach(key => setDetailCollapsed(key, detailCollapseState[key], false));
    page.querySelectorAll("[data-collapse-toggle]").forEach(button => {
      button.addEventListener("click", () => {
        const key = button.dataset.collapseToggle;
        setDetailCollapsed(key, !detailCollapseState[key]);
      });
    });
    const globalButton = page.querySelector("[data-details-global]");
    globalButton?.addEventListener("click", () => {
      const collapse = !Object.keys(DETAIL_COLLAPSE_DEFAULTS).every(key => detailCollapseState[key]);
      Object.keys(DETAIL_COLLAPSE_DEFAULTS).forEach(key => setDetailCollapsed(key, collapse));
    });
  }

  function cloneDefaults() { return { ...DEFAULTS, filters: { ...DEFAULTS.filters } }; }
  function validDate(value) { return /^\d{4}-\d{2}-\d{2}$/.test(value || "") && !Number.isNaN(Date.parse(`${value}T00:00:00Z`)); }
  function validMonth(value) { return /^\d{4}-(0[1-9]|1[0-2])$/.test(value || ""); }
  function monthRange(value) {
    const [year, month] = value.split("-").map(Number), last = new Date(Date.UTC(year, month, 0)).getUTCDate();
    return [`${value}-01`, `${value}-${String(last).padStart(2, "0")}`];
  }
  function readURLState() {
    const params = new URLSearchParams(location.search), state = cloneDefaults();
    if (["support", "cancellation", "general"].includes(params.get("scope"))) state.scope = params.get("scope");
    const legacyGroups = { operator: "provider", project: "provider", service: "provider", sla_status: "sla", contract_operator: null, isp: null };
    if (METRICS[params.get("metric")]) state.metric = params.get("metric");
    if (validDate(params.get("start"))) state.start = params.get("start");
    else if (validMonth(params.get("period_from"))) state.start = `${params.get("period_from")}-01`;
    if (validDate(params.get("end"))) state.end = params.get("end");
    else {
      const oldEnd = params.get("period_to") || params.get("period");
      if (validMonth(oldEnd)) state.end = monthRange(oldEnd)[1];
    }
    if (validMonth(params.get("compare_to"))) state.compare_to = params.get("compare_to");
    if (validMonth(params.get("selected_month"))) state.selected_month = params.get("selected_month");
    if (["all", "created", "closed", "backlog"].includes(params.get("chart_view"))) state.chart_view = params.get("chart_view");
    if (params.get("chart_origin") === "origin") state.chart_origin = "origin";
    if (params.has("include_cancelled")) state.include_cancelled = params.get("include_cancelled") !== "0";
    if (params.has("include_improcedente")) state.include_improcedente = params.get("include_improcedente") !== "0";
    const requestedGroup = params.get("group_by");
    const group = requestedGroup === "project" && state.scope !== "support" ? "project" : legacyGroups[requestedGroup] ?? requestedGroup;
    if (DIMENSIONS[group]) state.group_by = group;
    else if (!requestedGroup) state.group_by = { support: "provider", cancellation: "cancellation_stage", general: "operational_origin" }[state.scope];
    FILTER_KEYS.forEach(key => { if (params.has(key)) state.filters[key] = params.get(key) || "all"; });
    if (params.has("closing_reason") && !params.has("termination_reason")) state.filters.termination_reason = params.get("closing_reason") || "all";
    if (!params.has("provider") && params.has("operator")) state.filters.provider = params.get("operator") || "all";
    if (!params.has("sla") && params.has("sla_status")) state.filters.sla = params.get("sla_status") || "all";
    if (state.start > state.end) state.start = state.end;
    state.ticket_page = Math.max(1, Number(params.get("ticket_page")) || 1);
    state.ticket_page_size = [25, 50, 100].includes(Number(params.get("ticket_page_size"))) ? Number(params.get("ticket_page_size")) : 25;
    state.ticket_search = (params.get("ticket_search") || "").slice(0, 120);
    const pageNumber = Number(params.get("detail_page"));
    state.detail_page = Number.isInteger(pageNumber) && pageNumber > 0 ? pageNumber : 1;
    return state;
  }
  function normalizeState(state) {
    const legacyGroups = { operator: "provider", project: "provider", service: "provider", sla_status: "sla", contract_operator: null, isp: null };
    state.scope = ["support", "cancellation", "general"].includes(state.scope) ? state.scope : "support";
    state.metric = state.metric === "links" || !METRICS[state.metric] ? "tickets" : state.metric;
    state.group_by = state.group_by === "project" && state.scope !== "support" ? "project" : legacyGroups[state.group_by] ?? state.group_by;
    if (!DIMENSIONS[state.group_by]) state.group_by = { support: "provider", cancellation: "cancellation_stage", general: "operational_origin" }[state.scope];
    if (!validDate(state.start) && validMonth(state.period_from)) state.start = `${state.period_from}-01`;
    if (!validDate(state.end) && validMonth(state.period_to)) state.end = monthRange(state.period_to)[1];
    state.start = validDate(state.start) ? state.start : DEFAULT_START;
    state.end = validDate(state.end) ? state.end : DEFAULT_END;
    if (state.start > state.end) state.start = state.end;
    state.selected_month = validMonth(state.selected_month) ? state.selected_month : "";
    state.chart_view = ["all", "created", "closed", "backlog"].includes(state.chart_view) ? state.chart_view : "all";
    state.chart_origin = state.chart_origin === "origin" ? "origin" : "consolidated";
    state.include_cancelled = state.include_cancelled !== false;
    state.include_improcedente = state.include_improcedente !== false;
    const oldFilters = state.filters || {};
    state.filters = Object.fromEntries(FILTER_KEYS.map(key => [key, oldFilters[key] ?? "all"]));
    if (state.filters.responsibility === "IMPROCEDENTE" && state.metric !== "backlog") state.include_improcedente = true;
    if (state.filters.provider === "all" && oldFilters.operator) state.filters.provider = oldFilters.operator;
    if (state.filters.sla === "all" && oldFilters.sla_status) state.filters.sla = oldFilters.sla_status;
    state.detail_page = 1;
    state.ticket_page = Math.max(1, Number(state.ticket_page) || 1);
    state.ticket_page_size = [25, 50, 100].includes(Number(state.ticket_page_size)) ? Number(state.ticket_page_size) : 25;
    state.ticket_search = String(state.ticket_search || "").slice(0, 120);
    return state;
  }
  function queryPayload() {
    return { scope: queryState.scope, metric: queryState.metric, start: queryState.start, end: queryState.end, compare_to: queryState.compare_to || null, selected_month: monthExplicitlySelected ? queryState.selected_month || null : null, group_by: queryState.group_by, detail_page: queryState.detail_page, ticket_page: queryState.ticket_page, ticket_page_size: queryState.ticket_page_size, ticket_search: queryState.ticket_search, include_cancelled: queryState.include_cancelled, include_improcedente: queryState.include_improcedente, filters: { ...queryState.filters } };
  }
  function writeURL() {
    const params = new URLSearchParams();
    params.set("scope", queryState.scope);
    if (queryState.metric !== DEFAULTS.metric) params.set("metric", queryState.metric);
    if (queryState.start !== DEFAULTS.start) params.set("start", queryState.start);
    if (queryState.end !== DEFAULTS.end) params.set("end", queryState.end);
    if (queryState.compare_to) params.set("compare_to", queryState.compare_to);
    if (queryState.chart_view !== "all") params.set("chart_view", queryState.chart_view);
    if (queryState.scope === "general" && queryState.chart_origin === "origin") params.set("chart_origin", "origin");
    if (!queryState.include_cancelled) params.set("include_cancelled", "0");
    if (!queryState.include_improcedente) params.set("include_improcedente", "0");
    if (monthExplicitlySelected && queryState.selected_month) params.set("selected_month", queryState.selected_month);
    if (queryState.group_by !== DEFAULTS.group_by) params.set("group_by", queryState.group_by);
    if (queryState.detail_page > 1) params.set("detail_page", queryState.detail_page);
    if (queryState.ticket_page > 1) params.set("ticket_page", queryState.ticket_page);
    if (queryState.ticket_page_size !== 25) params.set("ticket_page_size", queryState.ticket_page_size);
    if (queryState.ticket_search) params.set("ticket_search", queryState.ticket_search);
    FILTER_KEYS.forEach(key => { if (queryState.filters[key] !== "all") params.set(key, queryState.filters[key]); });
    history.replaceState(null, "", `${location.pathname}${params.size ? `?${params}` : ""}${location.hash}`);
  }
  function readForm() {
    queryState.metric = form.elements.metric.value;
    queryState.start = form.elements.start.value || DEFAULT_START;
    queryState.end = form.elements.end.value || DEFAULT_END;
    queryState.compare_to = form.elements.compare_to.value;
    queryState.include_cancelled = form.elements.include_cancelled.checked;
    queryState.include_improcedente = form.elements.include_improcedente.checked;
    queryState.group_by = page.querySelector("[data-breakdown]").value;
    FILTER_KEYS.forEach(key => { queryState.filters[key] = form.elements.namedItem(key).value || "all"; });
    if (queryState.filters.responsibility === "IMPROCEDENTE" && queryState.metric !== "backlog") queryState.include_improcedente = true;
  }
  function syncForm() {
    for (const key of ["metric", "start", "end", "compare_to"]) form.elements.namedItem(key).value = queryState[key];
    FILTER_KEYS.forEach(key => { form.elements.namedItem(key).value = queryState.filters[key]; });
    form.elements.include_cancelled.checked = queryState.include_cancelled;
    form.elements.include_improcedente.checked = queryState.include_improcedente;
    page.querySelector("[data-breakdown]").value = queryState.group_by;
    const snapshot = queryState.metric === "backlog";
    form.elements.start.disabled = snapshot;
    form.elements.end.disabled = snapshot;
    updateMoreFilters();
    updateContextControls();
  }
  function updateContextControls() {
    page.querySelectorAll("[data-scope]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.scope === queryState.scope)));
    page.querySelectorAll("[data-scope-filter]").forEach(label => {
      const key = label.dataset.scopeFilter;
      label.hidden = (key === "stage" && queryState.scope !== "cancellation") || (key === "origin" && queryState.scope !== "general");
    });
    const reasonLabel = form.elements.termination_reason.closest("label");
    if (reasonLabel) {
      reasonLabel.hidden = queryState.scope === "cancellation";
      reasonLabel.firstChild.textContent = queryState.scope === "general" ? "Motivo de encerramento (OTRS)" : "Motivo";
    }
    page.querySelector("[data-scope-description]").textContent = {
      support: "Suporte operacional da fila NOC - ATACADO.",
      cancellation: "Processo de cancelamento identificado nos dados locais do OTRS.",
      general: "Carga de suporte e cancelamentos, identificada por origem."
    }[queryState.scope];
    const originControl = page.querySelector("[data-chart-origin-control]");
    if (originControl) originControl.hidden = queryState.scope !== "general";
    page.dataset.scope = queryState.scope;
  }
  function updateMoreFilters() {
    const count = ADVANCED_KEYS.filter(key => queryState.filters[key] !== "all").length + Number(Boolean(queryState.compare_to));
    page.querySelector("[data-more-label]").textContent = count ? `Mais filtros Â· ${count} ativo${count === 1 ? "" : "s"}` : "Mais filtros";
    if (count) page.querySelector("[data-more-filters]").open = true;
  }
  function escapeHTML(value) { return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]); }
  async function post(url, payload, signal) {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, credentials: "same-origin", body: JSON.stringify(payload), signal });
    const body = (response.headers.get("content-type") || "").includes("application/json") ? await response.json() : null;
    if (!response.ok) throw new Error(body?.error?.message || body?.error || "Falha na solicita\u00e7\u00e3o.");
    return body;
  }
  function renderOptions(options = {}) {
    for (const [key, values] of Object.entries(options)) {
      const select = form.elements.namedItem(key);
      if (!select) continue;
      const selected = queryState.filters[key], first = select.options[0];
      const normalized = values.map(item => typeof item === "object" ? item : { value: item, label: item });
      const list = [first, ...normalized.map(item => new Option(item.label, item.value))];
      if (selected === "high-critical" && key === "priority") list.push(new Option("Alto / Cr\u00edtico", selected));
      else if (selected !== "all" && !normalized.some(item => item.value === selected)) {
        const friendly = { CANCELED: "Cancelado", UNKNOWN: "N\u00e3o classificado" }[selected] || selected;
        list.push(new Option(`${friendly} \u00b7 fora das op\u00e7\u00f5es dispon\u00edveis`, selected));
      }
      select.replaceChildren(...list);
      select.value = selected;
      if (key === "source_state") sourceStateLabels = new Map(normalized.map(item => [String(item.value), String(item.label)]));
    }
    form.elements.compare_to.value = queryState.compare_to;
  }
  function renderKpis(items) {
    const help = {
      coverage: "Percentual dos tickets da análise que possuem vínculo com um provedor identificado no CMDB.",
      unresolved: "Tickets da análise cujo vínculo OTRS → ConfigItem → Link → Provedor não pôde ser concluído."
    };
    page.querySelector("[data-kpis]").innerHTML = (items || []).slice(0, 4).map(item => {
      const value = item.unit === "% dos tickets" ? `${number.format(item.value)}%` : number.format(item.value);
      const trend = item.trend;
      const sign = trend?.absolute_change > 0 ? "+" : "";
      const trendValue = trend ? (trend.unit === "p.p." ? `${sign}${number.format(trend.absolute_change)} p.p.` : item.key === "total" && trend.percentage_change !== null ? `${sign}${number.format(trend.percentage_change)}% vs período anterior` : `${sign}${number.format(trend.absolute_change)} vs período anterior`) : "";
      const trendClass = trend?.favorable === true ? "is-favorable" : trend?.favorable === false ? "is-unfavorable" : "is-neutral";
      const note = item.unit === "% dos tickets" ? "dos tickets encontrados" : item.unit || "";
      return `<article class="kpi-card dash-card" title="${escapeHTML(help[item.key] || "Resumo calculado conforme a consulta ativa.")}"><span class="kpi-label">${escapeHTML(item.label)}</span><strong class="kpi-value">${value}</strong><small class="kpi-note">${escapeHTML(note)}</small>${trend ? `<span class="cockpit-kpi-trend ${trendClass}" aria-label="Variação ${escapeHTML(trendValue)}">${trend.direction === "up" ? "↑" : trend.direction === "down" ? "↓" : "·"} ${escapeHTML(trendValue)}</span>` : ""}</article>`;
    }).join("");
  }
  function renderOperationalKpis(items) {
    const sourcePending = latestAnalytics?.scope === "cancellation"
      && latestAnalytics?.source_sync?.available === false;
    page.querySelector("[data-kpis]").innerHTML = (items || []).map(item => {
      const value = sourcePending && item.stage ? "—"
        : typeof item.value === "number" ? number.format(item.value) : escapeHTML(item.value);
      const finalBacklog = /^backlog (atual|final|ao fim do período)$/i.test(item.label || "");
      const label = finalBacklog ? "Backlog final" : item.label;
      const note = sourcePending && item.stage ? "Dados ainda não sincronizados" : (item.unit || "tickets");
      const action = item.stage ? `data-kpi-stage="${escapeHTML(item.stage)}" role="button" tabindex="0" aria-label="Filtrar tickets na etapa ${escapeHTML(item.label)}"` : "";
      const title = finalBacklog ? "Estoque no final do período selecionado." : item.title || "Indicador calculado no espelho local do OTRS.";
      return `<article class="kpi-card dash-card cockpit-operational-kpi${item.stage ? " is-drillable" : ""}" ${action} title="${escapeHTML(title)}"><span class="kpi-label">${escapeHTML(label)}</span><strong class="kpi-value">${value}</strong><small class="kpi-note">${escapeHTML(note)}</small></article>`;
    }).join("");
  }
  function renderSourceSync(sourceSync) {
    const badge = page.querySelector("[data-source-badge]");
    const label = badge?.querySelector("[data-source-badge-label]");
    if (!badge || !label || !sourceSync) return;
    const available = sourceSync.available === true;
    badge.classList.toggle("is-unavailable", !available);
    if (available && sourceSync.last_sync_at) {
      const at = new Date(sourceSync.last_sync_at);
      const timestamp = new Intl.DateTimeFormat("pt-BR", {
        day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit", timeZone: "America/Sao_Paulo",
      }).format(at);
      label.textContent = `Última sincronização OTRS · ${timestamp}`;
      badge.title = "Dados locais do OTRS sincronizados para as filas do Cockpit.";
    } else {
      label.textContent = "Dados OTRS ainda não sincronizados";
      badge.title = "Os números das etapas de Cancelamentos só serão exibidos após a primeira sincronização completa.";
    }
  }
  function renderSecondaryKpis(items) {
    const container = page.querySelector("[data-secondary-kpis]");
    container.hidden = !items?.length;
    container.innerHTML = (items || []).map(item => {
      const isNet = item.label === "Saldo dos eventos visíveis" && typeof item.value === "number";
      const isAverage = item.label === "Tempo médio de resolução";
      const isOlderThanDay = item.label === "Tickets acima de 24h";
      const label = isOlderThanDay ? "Acima de 24h" : item.label;
      let value = item.value == null ? "—" : typeof item.value === "number" ? number.format(item.value) : escapeHTML(item.value);
      let note = item.unit || "tickets";
      let title = item.help_text || "";
      if (isNet) {
        const amount = number.format(Math.abs(item.value));
        value = item.value > 0 ? `↑ +${amount}` : item.value < 0 ? `↓ ${amount}` : "0";
        note = item.value > 0 ? `${amount} tickets a mais no saldo do fluxo` : item.value < 0 ? `${amount} tickets a menos no saldo do fluxo` : "Novos e Resolvidos em equilíbrio";
      } else if (isAverage) {
        note = "resolução";
        if (typeof item.value === "number") {
          title = `${number.format(item.value)} horas`;
          if (item.value > 48) {
            const minutes = Math.round(item.value * 60);
            let days = Math.floor(minutes / 1440), hours = Math.round((minutes - days * 1440) / 60);
            if (hours === 24) { days += 1; hours = 0; }
            value = `${days}d${hours ? ` ${hours}h` : ""}`;
          }
        }
      } else if (isOlderThanDay) note = "tickets ativos";
      const info = item.help_text ? `<button type="button" class="cockpit-kpi-info" title="${escapeHTML(item.help_text)}" aria-label="${escapeHTML(item.help_text)}">i</button>` : "";
      return `<div class="cockpit-secondary-kpi"${title ? ` title="${escapeHTML(title)}"` : ""}><span>${escapeHTML(label)}${info}</span><strong>${value}</strong><small>${escapeHTML(note)}</small></div>`;
    }).join("");
  }
  function renderOperationalChart(data) {
    const operational = data.operational || {}, rows = operational.series || [];
    const mode = queryState.chart_view || "all", general = data.scope === "general";
    const originMode = general && queryState.chart_origin === "origin";
    const history = operational.backlog || {}, backlogAvailable = history.available === true;
    const originControl = page.querySelector("[data-chart-origin-control]");
    if (originControl) {
      originControl.hidden = !general;
      originControl.querySelectorAll("[data-chart-origin]").forEach(button => {
        button.setAttribute("aria-pressed", String(button.dataset.chartOrigin === (originMode ? "origin" : "consolidated")));
      });
    }
    const subtitle = {
      all: "Novos e Resolvidos por intervalo; snapshots de backlog no horário observado.",
      created: general && originMode ? "Entradas por intervalo, divididas por origem." : "Tickets novos em cada intervalo do per\u00edodo.",
      closed: general && originMode ? "Resolu\u00e7\u00f5es por intervalo, divididas por origem." : "Tickets resolvidos em cada intervalo do per\u00edodo.",
      backlog: "Snapshots FULL do estoque; cada ponto informa a data e hora observadas.",
    }[mode];
    page.querySelector("[data-operational-subtitle]").textContent = subtitle;
    page.querySelectorAll("[data-chart-view]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.chartView === mode));
    });
    const chartNote = message => message ? `<p class="cockpit-history-note" role="status">${escapeHTML(message)}</p>` : "";
    if (!rows.length) {
      return `<article class="cockpit-ops-chart" data-mode="${mode}">${chartNote("Nenhum ticket encontrado neste per\u00edodo.")}</article>`;
    }
    if (mode === "backlog" && !backlogAvailable) {
      return `<article class="cockpit-ops-chart" data-mode="backlog">${chartNote(history.message || "Backlog hist\u00f3rico indispon\u00edvel para este intervalo.")}</article>`;
    }

    let definitions;
    if (general) {
      const entryOriginDefs = [
        ["support_entries", "Novos · Suporte", "support-entry"],
        ["cancellation_entries", "Novos · Cancelamentos", "cancellation-entry"],
      ];
      const closedOriginDefs = [
        ["support_closed", "Resolvidos · Suporte", "support-closed"],
        ["cancellation_closed", "Resolvidos · Cancelamentos", "cancellation-closed"],
      ];
      const entryDefs = originMode ? entryOriginDefs : [["entries", "Novos", "entries"]];
      const closedDefs = originMode ? closedOriginDefs : [["closed", "Resolvidos", "closed"]];
      definitions = mode === "created" ? entryDefs
        : mode === "closed" ? closedDefs
          : mode === "backlog" ? [] : [...entryDefs, ...closedDefs];
    } else {
      const entriesKey = data.scope === "cancellation" ? "cancellation_entries" : "entries";
      const closedKey = data.scope === "cancellation" ? "cancellation_closed" : "closed";
      const closedDefs = [[closedKey, "Resolvidos", "closed"]];
      definitions = mode === "created" ? [[entriesKey, "Novos", "entries"]]
        : mode === "closed" ? closedDefs
          : mode === "backlog" ? []
            : [[entriesKey, "Novos", "entries"], ...closedDefs];
    }
    const flowMax = Math.max(0, ...rows.flatMap(row => definitions.map(([key]) => Number(row[key] || 0))));
    const flowScaleMax = flowMax ? flowMax / .84 : 0;
    const hasFlow = flowMax > 0;
    const backlogValues = rows.map(row => row.backlog).filter(value => value !== null && value !== undefined).map(Number);
    const minBacklog = Math.min(0, ...backlogValues), maxBacklog = Math.max(1, ...backlogValues);
    const backlogSpan = Math.max(1, maxBacklog - minBacklog);
    const backlogLow = Math.max(0, minBacklog - Math.ceil(backlogSpan * .12));
    const backlogHigh = maxBacklog + Math.max(1, Math.ceil(backlogSpan * .12));
    const plotTop = 12, plotBottom = 188, plotHeight = plotBottom - plotTop;
    const toY = value => plotBottom - ((Number(value) - backlogLow) / (backlogHigh - backlogLow)) * plotHeight;
    const points = rows.map((row, index) => row.backlog === null || row.backlog === undefined ? null : {
      x: ((index + Number(row.backlog_position ?? .5)) / rows.length) * 1000,
      y: toY(row.backlog),
    });
    const segments = [];
    let segment = [];
    points.forEach(point => {
      if (point) segment.push(point);
      else if (segment.length) { segments.push(segment); segment = []; }
    });
    if (segment.length) segments.push(segment);
    const linePath = segments.map(part => part.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ")).join(" ");
    const grain = operational.granularity || operational.grain || "day";
    const lineVisible = backlogAvailable && (mode === "all" || mode === "backlog");
    const lastBacklogIndex = points.reduce((last, point, index) => point ? index : last, -1);
    const backlogLabelIndexes = new Set(
      grain === "hour" && mode === "all" && lastBacklogIndex >= 0 ? [lastBacklogIndex]
        : mode === "backlog" && points.filter(Boolean).length <= 8
        ? points.map((point, index) => point ? index : -1).filter(index => index >= 0)
        : mode === "backlog" && lastBacklogIndex >= 0 ? [lastBacklogIndex] : [],
    );
    const lineLabels = lineVisible ? points.map((point, index) => {
      if (!point || !backlogLabelIndexes.has(index)) return "";
      const lastLabel = index === lastBacklogIndex;
      const x = lastLabel ? Math.max(18, point.x - 6) : point.x;
      const anchor = mode === "all" || lastLabel ? "end" : "middle";
      return `<text class="cockpit-ops-line-label" x="${x.toFixed(2)}" y="${Math.max(13, point.y - 8).toFixed(2)}" text-anchor="${anchor}">${number.format(rows[index].backlog)}</text>`;
    }).join("") : "";
    const svg = lineVisible ? `<svg class="cockpit-ops-line" viewBox="0 0 1000 200" preserveAspectRatio="none" aria-hidden="true"><path class="cockpit-ops-line-path" d="${linePath}"></path>${points.map((point, index) => point && (grain !== "hour" || index % 3 === 0 || index === lastBacklogIndex) ? `<circle cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="${grain === "hour" ? "2.2" : "3.2"}"></circle>` : "").join("")}${lineLabels}</svg>` : "";
    const visualHeight = grain === "hour" ? 260 : 268;
    const axisSpanPercent = ((visualHeight - 28) / (visualHeight - 14)) * 100;
    const makeAxis = values => values.map((value, index) => `<span class="cockpit-ops-axis-label" style="--axis-position:${values.length > 1 ? index / (values.length - 1) * axisSpanPercent : 0}%">${number.format(value)}</span>`).join("");
    const flowTicks = definitions.length && hasFlow ? [...new Set([Math.ceil(flowScaleMax), Math.ceil(flowScaleMax / 2), 0])] : [];
    const backlogTicks = lineVisible ? [...new Set([backlogHigh, Math.round((backlogHigh + backlogLow) / 2), backlogLow])] : [];
    const labelStep = rows.length > 18 ? (window.innerWidth < 760 ? 3 : 2) : rows.length > 10 && window.innerWidth < 760 ? 2 : 1;
    const shortLabel = value => {
      if (grain !== "week") return value;
      const match = String(value).match(/^(\d{2})\s+(.+?)(?:\/\d{2})?[\u2013-](\d{2})\s+(.+?)(?:\/\d{2})?$/);
      if (!match) return value;
      const [, firstDay, firstMonth, lastDay, lastMonth] = match;
      return firstMonth === lastMonth ? `${firstDay}\u2013${lastDay} ${lastMonth}` : `${firstDay} ${firstMonth}\u2013${lastDay} ${lastMonth}`;
    };
    const showFlowBarLabels = definitions.length <= 4;
    const pointsHTML = rows.map((row, index) => {
      const bars = definitions.map(([key, label, cls], barIndex) => {
        const value = Number(row[key] || 0);
        const height = value > 0 ? value / flowScaleMax * 100 : 0;
        const valueLift = grain === "hour" ? 2 + (barIndex % 2) * 8 : 2;
        const groupStart = general && originMode && mode === "all" && grain !== "hour" && barIndex === 2 ? " is-group-start" : "";
        return `<span class="cockpit-ops-bar-item${groupStart}" style="--bar-height:${height}%;--value-lift:${valueLift}px" aria-hidden="true">${value > 0 && showFlowBarLabels ? `<span class="cockpit-ops-bar-value">${number.format(value)}</span>` : ""}<i class="cockpit-ops-bar ${cls}"></i></span>`;
      }).join("");
      const net = Number(row.balance || 0), netLabel = `${net > 0 ? "+" : ""}${number.format(net)}`;
      const rowBacklog = row.backlog !== null && row.backlog !== undefined ? number.format(row.backlog) : "\u2014";
      const entryOriginDetail = general ? `<small class="cockpit-tooltip-detail">Suporte: ${number.format(row.support_entries)}</small><small class="cockpit-tooltip-detail">Cancelamentos: ${number.format(row.cancellation_entries)}</small>` : "";
      const closedOriginDetail = general ? `<small class="cockpit-tooltip-detail">Suporte: ${number.format(row.support_closed)}</small><small class="cockpit-tooltip-detail">Cancelamentos: ${number.format(row.cancellation_closed)}</small>` : "";
      const backlogTooltip = row.backlog !== null && row.backlog !== undefined
        ? `<small>Backlog observado: ${rowBacklog}</small><small>Snapshot FULL: ${escapeHTML(row.backlog_observed_label || "data indisponível")}</small>`
        : `<small>Sem snapshot FULL neste intervalo.</small>`;
      const tooltipLines = mode === "all"
        ? `<small class="entries">Novos: ${number.format(row.entries)}</small>${entryOriginDetail}<small class="closed">Resolvidos: ${number.format(row.closed)}</small>${closedOriginDetail}<small>Saldo dos eventos visíveis: ${escapeHTML(netLabel)}</small>${backlogAvailable ? backlogTooltip : `<small>${escapeHTML(history.message || "Backlog hist\u00f3rico indispon\u00edvel para este intervalo.")}</small>`}`
        : mode === "created" ? `<small class="entries">Novos: ${number.format(row.entries)}</small>${entryOriginDetail}`
          : mode === "closed" ? `<small class="closed">Resolvidos: ${number.format(row.closed)}</small>${closedOriginDetail}`
            : backlogTooltip;
      const originAccessible = general
        ? mode === "all" ? ` Suporte: ${number.format(row.support_entries)} novos, ${number.format(row.support_closed)} resolvidos. Cancelamentos: ${number.format(row.cancellation_entries)} novos, ${number.format(row.cancellation_closed)} resolvidos.`
          : mode === "created" ? ` Suporte: ${number.format(row.support_entries)} novos. Cancelamentos: ${number.format(row.cancellation_entries)} novos.`
            : mode === "closed" ? ` Suporte: ${number.format(row.support_closed)} resolvidos. Cancelamentos: ${number.format(row.cancellation_closed)} resolvidos.` : ""
        : "";
      const accessibleBacklog = row.backlog !== null && row.backlog !== undefined
        ? ` Backlog observado: ${rowBacklog} em ${row.backlog_observed_label || "data indisponível"}.`
        : " Sem snapshot de backlog neste intervalo.";
      const accessible = mode === "all"
        ? `${row.label}. Novos: ${number.format(row.entries)}. Resolvidos: ${number.format(row.closed)}. Saldo dos eventos visíveis: ${netLabel}.${backlogAvailable ? accessibleBacklog : " Backlog histórico indisponível."}${originAccessible}`
        : mode === "created" ? `${row.label}. Novos: ${number.format(row.entries)}.${originAccessible}`
          : mode === "closed" ? `${row.label}. Resolvidos: ${number.format(row.closed)}.${originAccessible}`
            : `${row.label}.${accessibleBacklog}`;
      const label = index % labelStep === 0 || (grain !== "hour" && index === rows.length - 1) ? row.label : "";
      return `<button type="button" class="cockpit-ops-point" data-chart-point aria-label="${escapeHTML(accessible)}"><span class="cockpit-ops-bars">${bars}</span><span class="cockpit-ops-x-label">${escapeHTML(label ? shortLabel(label) : "")}</span><span class="cockpit-point-tooltip" role="tooltip"><strong>${escapeHTML(row.label)}</strong>${tooltipLines}</span></button>`;
    }).join("");
    const legendItems = definitions.map(([, label, cls]) => `<span><i class="${cls}"></i>${escapeHTML(label)}</span>`);
    if (lineVisible) legendItems.push('<span><i class="backlog-line-key"></i>Backlog (snapshot FULL)</span>');
    const note = mode !== "backlog" && history.flow_message
      ? history.flow_message
      : !hasFlow && mode !== "backlog" ? `Nenhum movimento de ${mode === "created" ? "entrada" : mode === "closed" ? "resolu\u00e7\u00e3o" : "novos ou resolvidos"} neste per\u00edodo.` : "";
    return `<article class="cockpit-ops-chart" data-mode="${mode}" data-grain="${escapeHTML(grain)}" style="--ops-plot-height:${visualHeight}px"><div class="cockpit-ops-legend" aria-label="S\u00e9ries do gr\u00e1fico">${legendItems.join("")}</div>${chartNote(note)}<div class="cockpit-ops-chart-frame"><div class="cockpit-ops-axis cockpit-ops-axis-left" aria-hidden="true">${makeAxis(flowTicks)}</div><div class="cockpit-ops-plot-area"><div class="cockpit-ops-grid-lines" aria-hidden="true"></div>${svg}<div class="cockpit-ops-point-grid" style="--series-count:${rows.length}" role="group" aria-label="Pontos do per\u00edodo">${pointsHTML}</div></div><div class="cockpit-ops-axis cockpit-ops-axis-right" aria-hidden="true">${makeAxis(backlogTicks)}</div></div></article>`;
  }

  function renderAgingChart(data) {
    const operational = data.operational || {}, rows = operational.aging || [], general = data.scope === "general";
    if (!rows.length) return '<p class="cockpit-empty">Sem tickets ativos para calcular aging.</p>';
    if (rows.every(row => !Number(row.total))) return '<p class="cockpit-empty">Nenhum ticket ativo no contexto selecionado.</p>';
    const max = Math.max(1, ...rows.map(row => Number(row.total || 0)));
    const items = rows.map(row => {
      const supportWidth = Math.min(100, Number(row.support || 0) / max * 100);
      const cancellationWidth = Math.min(100 - supportWidth, Number(row.cancellation || 0) / max * 100);
      const valueText = general ? `Suporte ${number.format(row.support)} · Cancelamentos ${number.format(row.cancellation)}` : number.format(row.total);
      return `<li aria-label="${escapeHTML(row.label)}: ${escapeHTML(valueText)}"><strong>${escapeHTML(row.label)}</strong><span class="cockpit-aging-track" title="${escapeHTML(valueText)}"><i class="support" style="--bar-width:${supportWidth}%"></i><i class="cancellation" style="--bar-width:${cancellationWidth}%"></i></span><span>${number.format(row.total)}</span></li>`;
    }).join("");
    const legend = general ? '<div class="cockpit-ops-legend cockpit-aging-legend"><span><i class="support-entry"></i>Suporte</span><span><i class="cancellation-entry"></i>Cancelamentos</span></div>' : "";
    return `<div class="cockpit-aging-chart">${legend}<ul>${items}</ul></div>`;
  }
  function renderSeries(data) {
    const specs = data.chart_specs || [];
    const overview = specs.find(spec => spec.type === "temporal_overview");
    const dimensions = specs.filter(spec => spec.type !== "temporal_overview" && spec.type !== "monthly_calendar");
    const overviewGrid = page.querySelector("[data-overview-grid]");
    const dimensionGrid = page.querySelector("[data-dimension-grid]");
    overviewGrid.innerHTML = renderOperationalChart(data);
    page.querySelector("[data-aging-chart]").innerHTML = renderAgingChart(data);
    dimensionGrid.innerHTML = dimensions.length ? dimensions.map(renderDimension).join("") : '<p class="cockpit-empty">Não há dimensões complementares relevantes para esta consulta.</p>';
    renderCalendar(data.temporal);
    const base = page.querySelector("[data-period-semantics]");
    const baseDescription = data.metric === "canceled_tickets" ? "Período baseado no cancelamento dos tickets." : data.metric === "backlog" ? "Snapshot atual de tickets OPEN e PENDING." : ["resolved_tickets", "closed_tickets", "resolved_closed_tickets"].includes(data.metric) ? "Período baseado na resolução dos tickets." : "Período baseado na criação dos tickets.";
    base.title = baseDescription;
    base.setAttribute("aria-label", baseDescription);
    const metricTitles = { tickets: "Tickets criados", open_tickets: "Tickets em atendimento", pending_tickets: "Tickets pendentes", scheduled_tickets: "Tickets agendados", resolved_tickets: "Tickets resolvidos", closed_tickets: "Tickets resolvidos", resolved_closed_tickets: "Tickets resolvidos", canceled_tickets: "Tickets cancelados", links_affected: "Links afetados", recurrences: "Links reincidentes", backlog: "Backlog" };
    const facets = [
      ["provider", "provedor"], ["termination_reason", "motivo"], ["stage", "etapa"], ["origin", "origem"], ["uf", "UF"],
    ];
    const activeFacet = facets.find(([key]) => queryState.filters[key] && queryState.filters[key] !== "all");
    const facetLabel = activeFacet ? form.elements.namedItem(activeFacet[0]).selectedOptions[0]?.textContent : "";
    const title = `${metricTitles[data.metric] || "Análise operacional"}${activeFacet && facetLabel ? ` · ${facetLabel}` : ""}`;
    page.querySelector("[data-analysis-title]").textContent = title;
    const snapshot = data.metric === "backlog";
    page.querySelector("[data-current-period]").textContent = snapshot ? dateLabel(data.end_date || queryState.end) : `${dateLabel(data.start_date || queryState.start)} → ${dateLabel(data.end_date || queryState.end)}`;
    page.querySelector("[data-current-analysis]").title = `${title}. ${baseDescription}`;
    const comparison = data.comparison, node = page.querySelector("[data-comparison]");
    if (!comparison) { node.replaceChildren(); node.removeAttribute("title"); }
    else {
      const previous = comparison.previous_start && comparison.previous_end ? `${dateLabel(comparison.previous_start)} a ${dateLabel(comparison.previous_end)}` : monthLabel(queryState.compare_to);
      const change = comparison.percentage_change === null ? `${number.format(comparison.absolute_change)} tickets` : `${percentage.format(comparison.percentage_change)}%`;
      node.replaceChildren();
      const value = document.createElement("strong"), label = document.createElement("small");
      value.textContent = `${comparison.direction === "up" ? "↑" : comparison.direction === "down" ? "↓" : "·"} ${change}`;
      label.textContent = "vs período anterior";
      node.append(value, label); node.title = previous;
    }
  }
  function renderOverview(spec, grain) {
    const values = spec.values || [], labels = spec.labels || [], details = spec.details || [];
    const max = Math.max(1, ...values.map(Number));
    const monthly = grain === "month";
    const points = values.map((value, index) => {
      const item = details[index] || { label: labels[index], value, share: 0 };
      const month = monthly && /^\d{4}-\d{2}$/.test(item.key || "") ? `data-chart-month="${escapeHTML(item.key)}"` : "";
      const clickable = month ? "is-navigable" : "";
      const share = `${number.format(item.share || 0)}% do período`;
      const previous = item.previous_value;
      const comparison = previous === null || previous === undefined ? "" : previous === 0 ? "Novo no período" : `${number.format(value - previous)} vs período anterior`;
      const accessible = `${item.label}: ${number.format(value)} ${spec.unit || "tickets"}; ${share}${month ? "; selecionar este mês no calendário" : ""}`;
      return `<button type="button" class="cockpit-column cockpit-overview-point ${clickable}" ${month} data-chart-point aria-label="${escapeHTML(accessible)}"><span class="cockpit-bar-value">${number.format(value)}</span><i style="--bar-height:${Math.max(2, Number(value) / max * 100)}%"></i><span>${escapeHTML(labels[index])}</span><span class="cockpit-point-tooltip" role="tooltip"><strong>${escapeHTML(item.label)}</strong><span>${number.format(value)} ${escapeHTML(spec.unit || "tickets")}</span><small>${share}</small>${comparison ? `<small>${escapeHTML(comparison)}</small>` : ""}</span></button>`;
    }).join("");
    return `<article class="cockpit-chart-card cockpit-overview-card" data-chart-key="${escapeHTML(spec.key)}"><header><h3>${escapeHTML(spec.title)}</h3><p>${escapeHTML(spec.subtitle || "")}${monthly ? " · Selecione um mês para abrir o calendário" : ""}</p></header><div class="cockpit-chart" aria-label="${escapeHTML(spec.title)}"><div class="cockpit-bars" role="list" style="--series-count:${values.length}">${points}</div></div></article>`;
  }
  function renderDimension(spec) {
    const details = spec.details || [], unit = spec.unit || "tickets";
    const rows = details.map((item, index) => {
      const width = Math.max(item.value ? 2 : 0, Math.min(100, Number(item.value) / Math.max(1, ...details.map(row => Number(row.value))) * 100));
      const accessible = `${item.label}: ${number.format(item.value)} ${unit}, ${number.format(item.share || 0)}%`;
      if (spec.type === "ranking") {
        let label = escapeHTML(item.label);
        let logo = "";
        if (spec.dimension === "provider") {
          logo = item.logo_url
            ? `<span class="cockpit-provider-logo"><img src="${escapeHTML(item.logo_url)}" alt="" loading="lazy"></span>`
            : `<span class="cockpit-provider-logo" aria-hidden="true">${escapeHTML(item.logo_initials || "?")}</span>`;
        }
        if (spec.dimension === "uf") {
          const code = stateCode(item.key || item.label);
          const flag = code
            ? `<img class="cockpit-uf-flag" src="/static/imagens/bandeiras/estados/${code.toLowerCase()}.svg" alt="" loading="lazy">`
            : "";
          label = `${flag}<span class="cockpit-uf-code">${escapeHTML(code || item.label)}</span>`;
        }
        return `<li title="${escapeHTML(accessible)}" aria-label="${escapeHTML(accessible)}"><span class="cockpit-rank-position">${index + 1}</span>${logo}<strong>${label}</strong><span>${number.format(item.value)} ${escapeHTML(unit)}</span><small>${number.format(item.share || 0)}%</small></li>`;
      }
      return `<li title="${escapeHTML(accessible)}"><strong>${escapeHTML(item.label)}</strong><span class="cockpit-horizontal-track"><i style="--bar-width:${width}%"></i></span><span>${number.format(item.value)} ${escapeHTML(unit)}</span><small>${number.format(item.share || 0)}%</small></li>`;
    }).join("");
    const listClass = spec.type === "ranking" ? `cockpit-provider-ranking${spec.dimension === "provider" ? " is-provider" : ""}` : "cockpit-horizontal-list";
    const tag = spec.type === "ranking" ? "ol" : "ul";
    return `<article class="cockpit-chart-card cockpit-dimension-card ${spec.type === "ranking" ? "cockpit-ranking-card" : ""}" data-chart-key="${escapeHTML(spec.key)}" data-chart-type="${escapeHTML(spec.type)}"><header><h3>${escapeHTML(spec.title)}</h3><p>${escapeHTML(spec.subtitle || "")}</p></header><${tag} class="${listClass}">${rows || `<li>Nenhum resultado para ${escapeHTML(spec.title.toLowerCase())}.</li>`}</${tag}></article>`;
  }
  function renderCalendar(temporal) {
    const section = page.querySelector("[data-calendar]"), select = page.querySelector("[data-month-select]"), grid = page.querySelector("[data-calendar-grid]");
    if (!temporal || !temporal.selected_month) { section.hidden = true; return; }
    section.hidden = false;
    page.dataset.calendarUnit = temporal.unit || "tickets";
    queryState.selected_month = temporal.selected_month;
    const selected = (temporal.available_months || []).find(item => item.key === temporal.selected_month);
    const summary = temporal.month_summary || {}, monthTitle = temporal.selected_month_label || selected?.label || monthLabel(temporal.selected_month);
    page.querySelector("[data-calendar-title]").textContent = `Detalhe de ${monthTitle}`;
    page.querySelector("[data-calendar-subtitle]").textContent = queryState.scope === "cancellation" ? "Tickets do processo de cancelamento criados em cada dia." : queryState.scope === "general" ? "Entradas do suporte e do processo de cancelamento por dia." : `Distribui\u00e7\u00e3o di\u00e1ria de ${temporal.unit || "tickets"} da consulta.`;
    select.replaceChildren(...(temporal.available_months || []).map(item => new Option(item.label, item.key)));
    select.value = temporal.selected_month;
    const peak = summary.peak_day ? `${dateLabel(summary.peak_day)} · ${number.format(summary.peak_value)} ${temporal.unit || "tickets"}` : "Sem ocorrências";
    const indicators = [
      [`${number.format(summary.total || 0)} ${temporal.unit || "tickets"}`, "no mês selecionado"],
      [number.format(summary.average_per_day || 0), `média por dia · ${number.format(summary.days_in_query_period || 0)} dias no período`],
      [peak, `${number.format(summary.days_with_occurrence || 0)} dias com ocorrência`],
    ];
    page.querySelector("[data-calendar-summary]").innerHTML = indicators.map(([value, label]) => `<div><strong>${escapeHTML(value)}</strong><small>${escapeHTML(label)}</small></div>`).join("");
    const weekdays = temporal.weekdays || ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
    const skeleton = `<div class="cockpit-calendar-skeleton" data-calendar-skeleton aria-hidden="true">${"<span></span>".repeat(42)}</div>`;
    grid.innerHTML = `<div class="cockpit-calendar-weekdays" role="row">${weekdays.map(day => `<span role="columnheader">${escapeHTML(day)}</span>`).join("")}</div>${(temporal.calendar_weeks || []).map((week, weekIndex) => `<div class="cockpit-calendar-week" role="row">${cellsForWeek(week, weekIndex, temporal, monthTitle)}</div>`).join("")}${skeleton}`;
    section.dataset.calendarLoading = "false";
  }
  function cellsForWeek(week, _weekIndex, temporal, monthTitle) {
    return week.map(day => {
      if (!day.inside_month) return '<span class="cockpit-calendar-day is-outside" aria-hidden="true"></span>';
      const count = Number(day.value || 0), strength = `${4 + Number(day.intensity || 0) * 19}%`;
      const label = day.accessible_label || `${day.day} de ${monthTitle}, ${number.format(count)} ${temporal.unit || "tickets"}`;
      const title = `${label} · ${number.format(day.share_of_month || 0)}% do mês`;
      const content = `<span class="cockpit-calendar-date">${day.day}</span><strong>${number.format(count)}</strong><small>${escapeHTML(temporal.unit || "tickets")}</small>`;
      return day.inside_query_period
        ? `<button type="button" class="cockpit-calendar-day${count ? " has-data" : " is-empty"}" data-calendar-day="${escapeHTML(day.date)}" data-day-value="${count}" style="--day-strength:${strength}" title="${escapeHTML(title)}" aria-label="${escapeHTML(label)}">${content}</button>`
        : `<span class="cockpit-calendar-day is-disabled" title="Fora do período analisado" aria-label="${escapeHTML(label)}">${content}</span>`;
    }).join("");
  }
  function renderPagination(data) {
    const nav = page.querySelector("[data-detail-pagination]"), current = Number(data.detail_page || 1), pages = Number(data.detail_pages || 1), total = Number(data.detail_total || 0);
    nav.innerHTML = `<span>${number.format(total)} grupos · Página ${number.format(current)} de ${number.format(pages)}</span><div><button type="button" data-detail-page="${Math.max(1, current - 1)}" ${current <= 1 ? "disabled" : ""}>Anterior</button><button type="button" data-detail-page="${Math.min(pages, current + 1)}" ${current >= pages ? "disabled" : ""}>Próxima</button></div>`;
  }
  function renderBreakdown(data) {
    const detail = data.detail || [], groupName = DIMENSIONS[queryState.group_by] || "dimensão";
    page.querySelector("[data-breakdown-title]").textContent = `Distribuição completa por ${groupName.toLowerCase()}`;
    page.querySelector("[data-detail-dimension]").textContent = groupName;
    page.querySelector("[data-detail-metric]").textContent = data.metric_label;
    const unit = ["links_affected", "recurrences"].includes(data.metric) ? "links" : "tickets";
    page.querySelector("[data-detail-groups]").textContent = `${number.format(data.detail_total)} grupos`;
    page.querySelector("[data-detail-total]").textContent = `${number.format(data.detail_total)} grupos · ${number.format(data.value)} ${unit}`;
    page.querySelector("[data-detail-body]").innerHTML = detail.length ? detail.map(row => `<tr><td>${escapeHTML(row.dimension)}</td><td>${number.format(row.value)}</td><td>${number.format(row.share)}%</td></tr>`).join("") : '<tr><td colspan="3">Nenhum resultado para exibir.</td></tr>';
    const quality = data.provider_quality || {}, qualityNote = page.querySelector("[data-provider-quality]");
    if (queryState.group_by === "provider" && quality.unresolved) {
      const causes = quality.unresolved_causes || {};
      qualityNote.textContent = `${number.format(quality.unresolved)} tickets sem provedor identificado; cobertura ${number.format(quality.coverage_percent)}%. Causas: sem relação ${number.format(causes.no_config_item || 0)}, sem espelho ${number.format(causes.missing_mirror || 0)}, link inválido ${number.format(causes.invalid_link || 0)}, link sem provedor ${number.format(causes.link_without_provider || 0)}.`;
    } else qualityNote.textContent = "";
    renderPagination(data);
  }
  function renderTickets(data) {
    const records = data.tickets || [], body = page.querySelector("[data-ticket-body]");
    const sourceTag = row => `<span class="cockpit-source-tag" title="${escapeHTML(row.source === "OTRS + CMDB" ? "Ticket OTRS com vínculo CMDB resolvido" : "Dados do espelho OTRS")}">${row.source === "OTRS + CMDB" ? "OTRS + CMDB" : "OTRS"}</span>`;
    body.innerHTML = records.length ? records.map(row => `<tr data-ticket-row="${row.id}"><td><button type="button" class="cockpit-ticket-link" data-ticket-open="${row.id}">${escapeHTML(row.ticket)}</button><span class="cockpit-ticket-source">${sourceTag(row)}</span></td><td>${escapeHTML(row.base_date)}</td><td>${escapeHTML(row.created_at)}</td><td>${escapeHTML(row.resolved_at)}</td><td>${escapeHTML(row.status)}</td><td>${escapeHTML(row.reason)}</td><td>${escapeHTML(row.provider)}</td><td>${escapeHTML(row.uf)}</td><td>${escapeHTML(row.priority)}</td><td>${escapeHTML(row.sla)}</td></tr>`).join("") : '<tr><td colspan="10">Nenhum ticket corresponde a esta consulta.</td></tr>';
    const search = page.querySelector("[data-ticket-search]");
    if (search.value !== (data.ticket_search || "")) search.value = data.ticket_search || "";
    page.querySelector("[data-ticket-size]").value = String(data.ticket_page_size || 25);
    const dayNote = page.querySelector("[data-ticket-day-active]");
    dayNote.hidden = !data.ticket_day;
    if (data.ticket_day) {
      temporaryTicketDay = data.ticket_day;
      page.querySelector("[data-ticket-day-label]").textContent = `Visualização temporária de ${dateLabel(data.ticket_day)} · ${number.format(data.ticket_day_total || 0)} tickets. A análise principal continua com ${number.format(data.ticket_result_total)} tickets.`;
      page.querySelector("[data-ticket-count]").textContent = `${number.format(data.ticket_day_total || 0)} tickets no dia`;
    } else {
      temporaryTicketDay = "";
      page.querySelector("[data-ticket-count]").textContent = `${number.format(data.ticket_result_total)} tickets`;
    }
    const current = Number(data.ticket_page || 1), pages = Number(data.ticket_pages || 1), nav = page.querySelector("[data-ticket-pagination]");
    nav.innerHTML = `<span>Página ${number.format(current)} de ${number.format(pages)}</span><div><button type="button" data-ticket-page="${Math.max(1, current - 1)}" ${current <= 1 ? "disabled" : ""}>Anterior</button><button type="button" data-ticket-page="${Math.min(pages, current + 1)}" ${current >= pages ? "disabled" : ""}>Próxima</button></div>`;
    page._cockpitTicketRows = new Map(records.map(row => [String(row.id), row]));
  }
  function openTicket(id) {
    const row = page._cockpitTicketRows?.get(String(id)), dialog = page.querySelector("[data-ticket-drawer]");
    if (!row || !dialog) return;
    dialog.querySelector("[data-drawer-title]").textContent = `Ticket ${row.ticket}`;
    dialog.querySelector("[data-drawer-status]").textContent = row.status;
    const details = [["Criado em", row.created_at], ["Alterado em", row.changed_at], ["Base da an\u00e1lise", row.base_date], ["Fila OTRS", row.queue], ["Origem", row.origin], ["Etapa do cancelamento", row.operational_stage_label], ["Resolvido em", row.resolved_at], ["Origem da data de resolu\u00e7\u00e3o", row.resolved_at_source], ["\u00daltima transi\u00e7\u00e3o terminal registrada", row.terminal_at], ["Cancelado em", row.canceled_at], ["Motivo de encerramento", row.reason], ["Prioridade", row.priority], ["SLA", row.sla]];
    dialog.querySelector("[data-drawer-fields]").innerHTML = details.map(([label,value]) => `<div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value || "—")}</dd></div>`).join("");
    dialog.querySelector("[data-drawer-links]").innerHTML = row.links?.length ? row.links.map(link => `<article><strong>ConfigItem ${escapeHTML(link.config_item_id)}</strong><dl><div><dt>Number</dt><dd>${escapeHTML(link.number || "—")}</dd></div><div><dt>Designação</dt><dd>${escapeHTML(link.designation || "—")}</dd></div><div><dt>Circuito</dt><dd>${escapeHTML(link.circuit || "—")}</dd></div><div><dt>Provedor</dt><dd>${escapeHTML(link.provider || "Não identificado")}</dd></div><div><dt>UF</dt><dd>${escapeHTML(link.uf || "—")}</dd></div></dl></article>`).join("") : "<p>Sem vínculo CMDB disponível.</p>";
    dialog.querySelector("[data-drawer-evidence]").innerHTML = (row.evidence || []).map(item => `<li>${escapeHTML(item)}</li>`).join("");
    dialog.showModal();
  }
  function renderActive() {
    const chips = FILTER_KEYS.filter(key => queryState.filters[key] !== "all").map(key => `${FILTER_LABELS[key]}: ${form.elements.namedItem(key).selectedOptions[0]?.textContent || queryState.filters[key]}`);
    if (queryState.compare_to) chips.push(`Comparado a ${monthLabel(queryState.compare_to)}`);
    const preset = activePreset ? ` \u00b7 ${activePreset.label}${activePreset.modified ? " \u00b7 modificada" : ""}` : "";
    const activeFilters = page.querySelector("[data-active-filters]");
    if (activeFilters) activeFilters.textContent = chips.join(" \u00b7 ") + preset;
    const summary = page.querySelector("[data-query-summary]");
    const summaryContent = chips.map(chip => `<span class="cockpit-summary-chip">${escapeHTML(chip)}</span>`).join("") + (preset ? `<span class="cockpit-summary-chip">${escapeHTML(preset.trim().replace(/^\u00b7\s*/, ""))}</span>` : "");
    summary.innerHTML = summaryContent || '<span class="cockpit-summary-empty">Nenhum filtro adicional</span>';
    summary.title = chips.join(" \u00b7 ") + preset;
    page.querySelectorAll("[data-quick]").forEach(button => {
      const active = activePreset?.key === button.dataset.quick;
      button.classList.toggle("is-active", Boolean(active));
      button.setAttribute("aria-pressed", String(Boolean(active)));
      button.dataset.modified = String(Boolean(active && activePreset.modified));
    });
  }
  function setLoading(loading) {
    page.dataset.loading = String(loading);
    page.querySelector("[data-status]").textContent = loading ? "Aplicando filtros..." : "";
    const apply = form.querySelector('[type="submit"]');
    if (apply) { apply.disabled = loading; apply.textContent = loading ? "Aplicando filtros..." : "Aplicar análise"; }
  }
  function prepareLoading() {
    if (page.dataset.resultsReady !== "true") {
      page.dataset.initialLoading = "true";
      return;
    }
    page.querySelector("[data-results-loading]").hidden = false;
    const calendar = page.querySelector("[data-calendar]");
    if (calendar && !calendar.hidden) calendar.dataset.calendarLoading = "true";
  }
  async function refresh() {
    const requestId = ++latestRequest;
    controller?.abort(); controller = new AbortController();
    page.querySelector("[data-error]").hidden = true;
    setLoading(true); prepareLoading(); writeURL(); renderActive();
    try {
      const payload = queryPayload();
      if (temporaryTicketDay) payload.ticket_day = temporaryTicketDay;
      const result = await post(page.dataset.queryUrl, payload, controller.signal);
      if (requestId !== latestRequest) return;
      queryState.include_cancelled = result.normalized_query?.include_cancelled === true;
      queryState.include_improcedente = result.normalized_query?.include_improcedente === true;
      form.elements.include_cancelled.checked = queryState.include_cancelled;
      form.elements.include_improcedente.checked = queryState.include_improcedente;
      const normalizedReason = result.normalized_query?.filters?.termination_reason;
      if (normalizedReason && queryState.filters.termination_reason !== "all") {
        queryState.filters.termination_reason = normalizedReason;
      }
      renderOptions(result.options);
      latestAnalytics = result;
      renderSourceSync(result.source_sync);
      renderOperationalKpis(result.operational?.primary_kpis || result.kpis || []);
      renderSecondaryKpis(result.operational?.secondary_kpis || []);
      renderSeries(result); renderBreakdown(result); renderTickets(result);
      writeURL();
      queryState.detail_page = result.detail_page || 1;
      page.dataset.metric = result.metric; page.dataset.dimension = queryState.group_by; page.dataset.initialLoading = "false"; page.dataset.resultsReady = "true"; renderActive();
    } catch (error) {
      if (error.name !== "AbortError" && requestId === latestRequest) { page.querySelector("[data-error]").hidden = false; page.querySelector("[data-status]").textContent = ""; }
    } finally { if (requestId === latestRequest) { setLoading(false); page.querySelector("[data-results-loading]").hidden = true; page.querySelector("[data-calendar]").dataset.calendarLoading = "false"; } }
  }
  const PRESETS = {
    finance: { metric: "closed_tickets", group_by: "provider", label: "Pend\u00eancia Financeira", filters: { termination_reason: "pendencia_financeira" } },
    recurrence: { metric: "recurrences", group_by: "provider", label: "Reincid\u00eancia" },
    massive: { metric: "closed_tickets", group_by: "provider", label: "Falha Massiva", filters: { termination_reason: "falha_massiva" } },
    improper: { metric: "tickets", group_by: "provider", label: "Improcedentes", filters: { responsibility: "IMPROCEDENTE" } },
    proper: { metric: "tickets", group_by: "provider", label: "Procedentes", filters: { responsibility: "PROCEDENTE" } },
    "high-critical": { metric: "tickets", group_by: "provider", label: "Alto / Cr\u00edtico", filters: { priority: "high-critical" } },
    "over-24h": { metric: "backlog", group_by: "provider", label: "Acima de 24h", filters: { age_bucket: "OVER_24H" } },
    unassigned: { metric: "backlog", group_by: "provider", label: "Sem respons\u00e1vel", filters: { responsible: "unassigned" } },
    open: { metric: "open_tickets", group_by: "provider", label: "Em atendimento" },
    pending: { metric: "pending_tickets", group_by: "provider", label: "Pendentes" },
    resolved: { metric: "resolved_closed_tickets", group_by: "provider", label: "Resolvidos" },
  };
  function presetState(key) {
    const preset = PRESETS[key]; if (!preset) return;
    const { scope, start, end, compare_to, selected_month, chart_view, chart_origin, include_cancelled, include_improcedente } = queryState;
    const keepSelectedMonth = monthExplicitlySelected;
    queryState = cloneDefaults();
    queryState.scope = scope;
    queryState.start = start; queryState.end = end;
    queryState.compare_to = compare_to;
    queryState.selected_month = selected_month;
    queryState.chart_view = chart_view; queryState.chart_origin = chart_origin;
    queryState.include_cancelled = include_cancelled; queryState.include_improcedente = include_improcedente;
    monthExplicitlySelected = keepSelectedMonth;
    queryState.metric = preset.metric; queryState.group_by = preset.group_by;
    if (preset.filters) Object.assign(queryState.filters, preset.filters);
    if (key === "improper") queryState.include_improcedente = true;
    activePreset = { key, label: preset.label, modified: false };
    syncForm(); refresh();
  }
  function updatePresetStatus() {
    if (!activePreset) return;
    const preset = PRESETS[activePreset.key];
    activePreset.modified = queryState.metric !== preset.metric || queryState.group_by !== preset.group_by || FILTER_KEYS.some(key => queryState.filters[key] !== (preset.filters?.[key] || "all")) || queryState.start !== DEFAULT_START || queryState.end !== DEFAULT_END || Boolean(queryState.compare_to) || queryState.include_cancelled !== Boolean(preset.include_cancelled) || queryState.include_improcedente !== Boolean(preset.include_improcedente || activePreset.key === "improper");
  }
  async function loadSaved() {
    const data = await post(page.dataset.savedUrl, { action: "list" });
    const box = page.querySelector("[data-saved-list]");
    box.innerHTML = data.views.length ? data.views.map(view => `<article class="cockpit-saved-row"><button type="button" class="cockpit-saved-name" data-open="${view.id}">${escapeHTML(view.name)}</button><div><button type="button" data-rename="${view.id}" data-name="${escapeHTML(view.name)}">Renomear</button><button type="button" data-delete="${view.id}">Excluir</button></div></article>`).join("") : '<p class="cockpit-empty">Ainda n\u00e3o h\u00e1 consultas salvas.</p>';
  }
  function initSavedViews() {
    const panel = page.querySelector("[data-saved-panel]"); if (!panel) return;
    page.querySelector("[data-saved-toggle]").addEventListener("click", async () => { panel.hidden = !panel.hidden; if (!panel.hidden) try { await loadSaved(); } catch (_) { showStatusError("N\u00e3o foi poss\u00edvel carregar suas consultas salvas."); } });
    page.querySelector("[data-saved-close]").addEventListener("click", () => { panel.hidden = true; });
    const saveDialog = page.querySelector("[data-save-dialog]"), saveForm = page.querySelector("[data-save-form]");
    page.querySelector("[data-save-open]").addEventListener("click", () => saveDialog.showModal());
    page.querySelector("[data-dialog-close]").addEventListener("click", () => saveDialog.close());
    saveForm.addEventListener("submit", async event => {
      event.preventDefault();
      try { const configuration = queryPayload(); delete configuration.detail_page; delete configuration.ticket_page; delete configuration.ticket_page_size; delete configuration.ticket_search; await post(page.dataset.savedUrl, { action: "save", name: new FormData(saveForm).get("name"), configuration }); saveDialog.close(); saveForm.reset(); panel.hidden = false; await loadSaved(); }
      catch (_) { showStatusError("N\u00e3o foi poss\u00edvel salvar esta consulta."); }
    });
    const renameDialog = page.querySelector("[data-rename-dialog]"), renameForm = page.querySelector("[data-rename-form]");
    page.querySelector("[data-rename-close]").addEventListener("click", () => renameDialog.close());
    panel.addEventListener("click", async event => {
      const open = event.target.closest("[data-open]"), remove = event.target.closest("[data-delete]"), rename = event.target.closest("[data-rename]");
      try {
        if (open) {
          const { views } = await post(page.dataset.savedUrl, { action: "list" }), saved = views.find(view => String(view.id) === open.dataset.open);
          if (!saved) return;
          queryState = normalizeState({ ...cloneDefaults(), ...saved.configuration, filters: { ...cloneDefaults().filters, ...(saved.configuration.filters || {}) } });
          monthExplicitlySelected = Boolean(queryState.selected_month);
          activePreset = null; syncForm(); panel.hidden = true; refresh();
        } else if (remove) { await post(page.dataset.savedUrl, { action: "delete", id: remove.dataset.delete }); await loadSaved(); }
        else if (rename) { renameForm.elements.id.value = rename.dataset.rename; renameForm.elements.name.value = rename.dataset.name; renameDialog.showModal(); renameForm.elements.name.focus(); }
      } catch (_) { showStatusError("N\u00e3o foi poss\u00edvel atualizar suas consultas salvas."); }
    });
    renameForm.addEventListener("submit", async event => {
      event.preventDefault();
      try { await post(page.dataset.savedUrl, { action: "rename", id: renameForm.elements.id.value, name: renameForm.elements.name.value }); renameDialog.close(); await loadSaved(); }
      catch (_) { showStatusError("N\u00e3o foi poss\u00edvel renomear esta consulta."); }
    });
  }
  function showStatusError(message) { page.querySelector("[data-status]").textContent = message; }
  async function exportCurrent(format) {
    const button = page.querySelector(`[data-export="${format}"]`); button.disabled = true;
    const labels = { pdf: "PDF", csv: "CSV", xlsx: "XLSX" };
    page.querySelector("[data-status]").textContent = `Gerando ${labels[format]}...`;
    let failed = false;
    try {
      const response = await fetch(page.dataset.exportUrl, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, credentials: "same-origin", body: JSON.stringify({ ...queryPayload(), include_all_rows: true, format }) });
      if (!response.ok) { const body = await response.json(); throw new Error(body.error?.message || body.error || "Falha na exporta\u00e7\u00e3o."); }
      const blob = await response.blob(), href = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = href; link.download = response.headers.get("content-disposition")?.match(/filename="?([^";]+)"?/i)?.[1] || `arion-cockpit.${format}`;
      document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(href);
    } catch (_) { failed = true; showStatusError("N\u00e3o foi poss\u00edvel exportar esta an\u00e1lise."); }
    finally { button.disabled = false; if (!failed) page.querySelector("[data-status]").textContent = ""; }
  }
  function setScope(scope) {
    if (!["support", "cancellation", "general"].includes(scope) || scope === queryState.scope) return;
    temporaryTicketDay = "";
    queryState.scope = scope;
    queryState.chart_origin = "consolidated";
    queryState.group_by = { support: "provider", cancellation: "cancellation_stage", general: "operational_origin" }[scope];
    queryState.filters.stage = "all";
    queryState.filters.origin = "all";
    if (scope === "cancellation") queryState.filters.termination_reason = "all";
    queryState.detail_page = 1; queryState.ticket_page = 1; queryState.ticket_search = "";
    activePreset = null; syncForm(); refresh();
  }
  function setChartView(view) {
    if (!["all", "created", "closed", "backlog"].includes(view) || view === queryState.chart_view) return;
    queryState.chart_view = view;
    writeURL();
    if (latestAnalytics) {
      page.querySelector("[data-overview-grid]").innerHTML = renderOperationalChart(latestAnalytics);
    }
  }
  function setChartOrigin(origin) {
    if (!["consolidated", "origin"].includes(origin) || origin === queryState.chart_origin) return;
    queryState.chart_origin = origin;
    writeURL();
    if (latestAnalytics) page.querySelector("[data-overview-grid]").innerHTML = renderOperationalChart(latestAnalytics);
  }
  function clearFilters() { controller?.abort(); temporaryTicketDay = ""; monthExplicitlySelected = false; queryState = cloneDefaults(); activePreset = null; page.querySelector("[data-more-filters]").open = false; syncForm(); refresh(); }

  form.addEventListener("change", () => { readForm(); queryState.detail_page = 1; queryState.ticket_page = 1; syncForm(); updatePresetStatus(); renderActive(); });
  form.addEventListener("submit", event => { event.preventDefault(); temporaryTicketDay = ""; readForm(); queryState.detail_page = 1; queryState.ticket_page = 1; syncForm(); updatePresetStatus(); refresh(); });
  page.querySelector("[data-clear]").addEventListener("click", clearFilters);
  page.querySelector("[data-retry]").addEventListener("click", refresh);
  page.querySelectorAll("[data-scope]").forEach(button => button.addEventListener("click", () => setScope(button.dataset.scope)));
  page.querySelectorAll("[data-chart-view]").forEach(button => button.addEventListener("click", () => setChartView(button.dataset.chartView)));
  page.querySelectorAll("[data-chart-origin]").forEach(button => button.addEventListener("click", () => setChartOrigin(button.dataset.chartOrigin)));
  page.querySelector("[data-breakdown]").addEventListener("change", event => { temporaryTicketDay = ""; queryState.group_by = event.target.value; queryState.detail_page = 1; queryState.ticket_page = 1; updatePresetStatus(); refresh(); });
  page.querySelector("[data-month-select]").addEventListener("change", event => { queryState.selected_month = event.target.value; monthExplicitlySelected = true; refresh(); });
  page.querySelectorAll("[data-quick]").forEach(button => button.addEventListener("click", () => { temporaryTicketDay = ""; presetState(button.dataset.quick); }));
  const dayDialog = page.querySelector("[data-day-dialog]");
  page.querySelector("[data-day-close]").addEventListener("click", () => dayDialog.close());
  page.querySelector("[data-day-view]").addEventListener("click", () => {
    if (!selectedDayForDialog) return;
    temporaryTicketDay = selectedDayForDialog.date; queryState.ticket_page = 1;
    dayDialog.close(); setDetailCollapsed("tickets", false); refresh();
    const ticketsSection = page.querySelector('[data-detail-section="tickets"]');
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    window.setTimeout(() => ticketsSection.scrollIntoView({ behavior, block: "start" }), 190);
  });
  page.querySelector("[data-day-apply]").addEventListener("click", () => {
    if (!selectedDayForDialog) return;
    temporaryTicketDay = ""; queryState.start = selectedDayForDialog.date; queryState.end = selectedDayForDialog.date;
    queryState.selected_month = selectedDayForDialog.date.slice(0, 7); monthExplicitlySelected = true; queryState.ticket_page = 1; queryState.ticket_search = "";
    syncForm(); dayDialog.close(); refresh();
  });
  page.querySelector("[data-ticket-day-clear]").addEventListener("click", () => {
    temporaryTicketDay = ""; queryState.ticket_page = 1; queryState.ticket_search = ""; refresh();
  });
  page.addEventListener("click", event => {
    const stageKpi = event.target.closest("[data-kpi-stage]");
    if (stageKpi) {
      temporaryTicketDay = ""; queryState.metric = "backlog";
      queryState.filters.stage = stageKpi.dataset.kpiStage;
      queryState.detail_page = 1; queryState.ticket_page = 1; queryState.ticket_search = "";
      syncForm(); refresh(); return;
    }
    const monthPoint = event.target.closest("[data-chart-month]"), calendarDay = event.target.closest("[data-calendar-day]");
    if (monthPoint) { queryState.selected_month = monthPoint.dataset.chartMonth; monthExplicitlySelected = true; refresh(); return; }
    if (calendarDay) {
      selectedDayForDialog = { date: calendarDay.dataset.calendarDay, value: Number(calendarDay.dataset.dayValue || 0) };
      page.querySelector("[data-day-title]").textContent = dateLabel(selectedDayForDialog.date);
      const unit = page.dataset.calendarUnit || "tickets";
      page.querySelector("[data-day-count]").textContent = `${number.format(selectedDayForDialog.value)} ${unit} na consulta atual.`;
      dayDialog.showModal(); return;
    }
    const pageButton = event.target.closest("[data-detail-page]"), drill = event.target.closest("[data-drill]");
    if (pageButton) { queryState.detail_page = Number(pageButton.dataset.detailPage) || 1; refresh(); return; }
    const ticket = event.target.closest("[data-ticket-open]");
    if (ticket) { openTicket(ticket.dataset.ticketOpen); return; }
    const ticketPage = event.target.closest("[data-ticket-page]");
    if (ticketPage) { queryState.ticket_page = Number(ticketPage.dataset.ticketPage) || 1; refresh(); return; }
    if (drill) {
      temporaryTicketDay = "";
      const key = queryState.group_by;
      const filterKey = key === "cancellation_stage" ? "stage" : key === "operational_origin" ? "origin" : key;
      if (FILTER_KEYS.includes(filterKey)) queryState.filters[filterKey] = drill.dataset.drill;
      queryState.detail_page = 1; queryState.ticket_page = 1; syncForm(); updatePresetStatus(); refresh();
    }
  });
  page.addEventListener("keydown", event => {
    const stageKpi = event.target.closest("[data-kpi-stage]");
    if (stageKpi && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); stageKpi.click(); }
  });
  let ticketSearchTimer;
  page.querySelector("[data-ticket-search]").addEventListener("input", event => {
    clearTimeout(ticketSearchTimer); ticketSearchTimer = setTimeout(() => { queryState.ticket_search = event.target.value.trim().slice(0, 120); queryState.ticket_page = 1; refresh(); }, 300);
  });
  page.querySelector("[data-ticket-size]").addEventListener("change", event => { queryState.ticket_page_size = Number(event.target.value) || 25; queryState.ticket_page = 1; refresh(); });
  const drawer = page.querySelector("[data-ticket-drawer]");
  drawer.querySelector("[data-drawer-close]").addEventListener("click", () => drawer.close());
  page.addEventListener("pointerover", event => { const point = event.target.closest("[data-chart-point]"); if (point) point.dataset.tooltipOpen = "true"; });
  page.addEventListener("pointerout", event => { const point = event.target.closest("[data-chart-point]"); if (point && !point.contains(event.relatedTarget) && point.dataset.tooltipPinned !== "true") delete point.dataset.tooltipOpen; });
  page.addEventListener("focusin", event => { const point = event.target.closest("[data-chart-point]"); if (point) point.dataset.tooltipOpen = "true"; });
  page.addEventListener("focusout", event => { const point = event.target.closest("[data-chart-point]"); if (point && !point.contains(event.relatedTarget) && point.dataset.tooltipPinned !== "true") delete point.dataset.tooltipOpen; });
  page.addEventListener("click", event => {
    if (event.target.closest("[data-chart-month], [data-calendar-day]")) return;
    const point = event.target.closest("[data-chart-point]");
    page.querySelectorAll("[data-chart-point][data-tooltip-pinned='true']").forEach(item => { if (item !== point) { delete item.dataset.tooltipPinned; delete item.dataset.tooltipOpen; } });
    if (point) { const pinned = point.dataset.tooltipPinned === "true"; point.dataset.tooltipPinned = String(!pinned); point.dataset.tooltipOpen = String(!pinned); }
  });
  const exportOptions = page.querySelector("[data-export-options]"), exportToggle = page.querySelector("[data-export-toggle]");
  exportToggle.addEventListener("click", () => { exportOptions.hidden = !exportOptions.hidden; exportToggle.setAttribute("aria-expanded", String(!exportOptions.hidden)); });
  exportOptions.addEventListener("click", event => { const button = event.target.closest("[data-export]"); if (button) { exportOptions.hidden = true; exportToggle.setAttribute("aria-expanded", "false"); exportCurrent(button.dataset.export); } });
  let chartResizeFrame = 0;
  window.addEventListener("resize", () => {
    if (!latestAnalytics || chartResizeFrame) return;
    chartResizeFrame = window.requestAnimationFrame(() => {
      chartResizeFrame = 0;
      page.querySelector("[data-overview-grid]").innerHTML = renderOperationalChart(latestAnalytics);
    });
  });
  window.addEventListener("popstate", () => { queryState = readURLState(); monthExplicitlySelected = Boolean(queryState.selected_month); activePreset = null; syncForm(); refresh(); });

  syncForm(); initDetailCollapses(); initSavedViews(); refresh();
})();
