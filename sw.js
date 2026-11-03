const CACHE='habits-v1';
const CORE=['./','./index.html','./manifest.json','./icon.png',
 'https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js',
 'https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js',
 'https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>Promise.all(CORE.map(u=>c.add(u).catch(()=>{})))));self.skipWaiting()});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim()});
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=='GET')return;
  // данные Firebase не кешируем — ими управляет сам Firestore
  if(u.hostname.includes('googleapis.com')||u.hostname.includes('firebaseio'))return;
  const isPage=e.request.mode==='navigate'||u.pathname.endsWith('index.html');
  if(isPage){ // сначала сеть (чтобы получать обновления), без сети — кеш
    e.respondWith(fetch(e.request).then(r=>{const c=r.clone();caches.open(CACHE).then(x=>x.put('./index.html',c));return r}).catch(()=>caches.match('./index.html')));
    return}
  e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request).then(res=>{if(res.ok&&(u.origin===location.origin||u.hostname==='www.gstatic.com')){const c=res.clone();caches.open(CACHE).then(x=>x.put(e.request,c))}return res})));
});
self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.matchAll({type:'window'}).then(cs=>cs.length?cs[0].focus():clients.openWindow('./')))});
