(() => {
  "use strict";

  const body = document.body;
  const layout = document.querySelector(".quality-layout");
  const sidebarToggle = document.querySelector("[data-quality-sidebar-toggle]");
  const sidebarStorageKey = "arion_quality_sidebar_collapsed";
  const popoverSelector = ".quality-filter-details, .quality-list-filter-details, .quality-row-menu, .quality-contact-actions, .quality-mobile-nav";
  const filterSelector = ".quality-filter-details, .quality-list-filter-details";
  const popovers = [...document.querySelectorAll(popoverSelector)];
  let sidebarCollapsed = false;

  document.querySelectorAll("form[data-quality-persist]").forEach(form => {
    const screen = form.dataset.qualityPersist;
    const key = `arion_quality_filters:${form.dataset.qualityUser || "anonymous"}:${screen}`;
    const fields = [...form.elements].filter(field => field.name && field.type !== "submit" && field.type !== "button");
    const query = new URLSearchParams(window.location.search);
    const hasUrlState = fields.some(field => query.has(field.name));
    const readState = () => Object.fromEntries(fields.map(field => [field.name, field.type === "checkbox" ? field.checked : field.value]));
    const writeState = state => fields.forEach(field => {
      if (!Object.prototype.hasOwnProperty.call(state, field.name)) return;
      if (field.type === "checkbox") field.checked = Boolean(state[field.name]);
      else if (field.tagName === "SELECT" && ![...field.options].some(option => option.value === String(state[field.name]))) return;
      else field.value = String(state[field.name]);
    });
    const saveState = () => {
      const state = readState();
      try { window.localStorage.setItem(key, JSON.stringify(state)); } catch (_) { /* URL segue persistindo os filtros. */ }
      const params = new URLSearchParams();
      fields.forEach(field => {
        if (field.type === "checkbox") { if (field.checked) params.set(field.name, "on"); }
        else if (field.value !== "") params.set(field.name, field.value);
      });
      window.history.replaceState(null, "", `${window.location.pathname}${params.size ? `?${params}` : ""}${window.location.hash}`);
    };

    if (hasUrlState) {
      try { window.localStorage.setItem(key, JSON.stringify(readState())); } catch (_) { /* Armazenamento local opcional. */ }
    } else {
      try {
        const saved = window.localStorage.getItem(key);
        if (saved) {
          const before = JSON.stringify(readState());
          writeState(JSON.parse(saved));
          if (JSON.stringify(readState()) !== before) {
            saveState();
            form.requestSubmit();
          }
        }
      } catch (_) { /* Mantém os filtros padrão da página. */ }
    }
    form.addEventListener("input", saveState);
    form.addEventListener("change", saveState);
    form.addEventListener("submit", saveState);
  });

  document.querySelectorAll("[data-quality-clear-filters]").forEach(link => {
    link.addEventListener("click", () => {
      const screen = link.dataset.qualityClearFilters;
      const owner = document.querySelector(`form[data-quality-persist="${screen}"]`)?.dataset.qualityUser || "anonymous";
      try { window.localStorage.removeItem(`arion_quality_filters:${owner}:${screen}`); } catch (_) { /* Limpeza da URL continua funcional. */ }
    });
  });

  try {
    sidebarCollapsed = window.localStorage.getItem(sidebarStorageKey) === "true";
  } catch (_) {
    sidebarCollapsed = false;
  }

  const renderSidebar = () => {
    const canCollapse = window.matchMedia("(min-width: 901px)").matches;
    layout?.classList.toggle("quality-layout--collapsed", canCollapse && sidebarCollapsed);
    if (!sidebarToggle) return;
    const expanded = !canCollapse || !sidebarCollapsed;
    const label = sidebarCollapsed && canCollapse ? "Expandir menu" : "Recolher menu";
    sidebarToggle.setAttribute("aria-expanded", String(expanded));
    sidebarToggle.setAttribute("aria-label", label);
    sidebarToggle.setAttribute("title", label);
  };

  sidebarToggle?.addEventListener("click", () => {
    sidebarCollapsed = !sidebarCollapsed;
    try {
      window.localStorage.setItem(sidebarStorageKey, String(sidebarCollapsed));
    } catch (_) {
      // A preferência continua aplicada enquanto esta página estiver aberta.
    }
    renderSidebar();
  });
  window.addEventListener("resize", renderSidebar, { passive: true });
  renderSidebar();

  const syncPopover = details => {
    const summary = details.querySelector(":scope > summary");
    if (!summary) return;
    summary.setAttribute("aria-expanded", String(details.open));
    if (!details.matches(filterSelector)) return;
    const panel = details.querySelector(":scope > .quality-filter-panel, :scope > .quality-list-filter-panel");
    if (!panel) return;
    if (!panel.id) panel.id = `quality-filter-panel-${popovers.indexOf(details) + 1}`;
    summary.setAttribute("aria-controls", panel.id);
  };

  popovers.forEach(syncPopover);

  // Um único gerenciador mantém filtros e menus da Qualidade mutuamente exclusivos.
  document.addEventListener("toggle", event => {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.matches(popoverSelector)) return;
    syncPopover(details);
    if (!details.open) return;
    popovers.forEach(other => {
      if (other !== details && other.open) other.open = false;
    });
  }, true);

  document.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    popovers.forEach(details => {
      if (details.open && !details.contains(target)) details.open = false;
    });
  });

  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const openPopover = [...popovers].reverse().find(details => details.open);
    if (!openPopover) return;
    event.preventDefault();
    openPopover.open = false;
    openPopover.querySelector(":scope > summary")?.focus();
  });
})();
