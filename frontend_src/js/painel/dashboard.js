(() => {
  const page = document.querySelector(".dashboard-page");
  if (!page) return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const numberFormat = new Intl.NumberFormat("pt-BR");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const dataTag = document.getElementById("monthly-chart-data");
  const monthly = dataTag ? JSON.parse(dataTag.textContent) : [];
  const clientDataTag = document.getElementById("dashboard-client-distribution-data");
  const clientDistribution = clientDataTag ? JSON.parse(clientDataTag.textContent) : [];
  const tooltip = page.querySelector("[data-chart-tooltip]");

  const svg = (tag, attrs = {}) => {
    const element = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  };

  /* ---------- Count-up dos números ---------- */
  function animateCount(element) {
    const target = Number(element.dataset.count);
    if (!Number.isFinite(target)) return;
    if (reducedMotion || target === 0) {
      element.textContent = numberFormat.format(target);
      return;
    }
    const duration = Math.min(1100, 700 + Math.log10(target + 1) * 110);
    const start = performance.now();
    const easeOut = (t) => 1 - Math.pow(1 - t, 3);
    element.textContent = numberFormat.format(0);
    const step = (now) => {
      const progress = Math.min(1, (now - start) / duration);
      element.textContent = numberFormat.format(Math.round(target * easeOut(progress)));
      if (progress < 1) window.requestAnimationFrame(step);
    };
    window.requestAnimationFrame(step);
  }

  /* ---------- Tooltip compartilhado ---------- */
  function showTooltip(anchor, html, { x, y } = {}) {
    if (!tooltip) return;
    const rect = anchor.getBoundingClientRect();
    tooltip.innerHTML = html;
    tooltip.hidden = false;
    const width = tooltip.offsetWidth;
    const left = Math.min(window.innerWidth - width / 2 - 8, Math.max(width / 2 + 8, x ?? rect.left + rect.width / 2));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(tooltip.offsetHeight + 16, y ?? rect.top)}px`;
    tooltip.classList.add("is-visible");
  }

  function hideTooltip() {
    tooltip?.classList.remove("is-visible");
  }

  function tipRow(swatch, label, value) {
    return `<div class="tip-row"><i class="tip-swatch tip-swatch--${swatch}"></i>${label}<b>${value}</b></div>`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }

  function showRankingTooltip(element) {
    const label = element.dataset.tipLabel || element.getAttribute("aria-label") || "";
    const value = element.dataset.tipValue || "";
    const share = element.dataset.tipShare;
    const detail = share ? `${value} · ${share}` : value;
    const rect = element.getBoundingClientRect();
    const html = `<span class="tip-title">${escapeHtml(label)}</span>${tipRow("neutral", "Volume", escapeHtml(detail))}`;
    showTooltip(element, html, { y: rect.top });
  }

  function bindRankingTooltips() {
    page.querySelectorAll(".dashboard-aging-column-item,.dashboard-project-column").forEach((item) => {
      item.addEventListener("mouseenter", showRankingTooltip.bind(null, item));
      item.addEventListener("focus", showRankingTooltip.bind(null, item));
      item.addEventListener("mouseleave", hideTooltip);
      item.addEventListener("blur", hideTooltip);
    });
  }

  function arcPath(start, end) {
    const center = 90;
    const outer = 66;
    const inner = 46;
    const point = (radius, angle) => [center + radius * Math.cos(angle), center + radius * Math.sin(angle)];
    const [outerStartX, outerStartY] = point(outer, start);
    const [outerEndX, outerEndY] = point(outer, end);
    const [innerEndX, innerEndY] = point(inner, end);
    const [innerStartX, innerStartY] = point(inner, start);
    const largeArc = end - start > Math.PI ? 1 : 0;
    return [
      `M${outerStartX} ${outerStartY}`,
      `A${outer} ${outer} 0 ${largeArc} 1 ${outerEndX} ${outerEndY}`,
      `L${innerEndX} ${innerEndY}`,
      `A${inner} ${inner} 0 ${largeArc} 0 ${innerStartX} ${innerStartY}Z`,
    ].join(" ");
  }

  function buildClientDonut() {
    const chart = page.querySelector("[data-client-donut]");
    const arcs = chart?.querySelector("[data-client-donut-arcs]");
    if (!chart || !arcs || !clientDistribution.length) return;
    const total = clientDistribution.reduce((sum, item) => sum + Number(item.value || 0), 0);
    if (!total) return;

    let cursor = -Math.PI / 2;
    clientDistribution.forEach((item) => {
      const value = Math.max(0, Number(item.value) || 0);
      if (!value) return;
      const slice = Math.PI * 2 * value / total;
      const gap = Math.min(.035, slice / 3);
      const start = cursor + gap / 2;
      const end = cursor + slice - gap / 2;
      cursor += slice;
      const share = `${Number(item.share || 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
      const label = `${item.label}: ${numberFormat.format(value)} links, ${share}`;
      const segment = svg("path", {
        d: arcPath(start, end),
        class: `dashboard-client-arc dashboard-client-arc--${item.key}`,
        tabindex: "0",
        role: "img",
        "aria-label": label,
      });
      const show = (event) => {
        const rect = segment.getBoundingClientRect();
        const html = `<span class="tip-title">Distribuição por cliente</span>${tipRow(item.key, escapeHtml(item.label), `${numberFormat.format(value)} · ${escapeHtml(share)}`)}`;
        showTooltip(segment, html, { x: event?.clientX ?? rect.left + rect.width / 2, y: rect.top });
      };
      segment.addEventListener("mousemove", show);
      segment.addEventListener("focus", show);
      segment.addEventListener("mouseleave", hideTooltip);
      segment.addEventListener("blur", hideTooltip);
      arcs.append(segment);
    });
  }

  /* ---------- Escala "bonita" para o eixo Y ---------- */
  function niceMax(value) {
    const options = [3, 4].map((intervals) => {
      const raw = value / intervals;
      const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
      const step = [1, 1.5, 2, 2.5, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= raw);
      return { top: step * intervals, step, intervals };
    });
    return options.sort((a, b) => a.top - b.top || b.intervals - a.intervals)[0];
  }

  /* ---------- Chamados no período ---------- */
  function buildTrendChart() {
    const chart = page.querySelector("[data-monthly-chart]");
    if (!chart || !monthly.length) return;
    const scale = niceMax(Math.max(1, ...monthly.map((row) => row.calls)) * 1.04);
    const pct = (value) => (value / scale.top) * 100;
    chart.style.setProperty("--cols", monthly.length);

    const yAxis = document.createElement("div");
    yAxis.className = "trend-y";
    yAxis.setAttribute("aria-hidden", "true");
    const plot = document.createElement("div");
    plot.className = "trend-plot";
    for (let i = 0; i <= scale.intervals; i += 1) {
      const value = scale.step * i;
      const label = document.createElement("span");
      label.textContent = numberFormat.format(value);
      label.style.bottom = `${pct(value)}%`;
      yAxis.append(label);
      if (i > 0) {
        const line = document.createElement("i");
        line.className = "trend-grid";
        line.style.bottom = `${pct(value)}%`;
        plot.append(line);
      }
    }

    const columns = document.createElement("div");
    columns.className = "trend-cols";
    const xAxis = document.createElement("div");
    xAxis.className = "trend-x";
    xAxis.setAttribute("aria-hidden", "true");

    const line = svg("svg", { class: "trend-line", viewBox: "0 0 100 100", preserveAspectRatio: "none", "aria-hidden": "true" });
    const defs = svg("defs");
    const gradient = svg("linearGradient", { id: "trend-area-gradient", x1: "0", y1: "0", x2: "0", y2: "1" });
    gradient.append(svg("stop", { offset: "0", class: "area-stop-top" }), svg("stop", { offset: "1", class: "area-stop-bottom" }));
    defs.append(gradient);
    const points = monthly.map((row, index) => [((index + 0.5) / monthly.length) * 100, 100 - pct(row.repeat)]);
    const pointString = points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
    line.append(
      defs,
      svg("polygon", { points: `${points[0][0]},100 ${pointString} ${points[points.length - 1][0]},100` }),
      svg("polyline", { points: pointString }),
    );

    const dots = [];
    const labels = [];
    monthly.forEach((row, index) => {
      const column = document.createElement("div");
      column.className = "trend-col";
      column.tabIndex = 0;
      column.setAttribute("aria-label", `${row.label}: ${numberFormat.format(row.calls)} tickets criados, ${numberFormat.format(row.repeat)} links com dois ou mais tickets no mês`);
      const bar = document.createElement("i");
      bar.className = "trend-bar";
      bar.style.height = `${Math.max(1.5, pct(row.calls))}%`;
      bar.style.transitionDelay = `${120 + index * 60}ms`;
      column.append(bar);
      columns.append(column);

      const dot = document.createElement("i");
      dot.className = "trend-dot";
      dot.style.left = `${points[index][0]}%`;
      dot.style.bottom = `${pct(row.repeat)}%`;
      dot.style.transitionDelay = `${500 + index * 90}ms`;
      dots.push(dot);

      const label = document.createElement("span");
      label.textContent = row.month;
      labels.push(label);
      xAxis.append(label);

      const activate = () => {
        column.classList.add("is-active");
        dot.classList.add("is-active");
        label.classList.add("is-active");
        const html = `<span class="tip-title">${row.label}</span>${tipRow("calls", "Tickets criados", numberFormat.format(row.calls))}${tipRow("repeat", "Links com 2+ tickets", numberFormat.format(row.repeat))}`;
        const barRect = bar.getBoundingClientRect();
        showTooltip(column, html, { y: barRect.top });
      };
      const deactivate = () => {
        column.classList.remove("is-active");
        dot.classList.remove("is-active");
        label.classList.remove("is-active");
        hideTooltip();
      };
      column.addEventListener("mouseenter", activate);
      column.addEventListener("mouseleave", deactivate);
      column.addEventListener("focus", activate);
      column.addEventListener("blur", deactivate);
    });

    plot.append(columns, line, ...dots);
    chart.append(yAxis, plot, document.createElement("span"), xAxis);
  }

  /* ---------- Sparklines dos KPIs ---------- */
  function buildSparklines() {
    page.querySelectorAll("[data-sparkline]").forEach((target, index) => {
      const key = target.dataset.sparkline;
      const values = monthly.map((row) => row[key]).filter((value) => Number.isFinite(value));
      if (values.length < 2) return;
      const min = Math.min(...values);
      const max = Math.max(...values);
      const range = max - min || 1;
      const points = values.map((value, i) => [(i / (values.length - 1)) * 120, 28 - ((value - min) / range) * 24]);
      const d = points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
      const id = `spark-gradient-${index}`;
      const defs = svg("defs");
      const gradient = svg("linearGradient", { id, x1: "0", y1: "0", x2: "0", y2: "1" });
      gradient.append(svg("stop", { offset: "0", class: "spark-stop-top" }), svg("stop", { offset: "1", class: "spark-stop-bottom" }));
      defs.append(gradient);
      const [lastX, lastY] = points[points.length - 1];
      target.append(
        defs,
        svg("path", { d: `${d} L120 32 L0 32 Z`, fill: `url(#${id})` }),
        svg("path", { d, class: "spark-line" }),
        svg("circle", { cx: lastX, cy: lastY, r: 2.2, class: "spark-end" }),
      );
    });
  }

  /* ---------- Micro elementos ---------- */
  function buildDots() {
    page.querySelectorAll("[data-dots]").forEach((target) => {
      const count = Number(target.dataset.dots) || 0;
      for (let i = 0; i < count; i += 1) {
        const dot = document.createElement("i");
        dot.style.transitionDelay = `${300 + i * 18}ms`;
        target.append(dot);
      }
    });
  }

  function bindOperatorSegments() {
    page.querySelectorAll(".operator-segment").forEach((segment) => {
      const key = [...segment.classList].find((name) => name.startsWith("operator-segment--"))?.replace("operator-segment--", "");
      const show = (event) => {
        const html = `<span class="tip-title">Operadora</span>${tipRow(key, segment.dataset.tipTitle, segment.dataset.tipValue)}`;
        const rect = segment.getBoundingClientRect();
        showTooltip(segment, html, { x: event?.clientX ?? rect.left + rect.width / 2, y: rect.top });
      };
      segment.addEventListener("mousemove", show);
      segment.addEventListener("focus", () => show());
      segment.addEventListener("mouseleave", hideTooltip);
      segment.addEventListener("blur", hideTooltip);
    });
  }

  /* ---------- Revelação (barras, anel, splits) ---------- */
  function reveal() {
    page.querySelectorAll("[data-value][data-max]:not(.dashboard-aging-column-item):not(.dashboard-project-column)").forEach((bar) => {
      const value = Number(bar.dataset.value);
      const max = Number(bar.dataset.max);
      bar.style.width = `${max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0}%`;
    });
    page.querySelectorAll(".dashboard-aging-column-item[data-value][data-max],.dashboard-project-column[data-value][data-max]").forEach((column) => {
      const value = Number(column.dataset.value);
      const max = Number(column.dataset.max);
      const bar = column.querySelector(".dashboard-aging-column b,.dashboard-project-track b");
      if (bar) bar.style.height = `${max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0}%`;
    });
    page.querySelectorAll("[data-share]").forEach((segment) => {
      segment.style.flexBasis = `calc(${Number(segment.dataset.share) || 0}% - 2px)`;
    });
    page.querySelectorAll("[data-ring]").forEach((ring) => {
      ring.style.strokeDashoffset = String(100 - Math.max(0, Math.min(100, Number(ring.dataset.ring) || 0)));
    });
    page.classList.add("is-ready");
  }

  buildTrendChart();
  buildClientDonut();
  bindRankingTooltips();
  buildSparklines();
  buildDots();
  bindOperatorSegments();
  if (tooltip) tooltip.hidden = true;
  window.addEventListener("scroll", hideTooltip, { passive: true });

  page.querySelectorAll("[data-count]").forEach(animateCount);
  if (reducedMotion) {
    reveal();
  } else {
    // Dois frames garantem que o estado inicial (zerado) seja pintado antes da transição.
    window.requestAnimationFrame(() => window.requestAnimationFrame(reveal));
  }
})();
