const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const format = value => Number.isFinite(value) ? new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 }).format(value) : "Indisponível";

function drawIcon(kind, alertRing = false) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d");
  ctx.scale(2, 2);
  ctx.fillStyle = "#f7fafc";
  ctx.strokeStyle = alertRing ? "#d96b43" : "#355c78";
  ctx.lineWidth = alertRing ? 2 : 1.5;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.arc(16, 16, 14, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();

  ctx.strokeStyle = "#355c78";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  if (kind === "rain" || kind === "heavy") {
    ctx.moveTo(8, 17);
    ctx.bezierCurveTo(4, 12, 10, 9, 12, 11);
    ctx.bezierCurveTo(14, 5, 23, 9, 23, 13);
    ctx.bezierCurveTo(29, 14, 25, 19, 22, 18);
    ctx.lineTo(9, 18);
    for (const x of [11, 16, 21]) {
      ctx.moveTo(x, 21);
      ctx.lineTo(x - 1, kind === "heavy" ? 26 : 24);
    }
  } else if (kind === "heat") {
    ctx.arc(16, 16, 5, 0, Math.PI * 2);
    for (let a = 0; a < 8; a++) {
      const t = a * Math.PI / 4;
      ctx.moveTo(16 + 8 * Math.cos(t), 16 + 8 * Math.sin(t));
      ctx.lineTo(16 + 11 * Math.cos(t), 16 + 11 * Math.sin(t));
    }
  } else if (kind === "cold") {
    for (let a = 0; a < 6; a++) {
      const t = a * Math.PI / 3;
      ctx.moveTo(16, 16);
      ctx.lineTo(16 + 10 * Math.cos(t), 16 + 10 * Math.sin(t));
    }
  } else {
    for (const [y, end] of [[11, 22], [16, 25], [21, 20]]) {
      ctx.moveTo(7, y);
      ctx.lineTo(end, y);
      ctx.bezierCurveTo(end + 5, y, end + 2, y - 6, end, y - 3);
    }
  }
  ctx.stroke();
  return ctx.getImageData(0, 0, 64, 64);
}

