const LABFOUNDRY_CACHE = "labfoundry-pwa-v7";
const LABFOUNDRY_ASSETS = [
  "/manifest.webmanifest",
  "/favicon.ico",
  "/static/offline.html",
  "/static/app.css?v=esxi-pxe-kickstart-20260628-8",
  "/static/app.js?v=esxi-pxe-kickstart-20260628-8",
  "/static/pwa.js",
  "/static/brand/labfoundry-mark.svg",
  "/static/brand/labfoundry-appliance-graphic.svg",
  "/static/vendor/tabulator/tabulator.min.css",
  "/static/vendor/tabulator/tabulator.min.js",
  "/static/vendor/codemirror/labfoundry-codemirror.min.js",
  "/static/vendor/prism/prism-core.min.js",
  "/static/vendor/prism/prism-diff.min.js"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(LABFOUNDRY_CACHE).then((cache) => cache.addAll(LABFOUNDRY_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== LABFOUNDRY_CACHE).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

function isCacheableAsset(url) {
  return (
    url.pathname === "/manifest.webmanifest" ||
    url.pathname === "/favicon.ico" ||
    url.pathname.startsWith("/static/")
  );
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/static/offline.html"))
    );
    return;
  }

  if (!isCacheableAsset(url)) {
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      const refresh = fetch(request).then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(LABFOUNDRY_CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      });
      return cached || refresh;
    })
  );
});
