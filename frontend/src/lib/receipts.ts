"use client";

import { useInfiniteQuery, type InfiniteData, type QueryClient } from "@tanstack/react-query";

import { api, ApiError, duplicateOf, type Receipt, type ReceiptPage } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { pendingUploads, queueUpload, removeUpload } from "@/lib/offlineQueue";
import { queryKeys } from "@/lib/queryKeys";
import { uploadPinned } from "@/lib/pinnedUpload";
import { planFlush, tokenSubject } from "@/lib/uploadOwner";

const POLL_VISIBLE_MS = 2000;
// Keep polling in background tabs (so "ready" desktop notifications can fire), just less often.
const POLL_HIDDEN_MS = 10000;

/**
 * The receipt list, newest first, one page at a time (cursor pagination; fetchNextPage loads more).
 * `data` is the loaded pages flattened into one list. Polled while any loaded receipt is still queued
 * or being read by the worker.
 */
export function useReceipts() {
  return useInfiniteQuery({
    queryKey: queryKeys.receipts,
    queryFn: ({ pageParam }) => api.listReceipts(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    select: (data) => data.pages.flatMap((page) => page.items),
    refetchInterval: (query) => {
      const processing = query.state.data?.pages.some((page) => page.items.some((r) => r.status === "processing"));
      if (!processing) return false;
      return document.hidden ? POLL_HIDDEN_MS : POLL_VISIBLE_MS;
    },
    refetchIntervalInBackground: true,
  });
}

function newIdempotencyKey(): string {
  // randomUUID needs a secure context (https or localhost); fall back for anything else.
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

const unreachable = (err: unknown) => err instanceof ApiError && err.status === 0;

/**
 * Upload one file for the signed-in account, retrying once if the request may not have reached the
 * server or failed there (network error or 5xx). The retry reuses the same Idempotency-Key, so if the
 * first attempt did create the receipt, the server returns it instead of creating a duplicate. If the
 * server still can't be reached, the file is queued on this device for that account and "queued" is
 * returned. Pass the token captured when the user picked the files: see pinnedUpload.ts.
 */
export async function uploadWithRetry(file: File, token: string): Promise<Receipt | "queued"> {
  const key = newIdempotencyKey();
  return uploadPinned(token, {
    send: (pinned) => api.uploadReceipt(file, key, pinned),
    queue: (owner) => queueUpload(file, key, owner),
    unqueue: () => removeUpload(key),
    currentToken: getToken,
    online: () => navigator.onLine,
    transient: (err) => unreachable(err) || (err instanceof ApiError && err.status >= 500),
    unreachable,
    wait: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  });
}

let flushing: Promise<{ sent: number; failed: string[] }> | null = null;

/**
 * Send uploads queued while offline, oldest first; stops at the first sign we're still offline.
 * Each photo goes only to the account that queued it, with that account's token captured up front;
 * if the signed-in account changes mid-flush (logout, or a login in another tab), it stops.
 */
export function flushPendingUploads(queryClient: QueryClient) {
  flushing ??= (async () => {
    const result = { sent: 0, failed: [] as string[] };
    const token = getToken();
    const { send, discard } = planFlush(await pendingUploads(), tokenSubject(token));
    for (const item of discard) await removeUpload(item.key);
    for (const item of send) {
      if (getToken() !== token) break;
      try {
        const file = new File([item.blob], item.name, { type: item.type });
        const receipt = await api.uploadReceipt(file, item.key, token);
        addToList(queryClient, receipt);
        result.sent++;
      } catch (err) {
        if (unreachable(err)) break; // still offline: keep it and the rest for next time
        if (!duplicateOf(err)) result.failed.push(`${item.name}: ${(err as Error).message}`);
      }
      await removeUpload(item.key);
    }
    return result;
  })().finally(() => {
    flushing = null;
  });
  return flushing;
}

/** Something changed which receipts exist or their totals: refetch the list and the summary. */
export function refreshReceipts(queryClient: QueryClient) {
  queryClient.invalidateQueries({ queryKey: queryKeys.receipts });
  queryClient.invalidateQueries({ queryKey: queryKeys.summary });
}

/** A receipt was saved or sent back to the AI: show the server's version, then refresh the rest. */
export function receiptUpdated(queryClient: QueryClient, receipt: Receipt) {
  queryClient.setQueryData(queryKeys.receipt(receipt.id), receipt);
  refreshReceipts(queryClient);
}

export function receiptDeleted(queryClient: QueryClient, id: string) {
  queryClient.removeQueries({ queryKey: queryKeys.receipt(id) });
  refreshReceipts(queryClient);
}

/** Show a freshly uploaded receipt in the list right away, before the refetch lands. */
export function addToList(queryClient: QueryClient, receipt: Receipt) {
  queryClient.setQueryData<InfiniteData<ReceiptPage, string | null>>(queryKeys.receipts, (data) => {
    if (!data) return { pages: [{ items: [receipt], next_cursor: null }], pageParams: [null] };
    const pages = data.pages.map((page) => ({ ...page, items: page.items.filter((r) => r.id !== receipt.id) }));
    pages[0] = { ...pages[0], items: [receipt, ...pages[0].items] };
    return { ...data, pages };
  });
}
