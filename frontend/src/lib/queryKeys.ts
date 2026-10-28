// ["receipts"] is a prefix of every receipt key, so invalidating it refreshes the list and all details.
export const queryKeys = {
  me: ["me"] as const,
  receipts: ["receipts"] as const,
  receipt: (id: string) => ["receipts", id] as const,
  summary: ["summary"] as const,
  summaryFor: (month: string) => ["summary", month] as const,
};
