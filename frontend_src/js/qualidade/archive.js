(() => {
  "use strict";

  const dialog =
    document.getElementById("quality-order-delete-dialog") ||
    document.getElementById("quality-order-archive-dialog");
  if (!dialog) return;

  const form = dialog.querySelector("[data-quality-delete-form]") || dialog.querySelector("form");
  const confirmInput = dialog.querySelector("[data-quality-delete-confirm]");
  const submitButton = dialog.querySelector("[data-quality-delete-submit]");
  const reasonInput = dialog.querySelector('textarea[name="reason"]');
  const expectedCode = confirmInput ? (confirmInput.dataset.expectedCode || "").trim() : "";

  const syncSubmitState = () => {
    if (!confirmInput || !submitButton) return;
    const matches = Boolean(expectedCode) && confirmInput.value.trim() === expectedCode;
    submitButton.disabled = !matches;
  };

  const resetDialogState = () => {
    if (confirmInput) confirmInput.value = "";
    if (reasonInput) reasonInput.value = "";
    syncSubmitState();
  };

  document
    .querySelectorAll("[data-quality-open-delete], [data-quality-open-archive]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        const dropdown = button.closest("details");
        if (dropdown) dropdown.removeAttribute("open");
        resetDialogState();
        dialog.showModal();
        if (confirmInput) confirmInput.focus();
      });
    });

  dialog.querySelectorAll("[data-quality-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });

  dialog.addEventListener("close", resetDialogState);

  if (confirmInput) {
    confirmInput.addEventListener("input", syncSubmitState);
  }

  if (form) {
    form.addEventListener("submit", (event) => {
      if (!confirmInput || !expectedCode) return;
      if (confirmInput.value.trim() !== expectedCode) {
        event.preventDefault();
        syncSubmitState();
        confirmInput.focus();
      }
    });
  }

  syncSubmitState();
})();
