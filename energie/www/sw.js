// Service Worker von homi: macht die Seite installierbar, zeigt Push-Nachrichten
// und eine Offline-Seite, wenn der NUC nicht erreichbar ist. Live-Daten werden nie zwischengespeichert.
"use strict";
const CACHE = "homi-v1";
const OFFLINE = ["/offline.html", "/icons/homi-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(OFFLINE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Nur Seitenaufrufe abfangen: online normal laden, offline die gespeicherte Hinweisseite zeigen
self.addEventListener("fetch", e => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() => caches.match("/offline.html")));
});

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {text: e.data ? e.data.text() : ""}; }
  e.waitUntil(self.registration.showNotification(d.titel || "homi", {
    body: d.text || "",
    icon: "/icons/homi-192.png",
    badge: "/icons/badge-96.png",
    tag: d.thema || "homi",            // gleiche Themen ersetzen sich statt sich zu stapeln
    renotify: true,
    data: {url: d.url || "/"},
  }));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const ziel = new URL(e.notification.data && e.notification.data.url || "/", self.location.origin).href;
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(fenster => {
    for (const f of fenster) {
      if (f.url.startsWith(self.location.origin) && "focus" in f) { f.navigate(ziel); return f.focus(); }
    }
    return self.clients.openWindow(ziel);
  }));
});
