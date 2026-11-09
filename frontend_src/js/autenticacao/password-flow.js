(() => {
  const init = async () => {
    const linkForm = document.querySelector('[data-link-form]');
    const tokenInput = linkForm?.querySelector('[data-link-token]');
    const warning = document.querySelector('[data-link-warning]');
    if (linkForm && tokenInput) {
      const token = window.location.hash.slice(1) || tokenInput.value;
      if (window.location.hash) window.history.replaceState(null, '', window.location.pathname);
      if (/^[A-Za-z0-9_-]{32,128}$/.test(token)) {
        tokenInput.value = token;
        linkForm.hidden = true;
        try {
          const response = await fetch(window.location.pathname, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': linkForm.querySelector('[name="csrfmiddlewaretoken"]').value },
            body: new URLSearchParams({ action: 'inspect_token', token }),
          });
          if (!response.ok) throw new Error('Link unavailable');
          const recipient = await response.json();
          const title = document.querySelector('[data-flow-title]');
          if (title && linkForm.dataset.linkForm === 'activation') title.textContent = `Bem-vindo, ${recipient.display_name}`;
          linkForm.hidden = false;
        } catch {
          if (warning) warning.hidden = false;
        }
      } else {
        linkForm.hidden = true;
        if (warning) warning.hidden = false;
      }
    }

    document.querySelectorAll('[data-password-toggle]').forEach((button) => {
      const input = button.closest('.password-flow-input-wrap')?.querySelector('input');
      if (!input) return;
      button.addEventListener('click', () => {
        const reveal = input.type === 'password';
        input.type = reveal ? 'text' : 'password';
        button.textContent = reveal ? 'Ocultar' : 'Mostrar';
        button.setAttribute('aria-label', reveal ? 'Ocultar senha' : 'Mostrar senha');
      });
    });

    document.querySelectorAll('[data-password-form]').forEach((form) => {
      const first = form.querySelector('input[name="new_password1"]');
      const second = form.querySelector('input[name="new_password2"]');
      const length = form.querySelector('[data-requirement-length]');
      const match = form.querySelector('[data-requirement-match]');
      if (!first || !second) return;
      const update = () => {
        length?.classList.toggle('is-valid', first.value.length >= 8);
        match?.classList.toggle('is-valid', second.value.length > 0 && first.value === second.value);
      };
      first.addEventListener('input', update);
      second.addEventListener('input', update);
      update();
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
  window.addEventListener('hashchange', () => {
    if (window.location.hash && document.querySelector('[data-link-form]')) window.location.reload();
  });
})();
