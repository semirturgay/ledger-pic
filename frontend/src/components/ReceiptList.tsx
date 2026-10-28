"use client";

import { ChevronRight, ReceiptText } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import type { ReceiptListItem } from "@/lib/api";
import { CATEGORY_META, categoryColor, formatDate, formatMoney, initials } from "@/lib/format";
import { useReceipts } from "@/lib/receipts";

type Filter = "all" | "review" | "confirmed";

function placeholderName(receipt: ReceiptListItem): string {
  if (receipt.status === "processing") return "Reading receipt…";
  if (receipt.failure_reason === "not_a_receipt") return "Not a receipt";
  return "Unknown merchant";
}

const FILTERS: { id: Filter; label: string; match: (r: ReceiptListItem) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "review", label: "To review", match: (r) => r.status !== "confirmed" },
  { id: "confirmed", label: "Confirmed", match: (r) => r.status === "confirmed" },
];

export function ReceiptList() {
  const router = useRouter();
  const [filter, setFilter] = useState<Filter>("all");
  const receipts = useReceipts(); // shared with AppShell, which polls while anything is processing

  const all = receipts.data ?? [];
  const active = FILTERS.find((f) => f.id === filter)!;
  const visible = all.filter(active.match);

  return (
    <section className="card">
      <div className="card-header">
        <h2 className="card-title">Receipts</h2>
        <div className="segmented" role="group" aria-label="Filter receipts">
          {FILTERS.map((f) => (
            <button key={f.id} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
              {f.label}
              {/* Counts cover the receipts loaded so far: "+" while older ones haven't been loaded yet. */}
              <span className="count">
                {all.filter(f.match).length}
                {receipts.hasNextPage ? "+" : ""}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="card-body" style={{ paddingTop: 8 }}>
        {receipts.isPending && (
          <div className="stack" style={{ gap: 10, paddingTop: 8 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ height: 44 }} />
            ))}
          </div>
        )}
        {receipts.isError && <div className="notice error">{receipts.error.message}</div>}

        {receipts.data && visible.length === 0 && (
          <div className="empty">
            <div className="empty-icon">
              <ReceiptText size={22} />
            </div>
            <div className="empty-title">{all.length === 0 ? "No receipts yet" : "Nothing here"}</div>
            {all.length === 0 ? "Upload your first receipt above to start your ledger." : "No receipts match this filter."}
          </div>
        )}

        {visible.length > 0 && (
          <table className="table table-rows">
            <thead>
              <tr>
                <th>Merchant</th>
                <th className="hide-sm">Date</th>
                <th className="hide-sm">Status</th>
                <th className="right">Amount</th>
                <th aria-label="Open" />
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.id} onClick={() => router.push(`/receipts/${r.id}`)}>
                  <td>
                    <div className="merchant-cell">
                      <span className="avatar" style={{ background: categoryColor(r.category) }} aria-hidden="true">
                        {initials(r.merchant)}
                      </span>
                      <div style={{ minWidth: 0 }}>
                        <a
                          className="merchant-name"
                          href={`/receipts/${r.id}`}
                          onClick={(e) => e.preventDefault()}
                          style={{ display: "block" }}
                        >
                          {r.merchant ?? placeholderName(r)}
                        </a>
                        <span className="chip">
                          {r.category ? CATEGORY_META[r.category].label : "Uncategorized"}
                        </span>
                      </div>
                    </div>
                  </td>
                  <td className="hide-sm muted">{formatDate(r.purchased_at)}</td>
                  <td className="hide-sm">
                    <StatusBadge receipt={r} />
                  </td>
                  <td className="right amount">{formatMoney(r.total, r.currency)}</td>
                  <td className="right muted" style={{ width: 28 }}>
                    <ChevronRight size={16} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {receipts.hasNextPage && (
          <div style={{ display: "flex", justifyContent: "center", paddingTop: 12 }}>
            <button
              className="btn btn-sm"
              onClick={() => receipts.fetchNextPage()}
              disabled={receipts.isFetchingNextPage}
            >
              {receipts.isFetchingNextPage ? "Loading…" : "Load more"}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
