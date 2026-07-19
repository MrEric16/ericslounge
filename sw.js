// Service worker for Mr Eric's Lounge — caches the app shell so it still opens with no signal.
// Live data (weather, on-this-day, news links, videos) still needs a connection — the app
// already shows a "couldn't load" message gracefully in those spots when offline.

const CACHE_NAME = 'erics-lounge-v1';
const SHELL_FILES = [
  './',
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  const isShellFile = url.origin === self.location.origin;

  if (isShellFile) {
    // App shell: cache-first, so it loads instantly and works offline.
    event.respondWith(
      caches.match(req).then((cached) => {
        const fetchPromise = fetch(req)
          .then((res) => {
            if (res && res.status === 200) {
              caches.open(CACHE_NAME).then((cache) => cache.put(req, res.clone()));
            }
            return res;
          })
          .catch(() => cached);
        return cached || fetchPromise;
      })
    );
  }
  // Everything else (weather API, Wikipedia, YouTube, news sites) — just let it
  // hit the network normally. No point caching data that changes every visit.
});
