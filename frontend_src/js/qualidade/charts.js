import { token, palette, escapeHtml, common } from "../shared/chart_theme.js";

const instances = new Map();
let observer = null;
let resizeHandler = null;

function seriesValues(values) {
  return Array.isArray(values) ? values.map(value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0) : [];
}

function evolutionOption(data, p) {
  const labels = Array.isArray(data.labels) ? data.labels : [];
  const tooltipLabels = Array.isArray(data.tooltip_labels) ? data.tooltip_labels : labels;
  const recurrences = seriesValues(data.recurrences);
  const stabilized = seriesValues(data.stabilized);
  const completed = seriesValues(data.completed);
  const postTreatment = seriesValues(data.post_treatment);
  return {
    ...common(p),
    color: [p.rose, p.green, p.teal, p.orange],
    tooltip: {
      ...common(p).tooltip,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: params => {
        const index = params?.[0]?.dataIndex;
        if (!Number.isInteger(index)) return "";
        return [
          `<strong>${escapeHtml(tooltipLabels[index] || labels[index] || "")}</strong>`,
          `Novas reincidências: <strong>${recurrences[index] || 0}</strong>`,
          `Links estabilizados: <strong>${stabilized[index] || 0}</strong>`,
          `OQs concluídas: <strong>${completed[index] || 0}</strong>`,
          `Pós-tratamento: <strong>${postTreatment[index] || 0}</strong>`,
        ].join("<br>");
      },
    },
    grid: { left: 35, right: 15, top: 39, bottom: 29, containLabel: true },
    legend: { top: 0, right: 0, itemWidth: 8, itemHeight: 8, itemGap: 18, textStyle: { color: p.muted, fontSize: 9 } },
    xAxis: {
      type: "category", data: labels, boundaryGap: true,
      axisTick: { show: false }, axisLine: { lineStyle: { color: p.border } },
      axisLabel: { color: p.muted, fontSize: 8, margin: 10, hideOverlap: true },
    },
    yAxis: {
      type: "value", minInterval: 1,
      axisLabel: { color: p.muted, fontSize: 8 }, axisLine: { show: false },
      axisTick: { show: false }, splitLine: { lineStyle: { color: p.border, type: "dashed" } },
    },
    series: [
      { name: "Novas reincidências", type: "bar", data: recurrences, barMaxWidth: 18, barGap: "35%", itemStyle: { borderRadius: [4, 4, 0, 0] }, emphasis: { focus: "series" } },
      { name: "Links estabilizados", type: "bar", data: stabilized, barMaxWidth: 18, itemStyle: { borderRadius: [4, 4, 0, 0] }, emphasis: { focus: "series" } },
      { name: "OQs concluídas", type: "line", data: completed, smooth: 0.2, symbol: "circle", symbolSize: 6, lineStyle: { width: 2 }, itemStyle: { borderWidth: 2, borderColor: p.surface }, label: { show: true, position: "top", color: p.teal, fontSize: 8, formatter: params => Number(params.value) > 0 ? params.value : "" }, emphasis: { focus: "series", scale: 1.2 } },
      { name: "Pós-tratamento", type: "line", data: postTreatment, smooth: 0.2, symbol: "circle", symbolSize: 6, lineStyle: { width: 2 }, itemStyle: { borderWidth: 2, borderColor: p.surface }, label: { show: true, position: "top", color: p.orange, fontSize: 8, formatter: params => Number(params.value) > 0 ? params.value : "" }, emphasis: { focus: "series", scale: 1.2 } },
    ],
  };
}

function statusOption(data, p) {
  const values = Array.isArray(data) ? data.map(item => ({ name: String(item.name || ""), value: Math.max(0, Number(item.value) || 0) })).filter(item => item.name && item.value > 0) : [];
  const total = values.reduce((sum, item) => sum + item.value, 0);
  return {
    ...common(p),
    color: [p.teal, p.green, p.orange, p.rose, p.violet, p.blue, p.faint],
    tooltip: { ...common(p).tooltip, trigger: "item", formatter: params => `${params.name}<br><strong>${params.value}</strong> · ${params.percent}%` },
    graphic: [
      { type: "text", left: "center", top: "40%", silent: true, style: { text: String(total), fill: token("--text-primary", "#202421"), font: '600 24px Inter, "Segoe UI", sans-serif', textAlign: "center" } },
      { type: "text", left: "center", top: "56%", silent: true, style: { text: "ORDENS", fill: p.muted, font: '600 8px Inter, "Segoe UI", sans-serif', letterSpacing: 1.1, textAlign: "center" } },
    ],
    series: [{
      name: "Ordens de Qualidade", type: "pie", radius: ["63%", "82%"], center: ["50%", "51%"],
      avoidLabelOverlap: true, itemStyle: { borderColor: p.surface, borderWidth: 2, borderRadius: 3 },
      label: { show: false }, emphasis: { scale: true, scaleSize: 4, label: { show: false } },
      data: values,
    }],
  };
}

