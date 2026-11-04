(() => {
  const forms = document.querySelectorAll("[data-checklist-form]");
  if (!forms.length) return;

  const setVisualState = (row, button, completed, actionTitle) => {
    row.classList.toggle("is-complete", completed);
    const mark = button.querySelector("span");
    if (mark) mark.textContent = completed ? "\u2713" : "\u25cb";
    button.value = String(!completed);
    button.setAttribute("aria-label", `${completed ? "Reabrir" : "Concluir"}: ${actionTitle}`);
  };

  forms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
      const button = form.querySelector("[data-checklist-toggle]");
      const row = form.closest("[data-checklist-item]");
      if (!button || !row) return;
      event.preventDefault();
      if (form.dataset.pending === "true") return;

      const errorBox = form.closest(".quality-checklist")?.querySelector("[data-checklist-error]");
      const target = button.value === "true";
      const wasComplete = row.classList.contains("is-complete");
      const title = row.querySelector("strong")?.textContent?.trim() || "a\u00e7\u00e3o";
      const data = new URLSearchParams(new FormData(form));
      data.set("completed", String(target));
      form.dataset.pending = "true";
      button.disabled = true;
      if (errorBox) { errorBox.hidden = true; errorBox.textContent = ""; }
      setVisualState(row, button, target, title);

      try {
        const response = await fetch(form.action, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
          },
          body: data.toString(),
        });
        if (!response.ok) {
          let message = `N\u00e3o foi poss\u00edvel salvar a altera\u00e7\u00e3o (HTTP ${response.status}).`;
          if (response.headers.get("content-type")?.includes("application/json")) {
            try {
              const failure = await response.json();
              if (failure.message) message = failure.message;
            } catch (_) { /* A resposta de erro pode n\u00e3o conter JSON v\u00e1lido. */ }
          }
          throw new Error(message);
        }

        let result;
        try {
          result = await response.json();
        } catch (_) {
          throw new Error("O servidor retornou uma resposta inv\u00e1lida ao salvar o checklist.");
        }
        if (!result?.ok) throw new Error(result?.message || "N\u00e3o foi poss\u00edvel salvar a altera\u00e7\u00e3o.");

        setVisualState(row, button, result.completed, title);
        const oldMeta = row.querySelector("[data-checklist-meta]");
        if (oldMeta) oldMeta.remove();
        if (result.completed && result.completed_at) {
          const meta = document.createElement("small");
          meta.dataset.checklistMeta = "";
          meta.textContent = `Conclu\u00eddo em ${result.completed_at}${result.completed_by ? ` \u00b7 ${result.completed_by}` : ""}`;
          row.querySelector("div")?.append(meta);
        }
        const progress = form.closest(".quality-checklist")?.querySelector("[data-checklist-progress]");
        const label = form.closest(".quality-checklist")?.querySelector("[data-checklist-progress-label]");
        const summary = form.closest(".quality-checklist")?.querySelector("[data-checklist-summary]");
        const percent = result.total ? Math.round(result.completed_count / result.total * 100) : 0;
        if (progress) {
          progress.setAttribute("aria-valuemax", String(result.total));
          progress.setAttribute("aria-valuenow", String(result.completed_count));
          const bar = progress.querySelector("span");
          if (bar) bar.style.width = `${percent}%`;
        }
        if (label) label.textContent = `${result.completed_count} de ${result.total} etapas conclu\u00eddas`;
        if (summary) summary.textContent = `${result.completed_count} de ${result.total} etapas`;
      } catch (error) {
        setVisualState(row, button, wasComplete, title);
        if (errorBox) {
          errorBox.textContent = error.message || "N\u00e3o foi poss\u00edvel salvar a altera\u00e7\u00e3o.";
          errorBox.hidden = false;
        }
      } finally {
        form.dataset.pending = "false";
        button.disabled = false;
      }
    });
  });
})();
