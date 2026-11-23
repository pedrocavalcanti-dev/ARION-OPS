(() => {
  const form = document.querySelector("[data-brevo-form]");
  if (!form) return;

  const methodField = form.querySelector('[name="send_method"]');
  const tabs = [...form.querySelectorAll("[data-brevo-method]")];
  const panels = [...form.querySelectorAll("[data-brevo-panel]")];

  const activate = (method) => {
    if (!methodField || !["smtp", "api_rest"].includes(method)) return;
    methodField.value = method;
    tabs.forEach((tab) => {
      const selected = tab.dataset.brevoMethod === method;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.brevoPanel !== method;
    });
  };

  tabs.forEach((tab) => tab.addEventListener("click", () => activate(tab.dataset.brevoMethod)));
  const initial = methodField?.value || tabs.find((tab) => tab.getAttribute("aria-selected") === "true")?.dataset.brevoMethod;
  activate(initial || "smtp");
})();
