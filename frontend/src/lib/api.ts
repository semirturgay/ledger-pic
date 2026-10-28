import { clearToken, getToken } from "@/lib/auth";
import { receiptPath } from "@/lib/ids";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export const CATEGORIES = [
  "groceries",
  "dining",
  "transport",
  "travel",
  "shopping",
  "utilities",
  "entertainment",
  "health",
  "other",
] as const;
export type Category = (typeof CATEGORIES)[number];
export type Status = "processing" | "needs_review" | "confirmed" | "failed";
export type FailureReason = "not_a_receipt" | "nothing_readable" | "image_unreadable" | "ai_unavailable";

// Money and quantities arrive as decimal strings and go back as strings: never JS floats.
type LineItem = { id: number; description: string; quantity: string; unit_price: string };

export type ReceiptListItem = {
  id: string;
  status: Status;
  merchant: string | null;
  purchased_at: string | null;
  currency: string | null;
  category: Category | null;
  total: string | null;
  created_at: string;
  /** While processing: tries started so far (0 = queued) and the last transient error, if retrying. */
  extraction_attempts: number;
  extraction_error: string | null;
  /** Set when status is "failed": why, so the UI can suggest the right next step. */
  failure_reason: FailureReason | null;
};

export type ReceiptPage = { items: ReceiptListItem[]; next_cursor: string | null };
const RECEIPTS_PAGE_SIZE = 50;

export type Receipt = ReceiptListItem & {
  tax: string | null;
  line_items: LineItem[];
  updated_at: string;
  line_items_total: string | null;
  totals_mismatch: boolean;
  /** An earlier receipt with the same merchant, date, total and currency: probably the same purchase. */
  possible_duplicate_of: string | null;
};

export type ReceiptUpdate = {
  merchant: string;
  purchased_at: string;
  currency: string;
  category: Category;
  tax: string | null;
  total: string;
  line_items: { description: string; quantity: string; unit_price: string }[];
};

export type CurrencyTotal = { currency: string; total: string; count: number };
type CategoryTotal = { currency: string; category: Category; total: string };
export type Summary = {
  month: string;
  month_totals: CurrencyTotal[];
  month_by_category: CategoryTotal[];
  all_time_by_category: CategoryTotal[];
  all_time_totals: CurrencyTotal[];
  pending_review_count: number;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** Extra fields from the error body, e.g. receipt_id for a duplicate upload (409). */
    public body: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

const JSON_HEADERS = { "Content-Type": "application/json" };

async function request<T>(
  path: string,
  init: RequestInit = {},
  { auth = true, token = getToken() }: { auth?: boolean; token?: string | null } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  // ngrok's free tier answers browsers with an HTML warning page unless this header is present.
  headers.set("ngrok-skip-browser-warning", "1");
  if (auth && token) headers.set("Authorization", `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection or the API URL.");
  }

  // Only when the rejected token is still the stored one: a pinned upload from an account that has since
  // signed out must not sign out whoever is signed in now.
  if (response.status === 401 && auth && token === getToken()) {
    await clearToken();
    // A full page load on purpose (and outside React, so there is no router here): nothing held in
    // memory for the signed-out account survives.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, errorMessage(response.status, body), body);
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  return (contentType.includes("application/json") ? response.json() : response.blob()) as Promise<T>;
}

function errorMessage(status: number, body: { detail?: unknown }): string {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    // FastAPI validation errors: [{loc: ["body", "total"], msg: "..."}]
    return body.detail
      .map((e: { loc: (string | number)[]; msg: string }) => `${e.loc.slice(1).join(".")}: ${e.msg}`)
      .join("; ");
  }
  return `Request failed (${status})`;
}

/** The receipt a 409 duplicate upload points at, if that's what this error is. */
export function duplicateOf(err: unknown): string | null {
  return err instanceof ApiError && err.status === 409 && typeof err.body.receipt_id === "string"
    ? err.body.receipt_id
    : null;
}

type SignedIn = { access_token: string; device_token: string | null };

export const api = {
  /** captchaToken: from the Turnstile widget; null when the human check is not configured. */
  register: (email: string, password: string, captchaToken: string | null) =>
    request<SignedIn>(
      "/auth/register",
      { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ email, password, captcha_token: captchaToken }) },
      { auth: false },
    ),
  /** deviceToken: from an earlier sign-in to this account on this browser, so others' guesses can't lock it out. */
  login: (email: string, password: string, captchaToken: string | null, deviceToken: string | null) =>
    request<SignedIn>(
      "/auth/login",
      {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ email, password, captcha_token: captchaToken, device_token: deviceToken }),
      },
      { auth: false },
    ),
  me: () => request<{ id: string; email: string }>("/auth/me"),
  /** One page of receipts, newest first; pass the previous page's next_cursor to continue. */
  listReceipts: (cursor: string | null = null) =>
    request<ReceiptPage>(`/receipts?limit=${RECEIPTS_PAGE_SIZE}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
  getReceipt: (id: string) => request<Receipt>(receiptPath(id)),
  getReceiptImage: (id: string) => request<Blob>(receiptPath(id, "/image")),
  /**
   * idempotencyKey: reuse it when retrying the same file, so the server never creates a duplicate.
   * token: pin the account (offline flush), so a sign-in in another tab can't redirect the upload.
   */
  uploadReceipt: (file: File, idempotencyKey: string, token?: string | null) => {
    const form = new FormData();
    form.append("file", file);
    return request<Receipt>(
      "/receipts",
      { method: "POST", body: form, headers: { "Idempotency-Key": idempotencyKey } },
      token === undefined ? {} : { token },
    );
  },
  confirmReceipt: (id: string, body: ReceiptUpdate) =>
    request<Receipt>(receiptPath(id), { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(body) }),
  retryExtraction: (id: string) => request<Receipt>(receiptPath(id, "/extract"), { method: "POST" }),
  deleteReceipt: (id: string) => request<void>(receiptPath(id), { method: "DELETE" }),
  summary: (month: string) => {
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) throw new ApiError(400, "Invalid month");
    return request<Summary>(`/receipts/summary?month=${month}`);
  },
};
