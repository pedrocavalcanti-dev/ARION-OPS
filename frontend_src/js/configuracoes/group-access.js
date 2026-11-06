document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-group-access-form]");
  if (!form) return;

  const search = form.querySelector("[data-permission-search]");
  const modules = [...form.querySelectorAll("[data-permission-module]")];
  const choices = [...form.querySelectorAll("[data-permission-choice]")];
  const checkboxes = choices.map((choice) => choice.querySelector('input[type="checkbox"]')).filter(Boolean);
  const selectedCount = form.querySelector("[data-permission-selected-count]");
  const emptyState = form.querySelector("[data-permission-empty]");
  const unsavedNotice = form.querySelector("[data-permission-unsaved]");
  const saveStatus = form.querySelector("[data-permission-save-status]");
  const saveButton = form.querySelector("[data-permission-save]");
  const trackedFields = [...form.elements].filter((field) => (
    field.name && field.type !== "hidden" && field.type !== "submit" && field.type !== "button"
  ));
  const initialValues = trackedFields.map((field) => field.type === "checkbox" ? field.checked : field.value);
  const hasServerErrors = form.dataset.hasErrors === "true";
  let isSubmitting = false;

  const hasChanges = () => hasServerErrors || trackedFields.some((field, index) => (
    (field.type === "checkbox" ? field.checked : field.value) !== initialValues[index]
  ));

  const update = () => {
    let visibleChoices = 0;
    choices.forEach((choice) => {
      const query = (search?.value || "").trim().toLocaleLowerCase("pt-BR");
      const matches = !query || (choice.dataset.permissionSearchText || "")
        .toLocaleLowerCase("pt-BR").includes(query);
      choice.hidden = !matches;
      choice.classList.toggle("is-selected", choice.querySelector("input")?.checked === true);
      if (matches) visibleChoices += 1;
    });

    modules.forEach((module) => {
      const moduleChoices = [...module.querySelectorAll("[data-permission-choice]")];
      const visible = moduleChoices.filter((choice) => !choice.hidden);
      const checked = moduleChoices.filter((choice) => choice.querySelector("input")?.checked).length;
      const count = module.querySelector("[data-module-selected-count]");
      if (count) count.textContent = String(checked);
      module.hidden = Boolean(search?.value.trim()) && visible.length === 0;
      if (search?.value.trim() && visible.length) module.open = true;
    });

    const selected = checkboxes.filter((checkbox) => checkbox.checked).length;
    if (selectedCount) selectedCount.textContent = String(selected);
    if (emptyState) emptyState.hidden = visibleChoices > 0;

    const changed = hasChanges();
    if (unsavedNotice) unsavedNotice.hidden = !changed;
  };

  search?.addEventListener("input", update);
  checkboxes.forEach((checkbox) => checkbox.addEventListener("change", update));
  trackedFields.filter((field) => field.type !== "checkbox").forEach((field) => field.addEventListener("input", update));

  form.querySelector("[data-permission-expand]")?.addEventListener("click", () => {
    modules.filter((module) => !module.hidden).forEach((module) => { module.open = true; });
  });
  form.querySelector("[data-permission-collapse]")?.addEventListener("click", () => {
    modules.filter((module) => !module.hidden).forEach((module) => { module.open = false; });
  });

  window.addEventListener("beforeunload", (event) => {
    if (!hasChanges() || isSubmitting) return;
    event.preventDefault();
    event.returnValue = "";
  });

  form.addEventListener("submit", () => {
    isSubmitting = true;
    if (saveStatus) saveStatus.hidden = false;
    if (saveButton) {
      saveButton.disabled = true;
      saveButton.textContent = "Salvando…";
    }
  });

  update();
});
