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
