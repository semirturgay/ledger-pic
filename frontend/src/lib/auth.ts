"use client";

import { useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";

import { clearPendingUploads } from "@/lib/offlineQueue";
import { clearOfflineData } from "@/lib/serviceWorker";

// The JWT lives in localStorage and is sent as a Bearer header. Cookies would be third-party
// here (Vercel domain -> API domain), which browsers increasingly block.
const TOKEN_KEY = "ledgerpic.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/**
 * A different user may log in next: forget the previous one's offline copy and queued uploads, and
 * wait until that's done (bounded, so a stuck IndexedDB can't block signing in; the service worker's
 * per-account caches keep accounts apart even then).
 */
async function forgetDeviceData(): Promise<void> {
  const cleared = Promise.allSettled([clearOfflineData(), clearPendingUploads()]);
  await Promise.race([cleared, new Promise((resolve) => setTimeout(resolve, 3000))]);
}

/** Only resolves once the previous account's offline data is gone, so nothing of it outlives the switch. */
export async function setToken(token: string): Promise<void> {
  await forgetDeviceData();
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Storage blocked (private mode): the session just won't survive a reload.
  }
}

/** Signs out at once (the token is gone before anything else runs), then clears the offline data. */
export async function clearToken(): Promise<void> {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing to clear.
  }
  await forgetDeviceData();
}

function onTokenChange(callback: () => void): () => void {
  window.addEventListener("storage", callback);
  return () => window.removeEventListener("storage", callback);
}

/** Redirects to /login when there is no token. Returns true once it is safe to render. */
export function useRequireAuth(): boolean {
  const router = useRouter();
  const signedIn = useSyncExternalStore(onTokenChange, () => getToken() !== null, () => false);
  useEffect(() => {
    if (!getToken()) router.replace("/login");
    // Another tab signed out or signed in as someone else: reload, so nothing of the old account stays on screen.
    const onStorage = (event: StorageEvent) => event.key === TOKEN_KEY && window.location.reload();
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [router]);
  return signedIn;
}

// One device token per account signed in on this browser. It grants no access (only the device's own
// sign-in limit), so it is kept after sign-out: the owner can still sign in while someone guesses. The
// key is a hash of the email, so the browser doesn't keep a readable list of who signed in on it.
const DEVICE_KEY_PREFIX = "ledgerpic.device:";

async function deviceKey(email: string): Promise<string> {
  const bytes = new TextEncoder().encode(email.trim().toLowerCase());
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return DEVICE_KEY_PREFIX + Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function getDeviceToken(email: string): Promise<string | null> {
  try {
    return window.localStorage.getItem(await deviceKey(email));
  } catch {
    return null;
  }
}

export async function setDeviceToken(email: string, token: string | null): Promise<void> {
  if (!token) return;
  try {
    window.localStorage.setItem(await deviceKey(email), token);
  } catch {
    // Storage blocked: sign-in still works, just without the device's own limit.
  }
}
