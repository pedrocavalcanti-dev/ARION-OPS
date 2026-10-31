(() => {
  "use strict";

  const dialog = document.getElementById("quality-guide-dialog");
  if (!(dialog instanceof HTMLDialogElement)) return;

  const triggers = [...document.querySelectorAll("[data-quality-guide-open]")];
  const closeButton = dialog.querySelector("[data-quality-guide-close]");
  let opener = null;

  triggers.forEach(trigger => trigger.addEventListener("click", () => {
    if (dialog.open) return;
    opener = trigger;
    dialog.showModal();
    trigger.setAttribute("aria-expanded", "true");
    closeButton?.focus();
  }));

  closeButton?.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    triggers.forEach(trigger => trigger.setAttribute("aria-expanded", "false"));
    const returnFocus = opener;
    opener = null;
    if (returnFocus?.isConnected) {
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
        if (returnFocus.isConnected) returnFocus.focus();
      }));
    }
  });
})();
