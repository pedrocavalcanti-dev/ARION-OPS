(() => {
  "use strict";

  const createDialog = document.getElementById("quality-create-order-dialog");
  const actionDialog = document.getElementById("quality-order-action-dialog");
  const editDialog = document.getElementById("quality-order-edit-dialog");
  const reviewDialog = document.getElementById("quality-occurrence-review-dialog");
  const ticketLinkDialog = document.getElementById("quality-ticket-link-dialog");
  const createForm = createDialog?.querySelector("[data-quality-create-form]");
  const linkSelect = createDialog?.querySelector("[data-quality-link-select]");
  const linkSearch = createDialog?.querySelector("[data-quality-link-search]");
  const linkSearchStatus = createDialog?.querySelector("[data-quality-link-search-status]");
  let linkSearchTimer = null;
  let linkSearchRequest = null;

  const resetLinkOptions = () => {
    if (!linkSelect) return;
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Selecione um link do CMDB";
    linkSelect.replaceChildren(placeholder);
    if (linkSearchStatus) linkSearchStatus.textContent = "Digite ao menos 2 caracteres para buscar.";
  };

  const setLinkOption = (value, data) => {
    if (!linkSelect || !value) return;
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = data.label || "Link CMDB";
    option.dataset.client = data.client || "";
    option.dataset.project = data.project || "";
    option.dataset.providerName = data.provider_name || "";
    option.dataset.uf = data.uf || "";
    const previous = linkSelect.querySelector(`option[value="${CSS.escape(option.value)}"]`);
    previous?.remove();
    linkSelect.append(option);
    linkSelect.value = option.value;
    updateLinkPreview();
  };

  const closeDialog = (dialog) => {
    if (dialog?.open) dialog.close();
  };

  const updateLinkPreview = () => {
    if (!linkSelect) return;
    const option = linkSelect.selectedOptions?.[0];
    const values = {
      client: option?.dataset.client,
      project: option?.dataset.project,
      provider_name: option?.dataset.providerName,
      uf: option?.dataset.uf,
    };
    Object.entries(values).forEach(([key, value]) => {
      const target = createDialog.querySelector(`[data-preview-${key === "provider_name" ? "provider-name" : key}]`);
      if (target) target.textContent = value || "—";
    });
  };

  document.querySelectorAll("[data-quality-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => closeDialog(button.closest("dialog")));
  });

  document.querySelectorAll("[data-quality-edit-order]").forEach((button) => {
    button.addEventListener("click", () => editDialog?.showModal());
  });

  document.querySelectorAll("[data-quality-open-ticket-form]").forEach((button) => {
    button.addEventListener("click", () => ticketLinkDialog?.showModal());
  });

  document.querySelectorAll("[data-quality-copy-email]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.dataset.qualityCopyEmail || "";
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
      } catch {
        const input = document.createElement("textarea");
        input.value = value;
        input.setAttribute("readonly", "");
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.append(input);
        input.select();
        document.execCommand("copy");
        input.remove();
      }
      const original = button.textContent;
      button.textContent = "Copiado";
      window.setTimeout(() => { button.textContent = original; }, 1400);
    });
  });

  if (reviewDialog) {
    const reviewForm = reviewDialog.querySelector("[data-quality-review-form]");
    const classification = reviewForm?.querySelector("[name='classification']");
    const sameCauseField = reviewDialog.querySelector("[data-review-same-cause]");
    const exclusionField = reviewDialog.querySelector("[data-review-exclusion]");
    const ticketLabel = reviewDialog.querySelector("[data-quality-review-ticket-label]");
    const updateReviewFields = () => {
      if (sameCauseField) sameCauseField.hidden = classification?.value !== "RECURRENCE";
      if (exclusionField) exclusionField.hidden = classification?.value !== "EXCLUDED";
    };
    classification?.addEventListener("change", updateReviewFields);
    document.querySelectorAll("[data-quality-review-ticket]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!reviewForm) return;
        const template = reviewDialog.dataset.qualityReviewUrlTemplate || "";
        reviewForm.action = template.replace(/\/1\/revisar\/$/, `/${encodeURIComponent(button.dataset.qualityReviewTicket)}/revisar/`);
        const next = reviewForm.querySelector("[name='next']");
        if (next) next.value = `${window.location.pathname}${window.location.search}`;
        if (classification) classification.value = button.dataset.classification || "RECURRENCE";
        const sameCause = reviewForm.querySelector("[name='same_root_cause']");
        if (sameCause) sameCause.checked = button.dataset.sameCause === "true";
        const reviewValues = {
          reason: button.dataset.reason,
          root_cause: button.dataset.rootCause,
          exclusion_reason: button.dataset.exclusionReason,
        };
        Object.entries(reviewValues).forEach(([name, value]) => {
          const field = reviewForm.querySelector(`[name='${name}']`);
          if (field) field.value = value || "";
        });
        if (ticketLabel) ticketLabel.textContent = button.dataset.ticketNumber || "Ticket OTRS";
        updateReviewFields();
        reviewDialog.showModal();
        classification?.focus();
      });
    });
  }

  document.querySelectorAll("[data-quality-create-order]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!createDialog || !createForm) return;
      createForm.reset();
      resetLinkOptions();
      if (linkSelect) {
        if (button.dataset.linkId) {
          setLinkOption(button.dataset.linkId, {
            label: button.dataset.linkLabel,
            client: button.dataset.client,
            project: button.dataset.project,
            provider_name: button.dataset.providerName,
            uf: button.dataset.uf,
          });
        } else {
          updateLinkPreview();
        }
      }
      if (linkSearch) {
        linkSearch.value = "";
      }
      createDialog.showModal();
      (button.dataset.linkId ? createForm.querySelector("select[name='priority']") : linkSearch)?.focus();
    });
  });

  linkSelect?.addEventListener("change", updateLinkPreview);
  linkSearch?.addEventListener("input", () => {
    const query = linkSearch.value.trim().toLocaleLowerCase();
    if (linkSearchTimer) window.clearTimeout(linkSearchTimer);
    linkSearchRequest?.abort();
    if (query.length < 2) {
      const currentValue = linkSelect?.value || "";
      const currentOption = currentValue ? linkSelect.selectedOptions?.[0]?.cloneNode(true) : null;
      resetLinkOptions();
      if (currentOption && linkSelect) {
        linkSelect.append(currentOption);
        linkSelect.value = currentValue;
      }
      updateLinkPreview();
      if (linkSearchStatus) linkSearchStatus.textContent = "Digite ao menos 2 caracteres para buscar.";
      return;
    }
    linkSearchTimer = window.setTimeout(async () => {
      if (!createDialog) return;
      linkSearchRequest = new AbortController();
      if (linkSearchStatus) linkSearchStatus.textContent = "Buscando links no escopo autorizado…";
      const endpoint = new URL(createDialog.dataset.qualityLinkOptionsUrl || "", window.location.origin);
      endpoint.searchParams.set("q", query);
      try {
        const response = await fetch(endpoint, {
          credentials: "same-origin",
          headers: { "X-Requested-With": "XMLHttpRequest" },
          signal: linkSearchRequest.signal,
        });
        if (!response.ok) throw new Error("Link search failed");
        const data = await response.json();
        const currentValue = linkSelect?.value || "";
        const currentOption = currentValue ? linkSelect.selectedOptions?.[0]?.cloneNode(true) : null;
        resetLinkOptions();
        const seen = new Set();
        (data.results || []).forEach((item) => {
          const option = document.createElement("option");
          option.value = String(item.id);
          option.textContent = item.label;
          option.dataset.client = item.client || "";
          option.dataset.project = item.project || "";
          option.dataset.providerName = item.provider_name || "";
          option.dataset.uf = item.uf || "";
          seen.add(option.value);
          linkSelect?.append(option);
        });
        if (currentOption && !seen.has(currentOption.value)) linkSelect?.append(currentOption);
        if (currentValue && linkSelect) linkSelect.value = currentValue;
        if (linkSearchStatus) {
          linkSearchStatus.textContent = data.results?.length
            ? `${data.results.length} link(s) encontrados neste escopo.`
            : "Nenhum link encontrado neste escopo.";
        }
        updateLinkPreview();
      } catch (error) {
        if (error.name !== "AbortError" && linkSearchStatus) linkSearchStatus.textContent = "Não foi possível buscar agora. Tente novamente.";
      }
    }, 250);
  });

  if (actionDialog) {
    const ownerForm = actionDialog.querySelector("[data-quality-owner-form]");
    const priorityForm = actionDialog.querySelector("[data-quality-priority-form]");
    const actionLabel = actionDialog.querySelector("[data-quality-action-code]");
    const updateTemplate = actionDialog.dataset.qualityUpdateUrl || "";

    document.querySelectorAll("[data-quality-order-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const code = button.dataset.qualityOrderCode || "";
        const action = button.dataset.qualityOrderAction;
        const targetForm = action === "owner" ? ownerForm : action === "priority" ? priorityForm : null;
        if (!targetForm || !updateTemplate || !code) return;
        if (ownerForm) ownerForm.hidden = ownerForm !== targetForm;
        if (priorityForm) priorityForm.hidden = priorityForm !== targetForm;
        targetForm.action = updateTemplate.replace("OQ_CODE", encodeURIComponent(code));
        if (actionLabel) actionLabel.textContent = code;
        const select = targetForm.querySelector("select");
        if (select) select.value = action === "owner" ? (button.dataset.qualityOwner || "") : (button.dataset.qualityPriority || "MEDIA");
        actionDialog.showModal();
        select?.focus();
      });
    });
  }

  document.querySelectorAll("dialog.quality-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  document.querySelectorAll(".quality-row-menu").forEach((menu) => {
    const summary = menu.querySelector(":scope > summary");
    const panel = menu.querySelector(":scope > div");
    const placePanel = () => {
      if (!menu.open || !summary || !panel) return;
      const trigger = summary.getBoundingClientRect();
      const width = panel.offsetWidth || 180;
      const height = panel.offsetHeight || 120;
      const left = Math.max(8, Math.min(trigger.right - width, window.innerWidth - width - 8));
      const top = trigger.bottom + height + 8 <= window.innerHeight
        ? trigger.bottom + 4
        : Math.max(8, trigger.top - height - 4);
      menu.dataset.fixedMenu = "true";
      panel.style.left = `${left}px`;
      panel.style.top = `${top}px`;
    };
    const closeMenu = () => {
      menu.open = false;
      delete menu.dataset.fixedMenu;
      panel?.removeAttribute("style");
    };
    summary?.addEventListener("click", () => window.requestAnimationFrame(placePanel));
    window.addEventListener("resize", closeMenu, { passive: true });
    window.addEventListener("scroll", closeMenu, { passive: true, capture: true });
    menu.addEventListener("click", (event) => {
      if (event.target.closest("a,button")) closeMenu();
    });
  });

  if (editDialog && document.querySelector('[data-quality-open-edit-dialog="true"]')) {
    editDialog.showModal();
  }
})();