export function mountWeatherIcons({ map, page, state, positions, regionForUF, onOpen }) {
  let payload = null, day = "", popup = null;
  const toggle = page.querySelector("[data-weather-icons]");
  const type = page.querySelector("[data-weather-icon-type]");

  function install() {
    if (map.getSource("arion-weather-icons")) return;
    for (const kind of ["rain", "heavy", "heat", "cold", "wind"]) {
      map.addImage(`geo-${kind}`, drawIcon(kind, false), { pixelRatio: 2 });
      map.addImage(`geo-${kind}-alert`, drawIcon(kind, true), { pixelRatio: 2 });
    }
    map.addSource("arion-weather-icons", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
      cluster: true,
      clusterRadius: 38,
      clusterMaxZoom: 6,
    });
    map.addLayer({
      id: "arion-weather-icon-clusters",
      type: "symbol",
      source: "arion-weather-icons",
      filter: ["has", "point_count"],
      layout: {
        "text-field": ["concat", "☁ ", ["get", "point_count_abbreviated"]],
        "text-font": ["Open Sans Semibold"],
        "text-size": 13,
      },
      paint: { "text-color": "#355c78", "text-halo-color": "#fff", "text-halo-width": 2 },
    });
    map.addLayer({
      id: "arion-weather-icon-points",
      type: "symbol",
      source: "arion-weather-icons",
      filter: ["!", ["has", "point_count"]],
      layout: {
        "icon-image": ["get", "icon"],
        "icon-size": .86,
        "icon-allow-overlap": false,
        "icon-padding": 10,
      },
    });
    map.on("click", "arion-weather-icon-points", event => {
      const p = event.features?.[0]?.properties;
      if (!p) return;
      onOpen?.();
      const q = (key, unit) => `${format(p[key])}${Number.isFinite(p[key]) ? " " + unit : ""}`;
      popup?.remove();
      popup = new window.mapboxgl.Popup({ maxWidth: "290px", closeOnClick: true })
        .setLngLat(event.lngLat)
        .setHTML(
          `<div class="geo-weather-popover">`
          + `<strong>${escape(p.name)}</strong>`
          + `<p>${escape(p.period)} · ${escape(p.condition)}</p>`
          + `<dl><div><dt>Chuva</dt><dd>${q("rain", "mm")}</dd></div><div><dt>Temperatura</dt><dd>${q("temperature", "°C")}</dd></div><div><dt>Vento / rajada</dt><dd>${q("wind", "km/h")} / ${q("gust", "km/h")}</dd></div></dl>`
          + `<p>${p.incidents} incidentes válidos no recorte da ${p.local ? "localidade" : "UF"}${p.overlap ? " · evento climático coexistente" : ""}.</p>`
          + `<small>${escape(p.precision)} · Open-Meteo / Copernicus ERA5 (estimativa diária, sem radar em tempo real). Coincidência não confirma causa.</small>`
          + `</div>`
        )
        .addTo(map);
    });
    map.on("click", "arion-weather-icon-clusters", async event => {
      const feature = event.features?.[0];
      if (!feature) return;
      const zoom = await map.getSource("arion-weather-icons").getClusterExpansionZoom(feature.properties.cluster_id);
      map.easeTo({ center: feature.geometry.coordinates, zoom });
    });
    for (const layer of ["arion-weather-icon-points", "arion-weather-icon-clusters"]) {
      map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
    }
  }

  function resolveCondition({ mode, rain, maxRain, hot, cold, gust, thresholds, uf }) {
    const rainLimit = thresholds.rain ?? 1;
    const heavyLimit = thresholds.heavy_rain ?? 20;
    const heatLimit = thresholds.heat ?? 35;
    const coldLimit = thresholds.cold?.[regionForUF(uf)] ?? 10;
    const windLimit = thresholds.wind ?? 40;

    if (mode === "rain") {
      if (rain == null || rain < rainLimit) return null;
      const isHeavy = maxRain >= heavyLimit;
      return { kind: isHeavy ? "heavy" : "rain", condition: isHeavy ? "Chuva intensa amostrada em pelo menos um dia" : "Chuva registrada no recorte" };
    }
    if (mode === "heat") {
      if (hot == null || hot < heatLimit) return null;
      return { kind: "heat", condition: `Máxima amostrada ≥ ${heatLimit} °C` };
    }
    if (mode === "cold") {
      if (cold == null || cold > coldLimit) return null;
      return { kind: "cold", condition: `Mínima amostrada ≤ ${coldLimit} °C · limiar regional` };
    }
    if (mode === "wind") {
      if (gust == null || gust < windLimit) return null;
      return { kind: "wind", condition: "Vento / rajada amostrada" };
    }
    if (Number.isFinite(rain) && rain >= rainLimit) {
      const isHeavy = maxRain >= heavyLimit;
      return { kind: isHeavy ? "heavy" : "rain", condition: isHeavy ? "Chuva intensa amostrada" : "Precipitação no recorte" };
    }
    if (Number.isFinite(gust) && gust >= windLimit) {
      return { kind: "wind", condition: `Rajada amostrada ≥ ${windLimit} km/h` };
    }
    if (Number.isFinite(hot) && hot >= heatLimit) {
      return { kind: "heat", condition: `Máxima amostrada ≥ ${heatLimit} °C` };
    }
    if (Number.isFinite(cold) && cold <= coldLimit) {
      return { kind: "cold", condition: `Mínima amostrada ≤ ${coldLimit} °C` };
    }
    return null;
  }

  function update(next = payload, nextDay = day) {
    // Mounted from Mapbox's load event. Pending glyphs/data do not prevent adding
    // this source; waiting for isStyleLoaded would lose a completed UF's update.
    payload = next;
    day = nextDay;
    if (!map.getStyle() || !payload) return;
    install();
    const visible = state.mode === "climate" && toggle?.checked;
    for (const layer of ["arion-weather-icon-points", "arion-weather-icon-clusters"]) {
      map.setLayoutProperty(layer, "visibility", visible ? "visible" : "none");
    }
    if (!visible) {
      popup?.remove();
      return;
    }
    const thresholds = payload.icon_thresholds || {}, features = [];
    const locals = (payload.correlation?.localities || []).filter(row => row.available && (!state.selectedUF || row.uf === state.selectedUF));
    const candidates = (state.selectedUF || state.region) && locals.length
      ? locals.map(row => ({
          uf: row.uf,
          name: row.city,
          coordinates: row.coordinates,
          weather: row.weather,
          incidents: row.incidents || 0,
          local: true,
          precision: row.precision,
        }))
      : Object.entries(payload.weather || {}).map(([uf, weather]) => ({
          uf,
          name: uf + " · agregado estadual",
          coordinates: positions[uf],
          weather,
          incidents: payload.summary.states[uf]?.ticket_count || 0,
          precision: "Estimativa territorial da UF; não implica condição uniforme no estado.",
        }));

    const selectedMode = type?.value || "auto";
    for (const candidate of candidates) {
      if (!candidate.coordinates || (state.selectedUF && candidate.uf !== state.selectedUF)) continue;
      const weather = day ? candidate.weather?.daily?.find(row => row.date === day) : candidate.weather;
      if (!weather) continue;
      const rain = weather[day ? "precipitation_sum" : "precipitation_mm"];
      const temperature = weather[day ? "temperature_2m_mean" : "temperature_mean_c"];
      const wind = weather[day ? "wind_speed_10m_max" : "wind_max_kmh"];
      const gust = weather[day ? "wind_gusts_10m_max" : "gust_max_kmh"];
      const dailyRows = candidate.weather?.daily || [];
      const maxRain = day ? rain : Math.max(-1, ...dailyRows.map(r => r.precipitation_sum ?? -1));
      const hot = weather[day ? "temperature_2m_max" : "temperature_max_c"];
      const cold = weather[day ? "temperature_2m_min" : "temperature_min_c"];
      const resolved = resolveCondition({ mode: selectedMode, rain, maxRain, hot, cold, gust, thresholds, uf: candidate.uf });
      if (!resolved) continue;
      const overlap = Boolean(candidate.incidents > 0);
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: candidate.coordinates },
        properties: {
          ...candidate,
          weather: undefined,
          coordinates: undefined,
          icon: `geo-${resolved.kind}${overlap ? "-alert" : ""}`,
          overlap,
          condition: resolved.condition,
          period: day || payload.period.month,
          rain,
          temperature,
          wind,
          gust,
        },
      });
    }
    map.getSource("arion-weather-icons").setData({ type: "FeatureCollection", features });
  }

  toggle?.addEventListener("change", () => update());
  type?.addEventListener("change", () => update());
  return { update };
}
