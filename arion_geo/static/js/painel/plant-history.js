(() => {
  "use strict";

  const panel = document.querySelector("[data-plant-history]");
  if (!panel) return;

  const dataNode = document.getElementById("plant-history-initial-data");
  const chartBox = panel.querySelector("[data-plant-chart]");
  const legendBox = panel.querySelector("[data-plant-legend]");
  const tableBody = panel.querySelector("[data-plant-table-body]");
  const noteEl = panel.querySelector("[data-plant-note]");
  const filtersForm = panel.querySelector("[data-plant-filters]");
  const resetBtn = panel.querySelector("[data-plant-reset]");
  const modeButtons = panel.querySelectorAll("[data-plant-mode]");
  const tooltip = document.querySelector("[data-chart-tooltip]");

  const endpoint = panel.dataset.endpoint || "";
  const start = panel.dataset.start || "";
  const end = panel.dataset.end || "";

  let currentData = null;
  let currentMode = "rates";

  try {
    currentData = dataNode ? JSON.parse(dataNode.textContent) : null;
  } catch {
    currentData = null;
  }

  const fmtInt = value => (value === null || value === undefined ? "—" : Number(value).toLocaleString("pt-BR"));
  const fmtDec = (value, d = 1) => (
    value === null || value === undefined ? "—" : Number(value).toFixed(d).replace(".", ",")
  );
  const fmtMom = value => {
    if (value === null || value === undefined) return "—";
    const sign = Number(value) > 0 ? "+" : "";
    return `${sign}${fmtDec(value, 1)}%`;
  };
  const escapeHtml = str => String(str || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

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

  const renderKpis = data => {
    const s = data?.summary || {};
    const elPlant = panel.querySelector("[data-kpi-plant]");
    const elCov = panel.querySelector("[data-kpi-coverage]");
    const elRate = panel.querySelector("[data-kpi-rate]");
    const elRateNote = panel.querySelector("[data-kpi-rate-note]");
    const elAff = panel.querySelector("[data-kpi-affected]");
    const elMonths = panel.querySelector("[data-kpi-months]");
    if (elPlant) elPlant.textContent = s.latest_active_links !== null && s.latest_active_links !== undefined ? fmtInt(s.latest_active_links) : "—";
    if (elCov) elCov.textContent = s.latest_coverage_label || "Base histórica indisponível";
    if (elRate) elRate.textContent = s.latest_tickets_per_100_label || "—";
    if (elRateNote) elRateNote.textContent = s.latest_is_partial ? "Mês parcial em andamento" : "Taxa proporcional no mês";
    if (elAff) elAff.textContent = s.latest_affected_pct_label || "—";
    if (elMonths) elMonths.textContent = `${s.months_with_plant ?? 0} / ${s.months_total ?? 0} meses`;
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
    const height = 250;
    const padL = 52;
    const padR = 52;
    const padT = 20;
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
    const linePts = [];
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
        linePts.push([cx, py, row]);
        const dotFill = isRates ? "var(--accent-rose)" : "var(--accent-orange)";
        dots.push(`<circle cx="${cx}" cy="${py}" r="3.6" fill="${dotFill}" stroke="var(--surface-primary)" stroke-width="1.5"/>`);
      }

      xLabels.push(`<text x="${cx}" y="${height - 10}" text-anchor="middle" class="plant-svg-label">${escapeHtml(row.label)}</text>`);
      hitTargets.push(`<rect x="${padL + stepX * index}" y="${padT}" width="${stepX}" height="${plotH}" fill="transparent" data-plant-hit="${index}" style="cursor:pointer"/>`);
    });

    // Connect consecutive non-null line points (don't bridge across missing months in rates mode)
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

  const renderAll = data => {
    if (!data) return;
    currentData = data;
    renderKpis(data);
    renderChart(data);
    renderTable(data);
  };

  const fetchFiltered = async () => {
    if (!endpoint || !filtersForm) return;
    const params = new URLSearchParams();
    if (start) params.set("start", start);
    if (end) params.set("end", end);
    ["operator", "project", "uf"].forEach(name => {
      const field = filtersForm.querySelector(`[name='${name}']`);
      if (field?.value) params.set(name, field.value);
    });
    panel.classList.add("is-loading");
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
      panel.classList.remove("is-loading");
    }
  };

  modeButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      currentMode = btn.dataset.plantMode || "rates";
      modeButtons.forEach(other => other.setAttribute("aria-pressed", String(other === btn)));
      renderChart(currentData);
    });
  });

  filtersForm?.querySelectorAll("select").forEach(select => {
    select.addEventListener("change", fetchFiltered);
  });
  resetBtn?.addEventListener("click", () => {
    filtersForm?.querySelectorAll("select").forEach(select => { select.value = ""; });
    fetchFiltered();
  });

  if (currentData) renderAll(currentData);
})();