function linksOption(data, p) {
  const links = Array.isArray(data) ? data.filter(item => item && item.label).map(item => ({
    label: String(item.label),
    client: String(item.client || ""), project: String(item.project || ""),
    provider: String(item.provider_name || ""), last: String(item.last_occurrence || ""),
    url: String(item.url || ""), value: Math.max(0, Number(item.value) || 0),
  })).filter(item => item.value > 0) : [];
  return {
    ...common(p),
    color: [p.teal],
    tooltip: { ...common(p).tooltip, trigger: "axis", axisPointer: { type: "shadow" }, formatter: params => {
      const item = params?.[0];
      const row = links[item?.dataIndex];
      return row ? `<strong>${escapeHtml(row.label)}</strong><br><span style="color:${p.muted}">${escapeHtml(row.client)} · ${escapeHtml(row.project)} · ${escapeHtml(row.provider)}</span><br>${row.value} reincidência(s) · última ${escapeHtml(row.last)}` : "";
    } },
    grid: { left: 10, right: 20, top: 4, bottom: 4, containLabel: true },
    xAxis: { type: "value", minInterval: 1, axisLabel: { color: p.muted, fontSize: 8 }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: p.border, type: "dashed" } } },
    yAxis: {
      type: "category", inverse: true, data: links.map(item => item.label),
      axisTick: { show: false }, axisLine: { show: false },
      axisLabel: { color: p.text, fontSize: 8, width: 150, overflow: "truncate", formatter: value => value.length > 24 ? `${value.slice(0, 23)}…` : value },
    },
    series: [{ type: "bar", data: links.map(item => item.value), barMaxWidth: 19, showBackground: true, backgroundStyle: { color: p.border, borderRadius: [0, 4, 4, 0] }, itemStyle: { borderRadius: [0, 4, 4, 0] }, label: { show: true, position: "right", color: p.text, fontSize: 8 } }],
  };
}

export function mountQualityCharts(payload) {
  const specs = [
    ["evolution", document.querySelector('[data-quality-chart="evolution"]'), data => evolutionOption(data, palette()), payload?.evolution],
    ["status", document.querySelector('[data-quality-chart="status"]'), data => statusOption(data, palette()), payload?.status],
    ["links", document.querySelector('[data-quality-chart="links"]'), data => linksOption(data, palette()), payload?.links],
  ];

  const render = () => {
    const echarts = window.echarts;
    if (!echarts || typeof echarts.init !== "function") {
      for (const [, element] of specs) {
        if (element) element.setAttribute("aria-label", "Biblioteca de gráficos indisponível.");
      }
      console.error("A distribuição local do ECharts não foi carregada.");
      return;
    }
    for (const [key, element, buildOption, data] of specs) {
      if (!element || !data) continue;
      if (element.clientWidth <= 0 || element.clientHeight <= 0) continue;
      let chart = echarts.getInstanceByDom(element) || instances.get(key);
      if (!chart) {
        chart = echarts.init(element, null, { renderer: "canvas" });
        element.style.cursor = key === "links" ? "pointer" : "default";
        if (key === "links") {
          chart.on("click", params => {
            const target = data?.[params?.dataIndex]?.url;
            if (target) window.location.assign(target);
          });
        }
      }
      instances.set(key, chart);
      chart.resize({ width: element.clientWidth, height: element.clientHeight });
      chart.setOption(buildOption(data), { notMerge: true, lazyUpdate: true });
    }
  };

  if ("ResizeObserver" in window) observer = new ResizeObserver(entries => entries.forEach(entry => {
    const chart = window.echarts?.getInstanceByDom(entry.target);
    if (chart) chart.resize({ width: entry.target.clientWidth, height: entry.target.clientHeight });
    else render();
  }));
  else {
    resizeHandler = () => instances.forEach((chart, key) => {
      const element = specs.find(([specKey]) => specKey === key)?.[1];
      if (element) chart.resize({ width: element.clientWidth, height: element.clientHeight });
    });
    window.addEventListener("resize", resizeHandler, { passive: true });
  }
  for (const [, element] of specs) if (element) observer?.observe(element);
  render();
  const themeHandler = () => render();
  window.addEventListener("arion:themechange", themeHandler);

  window.addEventListener("beforeunload", () => {
    window.removeEventListener("arion:themechange", themeHandler);
    if (resizeHandler) window.removeEventListener("resize", resizeHandler);
    observer?.disconnect();
    instances.forEach(chart => chart.dispose());
    instances.clear();
  }, { once: true });
}
