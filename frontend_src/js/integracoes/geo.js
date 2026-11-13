(() => {
  const form = document.querySelector('[data-geo-configuration][data-provider="open_meteo"]');
  if (!form) return;
  const mode = form.querySelector('[name="mode"]');
  const endpoint = form.querySelector('[name="endpoint"]');
  const secret = form.querySelector('[data-weather-secret]');
  const provider = form.querySelector('[name="preferred_provider"]');
  if (!mode || !endpoint) return;
  const defaults = { public:'https://archive-api.open-meteo.com/v1/archive', commercial:'https://customer-archive-api.open-meteo.com/v1/archive' };
  function sync(changeEndpoint = false) {
    const openMeteo = !provider || provider.value === 'open_meteo';
    for (const input of [mode, endpoint]) input.closest('.geo-configuration-field').hidden = !openMeteo;
    if (secret) {
      secret.hidden = !openMeteo || mode.value === 'public';
      const input = secret.querySelector('input');
      if (input) input.disabled = secret.hidden;
    }
    if (changeEndpoint) endpoint.value = defaults[mode.value] || '';
  }
  mode.addEventListener('change', () => sync(true));
  provider?.addEventListener('change', () => sync());
  sync();
})();
