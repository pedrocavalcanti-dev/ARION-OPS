import { CAUSE_ORDER, causeColor, causeBadge, causeFilterKey } from "./cause-meta.js";

const regions = {
  N: ["AC", "AM", "AP", "PA", "RO", "RR", "TO"],
  NE: ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"],
  CO: ["DF", "GO", "MS", "MT"],
  SE: ["ES", "MG", "RJ", "SP"],
  S: ["PR", "RS", "SC"],
};
const regionNames = { N: "Norte", NE: "Nordeste", CO: "Centro-Oeste", SE: "Sudeste", S: "Sul" };
const allUF = Object.values(regions).flat().sort();
const fmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const intFmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const plural = (n, singular, pluralWord) => (Number(n) === 1 ? singular : pluralWord);
const formatDateBR = iso => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || "").trim());
  return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso || "");
};
const foldText = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR").trim();
const quantity = (value, unit = "") => value == null || Number.isNaN(Number(value)) && typeof value === "number" ? "Indisponível" : (typeof value === "number" ? fmt.format(value) : escape(value)) + (unit ? " " + unit : "");

function dailyChart(series, layer = "rain", activeDay = "") {
  if (!series.length) return "<p class='geo-status'>Sem série diária para este período.</p>";
  const width = 600, height = 186, left = 34, right = 34, top = 16, bottom = 24, plot = height - top - bottom;
  const config = {
    temperature: ["temperature_mean_c", "Temperatura média", "°C"],
    temperature_min: ["temperature_min_c", "Temperatura mínima", "°C"],
    temperature_max: ["temperature_max_c", "Temperatura máxima", "°C"],
    wind: ["gust_max_kmh", "Rajada máxima", "km/h"],
    wind_speed: ["wind_max_kmh", "Vento máximo", "km/h"],
  }[layer] || ["rain_mm", "Chuva estimada", "mm"];
  const [key, label, unit] = config;
  const combined = layer === "correlation";
  const rows = combined ? series.map(r => ({ ...r, tickets: r.technical_tickets })) : series;
  const observed = rows.map(r => r[key]).filter(Number.isFinite);
  const hasClimate = observed.length > 0;
  const low = Math.min(0, ...observed), high = Math.max(1, ...observed);
  const rainMax = Math.max(1e-6, high - low);
  const ticketMax = Math.max(1, ...rows.map(r => r.tickets || 0));
  const step = (width - left - right) / rows.length;
  const x = i => left + (i + .5) * step;
  const yRain = v => top + plot * (1 - (v - low) / rainMax);
  const yTicket = v => top + plot * (1 - (v || 0) / ticketMax);

  const gridLines = [0.25, 0.5, 0.75, 1].map(f => {
    const gy = top + plot * (1 - f);
    return `<line class="chart-grid" x1="${left}" y1="${gy}" x2="${width - right}" y2="${gy}"/>`;
  }).join("");

  const bars = rows.map((r, i) => {
    if (r[key] == null) return "";
    const barY = yRain(r[key]);
    const barH = Math.max(0, top + plot - barY);
    const activeStyle = activeDay && r.date === activeDay ? ' style="opacity:1"' : "";
    const brDate = formatDateBR(r.date);
    const incCount = r.tickets || 0;
    return `<rect class="rain-bar"${activeStyle} x="${x(i) - step * .32}" y="${barY}" width="${Math.max(1.5, step * .64)}" height="${barH}" data-chart-date="${escape(r.date)}" data-chart-climate="${escape(quantity(r[key], unit))}" data-chart-tickets="${incCount}"><title>${escape(brDate)}: ${escape(label)} ${quantity(r[key], unit)} · ${incCount} ${plural(incCount, "incidente", "incidentes")}</title></rect>`;
  }).join("");

  const points = rows.map((r, i) => `${x(i)},${yTicket(r.tickets)}`).join(" ");
  const dots = rows.length <= 45
    ? rows.map((r, i) => {
      const brDate = formatDateBR(r.date);
      const incCount = r.tickets || 0;
      return `<circle class="ticket-dot" cx="${x(i)}" cy="${yTicket(r.tickets)}" r="${activeDay && r.date === activeDay ? 4 : 2.3}" data-chart-date="${escape(r.date)}" data-chart-climate="${escape(r[key] == null ? "Sem clima" : quantity(r[key], unit))}" data-chart-tickets="${incCount}"><title>${escape(brDate)}: ${incCount} ${plural(incCount, "incidente", "incidentes")} · ${escape(label)} ${r[key] == null ? "indisponível" : quantity(r[key], unit)}</title></circle>`;
    }).join("")
    : "";

  const tickIndices = [...new Set([0, Math.floor((rows.length - 1) * .25), Math.floor((rows.length - 1) * .5), Math.floor((rows.length - 1) * .75), rows.length - 1])];
  const ticks = tickIndices.map(i => `<text x="${x(i)}" y="${height - 5}" text-anchor="middle">${rows.length > 31 ? rows[i].date.slice(8) + "/" + rows[i].date.slice(5, 7) : rows[i].date.slice(-2)}</text>`).join("");

  return `<div class="geo-chart-legend"><b>${hasClimate ? escape(label) + " · " + unit + " (esq.)" : "Clima indisponível · sem simulação"}</b><b>${combined ? "Incidentes técnicos" : "Incidentes válidos"} · qtd (dir.)</b></div>`
    + `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${hasClimate ? escape(label) + " em " + unit + " e incidentes diários" : "Incidentes diários; clima indisponível"}">`
    + `${gridLines}`
    + `<text x="${left - 4}" y="${top + 4}" text-anchor="end">${hasClimate ? fmt.format(high) : ""}</text>`
    + `<text x="${left - 4}" y="${top + plot}" text-anchor="end">${hasClimate ? fmt.format(low) : ""}</text>`
    + `<text x="${width - right + 4}" y="${top + 4}" text-anchor="start">${intFmt.format(ticketMax)}</text>`
    + `<text x="${width - right + 4}" y="${top + plot}" text-anchor="start">0</text>`
    + `${bars}<polyline class="ticket-line" points="${points}"/>${dots}${ticks}</svg>`;
}

