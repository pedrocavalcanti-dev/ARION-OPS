(() => {
  const page = document.querySelector("[data-data-explorer]");
  const dataNode = document.getElementById("data-explorer-catalog");
  if (!page || !dataNode) return;

  let catalog;
  try { catalog = JSON.parse(dataNode.textContent); } catch (_) { return; }
  const source = page.dataset.source;
  const sourceCatalog = catalog[source] || catalog.otrs;
  const mode = source === "cross" ? "cross" : source;
  const items = sourceCatalog.items || [];
  const itemById = new Map(items.map(item => [item.id, item]));
  const allItems = [...(catalog.otrs.items || []), ...(catalog.cmdb.items || [])];
  const allItemById = new Map(allItems.map(item => [item.id, item]));
  const selectedFields = { item: null, field: null, sort: "name", direction: 1, search: "" };
  const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const setText = (selector, value) => { const node = page.querySelector(selector); if (node) node.textContent = value || "—"; };
  const docsLink = slug => {
    const known = [...(catalog.otrs.links || []), ...(catalog.cmdb.links || [])].find(item => item.slug === slug);
    return known?.url || "#";
  };

  function renderSummary() {
    const summary = page.querySelector("[data-explorer-summary]");
    if (!summary) return;
    if (source === "cross") {
      summary.innerHTML = '<span><strong>1</strong>fluxo de correlação</span><span>OTRS → ConfigItemID → Link CMDB → Provider</span>';
      return;
    }
    const record = sourceCatalog;
    const entityLabel = source === "otrs" ? "tabelas externas" : "operações e entidades externas";
    const fieldLabel = source === "otrs" ? "campos usados pelo código" : "campos de origem mapeados";
    summary.innerHTML = `<span><strong>${Number(record.external_count || 0)}</strong>${entityLabel}</span><span><strong>${Number(record.external_fields || 0)}</strong>${fieldLabel}</span><span><strong>${Number(record.local_count || 0)}</strong>modelos locais · ${Number(record.local_fields || 0)} campos</span>`;
  }

  function renderReferenceLinks() {
    const target = page.querySelector("[data-source-links]");
    if (!target) return;
    const links = source === "cross"
      ? [...(catalog.otrs.links || []), ...(catalog.cmdb.links || [])].filter(item => ["otrs", "cmdb", "cmdb-otrs", "cmdb-relacionamentos", "otrs-relacionamentos"].includes(item.slug))
      : (sourceCatalog.links || []);
    const unique = [...new Map(links.map(item => [item.slug, item])).values()];
    target.innerHTML = unique.map(link => `<a href="${escapeHTML(link.url)}">${escapeHTML(link.label)}</a>`).join("");
  }

  function setupCatalog() {
    if (source === "cross") return;
    const list = page.querySelector("[data-item-list]");
    const search = page.querySelector("[data-item-search]");
    const category = page.querySelector("[data-item-category]");
    const categories = [...new Set(items.map(item => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pt-BR"));
    categories.forEach(name => category.add(new Option(name, name)));
    const external = items.filter(item => !String(item.id).startsWith("local:"));
    const local = items.filter(item => String(item.id).startsWith("local:"));
    let currentId = null;

    const renderList = () => {
      const term = search.value.trim().toLocaleLowerCase("pt-BR");
      const categoryFilter = category.value;
      const matches = item => (!categoryFilter || item.category === categoryFilter) && (!term || [item.name, item.label, item.description, item.purpose, ...(item.tags || []), ...(item.fields || []).flatMap(field => [field.name, field.type, field.description, field.transformation])].join(" ").toLocaleLowerCase("pt-BR").includes(term));
      const groups = [["Fonte externa e operações", external.filter(matches)], ["Modelos locais ARION OPS", local.filter(matches)]];
      const visibleCount = groups.reduce((count, group) => count + group[1].length, 0);
      page.querySelector("[data-item-count]").textContent = `${visibleCount} item(ns)`;
      list.innerHTML = groups.filter(group => group[1].length).map(([label, records]) => `<section class="dx-item-group"><h3>${escapeHTML(label)}</h3>${records.map(item => `<button type="button" class="dx-item" data-item-id="${escapeHTML(item.id)}" aria-current="${item.id === currentId}"><strong>${escapeHTML(item.label || item.name)}</strong><small>${escapeHTML(item.category)} · ${escapeHTML(item.type)}</small><em>${Number(item.field_count || 0)} campos</em></button>`).join("")}</section>`).join("") || '<p class="dx-no-fields">Nenhum item encontrado.</p>';
    };

    const renderFieldDetail = field => {
      const empty = page.querySelector("[data-field-detail-empty]");
      const detail = page.querySelector("[data-field-detail]");
      if (!field) { empty.hidden = false; detail.hidden = true; detail.innerHTML = ""; return; }
      empty.hidden = true;
      detail.hidden = false;
      const reference = field.reference_url || selectedFields.item?.reference_url || "#";
      const rows = [["Descrição", field.description], ["Origem", field.source], ["Tipo e chave", `${field.type || "—"} · ${field.key || "—"}`], ["Permite nulo", field.nullable], ["Transformação / destino", field.transformation], ["Modelo local", field.local_model], ["Relacionamento", field.relationship], ["Uso", field.usage], ["Validação e confiança", `${field.validation || "—"}. ${field.confidence || ""}`]];
      detail.innerHTML = rows.map(([label, value]) => `<div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value || "—")}</dd></div>`).join("") + `<div><dt>Referência</dt><dd><a href="${escapeHTML(reference)}">Abrir dicionário técnico</a></dd></div>`;
    };

    const renderFields = () => {
      const item = selectedFields.item;
      const tbody = page.querySelector("[data-field-rows]");
      const term = selectedFields.search.toLocaleLowerCase("pt-BR");
      const fields = [...(item?.fields || [])].filter(field => !term || [field.name, field.type, field.key, field.description, field.transformation, field.usage, field.source].join(" ").toLocaleLowerCase("pt-BR").includes(term));
      fields.sort((a, b) => String(a[selectedFields.sort] || "").localeCompare(String(b[selectedFields.sort] || ""), "pt-BR", { numeric: true }) * selectedFields.direction);
      tbody.innerHTML = fields.map(field => `<tr data-field-id="${escapeHTML(field.id)}" aria-selected="${selectedFields.field?.id === field.id}"><td>${escapeHTML(field.name)}</td><td>${escapeHTML(field.type || "—")}</td><td>${field.key && field.key !== "—" ? `<span class="dx-key">${escapeHTML(field.key)}</span>` : "—"}</td><td>${escapeHTML(field.nullable || "—")}</td><td>${escapeHTML(field.description || field.usage || "—")}</td></tr>`).join("");
      page.querySelector("[data-field-count]").textContent = `${fields.length} de ${Number(item?.field_count || 0)} campo(s)`;
      page.querySelector("[data-no-fields]").hidden = fields.length > 0;
      renderFieldDetail(selectedFields.field);
    };

    const selectItem = id => {
      const item = allItemById.get(id) || itemById.get(id);
      if (!item) return;
      currentId = item.id;
      selectedFields.item = item;
      selectedFields.field = null;
      selectedFields.search = "";
      page.querySelector("[data-field-search]").value = "";
      page.querySelector("[data-record-empty]").hidden = true;
      page.querySelector("[data-record-content]").hidden = false;
      setText("[data-record-type]", `${item.source || ""} · ${item.type || "estrutura"} · ${item.category || ""}`);
      setText("[data-record-name]", item.label || item.name);
      setText("[data-record-description]", item.description);
      setText("[data-record-origin]", item.origin);
      setText("[data-record-purpose]", item.purpose);
      setText("[data-record-validation]", item.validation);
      setText("[data-record-confidence]", item.confidence);
      const reference = page.querySelector("[data-record-reference]");
      reference.href = item.reference_url || "#";
      reference.textContent = item.reference_url ? "Abrir documentação técnica" : "Documentação técnica";
      const links = sourceCatalog.links || [];
      const relationSlug = source === "otrs" ? "otrs-relacionamentos" : "cmdb-relacionamentos";
      const consumerSlug = source === "otrs" ? "otrs-consumidores" : "cmdb-consumidores";
      const traceSlug = source === "otrs" ? "otrs-rastreabilidade" : "cmdb-rastreabilidade";
      const recordLinks = page.querySelector("[data-record-links]");
      recordLinks.replaceChildren(...links.filter(link => link.url === item.reference_url || [relationSlug, consumerSlug, traceSlug].includes(link.slug)).slice(0, 4).map(link => {
        const anchor = document.createElement("a");
        anchor.href = link.url;
        anchor.textContent = link.label;
        return anchor;
      }));
      renderFields();
      renderList();
    };

    search.addEventListener("input", renderList);
    category.addEventListener("change", renderList);
    page.querySelector("[data-field-search]").addEventListener("input", event => { selectedFields.search = event.target.value.trim(); renderFields(); });
    page.querySelector("[data-field-rows]").addEventListener("click", event => {
      const row = event.target.closest("[data-field-id]");
      if (!row || !selectedFields.item) return;
      selectedFields.field = selectedFields.item.fields.find(field => field.id === row.dataset.fieldId) || null;
      renderFields();
    });
    page.querySelectorAll("[data-sort-field]").forEach(button => button.addEventListener("click", () => {
      const key = button.dataset.sortField;
      selectedFields.direction = selectedFields.sort === key ? -selectedFields.direction : 1;
      selectedFields.sort = key;
      renderFields();
    }));
    list.addEventListener("click", event => { const button = event.target.closest("[data-item-id]"); if (button) selectItem(button.dataset.itemId); });
    renderList();
    if (external.length) selectItem(external[0].id);
  }

  function setupTabs() {
    const catalogView = page.querySelector("[data-catalog-view]");
    const diagramView = page.querySelector("[data-diagram-view]");
    page.querySelectorAll("[data-explorer-view]").forEach(button => button.addEventListener("click", () => {
      const showCatalog = button.dataset.explorerView === "catalog";
      catalogView.hidden = !showCatalog;
      diagramView.hidden = showCatalog;
      page.querySelectorAll("[data-explorer-view]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
      if (!showCatalog) drawDiagram();
    }));
    if (source !== "cross") {
      page.querySelector("[data-diagram-view]").hidden = true;
      page.querySelector("[data-catalog-view]").hidden = false;
    }
  }

  let graph;
  let graphScale = 0.9;
  let graphOffset = { x: 0, y: 0 };
  let selectedNodeId = "";
  let selectedEdge = null;
  let graphNodes = [];
  let graphEdges = [];
  let graphSize = { width: 1320, height: 900 };
  const nodePositions = new Map();

  function layoutNodes(nodes) {
    nodePositions.clear();
    if (source === "otrs") {
      const coordinates = {
        ticket: [70, 405], queue: [370, 130], ticket_state: [370, 275], ticket_priority: [370, 420],
        ticket_type: [370, 555], service: [370, 690], sla: [370, 825], ticket_state_type: [670, 275],
        dynamic_field_value: [670, 430], dynamic_field: [970, 430], ticket_history: [670, 585],
        ticket_history_type: [970, 585], link_relation: [670, 740], link_object: [970, 685],
        link_type: [970, 795], link_state: [970, 905], article: [670, 1030],
        article_data_mime: [970, 1030], article_sender_type: [1270, 1030],
        configitem: [1270, 250], general_catalog: [1570, 250],
      };
      nodes.forEach(node => { const point = coordinates[node.name]; if (point) nodePositions.set(node.id, { x: point[0], y: point[1] }); });
      graphSize = { width: 1780, height: 1150 };
      return;
    }
    if (source === "cmdb") {
      const coordinates = {
        "serviceup:search": [70, 190], "serviceup:get": [350, 190], "serviceup:response": [630, 190],
        "serviceup:cmdblinkmirror": [910, 110], "serviceup:cmdboperatormirror": [910, 280],
        "local:cmdblinkmirror": [1210, 110], "local:cmdboperatormirror": [1210, 280],
      };
      nodes.forEach(node => { const point = coordinates[node.id]; if (point) nodePositions.set(node.id, { x: point[0], y: point[1] }); });
      graphSize = { width: 1500, height: 500 };
      return;
    }
    if (source === "cross") {
      const coordinates = {
        "otrs:ticket": [45, 190], "otrs:link_relation": [290, 190], "otrs:configitem": [535, 190],
        "local:otrsticketconfigitem": [780, 190], "local:cmdblinkmirror": [1025, 190],
        "local:cmdboperatormirror": [1270, 190], "module:base": [1515, 60], "module:mapa": [1515, 180],
        "module:rankings": [1515, 300], "module:cockpit": [1515, 420],
      };
      nodes.forEach(node => { const point = coordinates[node.id]; if (point) nodePositions.set(node.id, { x: point[0], y: point[1] }); });
      graphSize = { width: 1740, height: 560 };
      return;
    }
    const columns = source === "otrs"
      ? { "Tickets e dimensões": 70, "Dynamic Fields": 370, "Histórico": 650, "Relacionamentos e ConfigItems": 930, "Artigos": 1210, "Outros": 70 }
      : { "Operações ServiceUp": 70, "ConfigItem externo": 420, "Resposta JSON": 700, "Espelho local": 990 };
    const grouped = new Map();
    nodes.forEach(node => { const key = node.category || "Outros"; if (!grouped.has(key)) grouped.set(key, []); grouped.get(key).push(node); });
    let maxRows = 1;
    grouped.forEach((group, key) => {
      maxRows = Math.max(maxRows, group.length);
      group.forEach((node, index) => nodePositions.set(node.id, { x: columns[key] ?? 70, y: 90 + index * 106 }));
    });
    graphSize = { width: source === "otrs" ? 1530 : 1270, height: Math.max(500, 110 + maxRows * 106) };
  }

  function nodeHTML(node, index, details) {
    const position = nodePositions.get(node.id);
    if (!position) return "";
    const active = node.id === selectedNodeId;
    const connected = !selectedNodeId || graphEdges.some(edge => (edge.source === selectedNodeId || edge.target === selectedNodeId) && (edge.source === node.id || edge.target === node.id));
    const subtitle = details ? (node.fields || []).slice(0, 3).map(field => field.name).join(" · ") : `${node.type || "estrutura"} · ${node.field_count ?? 0} campos`;
    const label = node.label || node.name;
    const clipped = label.length > 25 ? `${label.slice(0, 23)}…` : label;
    return `<g class="dx-diagram-node${active ? " is-active" : ""}${connected ? "" : " is-muted"}" data-node-id="${escapeHTML(node.id)}" transform="translate(${position.x} ${position.y})" tabindex="0" role="button" aria-label="${escapeHTML(label)}"><rect width="185" height="70" rx="7"></rect><text x="10" y="21">${escapeHTML(clipped)}</text><text class="dx-node-subtitle" x="10" y="39">${escapeHTML(subtitle.slice(0, 34))}</text><text class="dx-node-subtitle" x="10" y="56">${escapeHTML(node.category || node.source || "")}</text></g>`;
  }

  function edgeHTML(edge, index) {
    const from = nodePositions.get(edge.source), to = nodePositions.get(edge.target);
    if (!from || !to) return "";
    const x1 = from.x + 185, y1 = from.y + 35, x2 = to.x, y2 = to.y + 35;
    const active = selectedEdge === index || (selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId));
    const muted = selectedNodeId && edge.source !== selectedNodeId && edge.target !== selectedNodeId;
    const label = edge.label || edge.source_field || "relação";
    return `<g class="dx-diagram-edge${active ? " is-active" : ""}${muted ? " is-muted" : ""}" data-edge-index="${index}" tabindex="0" role="button" aria-label="${escapeHTML(label)}"><path d="M ${x1} ${y1} L ${x2} ${y2}"></path><path class="dx-edge-hit" d="M ${x1} ${y1} L ${x2} ${y2}"></path><text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 6}" text-anchor="middle">${escapeHTML(label.slice(0, 32))}</text></g>`;
  }

  function applyGraphTransform() {
    const world = page.querySelector("[data-diagram-world]");
    if (world) world.setAttribute("transform", `translate(${graphOffset.x} ${graphOffset.y}) scale(${graphScale})`);
    setText("[data-zoom-value]", `${Math.round(graphScale * 100)}%`);
  }

  function drawDiagram() {
    graph = catalog.diagrams?.[mode];
    if (!graph) return;
    const nodes = graph.nodes || [];
    const domain = page.querySelector("[data-diagram-domain]");
    const previous = domain.value;
    const domains = [...new Set(nodes.map(node => node.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pt-BR"));
    domain.innerHTML = '<option value="">Todos</option>' + domains.map(name => `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join("");
    if (domains.includes(previous)) domain.value = previous;
    const visible = nodes.filter(node => !domain.value || node.category === domain.value);
    const visibleIds = new Set(visible.map(node => node.id));
    graphNodes = visible;
    graphEdges = (graph.edges || []).filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target));
    layoutNodes(graphNodes);
    const svg = page.querySelector("[data-diagram]");
    svg.setAttribute("viewBox", `0 0 ${graphSize.width} ${graphSize.height}`);
    page.querySelector("[data-diagram-title]").textContent = graph.title;
    const detailed = page.querySelector("[data-diagram-detail]").checked;
    page.querySelector("[data-diagram-world]").innerHTML = graphEdges.map(edgeHTML).join("") + graphNodes.map((node, index) => nodeHTML(node, index, detailed)).join("");
    applyGraphTransform();
    if (selectedEdge != null && graphEdges[selectedEdge]) showEdge(graphEdges[selectedEdge]);
  }

  function showEdge(edge) {
    const panel = page.querySelector("[data-relation-detail]");
    const sourceNode = allItemById.get(edge.source) || graphNodes.find(node => node.id === edge.source);
    const targetNode = allItemById.get(edge.target) || graphNodes.find(node => node.id === edge.target);
    const title = `${sourceNode?.label || sourceNode?.name || edge.source} → ${targetNode?.label || targetNode?.name || edge.target}`;
    panel.innerHTML = `<strong>${escapeHTML(title)}</strong><span>${escapeHTML(edge.confidence || "Relação documentada pelo código")}</span><dl>${[["Relação", edge.label], ["Campo de origem", edge.source_field], ["Campo de destino", edge.target_field], ["Cardinalidade", edge.cardinality], ["Consulta / fluxo", edge.query], ["Possível multiplicação", edge.multiplication], ["Deduplicação", edge.deduplication]].map(([label, value]) => `<div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value || "—")}</dd></div>`).join("")}</dl>`;
  }

  function setupDiagram() {
    const diagram = page.querySelector("[data-diagram]");
    page.querySelector("[data-diagram-domain]").addEventListener("change", () => { selectedEdge = null; drawDiagram(); });
    page.querySelector("[data-diagram-detail]").addEventListener("change", drawDiagram);
    page.querySelector("[data-zoom-in]").addEventListener("click", () => { graphScale = Math.min(1.8, graphScale + 0.1); applyGraphTransform(); });
    page.querySelector("[data-zoom-out]").addEventListener("click", () => { graphScale = Math.max(0.4, graphScale - 0.1); applyGraphTransform(); });
    page.querySelector("[data-diagram-reset]").addEventListener("click", () => { graphScale = 0.9; graphOffset = { x: 0, y: 0 }; selectedNodeId = ""; selectedEdge = null; drawDiagram(); });
    diagram.addEventListener("click", event => {
      const node = event.target.closest("[data-node-id]");
      if (node) {
        selectedNodeId = node.dataset.nodeId;
        selectedEdge = null;
        page.querySelector("[data-relation-detail]").innerHTML = `<strong>${escapeHTML(node.getAttribute("aria-label"))}</strong><span>Selecione uma conexão para ver campos, cardinalidade, consulta e deduplicação.</span>`;
        drawDiagram();
        const item = allItemById.get(selectedNodeId);
        if (item && source !== "cross") page.querySelector(`[data-item-id="${CSS.escape(item.id)}"]`)?.click();
        return;
      }
      const edge = event.target.closest("[data-edge-index]");
      if (edge) {
        selectedEdge = Number(edge.dataset.edgeIndex);
        showEdge(graphEdges[selectedEdge]);
        drawDiagram();
      }
    });
    diagram.addEventListener("keydown", event => {
      if (!(["Enter", " "].includes(event.key))) return;
      const target = event.target.closest("[data-node-id], [data-edge-index]");
      if (target) { event.preventDefault(); target.dispatchEvent(new MouseEvent("click", { bubbles: true })); }
    });
    diagram.addEventListener("wheel", event => {
      event.preventDefault();
      graphScale = Math.max(0.4, Math.min(1.8, graphScale + (event.deltaY < 0 ? 0.06 : -0.06)));
      applyGraphTransform();
    }, { passive: false });
    let drag = null;
    diagram.addEventListener("pointerdown", event => {
      if (event.target.closest("[data-node-id], [data-edge-index]")) return;
      drag = { x: event.clientX, y: event.clientY, offsetX: graphOffset.x, offsetY: graphOffset.y };
      diagram.classList.add("is-panning");
      diagram.setPointerCapture(event.pointerId);
    });
    diagram.addEventListener("pointermove", event => {
      if (!drag) return;
      const bounds = diagram.getBoundingClientRect();
      const xFactor = graphSize.width / Math.max(bounds.width, 1);
      const yFactor = graphSize.height / Math.max(bounds.height, 1);
      graphOffset = { x: drag.offsetX + (event.clientX - drag.x) * xFactor, y: drag.offsetY + (event.clientY - drag.y) * yFactor };
      applyGraphTransform();
    });
    const endDrag = () => { drag = null; diagram.classList.remove("is-panning"); };
    diagram.addEventListener("pointerup", endDrag);
    diagram.addEventListener("pointercancel", endDrag);
    drawDiagram();
  }

  renderSummary();
  renderReferenceLinks();
  setupCatalog();
  setupTabs();
  setupDiagram();
})();
