(() => {
  "use strict";

  const editDialog = document.getElementById("quality-contact-dialog");
  const detailDialog = document.getElementById("quality-contact-detail");
  const form = editDialog?.querySelector("[data-quality-contact-form]");
  const title = editDialog?.querySelector("#quality-contact-dialog-title");
  const idField = form?.querySelector("[name='contact_id']");
  const fieldNames = ["name", "role", "company", "region", "client", "project", "contact_type", "escalation_level", "email", "phone", "teams", "notes"];
  let selectedDetailButton = null;

  const setField = (name, value) => {
    const field = form?.querySelector(`[name='${name}']`);
    if (field) field.value = value || "";
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
      name: "name", role: "role", company: "company", region: "region", client: "client",
      project: "project", contactType: "contactType", level: "level", email: "email",
      phone: "phone", teams: "teams", active: "active", notes: "notes",
    };
    Object.entries(fields).forEach(([field, dataKey]) => {
      const target = detailDialog?.querySelector(`[data-contact-detail-field='${field}']`);
      if (!target) return;
      const value = button.dataset[dataKey]?.trim();
      target.textContent = value || (field === "notes" ? "Nenhuma observação registrada." : "Não informado");
    });
    const email = detailDialog?.querySelector("[data-contact-detail-field='email']");
    if (email?.textContent && email.textContent !== "Não informado") {
      const link = document.createElement("a");
      link.href = `mailto:${email.textContent}`;
      link.textContent = email.textContent;
      email.replaceChildren(link);
    }
    const phone = detailDialog?.querySelector("[data-contact-detail-field='phone']");
    if (phone?.textContent && phone.textContent !== "Não informado") {
      const link = document.createElement("a");
      link.href = `tel:${phone.textContent}`;
      link.textContent = phone.textContent;
      phone.replaceChildren(link);
    }
    const editButton = detailDialog?.querySelector("[data-quality-contact-detail-edit]");
    if (editButton) editButton.hidden = !button.closest("tr, article")?.querySelector("[data-quality-contact-edit]");
  };

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
