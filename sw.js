/* Offline shell. The app itself is network-first so updates arrive on the next open;
   fonts and the file-reading libraries are cached after first use. AI calls are never cached. */
const CACHE = "nasem-v31";
const SHELL = ["./", "./index.html", "./runtime.js", "./config.json", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const su = new URL(e.request.url);
  // "Share to Nasem" from any app (Android): park the shared files/text, then open the app.
  if (e.request.method === "POST" && su.origin === location.origin && su.pathname.endsWith("/share")) {
    e.respondWith((async () => {
      try {
        const fd = await e.request.formData();
        const files = fd.getAll("files").filter((f) => f && typeof f === "object" && f.size);
        const cache = await caches.open("nasem-share");
        for (const k of await cache.keys()) await cache.delete(k);
        const meta = { title: fd.get("title") || "", text: fd.get("text") || "", url: fd.get("url") || "", at: Date.now(),
          files: files.slice(0, 3).map((f, i) => ({ name: f.name, type: f.type, key: "./shared/file" + i })) };
        for (let i = 0; i < meta.files.length; i++) await cache.put(meta.files[i].key, new Response(files[i], { headers: { "content-type": files[i].type || "application/octet-stream" } }));
        await cache.put("./shared/meta", new Response(JSON.stringify(meta), { headers: { "content-type": "application/json" } }));
      } catch (err) {}
      return Response.redirect("./?shared=1", 303);
    })());
    return;
  }
  if (e.request.method !== "GET") return;
  const u = new URL(e.request.url);
  if (u.origin === location.origin) {
    e.respondWith(
      fetch(e.request)
        .then((r) => { const c = r.clone(); caches.open(CACHE).then((ca) => ca.put(e.request, c)); return r; })
        .catch(() => caches.match(e.request).then((r) => r || caches.match("./index.html")))
    );
  } else if (/(^|\.)fonts\.(googleapis|gstatic)\.com$|^cdn\.jsdelivr\.net$/.test(u.hostname)) {
    e.respondWith(
      caches.match(e.request).then((r) => r || fetch(e.request).then((res) => {
        const c = res.clone(); caches.open(CACHE).then((ca) => ca.put(e.request, c)); return res;
      }))
    );
  }
});

/* Reminders: the payload is ciphertext; only this phone's key (in IndexedDB) can open it. */
function deviceKey() {
  return new Promise((res) => {
    const r = indexedDB.open("nasem", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("kv");
    r.onsuccess = () => { try { const g = r.result.transaction("kv").objectStore("kv").get("key"); g.onsuccess = () => res(g.result || null); g.onerror = () => res(null); } catch (e) { res(null); } };
    r.onerror = () => res(null);
  });
}
const fromB64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
self.addEventListener("push", (e) => {
  e.waitUntil((async () => {
    let title = "نسيم", body = "عندك تذكير", tag;
    try {
      const d = e.data.json(); tag = d.id;
      const key = await deviceKey();
      if (key) {
        const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: fromB64(d.iv) }, key, fromB64(d.c));
        const o = JSON.parse(new TextDecoder().decode(pt)); title = o.t || title; body = o.b || "";
      }
    } catch (err) {}
    await self.registration.showNotification(title, { body, tag, icon: "./icon-192.png", badge: "./icon-192.png", dir: "rtl", lang: "ar", data: { url: "./" } });
  })());
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((ws) => {
    for (const w of ws) if ("focus" in w) return w.focus();
    return self.clients.openWindow("./");
  }));
});