export function mountGeoIntelligence({ page, root, state, urlFor, refresh, onWeather, fitRegion, selectUF, onFilterUpdate }) {
  const panel = page.querySelector("[data-geo-intelligence]");
  if (!panel) return { update() {}, syncMode() {}, close() {}, allowedUFs: () => allUF };
  const $ = s => page.querySelector(s);
  const region = $("[data-region-filter]"), uf = $("[data-geo-uf]"), month = $("[data-geo-month]"), category = $("[data-category-filter]"), layer = $("[data-geo-layer]");
  let controller = null, sequence = 0, pollTimer = 0, timelineTimer = 0, current = null;
  let pendingPlanToken = "";
  let timelineOpen = false;
  let showAllStates = false;
  let localTab = "incidents";
  let localSearchQuery = "";
  let localUfFilter = "";
  let localQueueFilter = "all";
  let localPageSize = 5;
  let localPage = 1;
  const flagsData = (() => {
    try { return JSON.parse(document.getElementById("state-flags-data")?.textContent || "{}"); }
    catch { return {}; }
  })();
  const dayInput = $("[data-timeline-day]"), dayLabel = $("[data-timeline-label]"), dayPillLabel = $("[data-timeline-pill-label]"), play = $("[data-timeline-play]");
  const status = $("[data-weather-status]"), timeline = $("[data-geo-timeline]");
  const timelineToggle = $("[data-timeline-toggle]"), timelineControls = $("#geo-timeline-controls"), timelineCollapse = $("[data-timeline-collapse]");
  const currentTheme = () => document.documentElement.dataset.theme === "light" ? "light" : "dark";

  function setPanelState(value) {
    panel.dataset.panelState = value;
    const open = value !== "collapsed", expanded = value === "expanded";
    panel.hidden = !open;
    page.classList.toggle("has-analytics-drawer", expanded);
    $("#geo-analysis-body").hidden = !open;
    $("#geo-intelligence-content").hidden = !expanded;
    $("[data-analysis-expand]").hidden = !open;
    $("[data-analysis-close]").hidden = !open;
    const toggleBtn = $("[data-weather-toggle]");
    if (toggleBtn) {
      toggleBtn.setAttribute("aria-expanded", String(open));
      toggleBtn.setAttribute("aria-label", open ? "Fechar análises" : "Abrir análises GEO Inteligência");
      const labelSpan = toggleBtn.querySelector("span");
      if (labelSpan) labelSpan.textContent = open ? "Fechar" : "Análises";
      else toggleBtn.textContent = open ? "Fechar análises" : "Análises";
    }
    $("[data-analysis-expand]").textContent = expanded ? "Resumo" : "Expandir";
    $("[data-analysis-expand]").setAttribute("aria-expanded", String(expanded));
    if ($("[data-timeline-expand]")) {
      $("[data-timeline-expand]").setAttribute("aria-expanded", String(expanded));
      $("[data-timeline-expand]").textContent = expanded ? "Recolher" : "Detalhar";
    }
    page.dispatchEvent(new CustomEvent("geo:analysis", { detail: { state: value, expanded: open, full: expanded } }));
    page.dispatchEvent(new CustomEvent("geo:layout"));
  }

  function setTimelineOpen(open) {
    timelineOpen = Boolean(open);
    if (!timeline) return;
    timeline.dataset.timelineState = timelineOpen ? "open" : "collapsed";
    if (timelineControls) timelineControls.hidden = !timelineOpen;
    if (timelineToggle) {
      timelineToggle.hidden = timelineOpen;
      timelineToggle.setAttribute("aria-expanded", String(timelineOpen));
    }
    page.dispatchEvent(new CustomEvent("geo:layout"));
  }

  function syncControls() {
    if (region) region.value = state.region;
    const ufs = current?.summary?.presentation ? current.summary.allowed_ufs : (regions[state.region] || allUF);
    if (region && current?.summary?.presentation && current?.catalog?.regions) {
      region.innerHTML = '<option value="">Brasil</option>' + current.catalog.regions.map(r => `<option value="${r.sigla}">${escape(r.nome)}</option>`).join("");
      region.value = state.region;
    }
    if (uf) {
      uf.innerHTML = '<option value="">Todas as UFs</option>' + (ufs || allUF).map(code => `<option value="${code}">${code}</option>`).join("");
      uf.value = state.selectedUF;
    }
    if (month) {
      month.value = /^\d{4}-\d{2}$/.test(state.period) ? state.period : state.period === "this_month" ? root.dataset.currentPeriod : root.dataset.defaultPeriod;
      month.max = root.dataset.currentPeriod || "";
    }
    if (category) category.value = state.category || "";
    if (layer) layer.value = state.layer;
  }

  async function update(preloaded) {
    pendingPlanToken = "";
    const collectButton = $("[data-weather-collect]");
    if (collectButton) collectButton.textContent = "Coletar clima do recorte";
    syncControls();
    const own = ++sequence;
    controller?.abort();
    controller = new AbortController();
    const link = document.querySelector("[data-present-action]");
    if (link) link.href = urlFor(link.getAttribute("href"), { period: state.period }).href;
    try {
      const response = preloaded ? null : await fetch(urlFor(panel.dataset.url), {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(45000)]),
      });
      const payload = preloaded || await response.json();
      if (own !== sequence) return;
      if (response && !response.ok) throw new Error(payload.error?.message || payload.error || "Análise mensal indisponível.");
      current = payload;
      syncControls();
      pause();
      syncMode();
      if (state.day && !payload.daily.some(r => r.date === state.day)) state.day = "";
      if (dayInput) {
        dayInput.max = payload.daily.length;
        dayInput.value = state.day ? payload.daily.findIndex(r => r.date === state.day) + 1 : 0;
      }
      render();
    } catch (error) {
      if (error.name !== "AbortError" && own === sequence && status) status.textContent = error.message;
    }
  }

  function renderPeriodComparison(summary) {
    const target = $("[data-period-comparison]");
    if (!target) return;
    const comp = summary?.period_comparison;
    if (!comp || !["calls", "repeat", "motives", "climate"].includes(state.mode)) {
      target.hidden = true;
      target.innerHTML = "";
      return;
    }
    const diff = comp.delta ?? comp.ticket_diff ?? 0;
    const pct = comp.delta_percent ?? comp.ticket_pct;
    const prevStart = formatDateBR(comp.date_from || comp.previous_start || "");
    const prevEnd = formatDateBR(comp.date_to || comp.previous_end || "");
    const prevCount = comp.ticket_count ?? comp.previous_tickets ?? 0;
    const criterion = comp.criterion || (comp.is_partial ? "Janela equivalente do mês anterior" : "Mês anterior completo");
    const trendClass = diff > 0 ? "geo-delta-pill--up" : diff < 0 ? "geo-delta-pill--down" : "geo-delta-pill--flat";
    const diffText = diff === 0 ? "Estável" : `${diff > 0 ? "+" : ""}${intFmt.format(diff)}${pct != null ? ` (${pct > 0 ? "+" : ""}${fmt.format(pct)}%)` : ""}`;
    target.hidden = false;
    target.innerHTML = `<span><strong>${escape(criterion)}:</strong> ${escape(prevStart)} a ${escape(prevEnd)} (${intFmt.format(prevCount)} ${plural(prevCount, "incidente", "incidentes")})</span>`
      + `<span class="geo-delta-pill ${trendClass}">${escape(diffText)}</span>`;
  }

  function renderDiagnostics(payload) {
    const diagBox = $("[data-tech-diagnostics]");
    const diagBody = $("[data-tech-diagnostics-body]");
    if (!diagBox || !diagBody) return;
    if (payload.summary?.presentation) {
      diagBox.hidden = true;
      diagBody.innerHTML = "";
      return;
    }
    const parts = [];
    if (payload.climate?.source) parts.push(`Fonte climática: ${payload.climate.source} (${payload.climate.timezone || "UTC"})`);
    if (payload.climate?.available) {
      parts.push(`${payload.climate.covered_ufs} UFs em cache · ${payload.climate.points} células · completude ${quantity(payload.climate.completeness * 100, "%")}`);
      if (payload.climate.last_update) parts.push(`Última atualização: ${new Date(payload.climate.last_update).toLocaleString("pt-BR")}`);
    }
    if (payload.collection?.requests_today != null) {
      parts.push(`Orçamento HTTP diário: ${payload.collection.requests_today}/${payload.collection.daily_budget}`);
    }
    if (payload.collection?.checkpoint_cells) parts.push(`${payload.collection.checkpoint_cells} células com checkpoint em progresso`);
    if (payload.collection?.jobs) {
      const jobs = payload.collection.jobs;
      parts.push(`Fila de coleta: ${jobs.pending} pendentes · ${jobs.running} em processamento · ${jobs.error} com erro · ${jobs.paused} pausadas`);
    }
    if (payload.missing_ufs?.length) parts.push(`Sem cache nesta consulta: ${payload.missing_ufs.join(", ")}`);
    diagBox.hidden = !parts.length;
    diagBody.innerHTML = parts.map(text => `<div>${escape(text)}</div>`).join("");
  }

  function render() {
    const payload = current;
    if (!payload) return;
    const climate = state.day ? payload.daily.find(r => r.date === state.day) || {} : payload.climate;
    const summary = payload.summary || {};
    const dayText = state.day ? state.day.slice(8) + "/" + state.day.slice(5, 7) : "Mês completo";
    if (dayLabel) dayLabel.textContent = dayText;
    if (dayPillLabel) dayPillLabel.textContent = dayText;

    const regionText = state.selectedUF || region?.options[region.selectedIndex]?.text || "Brasil";
    const periodText = payload.period?.month || state.period;
    $("[data-geo-context]").textContent = [regionText, periodText, payload.period?.partial ? "Parcial" : "Completo"].join(" · ");

    const activeReasons = Object.entries(summary.category_counts || {})
      .filter(([label]) => !(summary.presentation && label === "Pendência Financeira"));
    const orderIndex = label => {
      const idx = CAUSE_ORDER.indexOf(label);
      return idx < 0 ? 999 : idx;
    };
    const reasons = activeReasons.sort((a, b) => b[1] - a[1] || orderIndex(a[0]) - orderIndex(b[0]));
    const nonUnclassifiedReasons = reasons.filter(([label]) => label !== "Sem classificação");
    const incidents = state.day ? (climate.tickets ?? 0) : (summary.ticket_count || 0);
    const distinct = field => new Set(Object.values(summary.states || {}).flatMap(r => Object.keys(r[field] || {})).filter(v => !v.includes("não identificado"))).size;
    const stateRanking = Object.entries(summary.states || {}).filter(([, r]) => (r.ticket_count || 0) > 0).sort((a, b) => b[1].ticket_count - a[1].ticket_count);
    const peak = payload.daily.reduce((best, r) => r.tickets > (best?.tickets || 0) ? r : best, null);

    const metrics = state.mode === "climate"
      ? [
          [payload.climate?.partial ? "Chuva · coberto" : "Chuva estimada", state.day ? climate.rain_mm : climate.precipitation_mm, "mm"],
          [state.day ? "Incidentes no dia" : "Dias chuvosos", state.day ? incidents : climate.rainy_days, state.day ? "" : "dias"],
          ["Temperatura média", climate.temperature_mean_c, "°C"],
          ["Rajada máxima", climate.gust_max_kmh, "km/h"],
        ]
      : ["base", "projects"].includes(state.mode)
        ? [
            ["Links CMDB", summary.filtered_source_total || 0, ""],
            ["UFs com links", Object.values(summary.states || {}).filter(r => (r.total_links || 0) > 0).length, ""],
            ["Projetos", distinct("project_counts"), ""],
            ["Provedores / ISP", distinct("provider_counts"), ""],
          ]
        : state.mode === "motives"
          ? [
              ["Categoria líder", nonUnclassifiedReasons[0]?.[0] || reasons[0]?.[0] || "Sem dados", ""],
              ["UF mais afetada", stateRanking[0]?.[0] || "Sem dados", ""],
              ["Pico diário", peak ? peak.date.slice(8) + "/" + peak.date.slice(5, 7) : "Sem dados", ""],
              ["Dias com chuva e chamado", payload.climate?.coincident_days, "dias"],
            ]
          : [
              [state.day ? "Incidentes no dia" : "Incidentes válidos", incidents, ""],
              ["Links afetados", summary.affected_link_count || 0, ""],
              ["Links reincidentes", summary.recurrence_link_count || 0, ""],
              ["Motivo líder", nonUnclassifiedReasons[0]?.[0] || reasons[0]?.[0] || "Sem dados", ""],
            ];

    $("[data-weather-metrics]").innerHTML = metrics.slice(0, 4).map(([label, value, unit]) =>
      `<div><strong>${quantity(value, unit)}</strong><span>${escape(label)}</span></div>`
    ).join("");

    renderPeriodComparison(summary);

    const coverageNotes = [];
    if (payload.regional?.coverage_headline) coverageNotes.push(payload.regional.coverage_headline);
    if (payload.climate?.territorial_coverage_label) coverageNotes.push(payload.climate.territorial_coverage_label);
    else if (payload.climate?.coverage_label) coverageNotes.push(payload.climate.coverage_label);
    if (payload.climate?.municipal_coverage_label) coverageNotes.push(payload.climate.municipal_coverage_label);
    if (payload.climate?.stale) coverageNotes.push("Cache territorial vencido");
    if (payload.climate?.temporal_partial) coverageNotes.push("Janela temporal parcial");
    if (payload.collection?.message && !summary.presentation) coverageNotes.push(payload.collection.message);
    else if (!payload.climate?.available) coverageNotes.push("Clima ainda não coletado para este recorte.");
    status.textContent = coverageNotes.join(" · ");
    status.dataset.state = payload.collection?.configuration_status === "not_configured" ? "not_configured" : payload.collection?.status || "unavailable";
    renderDiagnostics(payload);

    const canCollect = Boolean(payload.collection?.can_collect), busy = ["queued", "running"].includes(payload.collection?.status);
    const form = $("[data-geo-collect-form]");
    if (form) form.hidden = !canCollect || busy;
    const configure = $("[data-weather-configure]");
    if (configure) configure.hidden = payload.collection?.configuration_status === "ready";
    clearTimeout(pollTimer);
    if (busy) pollTimer = setTimeout(() => update(), 15000);

    // Section 2: Evolução
    const chartBox = $("[data-weather-chart]"), chartCaption = $("[data-chart-caption]");
    if (["base", "projects"].includes(state.mode) && !payload.climate?.available && !payload.daily.some(r => r.tickets > 0)) {
      const projCounts = {};
      Object.values(summary.states || {}).forEach(r => Object.entries(r.project_counts || {}).forEach(([name, n]) => { projCounts[name] = (projCounts[name] || 0) + n; }));
      if (chartCaption) chartCaption.textContent = "Top projetos CMDB";
      chartBox.innerHTML = Object.entries(projCounts).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([name, n]) => `<p class="geo-analysis-row"><span>${escape(name)}</span><b>${intFmt.format(n)}</b></p>`).join("") || '<p class="geo-status">Sem projetos neste recorte.</p>';
    } else if (payload.comparison_time_compatible === false && payload.climate?.available) {
      if (chartCaption) chartCaption.textContent = "Referência temporal incompatível";
      chartBox.innerHTML = '<p class="geo-status">Referência climática UTC. Comparação diária combinada com incidentes locais indisponível; consulte indicadores e ranking abaixo.</p>';
    } else {
      if (chartCaption) chartCaption.textContent = state.day ? `Dia ${dayText}` : `${payload.daily?.length || 0} dias`;
      chartBox.innerHTML = dailyChart(payload.daily || [], state.layer, state.day);
    }

    // Section 3: Principais motivos + Macrorregiões
    const reasonsBox = $("[data-geo-reasons]"), reasonsCaption = $("[data-reasons-caption]");
    const totalReasons = reasons.reduce((acc, [, v]) => acc + v, 0);
    if (reasonsCaption) reasonsCaption.textContent = `${intFmt.format(totalReasons)} classificados`;
    const themeName = currentTheme();
    const causeRowsHtml = reasons.filter(([, v]) => v > 0).slice(0, 9).map(([label, v]) => {
      const share = totalReasons ? (v / totalReasons) * 100 : 0;
      const color = causeColor(label, themeName);
      const badge = causeBadge(label);
      const key = causeFilterKey(label);
      const barWidth = Math.max(2, share);
      return `<button type="button" class="geo-cause-row" data-explore-category="${escape(key)}" title="Filtrar categoria ${escape(label)} no mapa">`
        + `<span class="geo-cause-row-label"><i class="geo-cause-dot" style="background:${color}"></i><kbd class="geo-cause-badge">${badge}</kbd><span>${escape(label)}</span></span>`
        + `<span class="geo-cause-row-values"><b>${intFmt.format(v)}</b><span>(${fmt.format(share)}%)</span></span>`
        + `<span class="geo-cause-track"><i style="width:${barWidth}%;background:${color}"></i></span>`
        + `</button>`;
    }).join("");

    const regCounts = summary.region_category_counts || {};
    const regTotals = ["N", "NE", "CO", "SE", "S"].map(sigla => {
      const cats = regCounts[sigla] || regCounts[regionNames[sigla]] || {};
      const val = Object.entries(cats)
        .filter(([lbl]) => !(summary.presentation && lbl === "Pendência Financeira"))
        .reduce((acc, [, n]) => acc + (n || 0), 0);
      return [sigla, val];
    });
    const regGridHtml = regTotals.some(([, n]) => n > 0)
      ? `<h4>Incidentes por macrorregião</h4><div class="geo-region-cause-grid">${regTotals.map(([sigla, n]) =>
          `<button type="button" class="geo-region-cause-cell" data-filter-region="${sigla}" title="Focar macrorregião ${regionNames[sigla]}"><span>${sigla}</span><strong>${intFmt.format(n)}</strong></button>`
        ).join("")}</div>`
      : "";

    const projected = Object.entries(summary.distribution_projected || {}).filter(([, v]) => v > 0);
    const projectedTotal = projected.reduce((n, [, v]) => n + v, 0);
    const projectedHtml = projected.length
      ? `<h4>Cenário estimado · não é diagnóstico registrado</h4><div class="geo-scenario" aria-label="Cenário estimado">${projected.map(([lbl, v]) => `<span style="flex:${v}" title="${escape(lbl)}: ${v} estimados (${quantity(v / projectedTotal * 100)}%)."></span>`).join("")}</div>`
      : "";

    reasonsBox.innerHTML = (causeRowsHtml || '<p class="geo-status">Sem classificações neste recorte.</p>') + regGridHtml + projectedHtml;

    // Fonte & Metodologia notes
    const policyTarget = $("[data-geo-operator-policy]");
    if (policyTarget && summary.operator_policy) {
      const p = summary.operator_policy;
      const opLabel = p.label || (p.operator ? p.operator : "Todas as operadoras");
      const excl = (p.excluded_groups || []).join(", ") || p.summary || "critérios padrão";
      policyTarget.innerHTML = `<p><strong>Elegibilidade aplicada (${escape(opLabel)}):</strong> exclui ${escape(excl)}${p.keeps_sem_intervencao ? "; mantém 'Sem intervenção'." : "."}</p>`;
    }

    const q = summary.reconciliation;
    const qualityEl = $("[data-geo-quality]");
    if (qualityEl) {
      qualityEl.textContent = q
        ? `${intFmt.format(q.incidents)} ${plural(q.incidents, "incidente distinto", "incidentes distintos")} · ${intFmt.format(q.classified)} ${plural(q.classified, "classificado", "classificados")} · ${intFmt.format(q.unclassified)} sem classificação confiável · ${intFmt.format(q.with_uf)} com UF · ${intFmt.format(q.without_uf)} sem localização · ${intFmt.format(q.affected_links)} links afetados · ${intFmt.format(q.recurrent_links)} reincidentes. Ocorrências estaduais (${intFmt.format(q.state_occurrences)}) podem repetir chamados com múltiplos links; não somar UFs.`
        : payload.notice || "";
    }

    onWeather(payload, state.day);
    renderClimateExtras(payload);

    const fields = {
      rain: "precipitation_sum",
      temperature: "temperature_2m_mean",
      temperature_min: "temperature_2m_min",
      temperature_max: "temperature_2m_max",
      wind: "wind_gusts_10m_max",
      wind_speed: "wind_speed_10m_max",
      direction: "wind_direction_10m_dominant",
      correlation: "precipitation_sum",
    };
    if (layer) {
      for (const option of layer.options) {
        const field = fields[option.value];
        if (!field) continue;
        option.disabled = !Object.values(payload.weather || {}).some(row => row.daily?.some(d => Number.isFinite(d[field])));
        option.title = option.disabled ? "Variável sem observações neste recorte; ausência não equivale a zero." : "";
      }
    }
  }

  function formatStateMetricValue(metricKey, row) {
    if (metricKey === "rainy_days") {
      const days = row.rainy_days ?? row.value;
      const pct = row.rainy_days_percent != null ? ` (${fmt.format(row.rainy_days_percent)}%)` : "";
      return days == null ? "Indisponível" : `${intFmt.format(days)} ${plural(days, "dia", "dias")}${pct}`;
    }
    const val = row[metricKey] ?? row.value;
    const units = {
      precipitation_mm: "mm",
      gust_max_kmh: "km/h",
      wind_max_kmh: "km/h",
      temperature_max_c: "°C",
      temperature_min_c: "°C",
    };
    return quantity(val, units[metricKey] || "");
  }

  function renderMunicipalList(payload) {
    const localities = $("[data-weather-localities]");
    const pagination = $("[data-local-pagination]");
    const localMetricSelect = $("[data-local-ranking-metric]");
    const metricWrap = $(".geo-local-metric-select");
    const queueWrap = $(".geo-local-queue-select");
    const ufSelect = $("[data-local-uf-filter]");
    const batchBtn = $("[data-local-batch]");
    const covNote = $("[data-local-coverage-note]");
    if (!localities) return;

    const c = payload.correlation || {};
    const allLocalities = Array.isArray(c.localities) ? c.localities : [];
    const mc = c.municipal_coverage || {};
    if (covNote) {
      covNote.textContent = mc.total_localities
        ? `${intFmt.format(mc.cache_collected || 0)}/${intFmt.format(mc.total_localities)} localidades com cache${mc.deduplicated_variants ? ` · ${intFmt.format(mc.deduplicated_variants)} variações normalizadas` : ""}`
        : "Cache local IBGE";
    }

    if (ufSelect) {
      const ufs = [...new Set(allLocalities.map(r => r.uf).filter(Boolean))].sort();
      const prevUf = localUfFilter;
      ufSelect.innerHTML = '<option value="">Todas as UFs</option>' + ufs.map(code => `<option value="${escape(code)}">${escape(code)}</option>`).join("");
      localUfFilter = ufs.includes(prevUf) ? prevUf : "";
      ufSelect.value = localUfFilter;
    }

    $("[data-local-tabs]")?.querySelectorAll("[data-local-tab]").forEach(btn => {
      btn.setAttribute("aria-selected", String(btn.dataset.localTab === localTab));
    });
    if (metricWrap) metricWrap.hidden = localTab === "pending";
    if (queueWrap) queueWrap.hidden = localTab !== "pending";

    const metricKey = localMetricSelect?.value || "rain_day_incidents";
    const q = foldText(localSearchQuery);

    let filtered = allLocalities.filter(row => {
      if (localUfFilter && row.uf !== localUfFilter) return false;
      if (q) {
        const hay = foldText([row.city, row.uf, row.ibge_code, ...(row.raw_variants || [])].join(" "));
        if (!hay.includes(q)) return false;
      }
      if (localTab === "coincident") {
        if (!row.available || !row.weather) return false;
        if (metricKey === "precipitation_mm") return (row.weather.precipitation_mm ?? 0) > 0;
        if (metricKey === "rainy_days") return (row.weather.rainy_days ?? 0) > 0;
        return (row[metricKey] ?? row.coincident_incidents ?? 0) > 0;
      }
      if (localTab === "pending") {
        if (row.available) return false;
        if (localQueueFilter !== "all" && row.queue_status !== localQueueFilter) return false;
        return true;
      }
      return (row.incidents || 0) > 0 || row.available;
    });

    filtered.sort((a, b) => {
      if (localTab === "coincident") {
        const va = metricKey === "precipitation_mm" ? (a.weather?.precipitation_mm ?? 0) : metricKey === "rainy_days" ? (a.weather?.rainy_days ?? 0) : (a[metricKey] ?? a.coincident_incidents ?? 0);
        const vb = metricKey === "precipitation_mm" ? (b.weather?.precipitation_mm ?? 0) : metricKey === "rainy_days" ? (b.weather?.rainy_days ?? 0) : (b[metricKey] ?? b.coincident_incidents ?? 0);
        return vb - va || (b.incidents || 0) - (a.incidents || 0) || a.uf.localeCompare(b.uf) || a.city.localeCompare(b.city, "pt-BR");
      }
      if (localTab === "pending") {
        return (b.eligible_for_collection ? 1 : 0) - (a.eligible_for_collection ? 1 : 0) || (b.incidents || 0) - (a.incidents || 0) || a.uf.localeCompare(b.uf) || a.city.localeCompare(b.city, "pt-BR");
      }
      if (metricKey === "rain_day_incidents" || metricKey === "coincident_incidents") {
        return (b.incidents || 0) - (a.incidents || 0) || (b.coincident_incidents || 0) - (a.coincident_incidents || 0) || a.uf.localeCompare(b.uf) || a.city.localeCompare(b.city, "pt-BR");
      }
      const va = metricKey === "precipitation_mm" ? (a.weather?.precipitation_mm ?? -1) : metricKey === "rainy_days" ? (a.weather?.rainy_days ?? -1) : (a[metricKey] ?? 0);
      const vb = metricKey === "precipitation_mm" ? (b.weather?.precipitation_mm ?? -1) : metricKey === "rainy_days" ? (b.weather?.rainy_days ?? -1) : (b[metricKey] ?? 0);
      return vb - va || (b.incidents || 0) - (a.incidents || 0) || a.uf.localeCompare(b.uf) || a.city.localeCompare(b.city, "pt-BR");
    });

    if (batchBtn) {
      const hasEligiblePending = allLocalities.some(r => !r.available && r.eligible_for_collection);
      batchBtn.hidden = !(panel.dataset.localBatchUrl && localTab === "pending" && hasEligiblePending);
    }

    const totalItems = filtered.length;
    const totalPages = Math.max(1, Math.ceil(totalItems / localPageSize));
    if (localPage > totalPages) localPage = totalPages;
    const slice = filtered.slice((localPage - 1) * localPageSize, localPage * localPageSize);

    const statusMeta = {
      collected: ["Coletado", "geo-queue-chip--ready"],
      pending: ["Na fila", "geo-queue-chip--pending"],
      running: ["Processando", "geo-queue-chip--running"],
      error: ["Com erro", "geo-queue-chip--error"],
      awaiting_queue: ["Aguardando fila", ""],
      validation_pending: ["Validação pendente", ""],
    };

    const emptyCopy = {
      incidents: "Nenhuma localidade com incidentes neste recorte.",
      coincident: "Nenhuma localidade com cache municipal e coincidência identificada neste recorte.",
      pending: "Nenhuma localidade aguardando coleta municipal nesta seleção.",
    }[localTab];

    localities.innerHTML = slice.length
      ? `<div class="geo-local-list">${slice.map(row => {
          const [chipLabel, chipCls] = statusMeta[row.queue_status] || [row.available ? "Coletado" : "Sem cache", ""];
          const ibgeTag = row.ibge_code ? ` <small class="geo-section-note">IBGE ${escape(row.ibge_code)}</small>` : "";
          const incidentText = `${intFmt.format(row.incidents || 0)} ${plural(row.incidents || 0, "incidente técnico", "incidentes técnicos")}`;
          const detailText = row.available && row.weather
            ? `${incidentText} · ${intFmt.format(row.coincident_incidents || 0)} ${plural(row.coincident_incidents || 0, "coincidente", "coincidentes")} · chuva ${quantity(row.weather.precipitation_mm, "mm")}`
            : `${incidentText} · ${row.pending_reason || "Aguardando coleta municipal"}`;
          const variantText = (row.merged_variants || 0) > 1 && row.raw_variants?.length
            ? `<div class="geo-locality-variants">${intFmt.format(row.merged_variants)} variações agrupadas (${escape(row.raw_variants.slice(0, 3).join("; "))})</div>`
            : "";
          const canAct = Boolean(panel.dataset.localUrl && (row.available || row.eligible_for_collection));
          const actionBtn = canAct
            ? `<button type="button" class="geo-text-action" data-local-city="${escape(row.city)}" data-local-uf="${escape(row.uf)}" data-local-action="${row.available ? "view" : "collect"}">${row.available ? "Investigar" : "Coletar local"}</button>`
            : "";
          return `<div class="geo-locality-card"><div class="geo-locality-info"><div class="geo-locality-title"><span>${escape(row.city)} / ${escape(row.uf)}</span>${ibgeTag}<span class="geo-queue-chip ${chipCls}">${escape(chipLabel)}</span></div><div class="geo-locality-meta">${escape(detailText)}</div>${variantText}</div>${actionBtn}</div>`;
        }).join("")}</div>`
      : `<p class="geo-status">${escape(emptyCopy)}</p>`;

    if (pagination) {
      pagination.hidden = totalItems <= 5 && localPageSize === 5;
      if (!pagination.hidden) {
        const nextLimit = localPageSize === 5 ? 10 : localPageSize === 10 ? 25 : 5;
        const limitLabel = localPageSize === 5 ? "Mostrar Top 10" : localPageSize === 10 ? "Mostrar 25" : "Mostrar Top 5";
        pagination.innerHTML = `<span>Exibindo ${intFmt.format(slice.length)} de ${intFmt.format(totalItems)}</span>`
          + `<div class="geo-local-pagination-actions">`
          + (totalItems > 5 ? `<button type="button" class="geo-text-action" data-local-limit="${nextLimit}">${limitLabel}</button>` : "")
          + (totalPages > 1 ? `<button type="button" class="geo-text-action" data-local-page="${Math.max(1, localPage - 1)}" ${localPage <= 1 ? "disabled" : ""}>Anterior</button><button type="button" class="geo-text-action" data-local-page="${Math.min(totalPages, localPage + 1)}" ${localPage >= totalPages ? "disabled" : ""}>Próxima</button>` : "")
          + `</div>`;
      }
    }
  }

  function renderClimateExtras(payload) {
    const climateSection = $("[data-climate-intelligence-section]");
    const highlightsEl = $("[data-weather-highlights]");
    const rankingToggle = $("[data-climate-ranking-toggle]");
    const ranking = $("[data-weather-ranking]"), select = $("[data-climate-ranking]");
    const context = $("[data-weather-correlation]"), localSection = $("[data-weather-local-section]");
    if (!ranking || !select || !context) return;

    const hl = payload.climate_highlights || {};
    if (highlightsEl) {
      const cards = [];
      if (hl.max_precipitation_mm) cards.push({ label: "Maior chuva (mm)", item: hl.max_precipitation_mm, value: quantity(hl.max_precipitation_mm.value, "mm") });
      if (hl.max_rainy_days) cards.push({ label: "Mais dias chuvosos", item: hl.max_rainy_days, value: `${intFmt.format(hl.max_rainy_days.value)} ${plural(hl.max_rainy_days.value, "dia", "dias")}${hl.max_rainy_days.rainy_days_percent != null ? ` (${fmt.format(hl.max_rainy_days.rainy_days_percent)}%)` : ""}` });
      if (hl.max_gust_kmh) cards.push({ label: "Maior rajada", item: hl.max_gust_kmh, value: quantity(hl.max_gust_kmh.value, "km/h") });
      if (hl.max_temperature_c) cards.push({ label: "Maior máxima", item: hl.max_temperature_c, value: quantity(hl.max_temperature_c.value, "°C") });
      if (hl.min_temperature_c) cards.push({ label: "Menor mínima", item: hl.min_temperature_c, value: quantity(hl.min_temperature_c.value, "°C") });
      highlightsEl.innerHTML = cards.map(c =>
        `<button type="button" class="geo-highlight-card" data-climate-uf="${escape(c.item.uf)}"><span>${escape(c.label)}</span><strong>${escape(c.value)}</strong><small>${escape(c.item.name)} (${escape(c.item.uf)})</small></button>`
      ).join("");
    }

    const metricKey = select.value || "precipitation_mm";
    const stateSummary = Array.isArray(payload.state_climate_summary) && payload.state_climate_summary.length
      ? [...payload.state_climate_summary]
      : (payload.rankings?.[metricKey] || []).map(r => ({ uf: r.uf, name: r.uf, [metricKey]: r.value, value: r.value, technical_incidents: 0, covered_local_incidents: 0, coincident_incidents: null }));

    const isMinMetric = metricKey === "temperature_min_c";
    const sortedStates = stateSummary
      .filter(r => r[metricKey] != null || r.value != null)
      .sort((a, b) => {
        const va = a[metricKey] ?? a.value ?? 0;
        const vb = b[metricKey] ?? b.value ?? 0;
        return isMinMetric ? va - vb || a.uf.localeCompare(b.uf) : vb - va || a.uf.localeCompare(b.uf);
      });

    if (rankingToggle) {
      rankingToggle.hidden = sortedStates.length <= 10;
      rankingToggle.textContent = showAllStates ? "Mostrar Top 10" : `Ver todas as ${sortedStates.length} UFs`;
    }

    const visibleStates = showAllStates ? sortedStates : sortedStates.slice(0, 10);
    const maxVal = Math.max(1e-6, ...sortedStates.map(r => Math.abs(Number(r[metricKey] ?? r.value ?? 0))));

    ranking.innerHTML = visibleStates.length
      ? '<p class="geo-status">Estados com cobertura territorial completa (ERA5). Clique para focar a UF:</p>'
        + `<div class="geo-climate-uf-list">${visibleStates.map((row, i) => {
          const rawVal = Number(row[metricKey] ?? row.value ?? 0);
          const barPct = Math.max(4, Math.min(100, (Math.abs(rawVal) / maxVal) * 100));
          const flag = flagsData[row.uf] ? `<img src="${escape(flagsData[row.uf])}" alt="" width="20" height="14" loading="lazy">` : "";
          const techCount = row.technical_incidents || 0;
          const techTxt = `${intFmt.format(techCount)} ${plural(techCount, "técnico", "técnicos")}`;
          const localTxt = row.covered_local_incidents > 0
            ? `${intFmt.format(row.coincident_incidents || 0)} coincid. (${intFmt.format(row.covered_local_incidents)} avaliados)`
            : "Sem base local suficiente";
          return `<button type="button" class="geo-climate-uf-row" data-climate-uf="${escape(row.uf)}">`
            + `<span class="geo-climate-uf-main"><b>#${String(i + 1).padStart(2, "0")}</b>${flag}<strong>${escape(row.name || row.uf)} (${escape(row.uf)})</strong></span>`
            + `<span class="geo-climate-uf-value">${escape(formatStateMetricValue(metricKey, row))}</span>`
            + `<span class="geo-climate-uf-meta"><span>${escape(techTxt)}</span><span>${escape(localTxt)}</span></span>`
            + `<span class="geo-climate-uf-bar"><i style="width:${barPct}%"></i></span>`
            + `</button>`;
        }).join("")}</div>`
      : '<p class="geo-status">Sem cache climático territorial completo para este período.</p>';

    const c = payload.correlation || {};
    const statusCounts = c.status_counts || {};
    const statusCards = [
      ["Sem dados suficientes", statusCounts.insufficient ?? c.insufficient ?? 0, ""],
      ["Sem coincidência identificada", statusCounts.none ?? c.none ?? 0, ""],
      ["Coincidência identificada", statusCounts.coincidence ?? ((c.coincidence || 0) + (c.contextual || 0)), "geo-corr-state-card--coincidence"],
      ["Causa confirmada", statusCounts.confirmed_cause ?? statusCounts.confirmed ?? 0, "geo-corr-state-card--confirmed"],
    ];

    const byCat = c.by_category || {};
    const catLabels = { rompimento: "Rompimento", massiva: "Massiva", equipamento: "Equipamento", energia: "Energia" };
    const catHtml = Object.entries(catLabels).map(([key, lbl]) => {
      const row = byCat[key];
      if (!row || !row.total) return "";
      const evaluable = row.evaluable ?? row.covered ?? 0;
      const missingLocal = (row.without_location ?? 0) + (row.without_cache ?? 0);
      return `<button type="button" class="geo-corr-cat-row" data-explore-category="${key}">`
        + `<span><strong>${lbl}</strong><small> · ${intFmt.format(evaluable)}/${intFmt.format(row.total)} avaliáveis (${intFmt.format(missingLocal)} sem base local)</small></span>`
        + `<b>${intFmt.format(row.coincident || 0)} coinc.</b></button>`;
    }).filter(Boolean).join("");

    const routeParts = [];
    if (c.uf_only_tickets) routeParts.push(`${intFmt.format(c.uf_only_tickets)} somente UF`);
    if (c.multi_city_tickets) routeParts.push(`${intFmt.format(c.multi_city_tickets)} multi-localidade`);

    const topRainyDaysHtml = (c.top_rainy_incident_days || []).length
      ? `<div class="geo-corr-sublist"><h4>Dias com maior coincidência</h4>${c.top_rainy_incident_days.slice(0, 5).map(d =>
          `<div class="geo-analysis-row"><span>${escape(formatDateBR(d.date))}</span><b>${intFmt.format(d.coincident || 0)} coinc. / ${intFmt.format(d.incidents || 0)} ${plural(d.incidents || 0, "técnico", "técnicos")}</b></div>`
        ).join("")}</div>`
      : "";

    const topCoincidentUfsHtml = (c.coincident_by_uf || []).length
      ? `<div class="geo-corr-sublist"><h4>UFs com base local avaliável</h4>${c.coincident_by_uf.slice(0, 5).map(u =>
          `<button type="button" class="geo-analysis-row" data-climate-uf="${escape(u.uf)}"><span>${escape(u.uf)} (${intFmt.format(u.covered_incidents)} avaliados)</span><b>${intFmt.format(u.coincident_incidents)} coinc.</b></button>`
        ).join("")}</div>`
      : "";

    const regRain = (c.region_rain_incidents || []).filter(r => r.tickets > 0).sort((a, b) => b.tickets - a.tickets);
    const regRainHtml = regRain.length
      ? `<div class="geo-corr-sublist"><h4>Macrorregiões em janela chuvosa</h4>${regRain.map(row =>
          `<p class="geo-analysis-row"><span>${escape(payload.catalog?.regions?.find(r => r.id === row.region_id)?.nome || "Região")} · janela chuvosa</span><b>${intFmt.format(row.tickets)}</b></p>`
        ).join("")}</div>`
      : "";

    const windTilesHtml = `<div class="geo-corr-wind-summary">`
      + `<div class="geo-corr-state-card"><strong>${intFmt.format(c.rain_incidents ?? 0)}</strong><span>Chuva na janela (${intFmt.format(c.rain_day_incidents ?? 0)} no dia)</span></div>`
      + `<div class="geo-corr-state-card"><strong>${intFmt.format(c.wind_incidents ?? 0)}</strong><span>Vento / rajada (${intFmt.format(c.wind_equipment_energy ?? 0)} equip./energia)</span></div>`
      + `</div>`;

    const eligibleCount = c.eligible_tickets ?? 0;
    context.innerHTML = `<p class="geo-status">${intFmt.format(eligibleCount)} ${plural(eligibleCount, "incidente técnico elegível", "incidentes técnicos elegíveis")} · ${intFmt.format(c.located_tickets ?? 0)} com município CMDB · ${intFmt.format(c.covered_tickets ?? 0)} avaliáveis com cache local horária${routeParts.length ? ` (${routeParts.join(", ")})` : ""}.</p>`
      + `<div class="geo-corr-states">${statusCards.map(([lbl, n, cls]) => `<div class="geo-corr-state-card ${cls}"><strong>${intFmt.format(n)}</strong><span>${escape(lbl)}</span></div>`).join("")}</div>`
      + windTilesHtml
      + (catHtml ? `<div class="geo-corr-categories">${catHtml}</div>` : "")
      + topRainyDaysHtml
      + topCoincidentUfsHtml
      + regRainHtml
      + `<p class="geo-corr-disclaimer">${escape(c.route_caveat || "")} Coincidência temporal ou geográfica com chuva ou rajada é indício operacional e não comprova causa meteorológica.</p>`;

    renderMunicipalList(payload);

    if (climateSection) {
      const hasAnyClimate = payload.climate?.available || state.mode === "climate" || (c.localities?.length || 0) > 0 || eligibleCount > 0;
      climateSection.hidden = !hasAnyClimate;
    }
    if (localSection) {
      localSection.hidden = !(c.localities?.length || eligibleCount > 0 || payload.climate?.available);
    }
  }

  $("[data-climate-ranking]")?.addEventListener("change", () => current && renderClimateExtras(current));
  $("[data-climate-ranking-toggle]")?.addEventListener("click", () => { showAllStates = !showAllStates; if (current) renderClimateExtras(current); });
  $("[data-local-ranking-metric]")?.addEventListener("change", () => { localPage = 1; if (current) renderMunicipalList(current); });
  $("[data-local-uf-filter]")?.addEventListener("change", event => { localUfFilter = event.target.value; localPage = 1; if (current) renderMunicipalList(current); });
  $("[data-local-queue-filter]")?.addEventListener("change", event => { localQueueFilter = event.target.value; localPage = 1; if (current) renderMunicipalList(current); });
  $("[data-local-search]")?.addEventListener("input", event => { localSearchQuery = event.target.value; localPage = 1; if (current) renderMunicipalList(current); });
  $("[data-local-tabs]")?.addEventListener("click", event => {
    const btn = event.target.closest("[data-local-tab]");
    if (!btn) return;
    localTab = btn.dataset.localTab || "incidents";
    localPage = 1;
    if (current) renderMunicipalList(current);
  });
  $("[data-local-pagination]")?.addEventListener("click", event => {
    const limitBtn = event.target.closest("[data-local-limit]");
    if (limitBtn) {
      localPageSize = Number(limitBtn.dataset.localLimit) || 5;
      localPage = 1;
      if (current) renderMunicipalList(current);
      return;
    }
    const pageBtn = event.target.closest("[data-local-page]");
    if (pageBtn) {
      localPage = Number(pageBtn.dataset.localPage) || 1;
      if (current) renderMunicipalList(current);
    }
  });
  page.addEventListener("click", event => {
    const ufBtn = event.target.closest("[data-climate-uf]");
    if (ufBtn && panel.contains(ufBtn)) selectUF(ufBtn.dataset.climateUf);
  });

  $("[data-weather-chart]")?.addEventListener("mousemove", event => {
    const target = event.target.closest("[data-chart-date]");
    const caption = $("[data-chart-caption]");
    if (!target || !caption) return;
    const count = Number(target.dataset.chartTickets || 0);
    caption.textContent = `${formatDateBR(target.dataset.chartDate)}: ${target.dataset.chartClimate} · ${count} ${plural(count, "incidente", "incidentes")}`;
  });

  let localContext = null;
  async function localRequest(city, targetUF, collect = false, ticket = "") {
    const result = $("[data-local-weather-result]");
    if (!result) return;
    result.textContent = collect ? "Solicitando coleta municipal ao Worker…" : "Consultando cache municipal…";
    if (!/^\d{4}-\d{2}$/.test(current?.period?.month || "")) {
      result.textContent = "Selecione um mês para investigar a localidade.";
      return;
    }
    const url = urlFor(panel.dataset.localUrl, { city, uf: targetUF, period: current.period.month, ticket });
    const csrf = page.querySelector("[name=csrfmiddlewaretoken]")?.value;
    try {
      const response = await fetch(url, {
        method: collect ? "POST" : "GET",
        ...(collect ? { body: new URLSearchParams(url.searchParams), headers: { "X-CSRFToken": csrf || "" } } : {}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || data.error || "Consulta local indisponível.");
      localContext = { city, uf: targetUF };
      const statusLabel = { success: "Concluído", pending: "Na fila", running: "Processando", error: "Falhou", unavailable: "Sem cache" }[data.status] || data.status;
      let html = `<div class="geo-local-result-card"><h4>${escape(data.city || city)} / ${escape(targetUF)}</h4><p class="geo-status">${escape(statusLabel)} · ${escape(data.precision)} ${escape(data.last_error || "")}</p>`;
      if (data.weather) {
        html += `<p class="geo-status">${quantity(data.weather.precipitation_mm, "mm")} · ${quantity(data.weather.temperature_mean_c, "°C")} · rajada ${quantity(data.weather.gust_max_kmh, "km/h")}. ${escape(data.source)} · IBGE ${escape(data.ibge_code)}.</p><form data-local-window-form><label>TicketID OTRS <input inputmode="numeric" pattern="[0-9]+" name="ticket" required aria-label="TicketID real para investigar janela horária"></label><button class="geo-text-action">Investigar janela horária</button></form>`;
      }
      if (data.window) {
        const winLabel = { insufficient: "Sem dados suficientes", none: "Sem coincidência identificada", coincidence: "Coincidência temporal e geográfica", contextual: "Evidência contextual relevante" }[data.window.state] || data.window.state;
        html += `<p class="geo-status">${escape(winLabel)} · ${escape(data.window.time_basis)} Coincidência não confirma causa.</p>` + data.window.hourly.map(row => `<p class="geo-analysis-row"><span>${escape(row.at.slice(0, 16))} UTC</span><b>${quantity(row.precipitation, "mm")} · ${quantity(row.wind_gusts_10m, "km/h")}</b></p>`).join("");
      }
      html += `</div>`;
      result.innerHTML = html;
      if (collect) await update();
    } catch (error) {
      result.innerHTML = `<p class="geo-status">${escape(error.message)}</p>`;
    }
  }

  $("[data-weather-localities]")?.addEventListener("click", event => {
    const b = event.target.closest("[data-local-city]");
    if (b) localRequest(b.dataset.localCity, b.dataset.localUf, b.dataset.localAction === "collect");
  });
  $("[data-local-batch]")?.addEventListener("click", async event => {
    const btn = event.currentTarget;
    const result = $("[data-local-weather-result]");
    if (!panel.dataset.localBatchUrl || !/^\d{4}-\d{2}$/.test(current?.period?.month || "")) return;
    btn.disabled = true;
    try {
      const targetUrl = urlFor(panel.dataset.localBatchUrl, { period: current.period.month, ...(localUfFilter ? { uf: localUfFilter } : {}) });
      const csrf = page.querySelector("[name=csrfmiddlewaretoken]")?.value;
      const body = new URLSearchParams(targetUrl.searchParams);
      if (csrf) body.set("csrfmiddlewaretoken", csrf);
      const response = await fetch(panel.dataset.localBatchUrl, {
        method: "POST",
        body,
        headers: { "X-CSRFToken": csrf || "" },
      });
      const data = await response.json().catch(() => ({ message: "Falha ao enfileirar localidades." }));
      if (!response.ok) throw new Error(data.message || data.error || "Não foi possível enfileirar localidades.");
      if (result) result.innerHTML = `<div class="geo-local-result-card"><p class="geo-status">${escape(data.message || "Fila atualizada.")}</p></div>`;
      await update();
    } catch (error) {
      if (result) result.innerHTML = `<p class="geo-status">${escape(error.message)}</p>`;
    } finally {
      btn.disabled = false;
    }
  });
  $("[data-local-weather-result]")?.addEventListener("submit", event => {
    if (!event.target.matches("[data-local-window-form]") || !localContext) return;
    event.preventDefault();
    localRequest(localContext.city, localContext.uf, false, new FormData(event.target).get("ticket"));
  });

  const changed = () => refresh().catch(() => { if (status) status.textContent = "Não foi possível atualizar este recorte."; });
  region?.addEventListener("change", () => {
    state.region = region.value;
    if (state.selectedUF && !(regions[state.region] || allUF).includes(state.selectedUF)) {
      state.selectedUF = "";
      const notice = $("[data-filter-notice]");
      if (notice) notice.textContent = "A UF anterior foi removida porque não pertence à região selecionada.";
    }
    syncControls();
    changed();
  });
  uf?.addEventListener("change", () => selectUF(uf.value));
  month?.addEventListener("change", () => {
    if (!month.value || !month.reportValidity()) return;
    state.period = month.value;
    state.dateFrom = "";
    state.dateTo = "";
    changed();
  });
  category?.addEventListener("change", () => {
    state.category = category.value;
    changed();
  });
  layer?.addEventListener("change", () => {
    state.layer = layer.value;
    if (["rain", "wind", "wind_speed", "temperature", "temperature_min", "temperature_max", "direction", "correlation"].includes(state.layer)) state.mode = "climate";
    else if (state.layer === "calls" || state.layer === "motives") state.mode = state.layer;
    changed();
  });

  $("[data-weather-refresh]")?.addEventListener("click", changed);
  $("[data-weather-toggle]")?.addEventListener("click", () => setPanelState(panel.dataset.panelState === "collapsed" ? "summary" : "collapsed"));
  $("[data-analysis-expand]")?.addEventListener("click", () => setPanelState(panel.dataset.panelState === "expanded" ? "summary" : "expanded"));
  $("[data-analysis-close]")?.addEventListener("click", () => { setPanelState("collapsed"); $("[data-weather-toggle]")?.focus(); });
  $("[data-timeline-expand]")?.addEventListener("click", () => setPanelState(panel.dataset.panelState === "expanded" ? "summary" : "expanded"));

  timelineToggle?.addEventListener("click", () => setTimelineOpen(true));
  timelineCollapse?.addEventListener("click", () => { setTimelineOpen(false); timelineToggle?.focus(); });

  $("[data-timeline-reset]")?.addEventListener("click", () => { pause(); if (dayInput) dayInput.value = 0; showDay(); });
  page.addEventListener("click", event => {
    const catBtn = event.target.closest("[data-explore-category]");
    if (catBtn && panel.contains(catBtn)) {
      state.category = catBtn.dataset.exploreCategory || "";
      if (!["calls", "repeat", "motives", "climate"].includes(state.mode)) state.mode = "calls";
      if (state.mode === "base" || state.mode === "projects") state.layer = "calls";
      onFilterUpdate?.();
      changed();
      return;
    }
    const regBtn = event.target.closest("[data-filter-region]");
    if (regBtn && panel.contains(regBtn)) {
      const nextReg = regBtn.dataset.filterRegion || "";
      state.region = state.region === nextReg ? "" : nextReg;
      if (state.selectedUF && !(regions[state.region] || allUF).includes(state.selectedUF)) state.selectedUF = "";
      syncControls();
      onFilterUpdate?.();
      changed();
    }
  });

  const pause = () => {
    clearInterval(timelineTimer);
    timelineTimer = 0;
    if (play) {
      play.textContent = "▶";
      play.setAttribute("aria-label", "Reproduzir dias");
    }
  };

  function syncMode() {
    if (month) {
      month.disabled = state.mode === "base";
      month.title = month.disabled ? "Base representa o inventário CMDB atual; período não altera os links." : "";
    }
    const icons = $("[data-weather-icons]"), iconType = $("[data-weather-icon-type]"), anim = $("[data-weather-animate]");
    for (const control of [icons, iconType]) {
      if (control) {
        control.disabled = state.mode !== "climate";
        control.title = state.mode === "climate" ? "" : "Disponível no modo Clima.";
      }
    }
    const temporal = ["calls", "motives", "climate"].includes(state.mode);
    if (anim) {
      anim.disabled = !temporal;
      anim.title = temporal ? "Aplica transição suave diária (estimativa espacial, sem radar em tempo real)." : "Disponível em Chamados, Motivos e Clima.";
    }
    if (timeline) {
      timeline.hidden = !temporal;
      page.classList.toggle("has-timeline", temporal);
      if (!temporal) {
        pause();
        state.day = "";
        setTimelineOpen(false);
      } else {
        setTimelineOpen(timelineOpen);
      }
    }
  }

  function showDay() {
    state.day = current?.daily[Number(dayInput?.value || 0) - 1]?.date || "";
    render();
  }

  dayInput?.addEventListener("input", () => { pause(); showDay(); });
  function start() {
    pause();
    if (!current || !play || !dayInput) return;
    play.textContent = "Ⅱ";
    play.setAttribute("aria-label", "Pausar reprodução");
    timelineTimer = setInterval(() => {
      dayInput.value = Number(dayInput.value) >= Number(dayInput.max) ? 1 : Number(dayInput.value) + 1;
      showDay();
    }, Number($("[data-timeline-speed]")?.value || 1200));
  }
  play?.addEventListener("click", () => timelineTimer ? pause() : start());
  $("[data-timeline-speed]")?.addEventListener("change", () => { if (timelineTimer) start(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) pause(); });
  $("[data-geo-fullscreen]")?.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      if (status) status.textContent = "Tela cheia indisponível neste navegador.";
    }
  });
  $("[data-geo-collect-form]")?.addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("[data-weather-collect]");
    button.disabled = true;
    try {
      const form = event.currentTarget, body = new URLSearchParams(urlFor(panel.dataset.url).searchParams);
      const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
      body.set("csrfmiddlewaretoken", csrf);
      if (pendingPlanToken) body.set("preview_token", pendingPlanToken);
      const response = await fetch(panel.dataset.collectUrl, { method: "POST", body, headers: { "X-CSRFToken": csrf } });
      const result = await response.json().catch(() => ({ message: response.status === 403 ? "Sem autorização ou sessão CSRF expirada. Recarregue a página." : "Resposta indisponível. Consulte a integração." }));
      status.dataset.state = result.status || "unavailable";
      if (!response.ok) throw new Error(result.message || result.error?.message || result.error || "Não foi possível solicitar a coleta.");
      status.textContent = result.message;
      if (result.status === "preview") {
        pendingPlanToken = result.preview_token;
        button.textContent = `Autorizar lote · ${result.estimated_requests} requisições`;
        return;
      }
      await update();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  syncControls();
  return {
    update,
    syncMode,
    close: () => { if (panel.dataset.panelState !== "collapsed") setPanelState("collapsed"); },
    openSummary: () => setPanelState("summary"),
    openExpanded: () => setPanelState("expanded"),
    getPanelState: () => panel.dataset.panelState || "collapsed",
    collapse: () => { if (panel.dataset.panelState === "expanded") setPanelState("summary"); },
    allowedUFs: () => current?.summary?.presentation ? current.summary.allowed_ufs : (regions[state.region] || allUF),
  };
}
