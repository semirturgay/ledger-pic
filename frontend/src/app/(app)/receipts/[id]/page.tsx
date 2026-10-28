"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CircleAlert, Copy, Hourglass, ImageOff, RefreshCw, Sparkles, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { DeleteReceiptButton } from "@/components/DeleteReceiptButton";
import { ReceiptForm } from "@/components/ReceiptForm";
import { ReceiptImage } from "@/components/ReceiptImage";
import { StatusBadge } from "@/components/StatusBadge";
import { api, type Receipt } from "@/lib/api";
import { failureCopy } from "@/lib/failures";
import { isReceiptId } from "@/lib/ids";
import { queryKeys } from "@/lib/queryKeys";
import { receiptUpdated } from "@/lib/receipts";

function ExtractionNotice({ receipt, onDeleted }: { receipt: Receipt; onDeleted: () => void }) {
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.retryExtraction(receipt.id),
    onSuccess: (updated) => receiptUpdated(queryClient, updated),
  });

  if (receipt.status === "processing") {
    return (
      <div className="notice info">
        <Hourglass size={18} />
        <div>
          <div className="notice-title">Reading your receipt</div>
          This runs in the background. Feel free to leave this page; we&apos;ll notify you when it&apos;s ready to review.
        </div>
      </div>
    );
  }
  if (receipt.status === "failed") {
    const copy = failureCopy(receipt.failure_reason);
    const wrongPhoto = receipt.failure_reason === "not_a_receipt" || receipt.failure_reason === "image_unreadable";
    return (
      <div className={`notice ${receipt.failure_reason === "not_a_receipt" ? "warn" : "error"}`}>
        {receipt.failure_reason === "not_a_receipt" ? <ImageOff size={18} /> : <CircleAlert size={18} />}
        <div style={{ flex: 1 }}>
          <div className="notice-title">{copy.title}</div>
          <div>{copy.nextStep}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
            {wrongPhoto && <DeleteReceiptButton id={receipt.id} onDeleted={onDeleted} />}
            {/* A damaged file isn't kept and can't be read again: only deleting or re-uploading helps. */}
            {receipt.failure_reason !== "image_unreadable" && (
              <button className="btn btn-sm" onClick={() => retry.mutate()} disabled={retry.isPending}>
                <RefreshCw size={14} className={retry.isPending ? "spin" : undefined} />
                {retry.isPending ? "Retrying…" : copy.retryLabel}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }
  if (receipt.status === "needs_review" && receipt.extraction_error) {
    return (
      <div className="notice warn">
        <TriangleAlert size={18} />
        <div>
          <div className="notice-title">Partially read</div>
          {receipt.extraction_error}
        </div>
      </div>
    );
  }
  if (receipt.status === "needs_review") {
    return (
      <div className="notice info">
        <Sparkles size={18} />
        <div>
          <div className="notice-title">AI draft ready</div>
          Compare it with the photo, fix anything that&apos;s off, then confirm.
        </div>
      </div>
    );
  }
  return null;
}

function DuplicateNotice({ receipt }: { receipt: Receipt }) {
  if (!receipt.possible_duplicate_of || receipt.status === "processing") return null;
  return (
    <div className="notice warn">
      <Copy size={18} />
      <div>
        <div className="notice-title">Possible duplicate</div>
        You already have a receipt from the same merchant, date and total.{" "}
        <Link href={`/receipts/${receipt.possible_duplicate_of}`}>Compare with it</Link>, and delete this one if
        it&apos;s the same purchase.
      </div>
    </div>
  );
}

function FormSkeleton() {
  return (
    <div className="form-section">
      <div className="stack" style={{ gap: 14 }}>
        <div className="skeleton" style={{ height: 40 }} />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
          <div className="skeleton" style={{ height: 40 }} />
          <div className="skeleton" style={{ height: 40 }} />
          <div className="skeleton" style={{ height: 40 }} />
        </div>
        <div className="skeleton" style={{ height: 120 }} />
        <div className="skeleton" style={{ height: 90 }} />
      </div>
    </div>
  );
}

export default function ReviewPage() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();

  const validId = isReceiptId(id);
  const query = useQuery({
    queryKey: queryKeys.receipt(id),
    queryFn: () => api.getReceipt(id),
    enabled: validId, // a malformed id from the URL is never sent to the API
    // Poll until the model is done; the form appears as soon as the draft lands.
    refetchInterval: (q) => (q.state.data?.status === "processing" ? 1500 : false),
  });

  const receipt = query.data;
  const processing = receipt?.status === "processing";

  return (
    <main className="container page">
        <Link href="/" className="breadcrumb">
          <ArrowLeft size={15} /> Back to overview
        </Link>

        {query.isError && <div className="notice error">{query.error.message}</div>}
        {!validId && <div className="notice error">Receipt not found.</div>}
        {validId && query.isPending && <div className="skeleton" style={{ height: 480 }} />}

        {receipt && (
          <>
            <div className="page-header">
              <div>
                <h1 className="page-title review-title">
                  {receipt.merchant ??
                    (processing
                      ? "Reading receipt…"
                      : receipt.failure_reason === "not_a_receipt"
                        ? "Not a receipt"
                        : "Review receipt")}
                  <StatusBadge receipt={receipt} />
                </h1>
                <p className="page-subtitle">
                  Uploaded{" "}
                  {new Date(receipt.created_at).toLocaleString("en-US", {
                    month: "short",
                    day: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </p>
              </div>
              <DeleteReceiptButton id={receipt.id} onDeleted={() => router.replace("/")} />
            </div>

            <div className="review-grid">
              <section className="card image-panel">
                <ReceiptImage id={receipt.id} scanning={processing} />
              </section>

              <section className="card">
                <div className="notice-slot">
                  <ExtractionNotice receipt={receipt} onDeleted={() => router.replace("/")} />
                  <DuplicateNotice receipt={receipt} />
                </div>
                {processing ? (
                  <FormSkeleton />
                ) : (
                  // Not rendered while processing, so it mounts fresh with each new AI draft.
                  <ReceiptForm key={receipt.id} receipt={receipt} />
                )}
              </section>
            </div>
          </>
        )}
    </main>
  );
}
