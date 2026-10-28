"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import { receiptDeleted } from "@/lib/receipts";

/** Two-step delete: the first click asks, the second deletes the receipt and its image. */
export function DeleteReceiptButton({ id, onDeleted }: { id: string; onDeleted?: () => void }) {
  const queryClient = useQueryClient();
  const [armed, setArmed] = useState(false);

  const remove = useMutation({
    mutationFn: () => api.deleteReceipt(id),
    onSuccess: () => {
      receiptDeleted(queryClient, id);
      onDeleted?.();
    },
  });

  if (remove.isError) return <span className="notice error">{remove.error.message}</span>;
  if (!armed) {
    return (
      <button className="btn btn-sm btn-danger" onClick={() => setArmed(true)}>
        <Trash2 size={15} /> Delete
      </button>
    );
  }
  return (
    <span style={{ display: "inline-flex", gap: 6 }}>
      <button className="btn btn-sm" disabled={remove.isPending} onClick={() => setArmed(false)}>
        Cancel
      </button>
      <button className="btn btn-sm btn-danger-solid" disabled={remove.isPending} onClick={() => remove.mutate()}>
        <Trash2 size={15} /> {remove.isPending ? "Deleting…" : "Delete for good"}
      </button>
    </span>
  );
}
