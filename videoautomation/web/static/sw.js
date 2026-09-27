// Minimal service worker so phones can install vauto as an app.
// It does not cache anything: vauto always talks to your own server.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
