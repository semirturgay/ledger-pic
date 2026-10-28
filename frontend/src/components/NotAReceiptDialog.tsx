"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ImageOff, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { ReceiptImage } from "@/components/ReceiptImage";
import { api, type ReceiptListItem } from "@/lib/api";
import { receiptDeleted } from "@/lib/receipts";

/** Shown when the AI says an uploaded image isn't a receipt, so the user decides what to do with it. */
export function NotAReceiptDialog({ receipt, onClose }: { receipt: ReceiptListItem; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const router = useRouter();
  const queryClient = useQueryClient();

  useEffect(() => {
    // No close() in a cleanup: it fires the `close` event (-> onClose) on StrictMode's re-mount and
    // would dismiss the dialog right away. Unmounting removes the element anyway.
    if (dialogRef.current && !dialogRef.current.open) dialogRef.current.showModal();
  }, [receipt.id]);

  const remove = useMutation({
    mutationFn: () => api.deleteReceipt(receipt.id),
    onSuccess: () => {
      receiptDeleted(queryClient, receipt.id);
      onClose();
    },
  });

  return (
    <dialog ref={dialogRef} className="modal" aria-labelledby="not-a-receipt-title" onClose={onClose}>
      <div className="modal-icon">
        <ImageOff size={22} />
      </div>
      <h2 id="not-a-receipt-title" className="modal-title">
        That image isn&apos;t a receipt
      </h2>
      <p className="modal-body">
        LedgerPic only reads receipts and invoices, and the AI didn&apos;t find one in this photo. Nothing was added
        to your totals.
      </p>
      <div className="modal-preview">
        <ReceiptImage id={receipt.id} scanning={false} />
      </div>
      {remove.isError && <div className="notice error">{remove.error.message}</div>}
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>
          Keep it
        </button>
        <button
          className="btn"
          onClick={() => {
            onClose();
            router.push(`/receipts/${receipt.id}`);
          }}
        >
          It is a receipt
        </button>
        <button className="btn btn-danger-solid" disabled={remove.isPending} onClick={() => remove.mutate()}>
          <Trash2 size={15} /> {remove.isPending ? "Deleting…" : "Delete it"}
        </button>
      </div>
    </dialog>
  );
}
