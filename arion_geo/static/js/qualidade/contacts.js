(() => {
  "use strict";

  const editDialog = document.getElementById("quality-contact-dialog");
  const detailDialog = document.getElementById("quality-contact-detail");
  const form = editDialog?.querySelector("[data-quality-contact-form]");
  const title = editDialog?.querySelector("#quality-contact-dialog-title");
  const idField = form?.querySelector("[name='contact_id']");
  const fieldNames = [
    "name", "role", "contact_category", "company", "region", "client", "project",
    "contact_type", "escalation_level", "email", "phone", "whatsapp", "teams",
    "service_hours", "notes",
  ];
  let selectedDetailButton = null;

  const setField = (name, value) => {
    const field = form?.querySelector(`[name='${name}']`);
    if (field) field.value = value || "";
  };

  const copyText = async (value, button) => {
    if (!value) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        const helper = document.createElement("textarea");
        helper.value = value;
        helper.setAttribute("readonly", "");
        helper.style.position = "fixed";
        helper.style.opacity = "0";
        document.body.appendChild(helper);
        helper.select();
        document.execCommand("copy");
        helper.remove();
      }
      if (button) {
        const original = button.dataset.originalLabel || button.textContent;
        button.dataset.originalLabel = original;
        button.textContent = "Copiado";
        button.classList.add("is-copied");
        window.setTimeout(() => {
          button.textContent = original;
          button.classList.remove("is-copied");
        }, 1600);
      }
    } catch {
      // Clipboard blocked by browser policy; keep silent and non-blocking.
    }
  };

  const makeCopyButton = (value, label) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "quality-copy-btn";
    btn.textContent = "Copiar";
    btn.setAttribute("aria-label", label);
    btn.addEventListener("click", () => copyText(value, btn));
    return btn;
  };

  const openNew = () => {
    if (!editDialog || !form) return;
    form.reset();
    if (idField) idField.value = "";
    const active = form.querySelector("[name='active']");
    if (active) active.checked = true;
    if (title) title.textContent = "Novo contato";
    editDialog.showModal();
    form.querySelector("[name='name']")?.focus();
  };

  const openEdit = button => {
    if (!editDialog || !form || !button) return;
    form.reset();
    if (idField) idField.value = button.dataset.contactId || "";
    fieldNames.forEach(name => {
      const datasetName = name.replace(/_([a-z])/g, (_match, letter) => letter.toUpperCase());
      const dataKey = name === "escalation_level" ? "level" : datasetName;
      setField(name, button.dataset[dataKey]);
    });
    const active = form.querySelector("[name='active']");
    if (active) active.checked = button.dataset.active === "true";
    if (title) title.textContent = "Editar contato";
    if (detailDialog?.open) detailDialog.close();
    editDialog.showModal();
    form.querySelector("[name='name']")?.focus();
  };

  const fillDetail = button => {
    selectedDetailButton = button;
    const fields = {
      name: "name", role: "role", category: "category", company: "company", region: "region",
      client: "client", project: "project", contactType: "contactType", level: "level",
      serviceHours: "serviceHours", email: "email", phone: "phone", whatsapp: "whatsapp",
      teams: "teams", active: "active", notes: "notes",
    };
    Object.entries(fields).forEach(([field, dataKey]) => {
      const target = detailDialog?.querySelector(`[data-contact-detail-field='${field}']`);
      if (!target) return;
      const value = button.dataset[dataKey]?.trim();
      target.textContent = value || (field === "notes" ? "Nenhuma observação registrada." : "Não informado");
    });
    const email = detailDialog?.querySelector("[data-contact-detail-field='email']");
    if (email?.textContent && email.textContent !== "Não informado") {
      const raw = email.textContent;
      const wrap = document.createElement("span");
      wrap.className = "quality-contact-direct__item";
      const link = document.createElement("a");
      link.href = `mailto:${raw}`;
      link.textContent = raw;
      wrap.append(link, makeCopyButton(raw, "Copiar e-mail"));
      email.replaceChildren(wrap);
    }
    const phone = detailDialog?.querySelector("[data-contact-detail-field='phone']");
    if (phone?.textContent && phone.textContent !== "Não informado") {
      const raw = phone.textContent;
      const wrap = document.createElement("span");
      wrap.className = "quality-contact-direct__item";
      const link = document.createElement("a");
      link.href = `tel:${raw}`;
      link.textContent = raw;
      wrap.append(link, makeCopyButton(raw, "Copiar telefone"));
      phone.replaceChildren(wrap);
    }
    const whatsapp = detailDialog?.querySelector("[data-contact-detail-field='whatsapp']");
    if (whatsapp?.textContent && whatsapp.textContent !== "Não informado") {
      const raw = whatsapp.textContent;
      const wrap = document.createElement("span");
      wrap.className = "quality-contact-direct__item";
      const span = document.createElement("span");
      span.textContent = raw;
      wrap.append(span, makeCopyButton(raw, "Copiar WhatsApp"));
      whatsapp.replaceChildren(wrap);
    }
    const editButton = detailDialog?.querySelector("[data-quality-contact-detail-edit]");
    if (editButton) editButton.hidden = !button.closest("tr, article")?.querySelector("[data-quality-contact-edit]");
  };

  document.querySelectorAll("[data-quality-copy]").forEach(button => {
    button.addEventListener("click", () => copyText(button.dataset.qualityCopy || "", button));
  });
  document.querySelectorAll("[data-quality-contact-new]").forEach(button => button.addEventListener("click", openNew));
  document.querySelectorAll("[data-quality-contact-edit]").forEach(button => button.addEventListener("click", () => openEdit(button)));
  document.querySelectorAll("[data-quality-contact-detail]").forEach(button => {
    button.addEventListener("click", () => {
      if (!detailDialog) return;
      fillDetail(button);
      detailDialog.showModal();
    });
  });
  detailDialog?.querySelector("[data-quality-contact-detail-edit]")?.addEventListener("click", () => {
    const source = selectedDetailButton?.closest("tr, article")?.querySelector("[data-quality-contact-edit]");
    openEdit(source);
  });

  document.querySelectorAll("[data-quality-dialog-close]").forEach(button => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });
  [editDialog, detailDialog].forEach(dialog => dialog?.addEventListener("click", event => {
    if (event.target === dialog) dialog.close();
  }));

  document.querySelectorAll(".quality-contact-actions").forEach(menu => {
    const summary = menu.querySelector(":scope > summary");
    const panel = menu.querySelector(":scope > div");
    if (!summary || !panel) return;
    const position = () => {
      if (!menu.open) return;
      const rect = summary.getBoundingClientRect();
      panel.dataset.fixedMenu = "true";
      panel.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - panel.offsetHeight - 8)}px`;
      panel.style.left = `${Math.max(8, Math.min(rect.right - panel.offsetWidth, window.innerWidth - panel.offsetWidth - 8))}px`;
    };
    menu.addEventListener("toggle", position);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", () => { if (menu.open) menu.open = false; }, true);
    document.addEventListener("click", event => {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    menu.addEventListener("click", event => {
      if (event.target.closest("button[type='button']")) menu.open = false;
    });
  });
})();
