(() => {
  "use strict";

  const modal = document.querySelector("dialog[data-plant-history]");
  if (!modal) return;

  const dataNode = document.getElementById("plant-history-initial-data");
  const openTriggers = document.querySelectorAll("[data-plant-modal-open]");
  const closeButtons = modal.querySelectorAll("[data-plant-modal-close]");
  const tabButtons = modal.querySelectorAll("[data-plant-tab]");
  const tabPanels = modal.querySelectorAll("[data-plant-panel]");
  const filtersForm = modal.querySelector("[data-plant-filters]");
  const resetBtn = modal.querySelector("[data-plant-reset]");
  const contextBar = modal.querySelector("[data-plant-context-bar]");
  const modeButtons = modal.querySelectorAll("[data-plant-mode]");
  const chartBox = modal.querySelector("[data-plant-chart]");
  const legendBox = modal.querySelector("[data-plant-legend]");
  const tableBody = modal.querySelector("[data-plant-table-body]");
  const noteEl = modal.querySelector("[data-plant-note]");
  const syncLabelEl = modal.querySelector("[data-plant-sync-label]");
  const tooltip = modal.querySelector("[data-plant-tooltip]");
  const compareSelectA = modal.querySelector("[data-compare-select='a']");
  const compareSelectB = modal.querySelector("[data-compare-select='b']");
  const compareNotice = modal.querySelector("[data-compare-notice]");
  const compareCards = modal.querySelector("[data-compare-cards]");
  const compareHeadA = modal.querySelector("[data-compare-head-a]");
  const compareHeadB = modal.querySelector("[data-compare-head-b]");
  const compareBody = modal.querySelector("[data-compare-table-body]");

  const endpoint = modal.dataset.endpoint || "";
  const defaultStart = modal.dataset.start || "";
  const defaultEnd = modal.dataset.end || "";
  const pageContext = modal.dataset.context || "dashboard";

  let currentData = null;
  let currentMode = "rates";
  let currentTab = "overview";
  let includeCancelled = true;
  let includeImprocedente = true;
  let lastTrigger = null;

  try {
    currentData = dataNode ? JSON.parse(dataNode.textContent) : null;
  } catch {
    currentData = null;
  }

  const fmtInt = value => (value === null || value === undefined ? "—" : Number(value).toLocaleString("pt-BR"));
  const fmtDec = (value, d = 1) => (
    value === null || value === undefined ? "—" : Number(value).toFixed(d).replace(".", ",")
  );
  const fmtSignedInt = value => {
    if (value === null || value === undefined) return "—";
    const num = Number(value);
    const sign = num > 0 ? "+" : "";
    return `${sign}${num.toLocaleString("pt-BR")}`;
  };
  const fmtSignedDec = (value, d = 1, suffix = "") => {
    if (value === null || value === undefined) return "—";
    const num = Number(value);
    const sign = num > 0 ? "+" : "";
    return `${sign}${num.toFixed(d).replace(".", ",")}${suffix}`;
  };
  const fmtMom = value => fmtSignedDec(value, 1, "%");
  const escapeHtml = str => String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

  const setText = (selector, text) => {
    const el = modal.querySelector(selector);
    if (el) el.textContent = text;
  };

  const setActiveTab = tabName => {
    currentTab = tabName;
    tabButtons.forEach(btn => {
      const active = btn.dataset.plantTab === tabName;
      btn.setAttribute("aria-selected", String(active));
    });
    tabPanels.forEach(panel => {
      panel.hidden = panel.dataset.plantPanel !== tabName;
    });
    if (tabName === "evolution" && currentData) {
      renderChart(currentData);
    } else if (tabName === "compare" && currentData) {
      renderCompare();
    }
  };

  const populateSelect = (name, options, selectedValue, emptyLabel) => {
    const select = filtersForm?.querySelector(`[name='${name}']`);
    if (!select) return;
    const current = selectedValue !== undefined ? selectedValue : select.value;
    const items = [`<option value="">${escapeHtml(emptyLabel)}</option>`];
    (options || []).forEach(opt => {
      if (typeof opt === "string") {
        items.push(`<option value="${escapeHtml(opt)}">${escapeHtml(opt)}</option>`);
      } else if (opt && typeof opt === "object") {
        items.push(`<option value="${escapeHtml(opt.code)}">${escapeHtml(opt.name)}</option>`);
      }
    });
    select.innerHTML = items.join("");
    if (current) select.value = current;
  };

  const syncFilterControls = data => {
    if (!filtersForm || !data) return;
    const opts = data.filter_options || {};
    const sel = data.selected || {};
    populateSelect("operator", opts.operators, sel.operator || "", "Todas");
    populateSelect("project", opts.projects, sel.project || "", "Todos");
    populateSelect("region", opts.regions, sel.region || "", "Todas");
    populateSelect("uf", opts.ufs, sel.uf || "", "Todas");
    const startInput = filtersForm.querySelector("[name='start']");
    const endInput = filtersForm.querySelector("[name='end']");
    if (startInput && data.start) startInput.value = data.start;
    if (endInput && data.end) endInput.value = data.end;
  };

  const showTooltip = (event, row) => {
    if (!tooltip || !row) return;
    const plantLine = row.active_links !== null
      ? `<div class="tip-row"><i class="tip-swatch" style="background:var(--accent-blue)"></i><span>Planta ativa</span><b>${fmtInt(row.active_links)}</b></div>`
      : `<div class="tip-row"><span>Planta ativa</span><b>Base histórica indisponível</b></div>`;
    const rateLine = row.tickets_per_100_links !== null
      ? `<div class="tip-row"><i class="tip-swatch" style="background:var(--accent-teal)"></i><span>Chamados / 100 links</span><b>${fmtDec(row.tickets_per_100_links, 1)}</b></div>
         <div class="tip-row"><i class="tip-swatch" style="background:var(--accent-rose)"></i><span>% Links afetados</span><b>${fmtDec(row.affected_links_pct, 1)}%</b></div>`
      : "";
    tooltip.innerHTML = `
      <div class="tip-head">${escapeHtml(row.label)}${row.is_partial_month ? " · Parcial" : ""}</div>
      ${plantLine}
      <div class="tip-row"><i class="tip-swatch tip-swatch--calls"></i><span>Chamados criados</span><b>${fmtInt(row.tickets)}</b></div>
      <div class="tip-row"><span>Links afetados</span><b>${fmtInt(row.affected_links)}</b></div>
      <div class="tip-row"><span>Links reincidentes</span><b>${fmtInt(row.recurrent_links)}</b></div>
      ${rateLine}
      <div class="tip-row"><span>Cobertura</span><b>${escapeHtml(row.coverage_label)}</b></div>
    `;
    tooltip.hidden = false;
    const pad = 14;
    const x = Math.min(window.innerWidth - tooltip.offsetWidth - pad, event.clientX + pad);
    const y = Math.min(window.innerHeight - tooltip.offsetHeight - pad, event.clientY + pad);
    tooltip.style.left = `${Math.max(pad, x)}px`;
    tooltip.style.top = `${Math.max(pad, y)}px`;
  };

  const hideTooltip = () => {
    if (tooltip) tooltip.hidden = true;
  };

  const renderOverview = data => {
    const ov = data?.overview;
    if (!ov) return;
    if (syncLabelEl) syncLabelEl.textContent = ov.cmdb_last_sync_label || "—";
    setText("[data-ov-registered]", fmtInt(ov.total_registered));
    setText("[data-ov-active]", fmtInt(ov.active_links));
    setText("[data-ov-active-detail]", `${ov.active_share_label || "0,0%"} da base · ${fmtInt(ov.operational_links)} operacionais${ov.maintenance_links ? ` · ${fmtInt(ov.maintenance_links)} em manutenção` : ""}`);
    setText("[data-ov-canceled]", fmtInt(ov.canceled_links));
    setText("[data-ov-canceled-detail]", `${ov.canceled_share_label || "0,0%"} da base cadastrada`);
    setText("[data-ov-unidentified]", fmtInt(ov.unidentified_links));
    setText("[data-ov-unidentified-detail]", `${fmtInt(ov.unidentified_operator_links)} sem operadora · ${fmtInt(ov.unidentified_project_links)} sem projeto`);

    const period = ov.period_totals || {};
    setText("[data-ov-period-label]", `${data.start || ""} a ${data.end || ""}`);
    setText("[data-ov-period-tickets]", fmtInt(period.tickets));
    setText("[data-ov-period-affected]", fmtInt(period.affected_links));
    setText("[data-ov-period-affected-pct]", `${period.affected_pct_active_label || "—"} da planta ativa atual`);
    setText("[data-ov-period-recurrent]", fmtInt(period.recurrent_links));
    setText("[data-ov-period-rate]", period.tickets_per_100_active_label || "—");

    const opList = modal.querySelector("[data-ov-operators]");
    if (opList) {
      const ops = ov.operator_distribution || [];
      opList.innerHTML = ops.length
        ? ops.map(op => `
            <li class="plant-dist-row${op.key === "unidentified" ? " is-unidentified" : ""}">
              <span class="plant-dist-name"><i class="plant-op-dot plant-op-dot--${escapeHtml(op.key)}" aria-hidden="true"></i>${escapeHtml(op.label)}</span>
              <span class="plant-dist-bar" aria-hidden="true"><b style="width:${Math.min(100, Number(op.share_pct || 0))}%"></b></span>
              <strong class="plant-dist-counts">${fmtInt(op.active)} <small>/ ${fmtInt(op.total)}</small></strong>
              <span class="plant-dist-share">${escapeHtml(op.share_label)}</span>
            </li>
          `).join("")
        : '<li class="plant-muted">Nenhum link no recorte selecionado.</li>';
    }

    const projList = modal.querySelector("[data-ov-projects]");
    if (projList) {
      const projs = ov.project_distribution || [];
      projList.innerHTML = projs.length
        ? projs.map(proj => `
            <li class="plant-dist-row${proj.is_unidentified ? " is-unidentified" : ""}">
              <span class="plant-dist-name" title="${escapeHtml(proj.label)}">${escapeHtml(proj.label)}</span>
              <span class="plant-dist-bar" aria-hidden="true"><b style="width:${Math.min(100, Number(proj.share_pct || 0))}%"></b></span>
              <strong class="plant-dist-counts">${fmtInt(proj.active)} <small>/ ${fmtInt(proj.total)}</small></strong>
              <span class="plant-dist-share">${escapeHtml(proj.share_label)}</span>
            </li>
          `).join("")
        : '<li class="plant-muted">Nenhum projeto no recorte selecionado.</li>';
    }
  };

  const renderKpis = data => {
    const s = data?.summary || {};
    setText("[data-kpi-month-label]", s.latest_month_label || "—");
    setText("[data-kpi-plant]", s.latest_active_links !== null && s.latest_active_links !== undefined ? fmtInt(s.latest_active_links) : "—");
    setText("[data-kpi-coverage]", s.latest_coverage_label || "Base histórica indisponível");
    setText("[data-kpi-rate]", s.latest_tickets_per_100_label || "—");
    setText("[data-kpi-rate-note]", s.latest_is_partial ? "Mês parcial em andamento" : "Taxa proporcional no mês");
    setText("[data-kpi-affected]", s.latest_affected_pct_label || "—");
    setText("[data-kpi-months]", `${s.months_with_plant ?? 0} / ${s.months_total ?? 0} meses`);
    if (noteEl && data?.methodology_note) noteEl.textContent = data.methodology_note;
  };

  const renderTable = data => {
    if (!tableBody) return;
    const rows = data?.series || [];
    if (!rows.length) {
      tableBody.innerHTML = '<tr><td colspan="9" class="plant-muted">Sem dados para o período selecionado.</td></tr>';
      return;
    }
    tableBody.innerHTML = rows.map(row => {
      const cls = row.coverage_status === "UNAVAILABLE" ? "is-unavailable" : (row.is_partial_month ? "is-partial" : "");
      const partialTag = row.is_partial_month ? ' <span class="plant-badge plant-badge--partial">Parcial</span>' : "";
      const plantText = row.active_links !== null ? fmtInt(row.active_links) : '<span class="plant-muted">Indisponível</span>';
      return `<tr class="${cls}">
        <td><strong>${escapeHtml(row.label)}</strong>${partialTag}</td>
        <td>${plantText}</td>
        <td>${fmtInt(row.tickets)}</td>
        <td>${fmtInt(row.affected_links)}</td>
        <td>${fmtInt(row.recurrent_links)}</td>
        <td><strong>${escapeHtml(row.tickets_per_100_label)}</strong></td>
        <td>${escapeHtml(row.affected_links_pct_label)}</td>
        <td>${fmtMom(row.mom_tickets_pct)}</td>
        <td><span class="plant-badge plant-badge--${escapeHtml((row.coverage_status || "").toLowerCase())}">${escapeHtml(row.coverage_label)}</span></td>
      </tr>`;
    }).join("");
  };

  const renderChart = data => {
    if (!chartBox) return;
    const rows = data?.series || [];
    if (!rows.length) {
      chartBox.innerHTML = '<p class="plant-muted">Sem meses disponíveis neste recorte.</p>';
      return;
    }

    const isRates = currentMode === "rates";
    if (legendBox) {
      legendBox.innerHTML = isRates
        ? `<span><i style="background:var(--accent-teal)"></i>Chamados / 100 links ativos (eixo esq.)</span>
           <span><i style="background:var(--accent-rose);border-radius:50%"></i>% de links afetados (eixo dir.)</span>
           <span><i style="background:var(--border-strong);opacity:.6"></i>Sem snapshot CMDB (base histórica indisponível)</span>`
        : `<span><i style="background:var(--accent-blue)"></i>Planta ativa CMDB (eixo esq.)</span>
           <span><i style="background:var(--accent-orange);border-radius:50%"></i>Chamados criados (eixo dir.)</span>
           <span><i style="background:var(--border-strong);opacity:.6"></i>Sem snapshot CMDB</span>`;
    }

    const width = 920;
    const height = 240;
    const padL = 52;
    const padR = 52;
    const padT = 18;
    const padB = 34;
    const plotW = width - padL - padR;
    const plotH = height - padT - padB;
    const stepX = plotW / Math.max(rows.length, 1);

    const leftValues = rows.map(r => (isRates ? r.tickets_per_100_links : r.active_links)).filter(v => v !== null);
    const rightValues = rows.map(r => (isRates ? r.affected_links_pct : r.tickets)).filter(v => v !== null);
    const maxL = Math.max(1, ...leftValues) * 1.15;
    const maxR = Math.max(1, ...rightValues) * 1.15;

    const yLeft = v => padT + plotH - (v / maxL) * plotH;
    const yRight = v => padT + plotH - (v / maxR) * plotH;

    const gridLines = [0, 0.25, 0.5, 0.75, 1].map(frac => {
      const y = padT + plotH * (1 - frac);
      const lVal = isRates ? fmtDec(maxL * frac, 0) : fmtInt(Math.round(maxL * frac));
      const rVal = isRates ? `${fmtDec(maxR * frac, 0)}%` : fmtInt(Math.round(maxR * frac));
      return `
        <line x1="${padL}" y1="${y}" x2="${width - padR}" y2="${y}" stroke="var(--border-primary)" stroke-dasharray="2 3"/>
        <text x="${padL - 8}" y="${y + 3}" text-anchor="end" class="plant-svg-axis">${lVal}</text>
        <text x="${width - padR + 8}" y="${y + 3}" text-anchor="start" class="plant-svg-axis">${rVal}</text>
      `;
    }).join("");

    const barW = Math.min(28, Math.max(10, stepX * 0.44));
    const bars = [];
    const dots = [];
    const xLabels = [];
    const hitTargets = [];

    rows.forEach((row, index) => {
      const cx = padL + stepX * index + stepX / 2;
      const lVal = isRates ? row.tickets_per_100_links : row.active_links;
      const rVal = isRates ? row.affected_links_pct : row.tickets;

      if (lVal !== null) {
        const y = yLeft(lVal);
        const h = Math.max(3, padT + plotH - y);
        const fill = isRates ? "var(--accent-teal)" : "var(--accent-blue)";
        const opacity = row.is_partial_month ? "0.55" : "0.82";
        const dash = row.is_partial_month ? ' stroke="var(--text-primary)" stroke-dasharray="3 2" stroke-width="1"' : "";
        bars.push(`<rect x="${cx - barW / 2}" y="${y}" width="${barW}" height="${h}" rx="4" fill="${fill}" opacity="${opacity}"${dash}/>`);
      } else {
        bars.push(`<rect x="${cx - barW / 2}" y="${padT + plotH - 8}" width="${barW}" height="8" rx="2" fill="var(--track-bg)" stroke="var(--border-primary)" stroke-dasharray="2 2"/>`);
      }

      if (rVal !== null) {
        const py = yRight(rVal);
        const dotFill = isRates ? "var(--accent-rose)" : "var(--accent-orange)";
        dots.push(`<circle cx="${cx}" cy="${py}" r="3.6" fill="${dotFill}" stroke="var(--surface-primary)" stroke-width="1.5"/>`);
      }

      xLabels.push(`<text x="${cx}" y="${height - 10}" text-anchor="middle" class="plant-svg-label">${escapeHtml(row.label)}</text>`);
      hitTargets.push(`<rect x="${padL + stepX * index}" y="${padT}" width="${stepX}" height="${plotH}" fill="transparent" data-plant-hit="${index}" style="cursor:pointer"/>`);
    });

    const segments = [];
    let currentSeg = [];
    rows.forEach((row, index) => {
      const rVal = isRates ? row.affected_links_pct : row.tickets;
      if (rVal !== null) {
        const cx = padL + stepX * index + stepX / 2;
        currentSeg.push(`${cx.toFixed(1)},${yRight(rVal).toFixed(1)}`);
      } else if (currentSeg.length > 0) {
        segments.push(currentSeg);
        currentSeg = [];
      }
    });
    if (currentSeg.length > 0) segments.push(currentSeg);
    const lineColor = isRates ? "var(--accent-rose)" : "var(--accent-orange)";
    const polylines = segments
      .filter(seg => seg.length >= 2)
      .map(seg => `<polyline fill="none" stroke="${lineColor}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${seg.join(" ")}"/>`)
      .join("");

    chartBox.innerHTML = `
      <svg class="plant-history-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
        ${gridLines}
        ${bars.join("")}
        ${polylines}
        ${dots.join("")}
        ${xLabels.join("")}
        ${hitTargets.join("")}
      </svg>
    `;

    chartBox.querySelectorAll("[data-plant-hit]").forEach(rect => {
      const idx = Number(rect.dataset.plantHit);
      rect.addEventListener("mousemove", ev => showTooltip(ev, rows[idx]));
      rect.addEventListener("mouseenter", ev => showTooltip(ev, rows[idx]));
      rect.addEventListener("mouseleave", hideTooltip);
    });
  };

  const populateCompareSelectors = data => {
    const rows = data?.series || [];
    if (!compareSelectA || !compareSelectB) return;
    const prevA = compareSelectA.value;
    const prevB = compareSelectB.value;
    const optionsHtml = rows.map(row => {
      const tag = row.coverage_status === "UNAVAILABLE"
        ? " (sem snapshot CMDB)"
        : (row.is_partial_month ? " (parcial)" : "");
      return `<option value="${escapeHtml(row.month)}">${escapeHtml(row.label)}${tag}</option>`;
    }).join("");
    compareSelectA.innerHTML = optionsHtml;
    compareSelectB.innerHTML = optionsHtml;

    const validMonths = new Set(rows.map(r => r.month));
    if (validMonths.has(prevA) && validMonths.has(prevB)) {
      compareSelectA.value = prevA;
      compareSelectB.value = prevB;
    } else if (rows.length >= 2) {
      const covered = rows.filter(r => r.active_links !== null);
      if (covered.length >= 2) {
        compareSelectA.value = covered[covered.length - 2].month;
        compareSelectB.value = covered[covered.length - 1].month;
      } else {
        compareSelectA.value = rows[rows.length - 2].month;
        compareSelectB.value = rows[rows.length - 1].month;
      }
    } else if (rows.length === 1) {
      compareSelectA.value = rows[0].month;
      compareSelectB.value = rows[0].month;
    }
  };

  const renderCompare = () => {
    const rows = currentData?.series || [];
    if (!compareCards || !compareBody || !rows.length) {
      if (compareCards) compareCards.innerHTML = '<p class="plant-muted">Sem meses disponíveis para comparação.</p>';
      if (compareBody) compareBody.innerHTML = "";
      return;
    }
    const rowA = rows.find(r => r.month === compareSelectA?.value) || rows[0];
    const rowB = rows.find(r => r.month === compareSelectB?.value) || rows[rows.length - 1];
    if (!rowA || !rowB) return;

    if (compareHeadA) compareHeadA.textContent = `${rowA.label}${rowA.is_partial_month ? " (parcial)" : ""}`;
    if (compareHeadB) compareHeadB.textContent = `${rowB.label}${rowB.is_partial_month ? " (parcial)" : ""}`;

    const notices = [];
    if (rowA.active_links === null || rowB.active_links === null) {
      notices.push("Um ou ambos os meses selecionados não possuem snapshot CMDB verificado ('Base histórica indisponível'); variações da planta e taxas proporcionais são omitidas para preservar rigor metodológico.");
    }
    if (rowA.is_partial_month || rowB.is_partial_month) {
      notices.push("Atenção: pelo menos um dos meses selecionados é parcial (em andamento) ou baseado em snapshot de data única.");
    }
    if (compareNotice) {
      compareNotice.hidden = notices.length === 0;
      compareNotice.textContent = notices.join(" ");
    }

    const diffAbs = (a, b) => (a !== null && b !== null ? b - a : null);
    const diffPct = (a, b) => (a !== null && b !== null && a > 0 ? ((b - a) / a) * 100 : null);

    const plantDiff = diffAbs(rowA.active_links, rowB.active_links);
    const plantPct = diffPct(rowA.active_links, rowB.active_links);
    const ticketsDiff = diffAbs(rowA.tickets, rowB.tickets);
    const ticketsPct = diffPct(rowA.tickets, rowB.tickets);
    const affectedDiff = diffAbs(rowA.affected_links, rowB.affected_links);
    const affectedPct = diffPct(rowA.affected_links, rowB.affected_links);
    const recDiff = diffAbs(rowA.recurrent_links, rowB.recurrent_links);
    const recPct = diffPct(rowA.recurrent_links, rowB.recurrent_links);
    const rateDiff = diffAbs(rowA.tickets_per_100_links, rowB.tickets_per_100_links);
    const ratePct = diffPct(rowA.tickets_per_100_links, rowB.tickets_per_100_links);
    const affPctDiff = diffAbs(rowA.affected_links_pct, rowB.affected_links_pct);
    const affPctRel = diffPct(rowA.affected_links_pct, rowB.affected_links_pct);

    const deltaClass = val => (val === null ? "" : (val > 0 ? "is-up" : (val < 0 ? "is-down" : "")));

    compareCards.innerHTML = `
      <article class="plant-compare-card">
        <span>Planta ativa (${escapeHtml(rowA.label)} → ${escapeHtml(rowB.label)})</span>
        <strong>${rowA.active_links !== null ? fmtInt(rowA.active_links) : "Indisponível"} → ${rowB.active_links !== null ? fmtInt(rowB.active_links) : "Indisponível"}</strong>
        <small class="plant-delta ${deltaClass(plantDiff)}">${plantDiff !== null ? `${fmtSignedInt(plantDiff)} links (${fmtMom(plantPct)})` : "Base histórica indisponível"}</small>
      </article>
      <article class="plant-compare-card">
        <span>Chamados criados (${escapeHtml(rowA.label)} → ${escapeHtml(rowB.label)})</span>
        <strong>${fmtInt(rowA.tickets)} → ${fmtInt(rowB.tickets)}</strong>
        <small class="plant-delta ${deltaClass(ticketsDiff)}">${fmtSignedInt(ticketsDiff)} chamados (${fmtMom(ticketsPct)})</small>
      </article>
      <article class="plant-compare-card">
        <span>Taxa proporcional (/100 links ativos)</span>
        <strong>${escapeHtml(rowA.tickets_per_100_label)} → ${escapeHtml(rowB.tickets_per_100_label)}</strong>
        <small class="plant-delta ${deltaClass(rateDiff)}">${rateDiff !== null ? `${fmtSignedDec(rateDiff, 1, " pts")} (${fmtMom(ratePct)})` : "Requer snapshot nos dois meses"}</small>
      </article>
    `;

    const metricRows = [
      {
        label: "Planta ativa CMDB",
        a: rowA.active_links !== null ? fmtInt(rowA.active_links) : "Indisponível",
        b: rowB.active_links !== null ? fmtInt(rowB.active_links) : "Indisponível",
        abs: plantDiff !== null ? fmtSignedInt(plantDiff) : "—",
        pct: fmtMom(plantPct),
      },
      {
        label: "Chamados criados (NOC)",
        a: fmtInt(rowA.tickets),
        b: fmtInt(rowB.tickets),
        abs: fmtSignedInt(ticketsDiff),
        pct: fmtMom(ticketsPct),
      },
      {
        label: "Links distintos afetados",
        a: fmtInt(rowA.affected_links),
        b: fmtInt(rowB.affected_links),
        abs: fmtSignedInt(affectedDiff),
        pct: fmtMom(affectedPct),
      },
      {
        label: "Links reincidentes (2+ chamados)",
        a: fmtInt(rowA.recurrent_links),
        b: fmtInt(rowB.recurrent_links),
        abs: fmtSignedInt(recDiff),
        pct: fmtMom(recPct),
      },
      {
        label: "Chamados / 100 links ativos",
        a: rowA.tickets_per_100_label,
        b: rowB.tickets_per_100_label,
        abs: rateDiff !== null ? fmtSignedDec(rateDiff, 1) : "—",
        pct: fmtMom(ratePct),
      },
      {
        label: "% de links afetados",
        a: rowA.affected_links_pct_label,
        b: rowB.affected_links_pct_label,
        abs: affPctDiff !== null ? fmtSignedDec(affPctDiff, 1, " p.p.") : "—",
        pct: fmtMom(affPctRel),
      },
    ];

    compareBody.innerHTML = metricRows.map(item => `
      <tr>
        <td><strong>${escapeHtml(item.label)}</strong></td>
        <td>${escapeHtml(item.a)}</td>
        <td>${escapeHtml(item.b)}</td>
        <td>${escapeHtml(item.abs)}</td>
        <td><strong>${escapeHtml(item.pct)}</strong></td>
      </tr>
    `).join("");
  };

  const renderAll = data => {
    if (!data) return;
    currentData = data;
    syncFilterControls(data);
    renderOverview(data);
    renderKpis(data);
    renderChart(data);
    renderTable(data);
    populateCompareSelectors(data);
    renderCompare();
  };

  const buildQueryParams = overrides => {
    const params = new URLSearchParams();
    const fields = ["operator", "project", "region", "uf", "start", "end"];
    fields.forEach(name => {
      const val = overrides && name in overrides
        ? overrides[name]
        : (filtersForm?.querySelector(`[name='${name}']`)?.value || "");
      if (val) params.set(name, val);
    });
    if (!params.get("start") && defaultStart) params.set("start", defaultStart);
    if (!params.get("end") && defaultEnd) params.set("end", defaultEnd);
    params.set("include_cancelled", includeCancelled ? "true" : "false");
    params.set("include_improcedente", includeImprocedente ? "true" : "false");
    return params;
  };

  const fetchFiltered = async (overrides = null) => {
    if (!endpoint) return;
    const params = buildQueryParams(overrides);
    modal.classList.add("is-loading");
    try {
      const response = await fetch(`${endpoint}?${params.toString()}`, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const data = await response.json();
      renderAll(data);
    } catch {
      // Preserve last valid view if network request fails.
    } finally {
      modal.classList.remove("is-loading");
    }
  };

  const readCockpitFilters = () => {
    const cockpitForm = document.querySelector("[data-cockpit] [data-filters]");
    if (!cockpitForm) return null;
    const valOf = name => {
      const el = cockpitForm.querySelector(`[name='${name}']`);
      if (!el) return "";
      if (el.type === "checkbox") return el.checked;
      const v = String(el.value || "").trim();
      return v.toLowerCase() === "all" ? "" : v;
    };
    const start = valOf("start") || defaultStart;
    const end = valOf("end") || defaultEnd;
    const project = valOf("project");
    const uf = valOf("uf");
    const canc = valOf("include_cancelled");
    const imp = valOf("include_improcedente");
    includeCancelled = typeof canc === "boolean" ? canc : true;
    includeImprocedente = typeof imp === "boolean" ? imp : true;
    return { start, end, project, uf, operator: "", region: "" };
  };

  const updateContextBanner = synced => {
    if (!contextBar) return;
    if (!synced) {
      contextBar.hidden = true;
      contextBar.textContent = "";
      return;
    }
    const parts = [];
    if (synced.start && synced.end) parts.push(`Período ${synced.start} a ${synced.end}`);
    if (synced.project) parts.push(`Projeto: ${synced.project}`);
    if (synced.uf) parts.push(`UF: ${synced.uf}`);
    if (!includeCancelled) parts.push("Sem cancelados");
    if (!includeImprocedente) parts.push("Sem improcedentes");
    contextBar.textContent = `Recorte sincronizado com o Cockpit: ${parts.join(" · ")}`;
    contextBar.hidden = parts.length === 0;
  };

  const openModal = trigger => {
    lastTrigger = trigger || null;
    const initialTab = trigger?.dataset.plantInitialTab || "overview";
    setActiveTab(initialTab);
    if (!modal.open) modal.showModal();

    if (pageContext === "cockpit" || trigger?.dataset.plantSyncCockpit === "true") {
      const synced = readCockpitFilters();
      updateContextBanner(synced);
      fetchFiltered(synced || {});
    } else {
      updateContextBanner(null);
      if (currentData) {
        renderAll(currentData);
      } else {
        fetchFiltered();
      }
    }
  };

  const closeModal = () => {
    hideTooltip();
    if (modal.open) modal.close();
    if (lastTrigger && typeof lastTrigger.focus === "function") {
      lastTrigger.focus();
    }
  };

  openTriggers.forEach(trigger => {
    trigger.addEventListener("click", ev => {
      ev.preventDefault();
      openModal(trigger);
    });
  });

  closeButtons.forEach(btn => btn.addEventListener("click", closeModal));

  modal.addEventListener("click", ev => {
    if (ev.target === modal) closeModal();
  });

  modal.addEventListener("cancel", () => {
    hideTooltip();
  });

  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => setActiveTab(btn.dataset.plantTab || "overview"));
  });

  modeButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      currentMode = btn.dataset.plantMode || "rates";
      modeButtons.forEach(other => other.setAttribute("aria-pressed", String(other === btn)));
      renderChart(currentData);
    });
  });

  filtersForm?.addEventListener("submit", ev => {
    ev.preventDefault();
    updateContextBanner(null);
    fetchFiltered();
  });

  filtersForm?.querySelectorAll("select").forEach(select => {
    select.addEventListener("change", () => {
      updateContextBanner(null);
      fetchFiltered();
    });
  });

  resetBtn?.addEventListener("click", () => {
    if (!filtersForm) return;
    filtersForm.querySelectorAll("select").forEach(select => { select.value = ""; });
    const startInput = filtersForm.querySelector("[name='start']");
    const endInput = filtersForm.querySelector("[name='end']");
    if (startInput) startInput.value = defaultStart;
    if (endInput) endInput.value = defaultEnd;
    includeCancelled = true;
    includeImprocedente = true;
    updateContextBanner(null);
    fetchFiltered({ operator: "", project: "", region: "", uf: "", start: defaultStart, end: defaultEnd });
  });

  compareSelectA?.addEventListener("change", renderCompare);
  compareSelectB?.addEventListener("change", renderCompare);

  if (currentData) renderAll(currentData);
})();
