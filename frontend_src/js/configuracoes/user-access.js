(() => {
  const init = () => {
    const button = document.querySelector('[data-copy-link]');
    const input = document.querySelector('[data-copy-source]');
    const feedback = document.querySelector('[data-copy-feedback]');
    if (!button || !input) return;
    button.addEventListener('click', async () => {
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(input.value);
        } else {
          input.focus();
          input.select();
          if (!document.execCommand('copy')) throw new Error('copy unavailable');
          input.setSelectionRange(0, 0);
        }
        button.textContent = 'Copiado';
        if (feedback) feedback.textContent = 'Link copiado para a área de transferência.';
      } catch {
        input.focus();
        input.select();
        if (feedback) feedback.textContent = 'Selecione e copie o link manualmente.';
      }
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
