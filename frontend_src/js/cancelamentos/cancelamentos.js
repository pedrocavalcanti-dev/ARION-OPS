"use strict";

(() => {
  let opener = null;
  let loadingDialog = false;
  let toastTimer;
  const closeMenus = () => document.querySelectorAll(".canc-action-menu[open]").forEach((menu) => { menu.open = false; });
  const feedback = (message) => {
    let target = document.querySelector("[data-list-feedback]");
    if (!target) {
      target = document.createElement("div");
      target.className = "canc-save-feedback";
      target.dataset.listFeedback = "";
      target.setAttribute("role", "status");
      document.body.append(target);
    }
    target.textContent = message;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { target.textContent = ""; }, 7000);
  };
  const wireDialog = (dialog) => {
    if (dialog.dataset.wired) return;
    dialog.dataset.wired = "1";
    dialog.addEventListener("close", () => {
      if (opener?.isConnected) opener.focus();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
    });
  };
  const fetchDialog = async (url, trigger) => {
    if (loadingDialog) return;
    loadingDialog = true;
    try {
      const endpoint = new URL(url, location.href);
      if (endpoint.origin !== location.origin) throw new Error("origin");
      const response = await fetch(endpoint, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok || !response.headers.get("Content-Type")?.includes("text/html")) {
        feedback("Ação indisponível para este registro ou permissão. Atualize a lista para conferir.");
        return;
      }
      const fragment = document.createElement("template");
      fragment.innerHTML = await response.text();
      const dialog = fragment.content.querySelector("dialog");
      if (!dialog) throw new Error("dialog");
      const same = document.getElementById(dialog.id);
      if (same instanceof HTMLDialogElement) { same.close(); same.remove(); }
      let host = document.querySelector("[data-dialog-host]");
      if (!host) { host = document.createElement("div"); host.dataset.dialogHost = ""; document.body.append(host); }
      host.querySelectorAll("dialog").forEach((previous) => previous.close());
      host.replaceChildren(dialog);
      if (document.querySelector("[data-cancellation-workspace]")) {
        const form = dialog.querySelector("form");
        if (form) {
          const context = document.createElement("input");
          context.type = "hidden"; context.name = "return_query"; context.value = location.search.slice(1);
          form.append(context);
        }
      }
      opener = trigger?.closest(".canc-action-menu")?.querySelector("summary") || trigger || opener;
      closeMenus();
      wireDialog(dialog);
      dialog.showModal();
    } catch {
      feedback("Não foi possível abrir o formulário. Tente novamente.");
    } finally { loadingDialog = false; }
  };
  const refreshList = async (url) => {
    const workspace = document.querySelector("[data-cancellation-workspace]");
    if (!workspace) return;
    const expanded = document.querySelector("[data-more-filters]")?.getAttribute("aria-expanded") === "true";
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "X-Cancellation-Fragment": "1" } });
    if (!response.ok || !response.headers.get("Content-Type")?.includes("application/json")) throw new Error("refresh");
    const data = await response.json();
    workspace.innerHTML = data.html;
    if (expanded) {
      document.getElementById("canc-advanced").hidden = false;
      document.querySelector("[data-more-filters]").setAttribute("aria-expanded", "true");
    }
  };
  const refreshDetail = async (active) => {
    const response = await fetch(location.href, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("refresh");
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    const section = doc.querySelector(".canc-detail-sections");
    if (!section) throw new Error("refresh");
    document.querySelector(".canc-detail-sections")?.replaceWith(section);
    doc.querySelectorAll(".canc-dialog").forEach((dialog) => {
      if (dialog.id === active.id) return;
      document.getElementById(dialog.id)?.remove();
      document.querySelector(".canc-shell").append(dialog);
      wireDialog(dialog);
    });
  };
  const openSection = () => {
    const target = document.getElementById(location.hash.slice(1));
    if (target instanceof HTMLDetailsElement) target.open = true;
  };
  window.addEventListener("hashchange", openSection);
  openSection();
  document.querySelectorAll(".canc-dialog").forEach(wireDialog);
  const autoDialog = document.getElementById(document.querySelector("[data-auto-dialog]")?.dataset.autoDialog);
  if (autoDialog instanceof HTMLDialogElement) autoDialog.showModal();
  document.addEventListener("toggle", (event) => {
    const menu = event.target;
    if (!menu.matches?.(".canc-action-menu[open]")) return;
    document.querySelectorAll(".canc-action-menu[open]").forEach((other) => { if (other !== menu) other.open = false; });
    const box = menu.querySelector("summary").getBoundingClientRect();
    const panel = menu.querySelector("div");
    panel.style.left = `${Math.max(12, Math.min(box.right - panel.offsetWidth, innerWidth - panel.offsetWidth - 12))}px`;
    panel.style.top = `${box.bottom + panel.offsetHeight < innerHeight - 12 ? box.bottom + 4 : Math.max(12, box.top - panel.offsetHeight - 4)}px`;
  }, true);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenus(); });
  document.addEventListener("change", (event) => {
    if (!event.target.matches("[name=with_open_pending]")) return;
    const stage = document.querySelector("[data-pending-stage]");
    if (stage) { stage.hidden = !event.target.checked; if (stage.hidden) stage.querySelector("select").value = ""; }
  });
  window.addEventListener("resize", closeMenus);
  document.addEventListener("click", async (event) => {
    if (!event.target.closest(".canc-action-menu")) closeMenus();
    const remote = event.target.closest("[data-dialog-url]");
    if (remote) { event.preventDefault(); await fetchDialog(remote.dataset.dialogUrl, remote); return; }
    const picker = event.target.closest("[data-pending-picker]");
    if (picker) {
      const url = new URL(picker.dataset.pendingPicker, location.href);
      url.searchParams.set("child_id", picker.closest("dialog").querySelector("select").value);
      await fetchDialog(url, opener); return;
    }
    const more = event.target.closest("[data-more-filters]");
    if (more) { const panel = document.getElementById(more.getAttribute("aria-controls")); if (panel) { panel.hidden = !panel.hidden; more.setAttribute("aria-expanded", String(!panel.hidden)); } }
    const assign = event.target.closest("[data-assign-entry]");
    if (assign) {
      const dialog = document.getElementById("list-assignment");
      const form = dialog?.querySelector("[data-assignment-form]");
      if (form) {
        delete form.dataset.saved;
        form.querySelectorAll("button[type=submit]").forEach((button) => { button.disabled = false; });
        form.action = assign.dataset.actionUrl; form.elements.revision.value = assign.dataset.revision; form.elements.owner.value = assign.dataset.owner;
        form.querySelector("[data-assignment-record]").textContent = assign.dataset.record;
        form.querySelector("[data-form-feedback]").textContent = "";
        opener = assign.closest(".canc-action-menu")?.querySelector("summary") || assign;
        closeMenus(); wireDialog(dialog); dialog.showModal();
      }
    }
    const open = event.target.closest("[data-open-dialog]");
    if (open) { const dialog = document.getElementById(open.dataset.openDialog); if (dialog instanceof HTMLDialogElement) { opener = open; wireDialog(dialog); dialog.showModal(); } }
    const close = event.target.closest("[data-close-dialog]");
    if (close) close.closest("dialog")?.close();
    const copy = event.target.closest("[data-copy-target]");
    if (copy) {
      const target = document.getElementById(copy.dataset.copyTarget);
      const status = copy.parentElement.querySelector("[data-copy-feedback]");
      if (!target) return;
      try { await navigator.clipboard.writeText(target.value); if (status) status.textContent = "Texto copiado. Confira antes de enviar."; }
      catch { target.focus(); target.select(); if (status) status.textContent = "Texto selecionado. Use a opção Copiar."; }
    }
  });
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-canc-form]");
    if (!form) return;
    event.preventDefault();
    if (form.dataset.busy || form.dataset.saved) return;
    const submitter = event.submitter;
    const endpoint = submitter?.getAttribute("formaction") || form.action;
    const status = form.querySelector("[data-form-feedback]");
    const body = new FormData(form);
    const repeat = submitter?.name === "add_another";
    const list = Boolean(document.querySelector("[data-cancellation-workspace]"));
    const buttons = [...form.querySelectorAll("button[type=submit], button:not([type])")];
    form.dataset.busy = "1"; buttons.forEach((button) => { button.disabled = true; });
    if (status) status.textContent = "Salvando…";
    try {
      const response = await fetch(endpoint, { method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.headers.get("Content-Type")?.includes("application/json")) {
        if (status) status.textContent = response.status === 403 ? "Acesso negado ou sessão expirada. Atualize a página." : "A sessão ou a página mudou. Atualize antes de tentar novamente.";
        return;
      }
      const data = await response.json();
      if (!response.ok || !data.ok) { if (status) status.textContent = (data.errors || ["Revise os dados."]).join(" "); return; }
      form.dataset.saved = "1";
      if (list || repeat) {
        try { if (list) await refreshList(data.url); else await refreshDetail(form.closest("dialog")); }
        catch { form.closest("dialog")?.close(); feedback("Registro salvo. Atualize a página para conferir o recorte antes de continuar."); return; }
        if (repeat) {
          const kind = form.elements.kind.value;
          const context = form.elements.return_query?.value;
          form.reset(); form.elements.revision.value = data.revision; form.elements.kind.value = kind;
          if (context !== undefined) form.elements.return_query.value = context;
          delete form.dataset.saved;
          if (status) status.textContent = "Pendência salva. Preencha a próxima.";
          document.querySelectorAll(".canc-dialog input[name=revision]").forEach((input) => { input.value = data.revision; });
          form.elements.description.focus();
        } else { form.closest("dialog")?.close(); }
        feedback("Registro salvo com histórico.");
        return;
      }
      window.location.assign(data.url + location.hash);
    } catch { if (status) status.textContent = "Não foi possível confirmar a operação. Recarregue para conferir o histórico antes de repetir."; }
    finally { delete form.dataset.busy; buttons.forEach((button) => { button.disabled = Boolean(form.dataset.saved); }); }
  });
})();
