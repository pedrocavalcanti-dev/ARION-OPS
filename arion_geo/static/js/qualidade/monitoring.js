(() => {
  const resultForm = document.querySelector("[data-monitoring-result-form]");
  const resultSelect = resultForm?.querySelector('[name="result"]');
  const syncResultFields = () => {
    if (!resultForm || !resultSelect) return;
    resultForm.querySelectorAll("[data-monitoring-result]").forEach((field) => {
      field.hidden = field.dataset.monitoringResult !== resultSelect.value;
    });
  };
  resultSelect?.addEventListener("change", syncResultFields);
  syncResultFields();
  const dialog = document.querySelector("[data-quality-monitoring-dialog]");
  if (!dialog) return;
  document.querySelector("[data-quality-monitoring-open]")?.addEventListener("click", () => dialog.showModal());
  dialog.querySelectorAll("[data-quality-monitoring-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  const endPreview = dialog.querySelector("[data-monitoring-end-preview]");
  const startValue = dialog.dataset.monitoringStartDate;
  const updatePreview = () => {
    if (!startValue || !endPreview) return;
    const [year, month, day] = startValue.split("-").map(Number);
    const end = new Date(Date.UTC(year, month - 1, day + 30));
    endPreview.textContent = new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC" }).format(end);
  };
  updatePreview();
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
})();
