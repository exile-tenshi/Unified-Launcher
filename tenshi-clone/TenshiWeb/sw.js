const CACHE_NAME = 'tenshi-cache-v1';
const ASSETS = [
  'index.html',
  'hub.html',
  'styles.css',
  'webapp.js',
  'script.js',
  'tenshi_logo.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
  );
});

self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request).then(response => response || fetch(event.request))
  );
});
