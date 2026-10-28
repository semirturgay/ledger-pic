import type { Category } from "@/lib/api";

// Fixed locale so the UI reads the same regardless of the browser's language.
const LOCALE = "en-US";

/** Formats a decimal string as currency without converting it to a float. */
export function formatMoney(amount: string | null, currency: string | null): string {
  if (amount === null) return "Not set";
  if (!currency) return amount;
  try {
    return new Intl.NumberFormat(LOCALE, { style: "currency", currency }).format(amount as `${number}`);
  } catch {
    return `${amount} ${currency}`; // unknown currency code
  }
}

/** "2026-10-14" -> "Oct 14, 2026" (a calendar date, so no timezone shifting). */
export function formatDate(isoDate: string | null): string {
  if (!isoDate) return "No date";
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString(LOCALE, {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export function shiftMonth(month: string, delta: number): string {
  const [year, m] = month.split("-").map(Number);
  const date = new Date(Date.UTC(year, m - 1 + delta, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function monthLabel(month: string, style: "long" | "short" = "long"): string {
  return new Date(`${month}-01T00:00:00Z`).toLocaleDateString(LOCALE, {
    month: style,
    ...(style === "long" ? { year: "numeric" } : {}),
    timeZone: "UTC",
  });
}

export const CATEGORY_META: Record<Category, { label: string; color: string }> = {
  groceries: { label: "Groceries", color: "#16a34a" },
  dining: { label: "Dining", color: "#ea580c" },
  transport: { label: "Transport", color: "#2563eb" },
  travel: { label: "Travel", color: "#7c3aed" },
  shopping: { label: "Shopping", color: "#db2777" },
  utilities: { label: "Utilities", color: "#0891b2" },
  entertainment: { label: "Entertainment", color: "#ca8a04" },
  health: { label: "Health", color: "#dc2626" },
  other: { label: "Other", color: "#64748b" },
};

const FALLBACK_COLOR = "#94a3b8";

export function categoryColor(category: Category | null): string {
  return category ? CATEGORY_META[category].color : FALLBACK_COLOR;
}

export function initials(text: string | null): string {
  const words = (text ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  return (words[0][0] + (words[1]?.[0] ?? "")).toUpperCase();
}
