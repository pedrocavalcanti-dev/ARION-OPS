(function () {
  "use strict";

  const page = document.querySelector("[data-identity-page]");
  if (!page) return;

  const drawer = document.querySelector("[data-identity-drawer]");
  const backdrop = document.querySelector("[data-identity-backdrop]");
  const body = document.querySelector("[data-identity-drawer-body]");
  const prevBtn = document.querySelector("[data-identity-prev]");
  const nextBtn = document.querySelector("[data-identity-next]");
  const posEl = document.querySelector("[data-identity-pos]");
  const closeBtn = document.querySelector("[data-identity-close]");

  const tab = page.dataset.tab || "links";
  const canManage = page.dataset.canManage === "1";
  const linkDetailTpl = page.dataset.linkDetailTemplate || "";
  const ticketDetailTpl = page.dataset.ticketDetailTemplate || "";

  let rowIds = [];
  try {
    rowIds = JSON.parse(page.dataset.rowIds || "[]");
  } catch (_) {
    rowIds = [];
  }

  let currentIndex = -1;
  let lastFocus = null;

  const urlForId = (tpl, id) => tpl.replace(/\/0(?=\/)/, `/${id}`);

  const csrfToken = () => {
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    const hidden = document.querySelector('[name="csrfmiddlewaretoken"]');
    return hidden ? hidden.value : "";
  };

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };

  const addField = (grid, label, value, wide) => {
    const wrap = el("div", wide ? "wide" : "");
    wrap.append(el("span", "", label), el("b", "", value || "—"));
    grid.appendChild(wrap);
  };

  const updateNav = () => {
    if (!rowIds.length || currentIndex < 0) {
      prevBtn.disabled = true;
      nextBtn.disabled = true;
      posEl.textContent = "—";
      return;
    }
    prevBtn.disabled = currentIndex <= 0;
    nextBtn.disabled = currentIndex >= rowIds.length - 1;
    posEl.textContent = `${currentIndex + 1} de ${rowIds.length}`;
  };

  const highlightSelectedRow = (id) => {
    page.querySelectorAll(".identity-row.is-selected").forEach((r) => r.classList.remove("is-selected"));
    const selector = tab === "tickets" ? `[data-ticket-row="${id}"]` : `[data-link-row="${id}"]`;
    const row = page.querySelector(selector);
    if (row) {
      row.classList.add("is-selected");
      row.scrollIntoView({ block: "nearest" });
    }
  };

  const openDrawer = () => {
    drawer.hidden = false;
    backdrop.hidden = false;
    drawer.removeAttribute("inert");
    drawer.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => drawer.classList.add("is-open"));
  };

  const closeDrawer = () => {
    drawer.classList.remove("is-open");
    drawer.setAttribute("aria-hidden", "true");
    drawer.setAttribute("inert", "");
    backdrop.hidden = true;
    page.querySelectorAll(".identity-row.is-selected").forEach((r) => r.classList.remove("is-selected"));
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
  };

  const updateKpis = (kpis) => {
    if (!kpis) return;
    const fmt = (n) => Number(n || 0).toLocaleString("pt-BR");
    Object.entries(kpis).forEach(([key, val]) => {
      document.querySelectorAll(`[data-kpi="${key}"]`).forEach((node) => {
        node.textContent = fmt(val);
      });
    });
    document.querySelectorAll('[data-kpi="pending_links_tab"], [data-kpi="pending_links_sub"]').forEach((node) => {
      node.textContent = fmt(kpis.pending_links);
    });
  };

  const updateTableRow = (identity) => {
    const row = page.querySelector(`[data-link-row="${identity.link_id}"]`);
    if (!row) return;
    const issueCell = row.querySelector('[data-cell="issue"]');
    if (issueCell) {
      issueCell.replaceChildren();
      issueCell.append(
        el("span", `identity-pill identity-pill--${String(identity.issue_type || "none").toLowerCase()}`, identity.issue_label),
        el("small", "", `${identity.review_status_label}${identity.is_manual_override ? " · Manual" : ""}`),
      );
    }
    const opCell = row.querySelector('[data-cell="operator"]');
    if (opCell) {
      opCell.replaceChildren();
      if (identity.operator) opCell.textContent = identity.operator;
      else opCell.append(el("span", "unclassified", "Não identificada"));
    }
    const projCell = row.querySelector('[data-cell="project"]');
    if (projCell) {
      projCell.replaceChildren();
      if (identity.is_internal_arion) {
        projCell.textContent = identity.internal_detail || "Infraestrutura ARION";
      } else if (identity.final_client && identity.project && identity.final_client.toLowerCase() !== identity.project.toLowerCase()) {
        projCell.append(el("strong", "", identity.final_client), el("small", "", identity.project));
      } else if (identity.project || identity.final_client) {
        projCell.textContent = identity.project || identity.final_client;
      } else {
        projCell.append(el("span", "unclassified", "Não identificado"));
      }
    }
    const suggCell = row.querySelector('[data-cell="suggestion"]');
    if (suggCell) {
      suggCell.replaceChildren();
      if (identity.has_conflict) {
        suggCell.append(el("span", "identity-conflict-note", identity.conflict_reason));
      } else {
        suggCell.append(el("small", "", `${identity.source_label} · ${identity.confidence_label}`));
      }
    }
  };

  const submitResolution = async (resolveUrl, payload, feedbackEl, advanceNext) => {
    const formData = new FormData();
    Object.entries(payload).forEach(([k, v]) => formData.append(k, v == null ? "" : String(v)));
    feedbackEl.textContent = "Salvando classificação…";
    feedbackEl.classList.remove("is-error");
    try {
      const resp = await fetch(resolveUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "XMLHttpRequest" },
        body: formData,
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        throw new Error(data.message || "Não foi possível salvar a classificação.");
      }
      updateKpis(data.kpis);
      if (data.identity) updateTableRow(data.identity);
      feedbackEl.textContent = data.message || "Salvo com sucesso.";
      if (advanceNext && currentIndex < rowIds.length - 1) {
        loadIndex(currentIndex + 1);
      } else {
        loadIndex(currentIndex, { preserveMessage: data.message || "Salvo com sucesso." });
      }
    } catch (err) {
      feedbackEl.textContent = err.message || "Erro ao salvar.";
      feedbackEl.classList.add("is-error");
    }
  };

  const renderLinkDetail = (data, preserveMessage) => {
    body.replaceChildren();
    const { link, identity, tickets, revisions } = data;

    // Header
    const header = el("header", "identity-drawer-header");
    const titleBox = el("div");
    titleBox.append(
      el("h2", "", link.designation || link.name || `CI ${link.config_item_id}`),
      el("p", "", `CI ${link.config_item_id}${link.source_number ? ` · Nº ${link.source_number}` : ""} · ${identity.issue_label} · ${identity.review_status_label}`),
    );
    const linksBox = el("div", "identity-external-links");
    if (link.base_url) {
      const baseA = el("a", "links-secondary-button", "Abrir na Base CMDB ↗");
      baseA.href = link.base_url;
      baseA.target = "_blank";
      baseA.rel = "noopener noreferrer";
      linksBox.append(baseA);
    }
    header.append(titleBox, linksBox);
    body.append(header);

    // Conflict banner
    if (identity.has_conflict && identity.conflict_reason) {
      const alertBox = el("div", "identity-alert");
      const strong = el("strong", "", "Conflito detectado: ");
      alertBox.append(strong, document.createTextNode(identity.conflict_reason));
      body.append(alertBox);
    }

    // Suggestion box
    if (identity.has_suggestion && (identity.suggested_operator || identity.suggested_project || identity.suggested_is_internal_arion)) {
      const suggBox = el("div", "identity-suggestion-box");
      const textWrap = el("div");
      textWrap.append(el("strong", "", "Sugestão automática disponível"));
      const parts = [];
      if (identity.suggested_is_internal_arion) parts.push("Infraestrutura ARION / POP");
      if (identity.suggested_operator) parts.push(`Operadora: ${identity.suggested_operator}`);
      if (identity.suggested_final_client) parts.push(`Cliente: ${identity.suggested_final_client}`);
      if (identity.suggested_project) parts.push(`Projeto: ${identity.suggested_project}`);
      textWrap.append(el("p", "", `${parts.join(" · ")} (${identity.suggested_source || "Evidência"} · Confiança ${identity.suggested_confidence || "—"})`));
      suggBox.append(textWrap);
      if (canManage) {
        const confirmBtn = el("button", "identity-action-btn identity-action-btn--primary", "Confirmar sugestão");
        confirmBtn.type = "button";
        confirmBtn.addEventListener("click", () => {
          const fb = body.querySelector("[data-identity-feedback]") || el("p");
          const noteInput = body.querySelector('[name="justification"]');
          submitResolution(data.resolve_url, {
            action: "CONFIRM_SUGGESTION",
            justification: noteInput ? noteInput.value : "Sugestão automática confirmada em revisão.",
          }, fb, false);
        });
        suggBox.append(confirmBtn);
      }
      body.append(suggBox);
    }

    // Section 1: Current identity
    const secCurrent = el("section", "drawer-section");
    secCurrent.append(el("h3", "", "Identidade operacional atual"));
    const gridCurrent = el("div", "drawer-fields");
    addField(gridCurrent, "Operadora contratante", identity.contracting_client || "Não identificada");
    addField(gridCurrent, "Cliente final", identity.is_internal_arion ? "Infraestrutura própria (sem cliente externo)" : (identity.final_client || "Não identificado"));
    addField(gridCurrent, "Projeto canônico", identity.business_project || "Não identificado");
    addField(gridCurrent, "ISP físico (CMDB)", link.provider || "Não classificado");
    addField(gridCurrent, "Origem / Confiança", `${identity.source_label} · ${identity.confidence_label}`);
    addField(gridCurrent, "Status de revisão", `${identity.review_status_label}${identity.is_manual_override ? " (protegido contra sync)" : ""}`);
    if (identity.reviewed_by) {
      addField(gridCurrent, "Última revisão manual", `${identity.reviewed_by} em ${identity.reviewed_at}${identity.review_note ? ` — ${identity.review_note}` : ""}`, true);
    }
    secCurrent.append(gridCurrent);
    body.append(secCurrent);

    // Section 2: Manual supervised correction
    if (canManage) {
      const secForm = el("section", "drawer-section");
      secForm.append(el("h3", "", "Correção manual supervisionada"));
      const feedback = el("p", "identity-feedback", preserveMessage || "");
      feedback.dataset.identityFeedback = "1";
      feedback.setAttribute("role", "status");

      const quick = el("div", "identity-quick-actions");
      const btnArion = el("button", "identity-action-btn", "Marcar como infra ARION / POP");
      btnArion.type = "button";
      const btnUnknown = el("button", "identity-action-btn", "Confirmar desconhecido");
      btnUnknown.type = "button";
      const btnForward = el("button", "identity-action-btn", "Encaminhar p/ revisão posterior");
      btnForward.type = "button";
      quick.append(btnArion, btnUnknown, btnForward);
      if (identity.is_manual_override) {
        const btnReopen = el("button", "identity-action-btn", "Reabrir p/ classificação automática");
        btnReopen.type = "button";
        btnReopen.addEventListener("click", () => {
          submitResolution(data.resolve_url, {
            action: "REOPEN_AUTO",
            justification: form.elements.justification.value || "Reaberto para reavaliação automática.",
          }, feedback, false);
        });
        quick.append(btnReopen);
      }
      secForm.append(quick);

      const form = el("form");
      const formGrid = el("div", "identity-form-grid");

      // Operator select
      const opLabel = el("label", "", "Operadora contratante");
      const opSelect = el("select");
      opSelect.name = "operator";
      const opOpts = [
        ["", "Selecione a operadora..."],
        ["Claro", "Claro"],
        ["Vivo", "Vivo"],
        ["Algar", "Algar"],
        ["ARION", "ARION (Infraestrutura própria)"],
        ["__other__", "Outras (especificar)"],
      ];
      const currentOp = identity.contracting_client || identity.suggested_operator || "";
      const knownOps = new Set(["", "Claro", "Vivo", "Algar", "ARION"]);
      opOpts.forEach(([val, lbl]) => {
        const o = el("option", "", lbl);
        o.value = val;
        if (val === currentOp || (!knownOps.has(currentOp) && val === "__other__")) o.selected = true;
        opSelect.append(o);
      });
      opLabel.append(opSelect);

      const opOtherLabel = el("label", "", "Nome da outra operadora");
      const opOtherInput = el("input");
      opOtherInput.name = "operator_other";
      opOtherInput.placeholder = "Ex: Telia, Ufinet, Motiva...";
      opOtherInput.value = !knownOps.has(currentOp) ? currentOp : "";
      opOtherLabel.hidden = opSelect.value !== "__other__";
      opOtherLabel.append(opOtherInput);
      opSelect.addEventListener("change", () => {
        opOtherLabel.hidden = opSelect.value !== "__other__";
      });

      const fcLabel = el("label", "", "Cliente final");
      const fcInput = el("input");
      fcInput.name = "final_client";
      fcInput.setAttribute("list", "identity-clients-list");
      fcInput.placeholder = "Ex: Bradesco, Banco CSF, Correios...";
      fcInput.value = identity.final_client || identity.suggested_final_client || "";
      fcLabel.append(fcInput);

      const projLabel = el("label", "", "Projeto canônico");
      const projInput = el("input");
      projInput.name = "project";
      projInput.setAttribute("list", "identity-projects-list");
      projInput.placeholder = "Ex: Bradesco, Carrefour, Fust...";
      projInput.value = identity.business_project || identity.suggested_project || "";
      projLabel.append(projInput);

      const noteLabel = el("label", "wide", "Justificativa / observação de auditoria");
      const noteInput = el("textarea");
      noteInput.name = "justification";
      noteInput.placeholder = "Descreva a evidência ou motivo da classificação manual...";
      noteInput.value = identity.review_note || "";
      noteLabel.append(noteInput);

      formGrid.append(opLabel, opOtherLabel, fcLabel, projLabel, noteLabel);
      form.append(formGrid);

      const footer = el("div", "identity-form-footer");
      footer.append(feedback);
      const btns = el("div", "identity-external-links");
      const saveBtn = el("button", "identity-action-btn identity-action-btn--primary", "Salvar classificação");
      saveBtn.type = "submit";
      const saveNextBtn = el("button", "identity-action-btn", "Salvar e próximo →");
      saveNextBtn.type = "button";
      saveNextBtn.disabled = currentIndex >= rowIds.length - 1;
      btns.append(saveBtn, saveNextBtn);
      footer.append(btns);
      form.append(footer);

      const collectManual = () => ({
        action: "MANUAL_UPDATE",
        operator: opSelect.value,
        operator_other: opOtherInput.value,
        final_client: fcInput.value,
        project: projInput.value,
        justification: noteInput.value,
      });

      form.addEventListener("submit", (event) => {
        event.preventDefault();
        submitResolution(data.resolve_url, collectManual(), feedback, false);
      });
      saveNextBtn.addEventListener("click", () => {
        submitResolution(data.resolve_url, collectManual(), feedback, true);
      });
      btnArion.addEventListener("click", () => {
        submitResolution(data.resolve_url, {
          action: "MARK_INTERNAL_ARION",
          internal_detail: "POP ARION",
          justification: noteInput.value || "Confirmado como infraestrutura própria ARION/POP.",
        }, feedback, false);
      });
      btnUnknown.addEventListener("click", () => {
        submitResolution(data.resolve_url, {
          action: "MARK_UNKNOWN",
          justification: noteInput.value || "Identificação confirmada como desconhecida após revisão.",
        }, feedback, false);
      });
      btnForward.addEventListener("click", () => {
        submitResolution(data.resolve_url, {
          action: "FORWARD_REVIEW",
          justification: noteInput.value || "Encaminhado para revisão posterior.",
        }, feedback, false);
      });

      secForm.append(form);
      body.append(secForm);
    }

    // Section 3: Original CMDB fields
    const secCmdb = el("section", "drawer-section");
    secCmdb.append(el("h3", "", "Dados originais da CMDB (imutáveis)"));
    const gridCmdb = el("div", "drawer-fields");
    addField(gridCmdb, "Nome original CMDB", link.name, true);
    addField(gridCmdb, "Designação", link.designation);
    addField(gridCmdb, "Designação operadora", link.operator_designation);
    addField(gridCmdb, "Circuito / Identificação", [link.circuit, link.identification].filter(Boolean).join(" · "));
    addField(gridCmdb, "Localidade", [link.city, link.state].filter(Boolean).join(" · "));
    addField(gridCmdb, "Implantação / Incidente", `${link.deployment_state || "—"} · ${link.incident_state || "—"}`);
    secCmdb.append(gridCmdb);
    body.append(secCmdb);

    // Section 4: OTRS evidence & ticket history
    const secTickets = el("section", "drawer-section");
    secTickets.append(el("h3", "", `Evidências e histórico de chamados OTRS (${tickets.length})`));

    const directOps = identity.evidence_summary?.direct_otrs?.operators || {};
    const directProjs = identity.evidence_summary?.direct_otrs?.projects || {};
    const histOps = identity.evidence_summary?.history_otrs?.operators || {};
    const pillWrap = el("div", "identity-evidence-pills");
    Object.entries(directOps).forEach(([op, count]) => {
      pillWrap.append(el("span", "identity-pill", `OTRS direto · ${op} (${count})`));
    });
    Object.entries(directProjs).forEach(([proj, count]) => {
      pillWrap.append(el("span", "identity-pill", `Projeto OTRS · ${proj} (${count})`));
    });
    Object.entries(histOps).forEach(([op, count]) => {
      pillWrap.append(el("span", "identity-pill", `Histórico designação · ${op} (${count})`));
    });
    if (pillWrap.childElementCount) secTickets.append(pillWrap);

    if (!tickets.length) {
      secTickets.append(el("p", "drawer-empty", "Nenhum chamado OTRS relacionado encontrado no espelho local."));
    } else {
      const list = el("div", "identity-ticket-list");
      tickets.forEach((t) => {
        const card = el("article", "identity-ticket-card");
        const head = el("header");
        if (t.otrs_url) {
          const a = el("a", "", `Ticket #${t.ticket_number} ↗`);
          a.href = t.otrs_url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          head.append(a);
        } else {
          head.append(el("strong", "", `Ticket #${t.ticket_number}`));
        }
        head.append(el("small", "", `${t.created_at} · ${t.status_label} · ${t.relation_kind}`));
        card.append(head);
        card.append(el("p", "", t.title || "Sem título"));
        const detected = [
          t.detected_operator ? `Operadora: ${t.detected_operator}${t.detected_operator_raw && t.detected_operator_raw !== t.detected_operator ? ` (${t.detected_operator_raw})` : ""}` : "",
          t.detected_project ? `Projeto: ${t.detected_project}` : "",
          t.detected_business_id ? `Designação: ${t.detected_business_id}` : "",
        ].filter(Boolean).join(" · ");
        card.append(el("small", "", detected || "Nenhuma operadora ou projeto identificado neste título."));
        list.append(card);
      });
      secTickets.append(list);
    }
    body.append(secTickets);

    // Section 5: Audit revisions
    if (revisions && revisions.length) {
      const secRev = el("section", "drawer-section");
      secRev.append(el("h3", "", "Histórico de auditoria da identidade"));
      const revList = el("div", "identity-revision-list");
      revisions.forEach((rev) => {
        const item = el("article", "identity-revision-item");
        item.append(el("strong", "", `${rev.action_label} — ${rev.actor} (${rev.created_at})`));
        const prevOp = rev.previous?.contracting_client || "—";
        const newOp = rev.new?.contracting_client || "—";
        const prevProj = rev.previous?.business_project || "—";
        const newProj = rev.new?.business_project || "—";
        item.append(el("small", "", `Operadora: ${prevOp} → ${newOp} · Projeto: ${prevProj} → ${newProj}`));
        if (rev.justification) item.append(el("small", "", `Justificativa: ${rev.justification}`));
        revList.append(item);
      });
      secRev.append(revList);
      body.append(secRev);
    }
  };

  const renderTicketDetail = (data) => {
    body.replaceChildren();
    const { ticket, parsed, candidates } = data;

    const header = el("header", "identity-drawer-header");
    const titleBox = el("div");
    titleBox.append(
      el("h2", "", `Ticket #${ticket.ticket_number}`),
      el("p", "", `${ticket.status_label} · ${ticket.created_at} · Fila: ${ticket.queue}`),
    );
    const linksBox = el("div", "identity-external-links");
    if (ticket.otrs_url) {
      const a = el("a", "links-secondary-button", "Abrir no OTRS ↗");
      a.href = ticket.otrs_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      linksBox.append(a);
    }
    header.append(titleBox, linksBox);
    body.append(header);

    const secTitle = el("section", "drawer-section");
    secTitle.append(el("h3", "", "Título oficial OTRS e evidências extraídas"));
    const grid = el("div", "drawer-fields");
    addField(grid, "Título completo OTRS", ticket.title, true);
    addField(grid, "Operadora detectada", parsed.operator || "Não identificada");
    addField(grid, "Projeto detectado", parsed.project || "Não identificado");
    addField(grid, "Designação detectada", parsed.business_id || "Não encontrada");
    addField(grid, "Referência CI no título", parsed.ci_reference || "—");
    secTitle.append(grid);
    body.append(secTitle);

    const secCand = el("section", "drawer-section");
    secCand.append(el("h3", "", `Candidatos na CMDB por designação (${candidates.length})`));
    if (!candidates.length) {
      secCand.append(el("p", "drawer-empty", "Nenhum link candidato encontrado na CMDB com a designação extraída do título."));
    } else {
      const list = el("div", "identity-ticket-list");
      candidates.forEach((c) => {
        const card = el("article", "identity-ticket-card");
        const h = el("header");
        h.append(el("strong", "", `${c.designation} (CI ${c.config_item_id})`));
        const a = el("a", "", "Abrir na Base ↗");
        a.href = c.base_url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        h.append(a);
        card.append(h);
        card.append(el("p", "", c.name));
        card.append(el("small", "", `${c.operator} · ${c.project} · ISP: ${c.provider} · ${c.state || "—"}`));
        list.append(card);
      });
      secCand.append(list);
    }
    body.append(secCand);
  };

  const loadIndex = async (index, options = {}) => {
    if (index < 0 || index >= rowIds.length) return;
    currentIndex = index;
    const id = rowIds[index];
    updateNav();
    highlightSelectedRow(id);
    openDrawer();
    body.replaceChildren(el("p", "drawer-loading", "Carregando evidências CMDB e OTRS…"));
    const url = tab === "tickets" ? urlForId(ticketDetailTpl, id) : urlForId(linkDetailTpl, id);
    try {
      const resp = await fetch(url, { credentials: "same-origin" });
      if (!resp.ok) throw new Error("Não foi possível carregar este registro.");
      const data = await resp.json();
      if (tab === "tickets") renderTicketDetail(data);
      else renderLinkDetail(data, options.preserveMessage || "");
    } catch (err) {
      body.replaceChildren(el("p", "identity-feedback is-error", err.message || "Erro ao carregar detalhes."));
    }
  };

  page.addEventListener("click", (event) => {
    const row = event.target.closest("[data-link-row], [data-ticket-row]");
    if (!row) return;
    lastFocus = row;
    const id = Number(row.dataset.linkRow || row.dataset.ticketRow);
    const idx = rowIds.indexOf(id);
    if (idx >= 0) loadIndex(idx);
  });

  page.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("[data-link-row], [data-ticket-row]");
    if (!row) return;
    event.preventDefault();
    lastFocus = row;
    const id = Number(row.dataset.linkRow || row.dataset.ticketRow);
    const idx = rowIds.indexOf(id);
    if (idx >= 0) loadIndex(idx);
  });

  prevBtn?.addEventListener("click", () => {
    if (currentIndex > 0) loadIndex(currentIndex - 1);
  });
  nextBtn?.addEventListener("click", () => {
    if (currentIndex < rowIds.length - 1) loadIndex(currentIndex + 1);
  });
  closeBtn?.addEventListener("click", closeDrawer);
  backdrop?.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && drawer.classList.contains("is-open")) closeDrawer();
  });
})();
