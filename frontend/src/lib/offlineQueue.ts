/**
 * Uploads made while offline, kept in IndexedDB with the Idempotency-Key they were first sent with,
 * and sent again when the connection is back. Reusing the key means an upload that did reach the
 * server before the connection dropped can't create a duplicate receipt.
 */

/** `owner`: the user id that queued it; only that account ever sends it (see uploadOwner.ts). */
type PendingUpload = { key: string; name: string; type: string; blob: Blob; queuedAt: number; owner?: string | null };

const DB_NAME = "ledgerpic";
const STORE = "pending-uploads";
const QUEUE_CHANGED = "ledgerpic:pending-uploads";
// The queue lives in IndexedDB, shared by every tab (and the installed app), so changes are broadcast to all of them.
const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("ledgerpic") : null;

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "key" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const request = action(db.transaction(STORE, mode).objectStore(STORE));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

const changed = () => {
  window.dispatchEvent(new Event(QUEUE_CHANGED));
  channel?.postMessage(QUEUE_CHANGED);
};

/** Call `listener` whenever the queue changes in this tab or any other. Returns an unsubscribe function. */
export function onQueueChanged(listener: () => void): () => void {
  const onMessage = (event: MessageEvent) => event.data === QUEUE_CHANGED && listener();
  window.addEventListener(QUEUE_CHANGED, listener);
  channel?.addEventListener("message", onMessage);
  return () => {
    window.removeEventListener(QUEUE_CHANGED, listener);
    channel?.removeEventListener("message", onMessage);
  };
}

export async function queueUpload(file: File, key: string, owner: string | null): Promise<void> {
  const item: PendingUpload = { key, name: file.name, type: file.type, blob: file, queuedAt: Date.now(), owner };
  await run("readwrite", (store) => store.put(item));
  changed();
}

export async function pendingUploads(): Promise<PendingUpload[]> {
  if (typeof indexedDB === "undefined") return [];
  const all = await run<PendingUpload[]>("readonly", (store) => store.getAll());
  return all.sort((a, b) => a.queuedAt - b.queuedAt);
}

export async function removeUpload(key: string): Promise<void> {
  await run("readwrite", (store) => store.delete(key));
  changed();
}

/** Queued uploads belong to whoever was logged in, so they're dropped on login and logout. */
export async function clearPendingUploads(): Promise<void> {
  if (typeof indexedDB === "undefined") return;
  await run("readwrite", (store) => store.clear());
  changed();
}
