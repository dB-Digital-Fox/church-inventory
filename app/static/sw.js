// Service worker for the phone app. Keeps the quick-check page and item list available offline.
// Other pages are always loaded from the server, so nothing private stays on the phone by accident.
const CACHE = "church-inventory-__VERSION__";
const STATIC = [
  "/static/style.css?v=__VERSION__", "/static/app.js?v=__VERSION__", "/static/mobile.js?v=__VERSION__",
  "/static/vendor/jsQR.js?v=__VERSION__", "/static/icon.svg", "/static/icon-192.png", "/static/icon-512.png",
];
const PAGES = ["/m/", "/m/items.json"];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.addAll(STATIC);
    for (const url of PAGES) {
      try {
        const r = await fetch(url, { credentials: "same-origin", redirect: "manual" });
        if (r.ok && r.type === "basic") await cache.put(url, r);
      } catch (e) { /* offline during install: cached on the next visit */ }
    }
    self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  if (event.data === "logout") event.waitUntil(caches.delete(CACHE));
});

async function networkFirst(request, cacheKey) {
  const cache = await caches.open(CACHE);
  try {
    const r = await fetch(request);
    if (cacheKey && r.ok && !r.redirected) await cache.put(cacheKey, r.clone());
    return r;
  } catch (e) {
    const hit = await cache.match(cacheKey || request);
    if (hit) return hit;
    if (request.mode === "navigate") {
      const page = await cache.match("/m/");
      if (page) return page;
    }
    throw e;
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
  } else if (PAGES.includes(url.pathname)) {
    event.respondWith(networkFirst(req, url.pathname));
  } else if (req.mode === "navigate") {
    event.respondWith(networkFirst(req, null));
  }
});
