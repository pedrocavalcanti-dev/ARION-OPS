(function () {
  "use strict";

  const page = document.querySelector(".tickets-page");
  if (!page) return;

  const mobileFilters = document.querySelector("[data-mobile-filter-sheet]");
  const mobileFiltersOpen = document.querySelector("[data-mobile-filters-open]");
  const mobileFiltersClose = document.querySelector("[data-mobile-filters-close]");
  const mobileFilterCount = mobileFiltersOpen?.querySelector("[data-filter-count]");
  const filterForm = document.querySelector(".ticket-filters");
  const activeFilterChips = document.querySelector("[data-ticket-active-filters]");
  const removeFilter = (name) => {
    const url = new URL(window.location.href);
    url.searchParams.delete(name);
    url.searchParams.delete("page");
    window.location.assign(url.toString());
  };
  const renderActiveFilterChips = () => {
    if (!filterForm || !activeFilterChips) return;
    activeFilterChips.replaceChildren();
    const addChip = (name, label) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "ticket-active-filter-chip";
      chip.setAttribute("aria-label", `Remover filtro ${label}`);
      const text = document.createElement("span");
      text.textContent = label;
      const close = document.createElement("span");
      close.setAttribute("aria-hidden", "true");
      close.textContent = "×";
      chip.append(text, close);
      chip.addEventListener("click", () => removeFilter(name));
      activeFilterChips.append(chip);
    };
    const query = filterForm.querySelector('input[name="q"]');
    if (query?.value.trim()) addChip("q", `Busca: ${query.value.trim()}`);
    filterForm.querySelectorAll("select").forEach((select) => {
      if (!select.value || (select.name === "sort" && select.value === "aging")) return;
      const title = select.closest("label")?.querySelector("span")?.textContent.trim() || select.name;
      addChip(select.name, `${title}: ${select.selectedOptions[0]?.textContent.trim() || select.value}`);
    });
    filterForm.querySelectorAll('input[type="checkbox"]:checked').forEach((input) => {
      const title = input.closest("label")?.textContent.trim() || input.name;
      addChip(input.name, title);
    });
    activeFilterChips.hidden = activeFilterChips.childElementCount === 0;
  };
  const refreshMobileFilterCount = () => {
    if (!mobileFilters || !mobileFilterCount) return;
    const activeSelects = [...mobileFilters.querySelectorAll("select")].filter((select) => select.value && !(select.name === "sort" && select.value === "aging"));
    const checked = mobileFilters.querySelectorAll('input[type="checkbox"]:checked').length;
    const search = filterForm?.querySelector('input[name="q"]')?.value.trim() ? 1 : 0;
    const count = activeSelects.length + checked + search;
    mobileFilterCount.hidden = count === 0;
    mobileFilterCount.textContent = String(count);
    const expanded = mobileFilters.classList.contains("is-open");
    mobileFiltersOpen.setAttribute("aria-expanded", String(expanded));
    mobileFiltersOpen.setAttribute("aria-label", expanded ? "Fechar filtros da fila" : count ? `Abrir filtros da fila, ${count} ativos` : "Abrir filtros da fila");
  };
  const setMobileFiltersExpanded = (expanded, returnFocus = false) => {
    if (!mobileFilters || !mobileFiltersOpen) return;
    mobileFilters.classList.toggle("is-open", expanded);
    refreshMobileFilterCount();
    if (returnFocus) mobileFiltersOpen.focus({ preventScroll: true });
  };
  mobileFiltersOpen?.addEventListener("click", () => setMobileFiltersExpanded(!mobileFilters?.classList.contains("is-open")));
  mobileFiltersClose?.addEventListener("click", () => setMobileFiltersExpanded(false, true));
  mobileFilters?.querySelectorAll("select, input[type='checkbox']").forEach((control) => control.addEventListener("change", () => { refreshMobileFilterCount(); renderActiveFilterChips(); }));
  filterForm?.querySelector('input[name="q"]')?.addEventListener("input", () => { refreshMobileFilterCount(); renderActiveFilterChips(); });
  refreshMobileFilterCount();
  renderActiveFilterChips();

  const drawer = document.querySelector("[data-ticket-drawer]");
  const backdrop = document.querySelector("[data-drawer-backdrop]");
  const queueDialog = document.querySelector("[data-queue-dialog]");
  const detailTemplate = page.dataset.detailTemplate || "";
  const trackingTemplate = page.dataset.trackingTemplate || "";
  const manage = page.dataset.manage === "1";
  let openedTicketId = "";
  let lastFocus = null;
  let searchTimer = null;

  const endpointFromTemplate = (template, id) => template.replace(/\/0(?=\/)/, `/${id}`);
  const trackingUrl = (id) => endpointFromTemplate(trackingTemplate, id);
  const csrfToken = () => {
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    const hiddenToken = document.querySelector('[name="csrfmiddlewaretoken"]');
    return hiddenToken ? hiddenToken.value : "";
  };
  const requestJSON = async (url, body) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "XMLHttpRequest" },
      body,
    });
    let data = {};
    try { data = await response.json(); } catch (_) { /* The view may return an HTML error. */ }
    if (!response.ok || !data.ok) {
      const error = new Error(data.message || "Não foi possível salvar a alteração.");
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  };
  const appendText = (parent, tag, text, className) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = text == null || text === "" ? "—" : String(text);
    parent.appendChild(node);
    return node;
  };
  const appendMeta = (parent, label, value) => {
    const item = document.createElement("div");
    item.className = "drawer-meta-item";
    appendText(item, "span", label);
    appendText(item, "strong", value || "—");
    parent.appendChild(item);
  };
  const showDrawerMessage = (message, isError) => {
    const feedback = drawer.querySelector("[data-drawer-feedback]");
    feedback.textContent = message || "";
    feedback.classList.toggle("is-error", Boolean(isError));
  };
  const openQueueDialog = (action, viewId, currentName) => {
    if (!queueDialog) return;
    queueDialog.dataset.action = action;
    queueDialog.dataset.viewId = viewId || "";
    const title = queueDialog.querySelector("[data-queue-dialog-title]");
    const input = queueDialog.querySelector('[name="name"]');
    const label = queueDialog.querySelector("[data-queue-name-label]");
    const submit = queueDialog.querySelector("[data-queue-dialog-submit]");
    const message = queueDialog.querySelector("[data-queue-dialog-message]");
    title.textContent = action === "rename" ? "Renomear fila pessoal" : "Salvar fila pessoal";
    input.value = currentName || "";
    label.hidden = false;
    input.required = true;
    submit.textContent = action === "rename" ? "Salvar nome" : "Salvar fila";
    message.textContent = "";
    queueDialog.showModal();
    input.focus();
    input.select();
  };

  const renderSummary = (data) => {
    const host = drawer.querySelector("[data-ticket-summary]");
    host.replaceChildren();
    const summary = document.createElement("section");
    summary.className = "drawer-summary";
    appendText(summary, "p", "Status oficial OTRS", "drawer-section-kicker");
    const headline = data.ticket.compact_title || data.ticket.title || "Chamado sem título";
    appendText(summary, "h3", headline, "drawer-compact-title");
    if (data.ticket.title && data.ticket.title !== headline) {
      const originalDetails = document.createElement("details");
      originalDetails.className = "drawer-original-title";
      appendText(originalDetails, "summary", "Título original OTRS");
      appendText(originalDetails, "p", data.ticket.title);
      summary.appendChild(originalDetails);
    }
    const meta = document.createElement("div");
    meta.className = "drawer-meta";
    appendMeta(meta, "Status oficial OTRS", data.ticket.status_label || data.ticket.external_state);
    appendMeta(meta, "Fila atual", data.ticket.queue);
    appendMeta(meta, "Prioridade OTRS", data.ticket.priority);
    appendMeta(meta, "Criado em", data.ticket.created_at ? new Date(data.ticket.created_at).toLocaleString("pt-BR") : "Sem data");
    appendMeta(meta, "Aging", `${data.ticket.age_label || "Sem data de criação"}${data.ticket.over_24h ? " · ARION >24h" : ""}`);
    if (data.ticket.closing_reason_label) appendMeta(meta, "Motivo de encerramento", data.ticket.closing_reason_label);
    summary.appendChild(meta);
    host.appendChild(summary);
    const events = data.ticket.source_events || [];
    if (events.length) {
      const history = document.createElement("details");
      history.className = "drawer-original-title";
      appendText(history, "summary", "Histórico oficial de atendimento OTRS · até 80 eventos recentes");
      const eventLabels = { NewTicket: "Criação", Move: "Transferência", StateUpdate: "Alteração de estado", Merged: "Mesclagem" };
      events.forEach((event) => {
        const date = new Date(event.at).toLocaleString("pt-BR");
        appendText(history, "p", `${date} · ${eventLabels[event.type] || event.type} · ${event.queue} · ${event.state}`);
      });
      host.appendChild(history);
    }

    const headerLink = drawer.querySelector("[data-drawer-ticket-link]");
    const headerNumber = drawer.querySelector("[data-drawer-ticket-number]");
    if (data.ticket.otrs_url) {
      headerLink.href = data.ticket.otrs_url;
      headerLink.textContent = data.ticket.ticket_number || "Chamado";
      headerLink.hidden = false;
      headerNumber.hidden = true;
      headerLink.setAttribute("aria-label", "Abrir chamado oficial no OTRS em nova aba");
    } else {
      headerLink.hidden = true;
      headerLink.removeAttribute("href");
      headerNumber.textContent = data.ticket.ticket_number || "Chamado";
      headerNumber.hidden = false;
    }
  };

  const renderLinks = (links) => {
    const host = drawer.querySelector("[data-ticket-links]");
    host.replaceChildren();
    if (!links.length) {
      appendText(host, "p", "Nenhum link relacionado dentro do seu escopo de dados.", "drawer-empty");
      return;
    }
    links.forEach((link) => {
      const card = document.createElement("article");
      card.className = "drawer-link-card";
      appendText(card, "strong", link.designation || link.number || link.config_item_id);
      const clientPart = link.client || "Operadora não identificada";
      const projectPart = link.final_client && link.project && link.final_client.toLowerCase() !== link.project.toLowerCase() && link.project !== "Não identificado"
        ? `${link.final_client} (${link.project})`
        : (link.final_client || link.project || "Projeto não identificado");
      appendText(card, "span", link.is_internal_arion ? `${clientPart} · Infraestrutura própria (${link.project || "ARION"})` : `${clientPart} · ${projectPart}`);
      appendText(card, "span", `${link.provider || "Provider não identificado"} · ${[link.city, link.state].filter(Boolean).join(" · ") || "Local não identificado"}`);
      appendText(card, "span", `CI ${link.config_item_id || "—"}${link.number ? ` · Link ${link.number}` : ""}`);
      if (link.deployment_state || link.incident_state) {
        appendText(card, "span", `Estado de implantação: ${link.deployment_state || "—"} · Estado do incidente: ${link.incident_state || "—"}`);
      }
      if (link.recurrent) appendText(card, "span", "Link reincidente nos últimos 30 dias", "drawer-recurrent");
      if (link.base_url) {
        const baseLink = document.createElement("a");
        baseLink.href = link.base_url;
        baseLink.textContent = "Ver este link na Base";
        card.appendChild(baseLink);
      }
      host.appendChild(card);
    });
  };

  const fillSelect = (select, choices, selected, includeEmpty) => {
    select.replaceChildren();
    if (includeEmpty) {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = includeEmpty;
      select.appendChild(empty);
    }
    (choices || []).forEach((choice) => {
      const option = document.createElement("option");
      option.value = choice.value;
      option.textContent = choice.label || `${choice.first_name || ""} ${choice.last_name || ""}`.trim() || choice.username || "";
      if (String(choice.value) === String(selected)) option.selected = true;
      select.appendChild(option);
    });
  };

  const renderTrackingForm = (data) => {
    const form = drawer.querySelector("[data-tracking-form]");
    form.hidden = !manage || !data.ticket.operational;
    if (!manage) return;
    const initial = data.tracking.form.initial;
    fillSelect(form.elements.internal_status, data.tracking.form.statuses, initial.internal_status);
    fillSelect(form.elements.assigned_to, data.tracking.form.assignees, initial.assigned_to, "Sem responsável");
    fillSelect(form.elements.internal_priority, data.tracking.form.priorities, initial.internal_priority, "Sem prioridade interna");
    form.elements.next_action_at.value = initial.next_action_at || "";
    form.elements.revision.value = initial.revision;
  };

  const renderTrackingSummary = (data) => {
    const host = drawer.querySelector("[data-tracking-summary]");
    host.replaceChildren();
    appendMeta(host, "Status ARION", data.tracking.internal_status_label || "Em Aberto");
    appendMeta(host, "Prioridade ARION", data.tracking.internal_priority_label || "Sem prioridade interna");
    appendMeta(host, "Próxima ação ARION", data.tracking.next_action_at
      ? new Date(data.tracking.next_action_at).toLocaleString("pt-BR")
      : "Não definida");

    const assignee = drawer.querySelector("[data-drawer-assignee]");
    const assume = drawer.querySelector("[data-drawer-assume]");
    const unassign = drawer.querySelector("[data-drawer-unassign]");
    const noteForm = drawer.querySelector("[data-note-form]");
    const assignedId = data.tracking.assigned_to_id == null ? "" : String(data.tracking.assigned_to_id);
    if (noteForm && noteForm.elements.revision) noteForm.elements.revision.value = data.tracking.revision;
    assignee.textContent = data.tracking.assigned_to || "Sem responsável";
    assume.hidden = !manage || !data.ticket.operational || Boolean(assignedId);
    unassign.hidden = !manage || !data.ticket.operational || !assignedId || assignedId !== page.dataset.currentUser;
    if (noteForm) noteForm.hidden = !manage || !data.ticket.operational;
  };

  const renderNotes = (notes) => {
    const host = drawer.querySelector("[data-ticket-notes]");
    host.replaceChildren();
    if (!notes.length) appendText(host, "p", "Nenhuma nota ARION registrada.", "drawer-empty");
    notes.forEach((note) => {
      const item = document.createElement("article");
      item.className = "drawer-note";
      appendText(item, "small", `${note.author} · ${new Date(note.created_at).toLocaleString("pt-BR")}`);
      appendText(item, "p", note.body);
      host.appendChild(item);
    });
  };

  const renderEvents = (events) => {
    const host = drawer.querySelector("[data-ticket-events]");
    host.replaceChildren();
    if (!events.length) appendText(host, "p", "Nenhuma alteração registrada.", "drawer-empty");
    events.forEach((event) => {
      const item = document.createElement("article");
      item.className = "drawer-event";
      const change = event.field ? `${event.field}: ${event.old_value || "—"} → ${event.new_value || "—"}` : event.type;
      appendText(item, "strong", change);
      appendText(item, "small", `${event.actor} · ${new Date(event.created_at).toLocaleString("pt-BR")}`);
      host.appendChild(item);
    });
  };

  const renderActions = (data) => {
    const base = drawer.querySelector("[data-base-link]");
    if (data.urls.base) {
      base.href = data.urls.base;
      base.hidden = false;
    } else {
      base.hidden = true;
    }
    const cockpit = drawer.querySelector("[data-cockpit-link]");
    if (data.urls.cockpit) cockpit.href = data.urls.cockpit;
    else cockpit.hidden = true;
  };

  const loadDetail = async (ticketId, announce) => {
    openedTicketId = String(ticketId);
    drawer.hidden = false;
    backdrop.hidden = false;
    drawer.setAttribute("aria-hidden", "false");
    document.body.classList.add("has-ticket-drawer");
    drawer.querySelector("[data-drawer-loading]").hidden = false;
    drawer.querySelector("[data-drawer-content]").hidden = true;
    if (announce) showDrawerMessage("Carregando o snapshot local…", false);
    requestAnimationFrame(() => drawer.classList.add("is-open"));
    try {
      const response = await fetch(endpointFromTemplate(detailTemplate, ticketId), { credentials: "same-origin" });
      if (!response.ok) throw new Error("Não foi possível carregar este chamado no seu escopo.");
      const data = await response.json();
      if (openedTicketId !== String(ticketId)) return;
      renderSummary(data);
      renderLinks(data.links || []);
      renderTrackingSummary(data);
      renderTrackingForm(data);
      renderNotes(data.notes || []);
      renderEvents(data.events || []);
      renderActions(data);
      drawer.querySelector("[data-drawer-loading]").hidden = true;
      drawer.querySelector("[data-drawer-content]").hidden = false;
      if (announce) showDrawerMessage("Dados carregados do mirror local.", false);
    } catch (error) {
      drawer.querySelector("[data-drawer-loading]").textContent = error.message;
      if (announce) showDrawerMessage(error.message, true);
    }
  };

  const closeDrawer = () => {
    openedTicketId = "";
    drawer.classList.remove("is-open");
    drawer.setAttribute("aria-hidden", "true");
    backdrop.hidden = true;
    document.body.classList.remove("has-ticket-drawer");
    window.setTimeout(() => { drawer.hidden = true; }, 180);
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
  };

  const adjustTabCount = (name, delta) => {
    const counter = document.querySelector(`[data-tab-count="${name}"]`);
    if (!counter || !delta) return;
    const current = Number(counter.dataset.count || 0);
    const next = Math.max(0, current + delta);
    counter.dataset.count = String(next);
    counter.textContent = new Intl.NumberFormat("pt-BR").format(next);
  };

  const updateRowOwner = (rowNode, data) => {
    if (!rowNode) return;
    const owner = rowNode.querySelector("[data-row-owner]");
    const assignee = owner && owner.querySelector("[data-row-assignee]");
    const form = rowNode.querySelector("[data-inline-status-form]");
    if (!owner || !assignee) return;
    const statusSelect = rowNode.querySelector("select[name='internal_status']");
    const previousId = owner.dataset.assignedUser || "";
    const assignedId = data.assigned_to_id == null ? "" : String(data.assigned_to_id);
    const currentId = page.dataset.currentUser;
    if (previousId === currentId && assignedId !== currentId) adjustTabCount("mine", -1);
    if (previousId !== currentId && assignedId === currentId) adjustTabCount("mine", 1);
    if (!previousId && assignedId) adjustTabCount("unassigned", -1);
    if (previousId && !assignedId) adjustTabCount("unassigned", 1);
    assignee.textContent = data.assigned_to || "Sem responsável";
    owner.dataset.assignedUser = assignedId;
    owner.querySelectorAll("[data-assume-ticket], [data-unassign-ticket]").forEach((button) => button.remove());
    if (form && form.elements.revision) form.elements.revision.value = data.revision;
    if (statusSelect && data.internal_status) {
      statusSelect.value = data.internal_status;
      statusSelect.dataset.savedValue = data.internal_status;
      statusSelect.className = `ticket-inline-status ticket-internal--${data.internal_status.toLowerCase()}`;
    }
    if (!manage || (assignedId && assignedId !== page.dataset.currentUser)) return;
    const button = document.createElement("button");
    button.className = "ticket-assume";
    button.type = "button";
    button.dataset.revision = data.revision;
    if (assignedId) {
      button.dataset.unassignTicket = rowNode.dataset.ticketId;
      button.textContent = "Desassumir";
    } else {
      button.dataset.assumeTicket = rowNode.dataset.ticketId;
      button.textContent = "Assumir";
    }
    owner.appendChild(button);
  };

  const updateVisibleRow = (ticketId, data) => {
    const row = Array.from(document.querySelectorAll(".ticket-row[data-ticket-id]")).find(
      (item) => item.dataset.ticketId === String(ticketId),
    );
    updateRowOwner(row, data);
  };

  document.addEventListener("click", (event) => {
    const row = event.target.closest(".ticket-row[data-ticket-id]");
    if (row && !event.target.closest("a,button,select,input,textarea,form,label")) {
      lastFocus = row;
      loadDetail(row.dataset.ticketId, true);
      return;
    }
    if (event.target.closest("[data-drawer-close]") || event.target === backdrop) closeDrawer();
    if (event.target.closest("[data-queue-dialog-cancel]") && queueDialog) queueDialog.close();

    const assumeButton = event.target.closest("[data-assume-ticket]");
    const unassignButton = event.target.closest("[data-unassign-ticket]");
    const rowActionButton = assumeButton || unassignButton;
    if (rowActionButton && manage) {
      const rowNode = rowActionButton.closest(".ticket-row");
      const form = rowNode && rowNode.querySelector("[data-inline-status-form]");
      if (!rowNode || !form) return;
      const action = assumeButton ? "assume" : "unassign";
      const ticketId = rowActionButton.dataset.assumeTicket || rowActionButton.dataset.unassignTicket;
      const body = new FormData(form);
      body.set("action", action);
      body.set("revision", form.elements.revision.value || rowActionButton.dataset.revision || "0");
      rowActionButton.disabled = true;
      requestJSON(trackingUrl(ticketId), body).then((data) => {
        updateRowOwner(rowNode, data);
        const feedback = rowNode.querySelector("[data-row-feedback]");
        if (feedback) {
          feedback.textContent = action === "assume" ? "Chamado assumido." : "Responsável removido.";
          window.setTimeout(() => { feedback.textContent = ""; }, 1800);
        }
        const params = new URLSearchParams(window.location.search);
        if ((action === "assume" && (params.get("tab") === "unassigned" || params.get("assignee") === "unassigned"))
            || (action === "unassign" && (params.get("tab") === "mine" || params.get("assignee") === page.dataset.currentUser))) {
          window.location.reload();
        }
      }).catch((error) => {
        const feedback = rowNode && rowNode.querySelector("[data-row-feedback]");
        if (feedback) {
          feedback.textContent = error.message;
          feedback.classList.add("is-error");
        }
        if (error.status === 409) window.setTimeout(() => window.location.reload(), 700);
      }).finally(() => { rowActionButton.disabled = false; });
    }

    const drawerAssume = event.target.closest("[data-drawer-assume]");
    const drawerUnassign = event.target.closest("[data-drawer-unassign]");
    if ((drawerAssume || drawerUnassign) && manage && openedTicketId) {
      const form = drawer.querySelector("[data-tracking-form]");
      const action = drawerAssume ? "assume" : "unassign";
      const body = new FormData(form);
      body.set("action", action);
      const button = drawerAssume || drawerUnassign;
      button.disabled = true;
      requestJSON(trackingUrl(openedTicketId), body).then((data) => {
        updateVisibleRow(openedTicketId, data);
        return loadDetail(openedTicketId, false).then(() => {
          showDrawerMessage(action === "assume" ? "Chamado assumido." : "Responsável removido.", false);
        });
      }).catch((error) => {
        showDrawerMessage(error.message, true);
        if (error.status === 409) window.setTimeout(() => window.location.reload(), 700);
      }).finally(() => { button.disabled = false; });
    }

    const saveButton = event.target.closest("[data-save-queue]");
    if (saveButton && manage) {
      openQueueDialog("save", "", "");
    }

    const renameButton = event.target.closest("[data-rename-view]");
    if (renameButton && manage) {
      openQueueDialog("rename", renameButton.dataset.renameView, renameButton.dataset.name || "");
    }

    const defaultButton = event.target.closest("[data-default-view]");
    if (defaultButton && manage) {
      const body = new URLSearchParams({ action: "default", id: defaultButton.dataset.defaultView });
      requestJSON(page.dataset.viewsAction, body).then(() => window.location.reload()).catch((error) => window.alert(error.message));
    }

    const deleteButton = event.target.closest("[data-delete-view]");
    if (deleteButton && manage && window.confirm(`Excluir a fila “${deleteButton.dataset.name || ""}”?`)) {
      const body = new URLSearchParams({ action: "delete", id: deleteButton.dataset.deleteView });
      requestJSON(page.dataset.viewsAction, body).then(() => window.location.reload()).catch((error) => window.alert(error.message));
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && drawer && !drawer.hidden) closeDrawer();
    if (event.key === "Escape" && mobileFilters?.classList.contains("is-open")) setMobileFiltersExpanded(false, true);
    if ((event.key === "Enter" || event.key === " ") && event.target.matches(".ticket-row[data-ticket-id]") && !event.target.closest("a,button,select,input,textarea,form")) {
      event.preventDefault();
      lastFocus = event.target;
      loadDetail(event.target.dataset.ticketId, true);
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target.matches(".ticket-filters select, .ticket-filters input[type='checkbox']")) {
      if (event.target.name === "otrs_status") {
        const tab = document.querySelector('.ticket-filters [name="tab"]');
        if (tab) tab.value = "all";
      }
      const form = event.target.closest("form");
      if (form) form.requestSubmit();
      return;
    }
    if (event.target.matches("[data-page-size]")) {
      const hidden = document.querySelector('.ticket-filters [name="page_size"]');
      if (hidden) hidden.value = event.target.value;
      const form = document.querySelector(".ticket-filters");
      if (form) form.requestSubmit();
    }
    if (event.target.matches("select[name='internal_status']") && event.target.closest("[data-inline-status-form]") && manage) {
      const select = event.target;
      const form = select.form;
      const row = form.closest(".ticket-row");
      const feedback = form.querySelector("[data-row-feedback]");
      const previous = select.dataset.savedValue || select.defaultValue;
      const body = new FormData(form);
      select.disabled = true;
      feedback.textContent = "Salvando…";
      feedback.classList.remove("is-error");
      requestJSON(trackingUrl(form.dataset.ticketId), body).then((data) => {
        updateRowOwner(row, data);
        select.dataset.savedValue = select.value;
        select.className = `ticket-inline-status ticket-internal--${select.value.toLowerCase()}`;
        feedback.textContent = "Salvo";
        const params = new URLSearchParams(window.location.search);
        if (params.has("arion_status")) window.location.reload();
        else window.setTimeout(() => { feedback.textContent = ""; }, 1800);
      }).catch((error) => {
        if (previous) select.value = previous;
        feedback.textContent = error.message;
        feedback.classList.add("is-error");
        if (error.status === 409) window.setTimeout(() => window.location.reload(), 700);
      }).finally(() => { select.disabled = false; });
    }
  });

  document.addEventListener("input", (event) => {
    if (!event.target.matches('.ticket-filters input[type="search"]')) return;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => event.target.form && event.target.form.requestSubmit(), 425);
  });

  document.addEventListener("submit", (event) => {
    if (event.target.matches("[data-queue-dialog-form]")) {
      event.preventDefault();
      if (!manage) return;
      const form = event.target;
      const dialog = form.closest("[data-queue-dialog]");
      const name = form.elements.name.value.trim();
      const message = dialog.querySelector("[data-queue-dialog-message]");
      const button = dialog.querySelector("[data-queue-dialog-submit]");
      if (!name) {
        form.elements.name.focus();
        return;
      }
      const payload = { action: dialog.dataset.action, name };
      if (dialog.dataset.action === "rename") payload.id = dialog.dataset.viewId;
      if (dialog.dataset.action === "save") {
        let filters = {};
        try { filters = JSON.parse(document.getElementById("ticket-queue-filters").textContent || "{}"); }
        catch (_) { message.textContent = "Não foi possível ler os filtros desta fila."; return; }
        if (Object.prototype.hasOwnProperty.call(filters, "page_size")) {
          filters.per_page = filters.page_size;
          delete filters.page_size;
        }
        if (Object.prototype.hasOwnProperty.call(filters, "over_24h")) {
          filters.aging = filters.over_24h;
          delete filters.over_24h;
        }
        payload.filters = JSON.stringify(filters);
      }
      button.disabled = true;
      message.textContent = "Salvando…";
      requestJSON(page.dataset.viewsAction, new URLSearchParams(payload)).then(() => {
        dialog.close();
        window.location.reload();
      }).catch((error) => {
        message.textContent = error.message;
      }).finally(() => { button.disabled = false; });
      return;
    }
    if (event.target.matches("[data-tracking-form]")) {
      event.preventDefault();
      if (!manage || !openedTicketId) return;
      const button = event.target.querySelector("button[type='submit']");
      button.disabled = true;
      showDrawerMessage("Salvando acompanhamento local…", false);
      requestJSON(trackingUrl(openedTicketId), new FormData(event.target)).then((data) => {
        updateVisibleRow(openedTicketId, data);
        showDrawerMessage("Acompanhamento ARION salvo.", false);
        return loadDetail(openedTicketId, false);
      }).catch((error) => {
        showDrawerMessage(error.message, true);
        if (error.status === 409) loadDetail(openedTicketId, false);
      }).finally(() => { button.disabled = false; });
    }
    if (event.target.matches("[data-note-form]")) {
      event.preventDefault();
      if (!manage || !openedTicketId) return;
      const form = event.target;
      const button = form.querySelector("button[type='submit']");
      button.disabled = true;
      const url = endpointFromTemplate(trackingTemplate, openedTicketId).replace(/tracking\/$/, "notes/");
      requestJSON(url, new FormData(form)).then((data) => {
        form.reset();
        return loadDetail(openedTicketId, false).then(() => showDrawerMessage("Nota ARION adicionada.", false));
      }).catch((error) => {
        showDrawerMessage(error.message, true);
        if (error.status === 409) loadDetail(openedTicketId, false);
      }).finally(() => { button.disabled = false; });
    }
  });
  const routeParams = new URLSearchParams(window.location.search);
  const requestedTicket = routeParams.get("open_ticket");
  if (/^\d+$/.test(requestedTicket || "")) {
    routeParams.delete("open_ticket");
    const cleanUrl = window.location.pathname + (routeParams.toString() ? "?" + routeParams.toString() : "") + window.location.hash;
    window.history.replaceState(null, "", cleanUrl);
    loadDetail(requestedTicket, true);
  }
}());
