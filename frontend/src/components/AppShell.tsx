"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { CloudOff, UploadCloud } from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from "react";

import { NavBar } from "@/components/NavBar";
import { NotAReceiptDialog } from "@/components/NotAReceiptDialog";
import { useToast } from "@/components/Toaster";
import type { ReceiptListItem, Status } from "@/lib/api";
import { failureCopy } from "@/lib/failures";
import { formatMoney } from "@/lib/format";
import { queryKeys } from "@/lib/queryKeys";
import { onQueueChanged, pendingUploads } from "@/lib/offlineQueue";
import { flushPendingUploads, refreshReceipts, useReceipts } from "@/lib/receipts";

/**
 * Layout for signed-in pages. It also watches the receipt list on every page, and when a
 * receipt the worker was processing finishes, it tells the user: a dialog if the image wasn't a
 * receipt, a toast otherwise (plus a desktop notification if the tab is in the background).
 */
export function AppShell({ children }: { children: ReactNode }) {
  const receipts = useReceipts();
  // Several uploads can be rejected at once; show them one dialog at a time.
  const [rejected, setRejected] = useState<ReceiptListItem[]>([]);
  useFinishedExtractionNotices(receipts.data, (receipt) => setRejected((queue) => [...queue, receipt]));
  const { online, pending } = useOfflineUploads();

  return (
    <>
      <NavBar />
      {(!online || pending > 0) && (
        <div className="container" style={{ paddingTop: 16 }}>
          <div className={`notice ${online ? "info" : "warn"}`}>
            {online ? <UploadCloud size={18} /> : <CloudOff size={18} />}
            <div>
              <div className="notice-title">
                {online ? `${pending} receipt${pending === 1 ? "" : "s"} waiting to upload` : "You're offline"}
              </div>
              {online
                ? "Saved on this device while the server was unreachable. They'll be sent automatically."
                : `Showing what's saved on this device.${pending ? ` ${pending} upload${pending === 1 ? "" : "s"} will be sent when you're back online.` : ""}`}
            </div>
          </div>
        </div>
      )}
      {children}
      {rejected[0] && (
        <NotAReceiptDialog
          key={rejected[0].id}
          receipt={rejected[0]}
          onClose={() => setRejected((queue) => queue.slice(1))}
        />
      )}
    </>
  );
}

/** Online state, plus uploads queued while offline: sent on load and whenever the connection returns. */
function useOfflineUploads() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [online, setOnline] = useState(true);
  const [pending, setPending] = useState(0);

  useEffect(() => {
    const recount = () => void pendingUploads().then((items) => setPending(items.length));
    // Another tab may have sent queued uploads: refresh the count and the receipt list.
    const onChange = () => {
      recount();
      refreshReceipts(queryClient);
    };
    const flush = async () => {
      const { sent, failed } = await flushPendingUploads(queryClient);
      if (sent) {
        refreshReceipts(queryClient);
        toast({ tone: "info", title: `${sent} saved receipt${sent === 1 ? "" : "s"} uploaded`, body: "The AI is reading them now." });
      }
      if (failed.length) toast({ tone: "error", title: "Some saved receipts couldn't be uploaded", body: failed.join(" · ") });
    };
    const update = () => {
      setOnline(navigator.onLine);
      if (navigator.onLine) void flush();
    };
    update();
    recount();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    const unsubscribe = onQueueChanged(onChange);
    // The browser can think it's online while the server is unreachable: keep retrying queued uploads.
    const retry = window.setInterval(async () => {
      if (navigator.onLine && (await pendingUploads()).length) await flush();
    }, 30_000);
    return () => {
      window.clearInterval(retry);
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
      unsubscribe();
    };
  }, [queryClient, toast]);

  return { online, pending };
}

function useFinishedExtractionNotices(
  receipts: ReceiptListItem[] | undefined,
  onNotAReceipt: (receipt: ReceiptListItem) => void,
) {
  const toast = useToast();
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const lastStatus = useRef<Map<string, Status> | null>(null);
  // Called from the effect, but not a reason to re-run it: it always sees the latest callback.
  const notAReceipt = useEffectEvent(onNotAReceipt);

  useEffect(() => {
    if (!receipts) return;
    const previous = lastStatus.current;
    lastStatus.current = new Map(receipts.map((r) => [r.id, r.status]));
    if (!previous) return; // first load: nothing has "just" finished

    const finished = receipts.filter((r) => previous.get(r.id) === "processing" && r.status !== "processing");
    if (finished.length === 0) return;
    // Drafts changed status: refresh their detail views and the "waiting for review" count.
    queryClient.invalidateQueries({ queryKey: queryKeys.summary });
    for (const receipt of finished) {
      queryClient.invalidateQueries({ queryKey: queryKeys.receipt(receipt.id) });
      if (pathname === `/receipts/${receipt.id}`) continue; // already looking at it
      const notice = describe(receipt);
      if (receipt.failure_reason === "not_a_receipt") notAReceipt(receipt);
      else toast({ ...notice, action: { label: "Review", href: `/receipts/${receipt.id}` } });
      showDesktopNotification(notice.title, notice.body, () => router.push(`/receipts/${receipt.id}`), receipt.id);
    }
  }, [receipts, toast, router, pathname, queryClient]);
}

function describe(receipt: ReceiptListItem) {
  if (receipt.status === "failed") {
    const copy = failureCopy(receipt.failure_reason);
    return { tone: "error" as const, title: copy.toastTitle, body: copy.toastBody };
  }
  const name = receipt.merchant ?? "Your receipt";
  const amount = receipt.total ? ` · ${formatMoney(receipt.total, receipt.currency)}` : "";
  return {
    tone: "ok" as const,
    title: `${name} is ready to review`,
    body: `The AI has drafted it${amount}. Check it and confirm.`,
  };
}

function showDesktopNotification(title: string, body: string, open: () => void, tag: string) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted" || !document.hidden) return;
  const notification = new Notification(title, { body, tag, icon: "/icon.svg" });
  notification.onclick = () => {
    window.focus();
    open();
    notification.close();
  };
}
