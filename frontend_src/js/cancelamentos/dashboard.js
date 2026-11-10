import { palette, common, escapeHtml } from "../shared/chart_theme.js";

const node = document.getElementById("cancellation-dashboard-data");
const page = document.querySelector(".canc-dashboard");
if (node && page) {
  const data = JSON.parse(node.textContent || "{}");
  const charts = new Map();
  const number = new Intl.NumberFormat("pt-BR");
  const compactMoney = new Intl.NumberFormat("pt-BR", { notation: "compact", style: "currency", currency: "BRL", maximumFractionDigits: 1 });
  // Number conversion is for chart coordinates only. Financial sums and exact
  // formatted labels come from Decimal aggregates on the server.
  const coordinate = value => value === null || value === undefined ? null : Number(value);
  const months = data.months || [];
  const axes = p => ({
    grid: { top: 45, right: 15, bottom: 10, left: 8, containLabel: true },
    xAxis: { type: "category", data: months.map(r => r.label), axisTick: { show: false },
      axisLabel: { color: p.muted, fontSize: 10, hideOverlap: true }, axisLine: { lineStyle: { color: p.border } } },
    yAxis: { type: "value", minInterval: 1, axisLabel: { color: p.muted, fontSize: 10 },
      splitLine: { lineStyle: { color: p.border, type: "dashed" } } },
  });
  const options = {
    months: p => ({ ...common(p), ...axes(p), color: [p.teal],
      tooltip: { ...common(p).tooltip, trigger: "axis", formatter: params => {
        const row = months[params?.[0]?.dataIndex];
        return row ? `<strong>${escapeHtml(row.label)}</strong><br>${number.format(row.count)} cancelamento(s)` : "";
      } },
      series: [{ name: "Cancelamentos", type: "bar", barMaxWidth: 32, data: months.map(r => r.count),
        itemStyle: { borderRadius: [4, 4, 0, 0] }, label: { show: true, position: "top", color: p.text, fontSize: 10, formatter: p => p.value || "" } }],
    }),
    finance: p => ({ ...common(p), ...axes(p), color: [p.teal, p.orange, p.violet],
      legend: { top: 0, itemWidth: 8, itemHeight: 8, textStyle: { color: p.muted, fontSize: 10 } },
      yAxis: { ...axes(p).yAxis, axisLabel: { color: p.muted, fontSize: 10, formatter: value => compactMoney.format(value) } },
      tooltip: { ...common(p).tooltip, trigger: "axis", formatter: params => {
        const row = data.finance?.[params?.[0]?.dataIndex];
        return row ? `<strong>${escapeHtml(row.label)}</strong><br>Multas: ${escapeHtml(row.contract_label)}<br>Equipamentos: ${escapeHtml(row.equipment_label)}<br>Outros: ${escapeHtml(row.other_label)}<br><strong>Total informado: ${escapeHtml(row.total_label)}</strong>${row.partial ? "<br>Subtotal conhecido · parcial" : ""}<br>Não comprova pagamento.` : "";
      } },
      series: [["contract", "Multas contratuais"], ["equipment", "Equipamentos"], ["other", "Outros"]].map(([key, name]) => ({
        name, type: "bar", stack: "informed", barMaxWidth: 32, data: (data.finance || []).map(r => coordinate(r[key])),
      })),
    }),
    stages: p => ({ ...common(p), color: [p.blue, p.teal, p.orange, p.green],
      tooltip: { ...common(p).tooltip, trigger: "item", formatter: params => `${escapeHtml(params.name)}<br><strong>${number.format(params.value)}</strong> · ${params.percent}%` },
      legend: { bottom: 0, itemWidth: 9, itemHeight: 9, textStyle: { color: p.text, fontSize: 10 } },
      series: [{ type: "pie", radius: ["49%", "72%"], center: ["50%", "45%"], label: { show: false },
        itemStyle: { borderColor: p.surface, borderWidth: 3 }, data: data.stages || [] }],
    }),
    providers: p => ({ ...common(p), color: [p.teal],
      grid: { top: 10, bottom: 15, left: 8, right: 30, containLabel: true },
      xAxis: { type: "value", minInterval: 1, axisLabel: { color: p.muted, fontSize: 10 }, splitLine: { lineStyle: { color: p.border, type: "dashed" } } },
      yAxis: { type: "category", inverse: true, data: (data.providers || []).map(r => r.label),
        axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: p.text, fontSize: 10, width: 135, overflow: "truncate" } },
      tooltip: { ...common(p).tooltip, trigger: "axis", formatter: params => {
        const row = data.providers?.[params?.[0]?.dataIndex];
        return row ? `<strong>${escapeHtml(row.label)}</strong><br>${number.format(row.count)} cancelamento(s)${row.total_label ? `<br>Total informado: ${escapeHtml(row.total_label)}${row.partial ? " · parcial" : ""}` : ""}` : "";
      } },
      series: [{ type: "bar", barMaxWidth: 20, itemStyle: { borderRadius: [0, 4, 4, 0] },
        data: (data.providers || []).map(r => r.count), label: { show: true, position: "right", color: p.text, fontSize: 10 } }],
    }),
  };
  const render = () => {
    page.querySelectorAll("[data-cancellation-chart]").forEach(element => {
      if (!window.echarts || !element.clientWidth || !element.clientHeight) return;
      const key = element.dataset.cancellationChart;
      const chart = charts.get(element) || window.echarts.init(element, null, { renderer: "canvas" });
      charts.set(element, chart);
      chart.resize();
      chart.setOption(options[key](palette()), { notMerge: true });
    });
  };
  const observer = window.ResizeObserver ? new ResizeObserver(() => charts.forEach(chart => chart.resize())) : null;
  render();
  page.querySelectorAll("[data-cancellation-chart]").forEach(element => observer?.observe(element));
  window.addEventListener("arion:themechange", render);
  window.addEventListener("resize", render, { passive: true });
  window.addEventListener("beforeunload", () => {
    observer?.disconnect();
    charts.forEach(chart => chart.dispose());
    window.removeEventListener("arion:themechange", render);
    window.removeEventListener("resize", render);
  }, { once: true });
  page.querySelectorAll('input[type="date"]').forEach(input => input.addEventListener("change", () => {
    const preset = page.querySelector('[name="period"]');
    if (preset) preset.value = "custom";
  }));
}
