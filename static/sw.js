const CACHE_NAME = 'cgm-receiver-v1';
const urlsToCache = [
  '/',
  '/static/icon.svg',
  '/manifest.json'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  // 如果是 API 請求或 TTS 語音檔，直接透過網路抓取
  if (event.request.url.includes('/api/') || event.request.url.includes('.mp3')) {
    event.respondWith(fetch(event.request));
    return;
  }
  
  // 其他資源（如網頁）採取 Network First 策略：優先用最新資料，斷線才用快取
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
