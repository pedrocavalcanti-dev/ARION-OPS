(() => {
  const root = document.querySelector("[data-ranking-root]");
  if (!root) return;

  const drawer = root.querySelector("[data-provider-drawer]");
  const backdrop = root.querySelector("[data-drawer-backdrop]");
  const content = root.querySelector("[data-drawer-content]");
  const title = root.querySelector("#drawer-title");
  const subtitle = root.querySelector("[data-drawer-subtitle]");
  const scoreDialog = root.querySelector("[data-score-dialog]");
  const ticketDialog = root.querySelector("[data-ticket-dialog]");
  const ticketTitle = root.querySelector("#ticket-dialog-title");
  const ticketContent = root.querySelector("[data-ticket-content]");
  const formatter = new Intl.NumberFormat("pt-BR");
  const decimal = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  let focusedBeforeDrawer = null;
  let activeProvider = null;
  let currentDetail = null;
  let currentTicketRecords = [];

  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const num = value => formatter.format(Number(value) || 0);
  const pct = value => decimal.format((Number(value) || 0) * 100) + "%";
  const score = value => decimal.format(Number(value) || 0);
  const unit = (value, singular, plural) => Number(value) === 1 ? singular : plural;

  function makeDetailUrl(providerId) {
    const path = (root.dataset.detailUrlTemplate || "").replace(/0\/$/, encodeURIComponent(providerId) + "/");
    const url = new URL(path, window.location.origin);
    const current = new URLSearchParams(window.location.search);
    ["month", "view", "ranking_view", "date_from", "date_to", "provider", "client", "project", "uf", "priority", "reason", "status", "only_aged", "only_recurrent", "q", "sort", "top", "min_links"].forEach(key => {
      if (current.has(key)) url.searchParams.set(key, current.get(key));
    });
    return url;
  }

  function listSection(heading, rows, label = "name", amount = "tickets") {
    const body = rows && rows.length
      ? '<ul class="drawer-data-list">' + rows.map(item => {
          const extras = [];
          if (item.links_affected != null) extras.push(num(item.links_affected) + " " + unit(item.links_affected, "link afetado", "links afetados"));
          if (item.over_24h != null && item.over_24h) extras.push(num(item.over_24h) + " >24h");
          if (item.procedentes != null) extras.push(num(item.procedentes) + " procedentes");
          if (item.improcedentes != null) extras.push(num(item.improcedentes) + " improcedentes");
          return '<li><div class="drawer-dimension-copy"><strong>' + escapeHtml(item[label]) + '</strong>' + (extras.length ? '<small class="drawer-dimension-meta">' + extras.map(value => '<span>' + escapeHtml(value) + '</span>').join("") + '</small>' : "") + '</div><b>' + num(item[amount]) + ' chamados</b></li>';
        }).join("") + '</ul>'
      : '<p class="drawer-empty-note">Sem dados neste recorte.</p>';
    return '<section class="drawer-section"><h3>' + heading + '</h3>' + body + '</section>';
  }

  function stateList(rows = []) {
    const body = rows.length ? '<ul class="drawer-data-list">' + rows.map(item => {
      const code = String(item.name || "").toUpperCase();
      const flag = /^[A-Z]{2}$/.test(code) ? '<img class="ranking-state-flag" src="/static/imagens/bandeiras/estados/' + code.toLowerCase() + '.svg" alt="" loading="lazy">' : "";
      return '<li><span>' + flag + escapeHtml(code || item.name) + '</span><b>' + num(item.tickets) + ' chamados</b></li>';
    }).join("") + '</ul>' : '<p class="drawer-empty-note">Sem dados neste recorte.</p>';
    return '<section class="drawer-section"><h3>Principais UFs</h3>' + body + '</section>';
  }

  function historyChart(items = []) {
    if (!items.length) return '<p class="drawer-empty-note">Sem hist\u00f3rico suficiente para esta compara\u00e7\u00e3o.</p>';
    const max = Math.max(1, ...items.map(item => Number(item.score) || 0));
    const names = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
    return '<div class="history-bars" role="list" aria-label="Evolu\u00e7\u00e3o mensal do \u00cdndice ARION">' + items.map((item, index) => {
      const value = Number(item.score) || 0;
      const previous = index ? Number(items[index - 1].score) || 0 : value;
      const trend = index === 0 ? "base" : value > previous + 2 ? "worse" : value < previous - 2 ? "better" : "stable";
      const trendLabel = trend === "worse" ? "\u2191 Piorou" : trend === "better" ? "\u2193 Melhorou" : trend === "stable" ? "\u2192 Est\u00e1vel" : "Base";
      const month = Number(item.month.split("-")[1]);
      const height = Math.max(4, value / max * 100);
      const label = (names[month - 1] || item.month) + " \u00b7 " + score(value) + " pontos \u00b7 " + trendLabel;
      return '<div role="listitem" aria-label="' + escapeHtml(label) + '"><b>' + score(value) + '</b><span class="history-trend history-trend--' + trend + '">' + trendLabel + '</span><div class="history-bar-track" aria-hidden="true"><i style="height:' + height + '%"></i></div><small>' + (names[month - 1] || item.month) + '</small></div>';
    }).join("") + '</div>';
  }

  function responsibilitySection(data = {}) {
    const total = Number(data.procedentes || 0) + Number(data.improcedentes || 0) + Number(data.indeterminados || 0);
    const positive = total ? Number(data.procedentes || 0) / total * 100 : 0;
    const negative = total ? Number(data.improcedentes || 0) / total * 100 : 0;
    const unknown = Math.max(0, 100 - positive - negative);
    const review = data.review
      ? '<p class="responsibility-review">Revisar responsabilidade \u00b7 incid\u00eancia alta de Improcedentes no recorte.</p>'
      : "";
    return '<section class="drawer-section responsibility-section"><header><h3>Responsabilidade dos chamados</h3><small title="A proced\u00eancia vem do fechamento do chamado no OTRS. Nesta vers\u00e3o \u00e9 exibida para an\u00e1lise e ainda n\u00e3o altera o \u00cdndice ARION V2.">ORIGEM: OTRS \u00b7 INFORMATIVO</small></header>' +
      '<div class="responsibility-kpis"><div><span>Procedentes</span><b>' + num(data.procedentes) + '</b></div><div><span>Improcedentes</span><b>' + num(data.improcedentes) + '</b></div><div><span>Indeterminados</span><b>' + num(data.indeterminados) + '</b></div><div><span>Taxa de proced\u00eancia</span><b>' + pct(data.procedure_rate) + '</b></div></div>' +
      '<div class="responsibility-bar" role="img" aria-label="' + num(data.procedentes) + ' procedentes, ' + num(data.improcedentes) + ' improcedentes, ' + num(data.indeterminados) + ' indeterminados"><i class="responsibility-bar--procedente" style="width:' + positive + '%"></i><i class="responsibility-bar--improcedente" style="width:' + negative + '%"></i><i class="responsibility-bar--unknown" style="width:' + unknown + '%"></i></div>' +
      '<small class="aging-disclaimer">A taxa de proced\u00eancia usa somente classifica\u00e7\u00f5es conhecidas. A categoria n\u00e3o determina culpa nem altera o \u00cdndice V2.</small>' + review + '</section>';
  }

  function linkRows(items = []) {
    if (!items.length) return '<p class="drawer-empty-note">Nenhum link com chamados no recorte.</p>';
    const base = new URL(root.dataset.baseUrl || "/base/", window.location.origin);
    return '<div class="drawer-link-list">' + items.map(item => {
      const url = new URL(base);
      url.searchParams.set("config_item_id", item.config_item_id);
      const badges = [];
      if (item.recurrent) badges.push('<span class="is-recurrent">Reincidente</span>');
      if (Number(item.over_24h) > 0) badges.push('<span class="is-aged">' + num(item.over_24h) + ' acima de 24h</span>');
      if (!badges.length) badges.push('<span>Sem reincid\u00eancia</span>');
      return '<article><div class="drawer-link-main"><a href="' + escapeHtml(url.toString()) + '">' + escapeHtml(item.name) + '</a><small>' + escapeHtml(item.state || "UF n\u00e3o informada") + ' \u00b7 CI ' + escapeHtml(item.config_item_id) + '</small></div><span class="drawer-link-count">' + num(item.tickets) + ' ' + unit(item.tickets, "chamado", "chamados") + '</span><div class="drawer-link-badges">' + badges.join("") + '</div><small class="drawer-link-responsibility">' + num(item.procedentes) + ' procedentes \u00b7 ' + num(item.improcedentes) + ' improcedentes</small></article>';
    }).join("") + '</div>';
  }

  function ticketTable(item) {
    const rows = item.ticket_records || [];
    currentTicketRecords = rows;
    const body = rows.length ? rows.map((ticket, index) => {
      const firstLink = (ticket.links || [])[0];
      const linkLabel = firstLink ? firstLink.name : "\u2014";
      const responsibility = ticket.responsibility_class || "INDETERMINADO";
      return '<tr><td><button type="button" class="ticket-open" data-ticket-open="' + index + '">' + escapeHtml(ticket.number) + '</button></td><td>' + escapeHtml(ticket.created_at || "\u2014") + '</td><td>' + escapeHtml(ticket.client || "\u2014") + '</td><td>' + escapeHtml(ticket.project || "\u2014") + '</td><td>' + escapeHtml(ticket.state || "\u2014") + '</td><td>' + escapeHtml(ticket.status) + '</td><td>' + escapeHtml(ticket.reason || ticket.closing_code_raw || "\u2014") + '</td><td>' + escapeHtml(responsibility) + '</td><td>' + (ticket.over_24h ? "Sim" : "N\u00e3o") + '</td><td>' + escapeHtml(linkLabel) + '</td></tr>';
    }).join("") : '<tr><td colspan="10">Nenhum chamado para este Provedor / ISP.</td></tr>';
    let controls = "";
    if (item.ticket_records_total > 10 && item.ticket_page_size === 10) {
      controls = '<button type="button" class="ranking-button ranking-button--quiet" data-ticket-expand>Ver todos os chamados (' + num(item.ticket_records_total) + ')</button>';
    } else if (item.ticket_pages > 1) {
      const start = (item.ticket_page - 1) * item.ticket_page_size + 1;
      const end = Math.min(item.ticket_page * item.ticket_page_size, item.ticket_records_total);
      controls = '<div class="ticket-page-controls"><span>' + num(start) + "\u2013" + num(end) + ' de ' + num(item.ticket_records_total) + '</span>' +
        (item.ticket_page > 1 ? '<button type="button" class="ranking-button ranking-button--quiet" data-ticket-page="' + (item.ticket_page - 1) + '">Anterior</button>' : "") +
        (item.ticket_page < item.ticket_pages ? '<button type="button" class="ranking-button ranking-button--quiet" data-ticket-page="' + (item.ticket_page + 1) + '">Pr\u00f3xima</button>' : "") + '</div>';
    }
    return '<section class="drawer-section drawer-tickets-section"><header><h3>Chamados que comp\u00f5em a an\u00e1lise</h3><small>' + num(item.ticket_records_total) + ' ' + unit(item.ticket_records_total, "chamado", "chamados") + '</small></header><div class="ticket-table-scroll"><table class="drawer-ticket-table"><thead><tr><th>Chamado</th><th>Data</th><th>Cliente</th><th>Projeto</th><th>UF</th><th>Status</th><th>Motivo</th><th>Responsabilidade</th><th>&gt;24h</th><th>Link</th></tr></thead><tbody>' + body + '</tbody></table></div><div class="ticket-page-actions">' + controls + '</div></section>';
  }

  function renderDetail(item) {
    currentDetail = item;
    const base = new URL(root.dataset.baseUrl || "/base/", window.location.origin);
    base.searchParams.set("view", "providers");
    base.searchParams.set("provider_id", item.id);
    const cockpit = new URL(root.dataset.cockpitUrl || "/cockpit/", window.location.origin);
    const current = new URLSearchParams(window.location.search);
    if (current.has("date_from")) cockpit.searchParams.set("start", current.get("date_from"));
    if (current.has("date_to")) cockpit.searchParams.set("end", current.get("date_to"));
    cockpit.searchParams.set("provider", item.name);
    const responsibility = item.responsibility || {};
    const overviewMetric = (label, value) => '<div><span>' + label + '</span><strong>' + escapeHtml(value) + '</strong></div>';
    const summary = '<section class="drawer-score-summary"><div class="drawer-score-main"><span title="Indicador auxiliar baseado nos dados operacionais disponíveis. Os critérios definitivos serão definidos com a operação.">Indicador operacional</span><strong>' + score(item.risk_score) + '<small>/100</small></strong><em class="risk-pill risk-pill--' + escapeHtml(item.risk_class) + '">' + escapeHtml(item.risk_label) + '</em></div><div class="drawer-overview">' +
      overviewMetric("Links na CMDB", num(item.links_total) + " " + unit(item.links_total, "link", "links")) +
      overviewMetric("Chamados", num(item.tickets)) +
      overviewMetric("Chamados / 100 links CMDB", item.tickets_per_100_active_links == null ? "—" : decimal.format(item.tickets_per_100_active_links)) +
      overviewMetric("Tempo médio de resolução", item.avg_resolution_display || "—") +
      overviewMetric("Links afetados", num(item.links_affected)) +
      overviewMetric("Reincidentes", num(item.recurrent_links)) +
      overviewMetric("ARION >24h", num(item.tickets_over_24h)) +
      overviewMetric("Backlog", num(item.backlog_current)) + '</div></section>';
    const sample = item.sample_small ? '<p class="small-sample"><span aria-hidden="true">!</span> Amostra pequena \u00b7 ' + num(item.links_total) + ' ' + unit(item.links_total, "link", "links") + '. A exposi\u00e7\u00e3o segue vis\u00edvel no c\u00e1lculo.</p>' : "";
    const componentRow = (label, value, max, detail, note = "") => {
      const points = Math.max(0, Math.min(max, Number(value) || 0));
      const width = max ? Math.max(0, Math.min(100, points / max * 100)) : 0;
      return '<article><div><h4>' + label + '</h4><b>' + score(points) + ' / ' + max + '</b></div><div class="component-track" role="progressbar" aria-label="' + label + '" aria-valuemin="0" aria-valuemax="' + max + '" aria-valuenow="' + points.toFixed(1) + '"><i style="width:' + width.toFixed(1) + '%"></i></div><p>' + escapeHtml(detail) + '</p>' + (note ? '<small>' + escapeHtml(note) + '</small>' : '') + '</article>';
    };
    const components = '<section class="drawer-section"><h3>Risco atual · componentes do indicador</h3><div class="drawer-components drawer-components--v2">' +
      componentRow("Impacto no NOC", item.impact_noc_score, 30, num(item.tickets) + " chamados \u00b7 " + num(item.links_affected) + " links afetados", "Volume e links afetados normalizados pelo P95 do conjunto filtrado.") +
      componentRow("Base afetada", item.affected_base_score, 20, num(item.links_affected) + " de " + num(item.links_total) + " links \u00b7 " + decimal.format(item.affected_rate * 100) + "%") +
      componentRow("Reincid\u00eancia", item.recurrent_score, 20, num(item.recurrent_links) + " de " + num(item.links_affected) + " links afetados \u00b7 " + decimal.format(item.recurrent_rate * 100) + "%") +
      componentRow("ARION >24h", item.over_24h_score, 20, num(item.tickets_over_24h) + " de " + num(item.eligible_tickets) + " chamados avali\u00e1veis \u00b7 " + decimal.format(item.over_24h_rate * 100) + "%") +
      componentRow("Backlog atual", item.backlog_score, 10, num(item.backlog_current) + " " + unit(item.backlog_current, "chamado", "chamados")) +
      '</div><small class="aging-disclaimer">Os cinco componentes e a fórmula vigente permanecem inalterados. Links representam o snapshot atual completo da CMDB; chamados seguem o período selecionado. Este indicador é uma leitura auxiliar, e seus critérios definitivos serão definidos com a operação. O limite de 24h não representa necessariamente o SLA contratual.</small></section>';
    const cockpitLink = '<footer class="drawer-actions"><a class="ranking-button ranking-button--primary" href="' + escapeHtml(base.toString()) + '">Ver links na Base</a><a class="ranking-button ranking-button--quiet" href="' + escapeHtml(cockpit.toString()) + '">Analisar no Cockpit</a></footer>';
    content.innerHTML = summary + sample + components + responsibilitySection(responsibility) +
      '<section class="drawer-section"><h3>Evolu\u00e7\u00e3o do indicador operacional \u00b7 3 meses</h3>' + historyChart(item.history) + '</section>' +
      listSection("Projetos com mais ocorr\u00eancias", item.projects_top) +
      listSection("Clientes com mais ocorr\u00eancias", item.clients_top) +
      stateList(item.ufs_top) +
      listSection("Motivos normalizados", item.reasons_top) +
      '<section class="drawer-section"><h3>Links mais problem\u00e1ticos</h3>' + linkRows(item.problematic_links) + '</section>' +
      ticketTable(item) +
      '<section class="drawer-section"><h3>Chamados ARION &gt;24h</h3>' + simpleTicketList(item.over_24h_tickets) + '</section>' +
      '<section class="drawer-section"><h3>Backlog atual</h3>' + simpleTicketList(item.backlog_tickets) + '</section>' +
      cockpitLink +
      '<p class="drawer-readonly-note">Fonte: espelhos locais OTRS/CMDB \u00b7 consulta somente leitura \u00b7 disponibilidade n\u00e3o calculada.</p>';
  }

  function simpleTicketList(items = []) {
    if (!items.length) return '<p class="drawer-empty-note">Nenhum chamado neste grupo.</p>';
    return '<div class="drawer-ticket-list">' + items.slice(0, 50).map(item => '<article><div><strong>' + escapeHtml(item.number) + '</strong><span>' + escapeHtml(item.status) + (item.priority ? ' \u00b7 ' + escapeHtml(item.priority) : '') + '</span></div><p>' + escapeHtml(item.title || "Sem t\u00edtulo") + '</p><small>' + escapeHtml(item.created_at) + (item.operational_end_at ? ' \u00b7 fim operacional ' + escapeHtml(item.operational_end_at) : '') + (item.terminal_at ? ' \u00b7 terminal OTRS ' + escapeHtml(item.terminal_at) : '') + (item.reason ? ' \u00b7 ' + escapeHtml(item.reason) : '') + '</small>' + (item.over_24h ? '<em>ARION &gt;24h</em>' : '') + '</article>').join("") + '</div>';
  }

  function showTicket(ticket) {
    if (!ticket) return;
    ticketTitle.textContent = ticket.number || "Chamado";
    const links = (ticket.links || []).map(link => '<li><strong>' + escapeHtml(link.name) + '</strong><span>CI ' + escapeHtml(link.config_item_id) + ' \u00b7 ' + escapeHtml(link.project || "Projeto n\u00e3o identificado") + ' \u00b7 ' + escapeHtml(link.city ? link.city + " / " + (link.state || "UF n\u00e3o informada") : link.state || "UF n\u00e3o informada") + '</span></li>').join("");
    const why = (ticket.why || []).map(reason => '<li>' + escapeHtml(reason) + '</li>').join("");
    ticketContent.innerHTML = '<section class="ticket-modal-section"><h3>Dados do chamado</h3><dl><div><dt>N\u00famero</dt><dd>' + escapeHtml(ticket.number) + '</dd></div><div><dt>T\u00edtulo OTRS</dt><dd>' + escapeHtml(ticket.title || "Sem t\u00edtulo") + '</dd></div><div><dt>Cliente can\u00f4nico</dt><dd>' + escapeHtml(ticket.client || "\u2014") + '</dd></div><div><dt>Projeto can\u00f4nico</dt><dd>' + escapeHtml(ticket.project || "\u2014") + '</dd></div><div><dt>Provedor / ISP</dt><dd>' + escapeHtml(ticket.provider || "\u2014") + '</dd></div><div><dt>Cidade / UF</dt><dd>' + escapeHtml(ticket.city ? ticket.city + " / " + (ticket.state || "UF n\u00e3o informada") : ticket.state || "\u2014") + '</dd></div><div><dt>Status</dt><dd>' + escapeHtml(ticket.status || "\u2014") + '</dd></div><div><dt>Prioridade</dt><dd>' + escapeHtml(ticket.priority || "\u2014") + '</dd></div><div><dt>Criado em</dt><dd>' + escapeHtml(ticket.created_at || "\u2014") + '</dd></div><div><dt>Resolvido em</dt><dd>' + escapeHtml(ticket.resolved_at || "\u2014") + '</dd></div><div><dt>Terminal StateUpdate OTRS em</dt><dd>' + escapeHtml(ticket.terminal_at || "\u2014") + '</dd></div><div><dt>T\u00e9rmino operacional usado</dt><dd>' + escapeHtml(ticket.operational_end_at || "\u2014") + '</dd></div><div><dt>Origem do t\u00e9rmino operacional</dt><dd>' + escapeHtml(ticket.operational_end_source || "\u2014") + '</dd></div><div><dt>Dura\u00e7\u00e3o (horas)</dt><dd>' + (ticket.duration_hours == null ? "\u2014" : decimal.format(ticket.duration_hours)) + '</dd></div><div><dt>ARION &gt;24h</dt><dd>' + (ticket.over_24h ? "Sim" : "N\u00e3o") + '</dd></div><div><dt>C\u00f3digo / motivo de fechamento</dt><dd>' + escapeHtml(ticket.closing_code_raw || "\u2014") + (ticket.reason ? " \u00b7 " + escapeHtml(ticket.reason) : "") + '</dd></div><div><dt>Responsabilidade</dt><dd>' + escapeHtml(ticket.responsibility_class || "INDETERMINADO") + (ticket.responsibility_raw ? ' \u00b7 valor raw: ' + escapeHtml(ticket.responsibility_raw) : '') + '</dd></div></dl></section>' +
      '<section class="ticket-modal-section"><h3>Links relacionados</h3><ul class="ticket-modal-links">' + (links || '<li>Sem links no espelho local.</li>') + '</ul></section>' +
      '<section class="ticket-modal-section"><h3>Por que este chamado participa desta an\u00e1lise?</h3><ul class="ticket-why-list">' + why + '</ul></section>' +
      '<p class="drawer-readonly-note">Apenas campos operacionais espelhados. Artigos, notas internas e dados de contato n\u00e3o s\u00e3o exibidos.</p>';
    ticketDialog.showModal();
  }

  async function requestTicketPage(page, pageSize) {
    if (!activeProvider) return;
    const url = makeDetailUrl(activeProvider);
    url.searchParams.set("ticket_page", String(page));
    url.searchParams.set("ticket_page_size", String(pageSize));
    const button = content.querySelector("[data-ticket-expand], [data-ticket-page]");
    if (button) button.disabled = true;
    try {
      const response = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      const item = await response.json();
      if (!response.ok) throw new Error("Falha ao consultar chamados.");
      currentDetail = item;
      renderDetail(item);
    } catch (_) {
      const controls = content.querySelector(".ticket-page-actions");
      if (controls) controls.insertAdjacentHTML("beforeend", '<span class="drawer-error" role="alert">N\u00e3o foi poss\u00edvel carregar a p\u00e1gina de chamados.</span>');
    }
  }

  async function openDrawer(providerId, trigger) {
    activeProvider = String(providerId);
    focusedBeforeDrawer = trigger || document.activeElement;
    drawer.hidden = false;
    backdrop.hidden = false;
    document.body.classList.add("ranking-drawer-open");
    title.textContent = "Carregando provedor";
    subtitle.textContent = "Consultando dados no escopo autorizado\u2026";
    content.innerHTML = '<p class="drawer-loading" role="status">Carregando an\u00e1lise do provedor\u2026</p>';
    root.querySelector("[data-drawer-close]").focus();
    try {
      const response = await fetch(makeDetailUrl(providerId), { headers: { Accept: "application/json" }, credentials: "same-origin" });
      const item = await response.json();
      if (!response.ok) throw new Error(item.error || "Falha ao consultar o provedor.");
      if (String(providerId) !== activeProvider) return;
      title.textContent = item.name;
      subtitle.textContent = num(item.links_total) + " " + unit(item.links_total, "link", "links") + " \u00b7 " + score(item.risk_score) + " / 100 \u00b7 " + item.risk_label;
      renderDetail(item);
    } catch (_) {
      if (String(providerId) !== activeProvider) return;
      content.innerHTML = '<p class="drawer-error" role="alert">N\u00e3o foi poss\u00edvel carregar os detalhes deste provedor no escopo autorizado.</p>';
    }
  }

  function closeDrawer() {
    drawer.hidden = true;
    backdrop.hidden = true;
    activeProvider = null;
    currentDetail = null;
    document.body.classList.remove("ranking-drawer-open");
    if (focusedBeforeDrawer?.isConnected) focusedBeforeDrawer.focus();
  }

  root.addEventListener("click", event => {
    const opener = event.target.closest("[data-provider-open]");
    if (opener) {
      openDrawer(opener.dataset.providerOpen, opener);
      return;
    }
    const row = event.target.closest("[data-provider-row]");
    if (!row || event.target.closest("a, button, input, select, summary")) return;
    openDrawer(row.dataset.providerRow, row);
  });
  root.addEventListener("keydown", event => {
    const row = event.target.closest("[data-provider-row]");
    if (!row || event.target !== row || !["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    openDrawer(row.dataset.providerRow, row);
  });
  content.addEventListener("click", event => {
    const ticketButton = event.target.closest("[data-ticket-open]");
    if (ticketButton) return showTicket(currentTicketRecords[Number(ticketButton.dataset.ticketOpen)]);
    if (event.target.closest("[data-ticket-expand]")) return requestTicketPage(1, 50);
    const pageButton = event.target.closest("[data-ticket-page]");
    if (pageButton && currentDetail) return requestTicketPage(Number(pageButton.dataset.ticketPage), currentDetail.ticket_page_size || 50);
  });
  root.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => {
    root.querySelector(".ranking-filter-form").elements.namedItem("view").value = button.dataset.view;
    root.querySelector(".ranking-filter-form").requestSubmit();
  }));
  root.querySelector("[data-drawer-close]").addEventListener("click", closeDrawer);
  backdrop.addEventListener("click", closeDrawer);
  root.querySelector("[data-open-score]").addEventListener("click", () => scoreDialog.showModal());
  root.querySelector("[data-score-close]").addEventListener("click", () => scoreDialog.close());
  root.querySelector("[data-ticket-close]").addEventListener("click", () => ticketDialog.close());
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !drawer.hidden && !ticketDialog.open) closeDrawer();
    if (event.key !== "Tab" || drawer.hidden || ticketDialog.open) return;
    const focusable = [...drawer.querySelectorAll('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])')];
    if (!focusable.length) return;
    if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable.at(-1).focus(); }
    else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0].focus(); }
  });
})();
