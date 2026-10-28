/** Prefix of the per-account caches of API responses. Must match API_CACHE in public/sw.js. */
const OFFLINE_DATA_CACHE = "ledgerpic-api";

/**
 * Registers public/sw.js in production builds only: in `next dev` a service worker would keep
 * serving stale code while you edit. Needs a secure context (https, or localhost).
 */
export function registerServiceWorker(): void {
  if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
  // Read directly (not via lib/api) to keep this module free of the api -> auth import chain.
  const apiOrigin = new URL(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000", window.location.href).origin;
  navigator.serviceWorker.register(`/sw.js?api=${encodeURIComponent(apiOrigin)}`).catch((error) => {
    console.warn("Service worker not registered:", error);
  });
}

/**
 * Forget the offline copy of the previous user's receipts (on login and logout). The service worker
 * also refuses to store any response still in flight, so a poll that finishes after logout can't
 * put the old user's data back.
 */
export async function clearOfflineData(): Promise<void> {
  const worker = navigator.serviceWorker;
  worker?.controller?.postMessage("clear-api-cache");
  if (worker && !worker.controller) {
    // Not controlled (first load, hard reload): tell the active worker directly, if there is one.
    const registration = await worker.getRegistration();
    registration?.active?.postMessage("clear-api-cache");
  }
  if (typeof caches === "undefined") return;
  // One cache per account ("ledgerpic-api:<user id>"): forget all of them.
  const names = (await caches.keys()).filter((name) => name.startsWith(OFFLINE_DATA_CACHE));
  await Promise.all(names.map((name) => caches.delete(name)));
}
