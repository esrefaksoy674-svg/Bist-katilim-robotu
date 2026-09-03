const C="katilim-v4-1";
self.addEventListener("install",event=>{
  self.skipWaiting();
  event.waitUntil(caches.open(C));
});
self.addEventListener("activate",event=>{
  event.waitUntil(
    caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==C).map(k=>caches.delete(k))))
      .then(()=>self.clients.claim())
  );
});
self.addEventListener("fetch",event=>{
  if(event.request.method!=="GET") return;
  const url=new URL(event.request.url);
  if(url.pathname.endsWith("/") || url.pathname.endsWith("/index.html")){
    event.respondWith(fetch(event.request,{cache:"no-store"}).catch(()=>caches.match("./index.html")));
  } else {
    event.respondWith(caches.match(event.request).then(r=>r||fetch(event.request)));
  }
});
