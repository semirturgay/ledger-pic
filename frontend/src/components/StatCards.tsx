import { Clock, Layers, Wallet } from "lucide-react";

import type { CurrencyTotal, Summary } from "@/lib/api";
import { formatMoney, monthLabel } from "@/lib/format";

/** The biggest currency headlines the card; any others are listed underneath. Never summed together. */
function Amounts({ totals }: { totals: CurrencyTotal[] }) {
  if (totals.length === 0) return <div className="stat-value">0.00</div>;
  const [first, ...rest] = [...totals].sort((a, b) => b.count - a.count);
  return (
    <>
      <div className="stat-value">{formatMoney(first.total, first.currency)}</div>
      {rest.length > 0 && (
        <div className="stat-extra">+ {rest.map((t) => formatMoney(t.total, t.currency)).join(" · ")}</div>
      )}
    </>
  );
}

const count = (totals: CurrencyTotal[]) => totals.reduce((n, t) => n + t.count, 0);
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function StatCards({ summary }: { summary: Summary | undefined }) {
  if (!summary) {
    return (
      <div className="stats">
        {[0, 1, 2].map((i) => (
          <div key={i} className="card stat">
            <div className="skeleton" style={{ height: 14, width: "45%" }} />
            <div className="skeleton" style={{ height: 30, width: "70%", marginTop: 8 }} />
          </div>
        ))}
      </div>
    );
  }

  const monthCount = count(summary.month_totals);
  const allCount = count(summary.all_time_totals);
  const pending = summary.pending_review_count;

  return (
    <div className="stats">
      <div className="card stat featured">
        <div className="stat-label">
          <span className="stat-icon">
            <Wallet size={16} />
          </span>
          Spent in {monthLabel(summary.month, "short")}
        </div>
        <Amounts totals={summary.month_totals} />
        <div className="stat-extra">{monthCount ? plural(monthCount, "confirmed receipt") : "Nothing confirmed yet"}</div>
      </div>

      <div className="card stat">
        <div className="stat-label">
          <span className="stat-icon">
            <Layers size={16} />
          </span>
          All-time spend
        </div>
        <Amounts totals={summary.all_time_totals} />
        <div className="stat-extra">{plural(allCount, "receipt")} in your ledger</div>
      </div>

      <div className="card stat">
        <div className="stat-label">
          <span className={`stat-icon${pending ? " warn" : ""}`}>
            <Clock size={16} />
          </span>
          Waiting for review
        </div>
        <div className="stat-value">{pending}</div>
        <div className="stat-extra">{pending ? "Not counted until you confirm them" : "You're all caught up"}</div>
      </div>
    </div>
  );
}
