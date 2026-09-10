/* ============================================================
   TikTok Extractor Pro — Service Worker
   Cache-first for static assets, network-first for API
   ============================================================ */

const CACHE_VERSION = 'tt-extractor-v1';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;

const STATIC_ASSETS = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// ───── Install: pre-cache static assets ─────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(STATIC_ASSETS).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

// ───── Activate: clean old caches ─────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((k) => !k.startsWith(CACHE_VERSION))
          .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ───── Fetch: routing ─────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') return;

  // Skip cross-origin requests (except our own)
  if (url.origin !== self.location.origin) return;

  // API calls → network-first (don't cache JSON responses)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Cache successful GET responses for 30s
          if (response.ok && request.method === 'GET') {
            const respClone = response.clone();
            caches.open(RUNTIME_CACHE).then((cache) => {
              cache.put(request, respClone);
              // Expire after 30s
              setTimeout(() => cache.delete(request), 30000);
            });
          }
          return response;
        })
        .catch(() => caches.match(request).then((r) => r || new Response('offline', { status: 503 })))
    );
    return;
  }

  // Static assets → cache-first
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response.ok && response.type === 'basic') {
          const respClone = response.clone();
          caches.open(STATIC_CACHE).then((cache) => cache.put(request, respClone));
        }
        return response;
      });
    })
  );
});

// ───── Message handler (for skipWaiting from page) ─────
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});
