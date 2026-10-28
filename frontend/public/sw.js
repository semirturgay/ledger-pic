/* LedgerPic service worker: the app opens offline, and receipts you have viewed stay readable.
 *
 * - Pages: network first; offline, the last copy of that page, else /offline.html.
 * - /_next/static/*: cache first (file names are content-hashed, so they never go stale).
 * - API reads (receipts, summary, images, /auth/me): network first, falling back to the last
 *   response. These hold one user's data, so the page deletes this cache on login and logout.
 * - Anything that isn't a GET (uploads, edits, deletes) always goes to the network; offline
 *   uploads are queued by the page instead (src/lib/offlineQueue.ts).
 *
 * Registered as /sw.js?api=<API origin>, so it knows which cross-origin requests are API calls.
 */

const SHELL_CACHE = "ledgerpic-shell-v1";
const API_CACHE = "ledgerpic-api"; // prefix; must match OFFLINE_DATA_CACHE in src/lib/serviceWorker.ts
const PRECACHE = ["/", "/login", "/offline.html", "/manifest.webmanifest", "/icons/icon-192.png"];
const API_ORIGIN = new URL(self.location.href).searchParams.get("api");
const API_READS = /^\/(receipts|auth\/me)(\/|$|\?)/;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  // Drop shells from older versions of this file; the API cache is the page's to manage.
  // Also the single shared API cache of older versions of this file (now one cache per account).
  const stale = (name) => (name.startsWith("ledgerpic-shell-") && name !== SHELL_CACHE) || name === API_CACHE;
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter(stale).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

// API responses are cached per account: the cache name includes the account (JWT sub) of the token the
// request was sent with, and an offline lookup only reads the cache of the requesting account. A stale
// tab still sending the previous account's token can therefore never fill the next account's cache.
// Login and logout also delete every account's cache, and a generation counter drops responses whose
// request started before that.
let apiGeneration = 0;
const deleteApiCaches = async () =>
  Promise.all((await caches.keys()).filter((name) => name.startsWith(API_CACHE)).map((name) => caches.delete(name)));
self.addEventListener("message", (event) => {
  if (event.data === "clear-api-cache") {
    apiGeneration++;
    event.waitUntil(deleteApiCaches());
  }
});

function accountCache(request) {
  const token = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.sub === "string" ? `${API_CACHE}:${payload.sub}` : null;
  } catch {
    return null; // no or malformed token: never cached
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  // API reads first: even if the API were ever served from this origin, account data must only go to
  // the per-account cache, never the shell cache (which is not cleared on sign-out).
  if (API_ORIGIN && url.origin === API_ORIGIN && API_READS.test(url.pathname)) {
    const cacheName = accountCache(request);
    if (cacheName) event.respondWith(networkFirst(request, cacheName));
  } else if (request.mode === "navigate") {
    event.respondWith(page(request));
  } else if (url.origin === self.location.origin) {
    event.respondWith(url.pathname.startsWith("/_next/static/") ? cacheFirst(request) : networkFirst(request, SHELL_CACHE));
  }
});

async function page(request) {
  try {
    return await fetchAndStore(request, SHELL_CACHE);
  } catch {
    const shell = await caches.open(SHELL_CACHE);
    return (await shell.match(request, { ignoreSearch: true })) || shell.match("/offline.html");
  }
}

async function networkFirst(request, cacheName) {
  try {
    return await fetchAndStore(request, cacheName);
  } catch {
    const cached = await (await caches.open(cacheName)).match(request);
    if (cached) return cached;
    return new Response(JSON.stringify({ detail: "You're offline and this hasn't been saved on this device yet." }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}

async function cacheFirst(request) {
  return (await (await caches.open(SHELL_CACHE)).match(request)) || fetchAndStore(request, SHELL_CACHE);
}

async function fetchAndStore(request, cacheName) {
  const generation = apiGeneration;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(cacheName);
    // Checked after the awaits too: a logout handled while the cache opened must still win.
    if (!cacheName.startsWith(API_CACHE) || generation === apiGeneration) await cache.put(request, response.clone());
  }
  return response;
}
