// Bol Ke Order service worker: makes the pages installable and keeps the app shell available offline.
// Orders, prices and replies (/api/*) always come from the network, never from a stale cache.
const CACHE = "bko-shell-v4";
const SHELL = ["/", "/order", "/static/common.css?v=4", "/static/items.js?v=4", "/static/icon-192.png", "/static/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  // network first, so the shop always sees the newest page; the cached copy is only a fallback
  event.respondWith(fetch(event.request)
    .then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((cache) => cache.put(event.request, copy));
      return res;
    })
    .catch(() => caches.match(event.request)));
});
