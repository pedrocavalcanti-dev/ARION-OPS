(() => {
  const form = document.querySelector('[data-geo-configuration][data-provider="open_meteo"]');
  if (!form) return;
  const mode = form.querySelector('[name="mode"]');
  const endpoint = form.querySelector('[name="endpoint"]');
  const secret = form.querySelector('[data-weather-secret]');
  if (!mode || !endpoint) return;
  const defaults = { public:'https://archive-api.open-meteo.com/v1/archive', commercial:'https://customer-archive-api.open-meteo.com/v1/archive' };
  function sync(changeEndpoint = false) {
    if (secret) {
      secret.hidden = mode.value === 'public';
      const input = secret.querySelector('input');
      if (input) input.disabled = secret.hidden;
    }
    if (changeEndpoint) endpoint.value = defaults[mode.value] || '';
  }
  mode.addEventListener('change', () => sync(true));
  sync();
})();
