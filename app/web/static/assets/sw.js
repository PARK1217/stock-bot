/* 스탁봇 PWA 서비스워커 — 앱 셸 캐시(오프라인 진입), 시세/API는 항상 네트워크(최신성). */
const CACHE = 'stockbot-v1';

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/', '/assets/icon-192.png']).catch(() => {})));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  // 주문·시세·로그인 등 API와 비-GET은 캐시 금지(항상 최신 데이터).
  if (e.request.method !== 'GET' || u.pathname.startsWith('/api/')) return;
  // 정적 셸: 네트워크 우선 → 실패 시 캐시(오프라인엔 마지막 화면).
  e.respondWith(
    fetch(e.request).then(r => {
      const cp = r.clone();
      caches.open(CACHE).then(c => c.put(e.request, cp).catch(() => {}));
      return r;
    }).catch(() => caches.match(e.request).then(m => m || caches.match('/')))
  );
});
