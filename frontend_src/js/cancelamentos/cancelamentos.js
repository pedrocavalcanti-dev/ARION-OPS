"use strict";

(() => {
  let opener = null;
  document.addEventListener("click", async (event) => {
    const open = event.target.closest("[data-open-dialog]");
    if (open) {
      const dialog = document.getElementById(open.dataset.openDialog);
      if (dialog instanceof HTMLDialogElement) {
        opener = open;
        dialog.showModal();
      }
    }
    const close = event.target.closest("[data-close-dialog]");
    if (close) close.closest("dialog")?.close();
    const copy = event.target.closest("[data-copy-target]");
    if (copy) {
      const target = document.getElementById(copy.dataset.copyTarget);
      const feedback = copy.parentElement.querySelector("[data-copy-feedback]");
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.value);
        if (feedback) feedback.textContent = "Texto copiado. Confira antes de enviar.";
      } catch {
        target.focus();
        target.select();
        if (feedback) feedback.textContent = "Texto selecionado. Use Ctrl+C ou a opção Copiar.";
      }
    }
  });
  document.querySelectorAll(".canc-dialog").forEach((dialog) => {
    dialog.addEventListener("close", () => opener?.focus());
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
    });
  });
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-canc-form]");
    if (!form) return;
    event.preventDefault();
    if (form.dataset.busy) return;
    const submitter = event.submitter;
    const endpoint = submitter?.getAttribute("formaction") || form.action;
    const feedback = form.querySelector("[data-form-feedback]");
    const body = new FormData(form);
    const buttons = [...form.querySelectorAll("button[type=submit], button:not([type])")];
    form.dataset.busy = "1";
    buttons.forEach((button) => { button.disabled = true; });
    if (feedback) feedback.textContent = "Salvando…";
    try {
      const response = await fetch(endpoint, { method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.headers.get("Content-Type")?.includes("application/json")) {
        if (feedback) feedback.textContent = response.status === 403 ? "Acesso negado ou sessão expirada. Atualize a página." : "A sessão ou a página mudou. Atualize antes de tentar novamente.";
        return;
      }
      const data = await response.json();
      if (response.ok && data.ok) {
        window.location.assign(data.url);
        return;
      }
      if (feedback) feedback.textContent = (data.errors || ["Revise os dados."]).join(" ");
    } catch {
      if (feedback) feedback.textContent = "Não foi possível confirmar a operação. Recarregue para conferir o histórico antes de repetir.";
    } finally {
      delete form.dataset.busy;
      buttons.forEach((button) => { button.disabled = false; });
    }
  });
})();
