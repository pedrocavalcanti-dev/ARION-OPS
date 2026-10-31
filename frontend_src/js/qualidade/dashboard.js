import { mountQualityCharts } from "./charts.js";

(() => {
  const dataNode = document.getElementById("quality-chart-data");
  if (!dataNode) return;
  try {
    mountQualityCharts(JSON.parse(dataNode.textContent || "{}"));
  } catch (error) {
    document.querySelectorAll("[data-quality-chart]").forEach(element => {
      element.setAttribute("aria-label", "Não foi possível renderizar este gráfico.");
    });
    console.error("Falha ao inicializar os gráficos de Qualidade", error);
  }
})();
