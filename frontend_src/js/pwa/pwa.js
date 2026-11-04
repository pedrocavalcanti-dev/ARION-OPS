(() => {
  const standaloneQuery = window.matchMedia?.("(display-mode: standalone)");
  const isStandalone = () => Boolean(standaloneQuery?.matches || window.navigator.standalone === true);
  const installButtons = [...document.querySelectorAll("[data-pwa-install]")];
  const installedLabels = [...document.querySelectorAll("[data-pwa-installed]")];
  const installHelp = document.querySelector("[data-pwa-install-help]");
  const updateNotice = document.querySelector("[data-pwa-update]");
  const launchScreen = document.querySelector("[data-pwa-launch]");
  const versionLabel = updateNotice?.querySelector("[data-pwa-update-version]");
  const newsLink = updateNotice?.querySelector("[data-pwa-update-news]");
  const releaseVersionLabel = document.querySelector("[data-release-current-version]");
  const releaseVersionStatus = document.querySelector("[data-release-version-status]");
  let installPrompt = null;
  let waitingWorker = null;
  let serviceWorkerRegistration = null;
  let serverVersion = "";
  let serviceWorkerCheckComplete = !("serviceWorker" in navigator) || !window.isSecureContext;
  let pendingUpdateActivation = false;
  let updateInProgress = false;
  let reloadStarted = false;
  let lastVersionCheck = 0;
  const pendingVersionKey = "arionOpsUpdateVersion";
  const dismissedVersionKey = "arionOpsUpdateDismissedVersion";
  const versionEndpoint = updateNotice?.dataset.pwaVersionEndpoint;
  const pageVersion = updateNotice?.dataset.pwaPageVersion || "";
  const releaseUrl = newsLink?.getAttribute("href") || "";
  let pendingVersion = readStoredValue(pendingVersionKey);
  let dismissalVersion = readStoredValue(dismissedVersionKey);
  let toastDismissed = Boolean(pendingVersion && pendingVersion === dismissalVersion);
  const updateMenus = [...document.querySelectorAll("[data-pwa-update-menu]")];
  const updateIndicators = updateMenus.map((menu) => menu.querySelector("[data-pwa-update-indicator]"));
  const updatePopovers = updateMenus.map((menu) => menu.querySelector("[data-pwa-update-popover]"));
  const updateActionButtons = [...document.querySelectorAll("[data-pwa-update-accept]")];
  const updateStatusLabels = [...document.querySelectorAll("[data-pwa-update-status]")];

  function updateInstallControls() {
    const installed = isStandalone();
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    installButtons.forEach((button) => {
      button.hidden = installed || (!installPrompt && !ios);
      button.dataset.installHelp = ios && !installPrompt ? "ios" : "native";
    });
    installedLabels.forEach((label) => { label.hidden = !installed; });
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    // Keep the native install flow when a page has no explicit install control.
    if (!installButtons.length) return;
    event.preventDefault();
    installPrompt = event;
    updateInstallControls();
  });

  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    updateInstallControls();
  });

  installButtons.forEach((button) => button.addEventListener("click", async () => {
    document.querySelector("[data-mobile-navigation]")?.close();
    if (button.dataset.installHelp === "ios") {
      installHelp?.showModal();
      return;
    }
    if (!installPrompt) return;
    const prompt = installPrompt;
    installPrompt = null;
    updateInstallControls();
    try {
      await prompt.prompt();
      await prompt.userChoice;
    } catch {
      updateInstallControls();
    }
  }));

  document.querySelector("[data-pwa-install-close]")?.addEventListener("click", () => installHelp?.close());
  installHelp?.addEventListener("click", (event) => { if (event.target === installHelp) installHelp.close(); });

  const launchClass = "pwa-launch-pending";
  if (document.documentElement.classList.contains(launchClass)) {
    requestAnimationFrame(() => document.documentElement.classList.remove(launchClass));
    window.setTimeout(() => {
      document.documentElement.classList.remove(launchClass);
      launchScreen?.remove();
    }, 720);
  } else {
    launchScreen?.remove();
  }

  const menu = document.querySelector("[data-mobile-navigation]");
  const openMenu = document.querySelector("[data-mobile-nav-open]");
  const closeMenu = document.querySelector("[data-mobile-nav-close]");
  if (menu && openMenu) {
    openMenu.addEventListener("click", () => {
      menu.showModal();
      openMenu.setAttribute("aria-expanded", "true");
      closeMenu?.focus();
    });
    const restoreMenuFocus = () => {
      openMenu.setAttribute("aria-expanded", "false");
      if (window.matchMedia("(max-width: 900px)").matches) openMenu.focus();
    };
    closeMenu?.addEventListener("click", () => menu.close());
    menu.addEventListener("close", restoreMenuFocus);
    menu.addEventListener("click", (event) => { if (event.target === menu) menu.close(); });
    menu.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => menu.close()));
    window.matchMedia("(min-width: 901px)").addEventListener("change", (event) => { if (event.matches && menu.open) menu.close(); });
  }

  const setUpdateVisible = (visible) => {
    if (updateNotice) updateNotice.hidden = !visible || toastDismissed;
  };

  function setReleaseTarget(version) {
    if (!newsLink || !releaseUrl) return;
    const target = new URL(releaseUrl, window.location.origin);
    if (version) target.hash = `release-v${version}`;
    newsLink.href = target.pathname + target.search + target.hash;
  }

  function readStoredValue(key) {
    try {
      return window.localStorage.getItem(key) || "";
    } catch {
      return "";
    }
  }

  function writeStoredValue(key, value) {
    try {
      if (value) window.localStorage.setItem(key, value);
      else window.localStorage.removeItem(key);
    } catch {
      // The current document still works when browser storage is unavailable.
    }
  }

  function clearStoredUpdateState() {
    writeStoredValue(pendingVersionKey, "");
    writeStoredValue(dismissedVersionKey, "");
  }

  function clearResolvedUpdateState() {
    if (!serviceWorkerCheckComplete || !serverVersion || serverVersion !== pageVersion) return;
    if (serviceWorkerRegistration?.waiting || serviceWorkerRegistration?.installing) return;
    pendingVersion = "";
    dismissalVersion = "";
    toastDismissed = false;
    clearStoredUpdateState();
  }

  function setUpdateStatus(message) {
    updateStatusLabels.forEach((label) => {
      label.textContent = message;
      label.hidden = !message;
    });
  }

  function setUpdateActionState(state) {
    updateActionButtons.forEach((button) => {
      button.disabled = state === "busy";
      button.textContent = state === "busy"
        ? "Atualizando..."
        : state === "error" ? "Tentar novamente" : "Atualizar agora";
    });
  }

  function closeUpdatePopovers() {
    updateMenus.forEach((menu, index) => {
      if (updatePopovers[index]) updatePopovers[index].hidden = true;
      updateIndicators[index]?.setAttribute("aria-expanded", "false");
    });
  }

  function syncUpdateState(available, version) {
    const targetVersion = version || pendingVersion || pageVersion || "";
    if (!available && updateInProgress) return;

    if (available) {
      const versionChanged = Boolean(targetVersion && targetVersion !== pendingVersion);
      pendingVersion = targetVersion;
      if (targetVersion) writeStoredValue(pendingVersionKey, targetVersion);
      dismissalVersion = readStoredValue(dismissedVersionKey);
      toastDismissed = Boolean(targetVersion && targetVersion === dismissalVersion);
      if (versionChanged) {
        setUpdateStatus("");
        setUpdateActionState("idle");
      }
    } else {
      toastDismissed = false;
    }

    updateMenus.forEach((menu, index) => {
      menu.hidden = !available || !toastDismissed;
      const versionText = targetVersion ? `v${targetVersion}` : "Nova vers\u00e3o";
      const indicatorVersion = menu.querySelector("[data-pwa-indicator-version]");
      const popoverVersion = menu.querySelector("[data-pwa-popover-version]");
      if (indicatorVersion) indicatorVersion.textContent = versionText;
      if (popoverVersion) popoverVersion.textContent = versionText;
      if (!available && updatePopovers[index]) updatePopovers[index].hidden = true;
      if (!available) updateIndicators[index]?.setAttribute("aria-expanded", "false");
    });

    if (available) {
      if (versionLabel) versionLabel.textContent = targetVersion ? `v${targetVersion}` : "Nova vers\u00e3o";
      setReleaseTarget(targetVersion);
      setUpdateVisible(!toastDismissed);
      return;
    }

    closeUpdatePopovers();
    setUpdateVisible(false);
    setUpdateStatus("");
    setUpdateActionState("idle");
    clearResolvedUpdateState();
  }

  function dismissUpdate() {
    toastDismissed = true;
    dismissalVersion = pendingVersion;
    if (dismissalVersion) writeStoredValue(dismissedVersionKey, dismissalVersion);
    setUpdateVisible(false);
    updateMenus.forEach((menu) => { menu.hidden = false; });
  }

  function reloadOnce() {
    if (reloadStarted) return;
    reloadStarted = true;
    window.location.reload();
  }

  function withTimeout(promise, timeoutMs) {
    let timer;
    const timeout = new Promise((resolve, reject) => {
      timer = window.setTimeout(() => reject(new Error("Update timed out")), timeoutMs);
    });
    return Promise.race([Promise.resolve(promise), timeout]).finally(() => window.clearTimeout(timer));
  }

  function waitForWaitingWorker(registration, timeoutMs = 10000) {
    return new Promise((resolve) => {
      let timer;
      let observedWorker = null;
      const finish = (worker) => {
        window.clearTimeout(timer);
        registration.removeEventListener("updatefound", inspect);
        observedWorker?.removeEventListener("statechange", inspect);
        resolve(worker);
      };
      const inspect = () => {
        if (registration.waiting) {
          finish(registration.waiting);
          return;
        }
        const installing = registration.installing;
        if (installing && installing !== observedWorker) {
          observedWorker?.removeEventListener("statechange", inspect);
          observedWorker = installing;
          observedWorker.addEventListener("statechange", inspect);
        }
        if (observedWorker?.state === "redundant") finish(null);
      };
      registration.addEventListener("updatefound", inspect);
      timer = window.setTimeout(() => finish(registration.waiting || null), timeoutMs);
      inspect();
    });
  }

  function waitForControllerChange(timeoutMs = 10000) {
    return new Promise((resolve) => {
      let timer;
      const finish = (changed) => {
        window.clearTimeout(timer);
        navigator.serviceWorker?.removeEventListener("controllerchange", onChange);
        resolve(changed);
      };
      const onChange = () => finish(true);
      navigator.serviceWorker?.addEventListener("controllerchange", onChange, { once: true });
      timer = window.setTimeout(() => finish(false), timeoutMs);
    });
  }

  async function applyUpdate() {
    if (updateInProgress) return;
    updateInProgress = true;
    pendingUpdateActivation = true;
    setUpdateActionState("busy");
    setUpdateStatus("Atualizando...");

    try {
      if (!serviceWorkerRegistration) {
        reloadOnce();
        return;
      }
      await withTimeout(serviceWorkerRegistration.update(), 15000);
      waitingWorker = serviceWorkerRegistration.waiting || await waitForWaitingWorker(serviceWorkerRegistration);
      if (!waitingWorker) {
        reloadOnce();
        return;
      }

      const controllerChange = waitForControllerChange();
      waitingWorker.postMessage({ type: "SKIP_WAITING" });
      if (!await controllerChange) throw new Error("Controller change timed out");
      reloadOnce();
    } catch {
      updateInProgress = false;
      pendingUpdateActivation = false;
      setUpdateActionState("error");
      setUpdateStatus("N\u00e3o foi poss\u00edvel atualizar agora.");
    }
  }

  updateIndicators.forEach((indicator, index) => {
    indicator?.addEventListener("click", () => {
      const popover = updatePopovers[index];
      if (!popover || updateMenus[index]?.hidden) return;
      const opening = popover.hidden;
      closeUpdatePopovers();
      popover.hidden = !opening;
      indicator.setAttribute("aria-expanded", String(opening));
    });
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest?.("[data-pwa-update-menu]")) closeUpdatePopovers();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const openIndex = updatePopovers.findIndex((popover) => popover && !popover.hidden);
    if (openIndex < 0) return;
    closeUpdatePopovers();
    updateIndicators[openIndex]?.focus();
  });
  updateActionButtons.forEach((button) => button.addEventListener("click", applyUpdate));
  document.querySelector("[data-pwa-update-dismiss]")?.addEventListener("click", dismissUpdate);
  navigator.serviceWorker?.addEventListener("controllerchange", () => {
    if (!pendingUpdateActivation) return;
    pendingVersion = "";
    dismissalVersion = "";
    toastDismissed = false;
    clearStoredUpdateState();
    reloadOnce();
  });

  async function checkServerVersion() {
    if (!versionEndpoint) return;
    lastVersionCheck = Date.now();
    try {
      const response = await fetch(versionEndpoint, {
        method: "GET",
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      if (typeof payload.version !== "string" || !payload.version) return;
      serverVersion = payload.version;
      const updateAvailable = serverVersion !== pageVersion || Boolean(waitingWorker);
      if (releaseVersionLabel) releaseVersionLabel.textContent = `v${serverVersion}`;
      if (releaseVersionStatus) {
        releaseVersionStatus.textContent = updateAvailable
          ? "Atualiza\u00e7\u00e3o dispon\u00edvel para aplicar."
          : "Seu ARION est\u00e1 atualizado.";
      }
      syncUpdateState(updateAvailable, serverVersion);
    } catch {
      // Version checks are advisory and never block normal application use.
    }
  }

  async function registerWorker() {
    if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
    try {
      const registration = await navigator.serviceWorker.register("/service-worker.js", { scope: "/", updateViaCache: "none" });
      serviceWorkerRegistration = registration;
      const showWaitingWorker = (worker) => {
        if (!worker) return;
        waitingWorker = worker;
        syncUpdateState(true, serverVersion || pendingVersion || pageVersion);
        checkServerVersion();
        if (pendingUpdateActivation) worker.postMessage({ type: "SKIP_WAITING" });
      };
      showWaitingWorker(registration.waiting);
      let observedInstalling = null;
      const watchInstalling = (installing) => {
        if (!installing || installing === observedInstalling) return;
        observedInstalling = installing;
        installing.addEventListener("statechange", () => {
          if (installing.state === "installed") showWaitingWorker(registration.waiting);
          if (installing.state === "installed" || installing.state === "redundant") clearResolvedUpdateState();
        });
      };
      registration.addEventListener("updatefound", () => watchInstalling(registration.installing));
      watchInstalling(registration.installing);
      await registration.update();
      serviceWorkerCheckComplete = true;
      watchInstalling(registration.installing);
      showWaitingWorker(registration.waiting);
      clearResolvedUpdateState();
    } catch {
      // The application remains fully usable when offline support is unavailable.
    }
  }

  const checkForUpdates = () => {
    if (Date.now() - lastVersionCheck < 15 * 60 * 1000) return;
    checkServerVersion();
    serviceWorkerRegistration?.update().catch(() => {});
  };
  window.addEventListener("focus", checkForUpdates);
  window.setInterval(checkForUpdates, 20 * 60 * 1000);

  updateInstallControls();
  standaloneQuery?.addEventListener("change", updateInstallControls);
  checkServerVersion();
  registerWorker();
})();
