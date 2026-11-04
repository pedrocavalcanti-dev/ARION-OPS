(() => {
  "use strict";

  const dialog = document.getElementById("quality-order-archive-dialog");
  if (!dialog) return;

  document.querySelectorAll("[data-quality-open-archive]").forEach((button) => {
    button.addEventListener("click", () => dialog.showModal());
  });

  dialog.querySelectorAll("[data-quality-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
})();
