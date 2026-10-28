"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { CategoryBreakdown } from "@/components/CategoryBreakdown";
import { MonthPicker } from "@/components/MonthPicker";
import { ReceiptList } from "@/components/ReceiptList";
import { StatCards } from "@/components/StatCards";
import { UploadDropzone } from "@/components/UploadDropzone";
import { api } from "@/lib/api";
import { currentMonth } from "@/lib/format";
import { queryKeys } from "@/lib/queryKeys";

export default function DashboardPage() {
  // The browser's month, not the server's, so "this month" matches the user's calendar.
  const [month, setMonth] = useState(currentMonth);
  const summary = useQuery({
    queryKey: queryKeys.summaryFor(month),
    queryFn: () => api.summary(month),
    placeholderData: (previous) => previous,
  });

  return (
    <main className="container page">
        <div className="page-header">
          <div>
            <h1 className="page-title">Overview</h1>
            <p className="page-subtitle">Totals count confirmed receipts only. The AI drafts, you decide.</p>
          </div>
          <MonthPicker month={month} onChange={setMonth} />
        </div>

        <div className="stack">
          <UploadDropzone />
          {summary.isError && <div className="notice error">{summary.error.message}</div>}
          <StatCards summary={summary.data} />
          <div className="dashboard-grid">
            <ReceiptList />
            <CategoryBreakdown summary={summary.data} />
          </div>
        </div>
    </main>
  );
}
