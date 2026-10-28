"use client";

import { ChartPie } from "lucide-react";
import { useState } from "react";

import type { Summary } from "@/lib/api";
import { CATEGORY_META, formatMoney, monthLabel } from "@/lib/format";
import { toCents } from "@/lib/money";

type Scope = "month" | "all";

export function CategoryBreakdown({ summary }: { summary: Summary | undefined }) {
  const [scope, setScope] = useState<Scope>("month");
  const rows = (scope === "month" ? summary?.month_by_category : summary?.all_time_by_category) ?? [];
  const totals = (scope === "month" ? summary?.month_totals : summary?.all_time_totals) ?? [];
  // Bar widths are relative to each currency's own total for the chosen period; currencies never mix.
  const currencyCents = new Map(totals.map((t) => [t.currency, toCents(t.total) ?? 0]));
  const multiCurrency = totals.length > 1;

  return (
    <section className="card">
      <div className="card-header">
        <h2 className="card-title">By category</h2>
        <div className="segmented" role="group" aria-label="Period">
          <button aria-pressed={scope === "month"} onClick={() => setScope("month")}>
            {summary ? monthLabel(summary.month, "short") : "Month"}
          </button>
          <button aria-pressed={scope === "all"} onClick={() => setScope("all")}>
            All time
          </button>
        </div>
      </div>
      <div className="card-body">
        {!summary && <div className="skeleton" style={{ height: 120 }} />}
        {summary && rows.length === 0 && (
          <div className="empty" style={{ padding: "20px 8px" }}>
            <div className="empty-icon">
              <ChartPie size={22} />
            </div>
            <div className="empty-title">{scope === "month" ? `No spending in ${monthLabel(summary.month)}` : "No spending yet"}</div>
            {scope === "month" ? "Pick another month, or switch to All time." : "Confirmed receipts show up here by category."}
          </div>
        )}
        {rows.length > 0 && (
          <div className="bars">
            {rows.map((row) => {
              const meta = CATEGORY_META[row.category];
              const share = (toCents(row.total) ?? 0) / (currencyCents.get(row.currency) || 1);
              return (
                <div key={`${row.currency}-${row.category}`}>
                  <div className="bar-row-top">
                    <span className="chip" style={{ fontSize: 13.5, color: "var(--text)" }}>
                      <span className="chip-dot" style={{ background: meta.color }} />
                      {meta.label}
                      {multiCurrency && <span className="muted">· {row.currency}</span>}
                    </span>
                    <span className="amount">{formatMoney(row.total, row.currency)}</span>
                  </div>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ width: `${Math.max(share * 100, 2)}%`, background: meta.color }} />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
