const APP_CACHE = "arion-shell-__ARION_APP_VERSION__-__ARION_CACHE_REVISION__";
const STATIC_CACHE = "arion-static-__ARION_APP_VERSION__-__ARION_CACHE_REVISION__";
const OFFLINE_URL = "/static/pwa/offline.html";
const SHELL_ASSETS = [
  OFFLINE_URL,
  "/static/css/pwa-offline.css",
  "/static/branding/symbols/arion-symbol-primary.png",
  "/static/branding/ari/ari-icon.png",
  "/static/pwa/arion-192.png",
  "/static/pwa/arion-512.png",
  "/static/pwa/arion-512-maskable.png",
  "/static/pwa/arion-apple-touch-icon.png",
];
const CACHEABLE_TYPES = /^(text\/css|text\/javascript|application\/javascript|image\/(svg\+xml|png|webp|avif)|font\/(woff2?|ttf|otf)|application\/font-woff2?)\b/i;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(APP_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const cacheNames = await caches.keys();
    await Promise.all(cacheNames
      .filter((name) => name.startsWith("arion-shell-") && name !== APP_CACHE || name.startsWith("arion-static-") && name !== STATIC_CACHE)
      .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(async () => {
      const cache = await caches.open(APP_CACHE);
      return (await cache.match(OFFLINE_URL)) || new Response(
        "<!doctype html><html lang=\"pt-br\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Sem conexão · ARION OPS</title><link rel=\"stylesheet\" href=\"/static/css/pwa-offline.css\"><body><main><h1>ARION OPS</h1><h2>Sem conexão</h2><p>Não foi possível acessar os dados operacionais. Verifique sua conexão e tente novamente.</p><a href=\"/\">Tentar novamente</a></main></body></html>",
        { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } },
      );
    }));
    return;
  }

  if (!url.pathname.startsWith("/static/") || !/\.(css|m?js|svg|png|webp|avif|woff2?|ttf|otf)$/i.test(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(STATIC_CACHE);
    try {
      const publicRequest = new Request(request, { credentials: "omit", cache: "no-cache" });
      const response = await fetch(publicRequest);
      const cacheControl = response.headers.get("Cache-Control") || "";
      const contentType = response.headers.get("Content-Type") || "";
      if (response.ok && response.type === "basic" && CACHEABLE_TYPES.test(contentType) && !/private|no-store/i.test(cacheControl) && !/\*/.test(response.headers.get("Vary") || "")) {
        await cache.put(request, response.clone());
        const keys = await cache.keys();
        if (keys.length > 120) await cache.delete(keys[0]);
      }
      return response;
    } catch {
      const shell = await caches.open(APP_CACHE);
      return (await cache.match(request)) || (await shell.match(request)) || Response.error();
    }
  })());
});
