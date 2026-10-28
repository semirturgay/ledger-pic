"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, Plus, TriangleAlert, X } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api, CATEGORIES, type Category, type Receipt, type ReceiptUpdate } from "@/lib/api";
import { CATEGORY_META } from "@/lib/format";
import { formatCents, lineAmountCents, toCents, trimUnitPrice } from "@/lib/money";
import { receiptUpdated } from "@/lib/receipts";

type DraftItem = { key: number; description: string; quantity: string; unit_price: string };
type Draft = {
  merchant: string;
  purchased_at: string;
  currency: string;
  category: Category | "";
  tax: string;
  total: string;
  items: DraftItem[];
};

let nextKey = 0;
const newItem = (item?: Partial<DraftItem>): DraftItem => ({
  key: nextKey++,
  description: item?.description ?? "",
  quantity: item?.quantity ?? "1",
  unit_price: item?.unit_price ?? "",
});

function draftFrom(receipt: Receipt): Draft {
  return {
    merchant: receipt.merchant ?? "",
    purchased_at: receipt.purchased_at ?? "",
    currency: receipt.currency ?? "",
    category: receipt.category ?? "",
    tax: receipt.tax ?? "",
    total: receipt.total ?? "",
    // Server quantities come as "2.000"; show "2".
    items: receipt.line_items.map((li) =>
      newItem({
        ...li,
        quantity: li.quantity.includes(".") ? li.quantity.replace(/\.?0+$/, "") : li.quantity,
        unit_price: trimUnitPrice(li.unit_price),
      }),
    ),
  };
}

function toUpdate(draft: Draft): ReceiptUpdate {
  return {
    merchant: draft.merchant.trim(),
    purchased_at: draft.purchased_at,
    currency: draft.currency.trim().toUpperCase(),
    category: draft.category as Category,
    tax: draft.tax.trim() === "" ? null : draft.tax.trim(),
    total: draft.total.trim(),
    line_items: draft.items.map(({ description, quantity, unit_price }) => ({
      description: description.trim(),
      quantity: quantity.trim(),
      unit_price: unit_price.trim(),
    })),
  };
}

// Unit prices may have up to 6 decimals (0.14039 per kWh); amounts stay in cents.
const UNIT_PRICE_PATTERN = "-?\\d+(\\.\\d{1,6})?";

/** The editable record. The AI's draft fills it; whatever the user saves becomes the truth. */
export function ReceiptForm({ receipt }: { receipt: Receipt }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(() => draftFrom(receipt));
  const [saved, setSaved] = useState(false);

  const confirm = useMutation({
    mutationFn: (body: ReceiptUpdate) => api.confirmReceipt(receipt.id, body),
    onSuccess: (updated) => {
      receiptUpdated(queryClient, updated);
      setSaved(true);
    },
  });

  function set<K extends keyof Draft>(field: K, value: Draft[K]) {
    setSaved(false);
    setDraft((d) => ({ ...d, [field]: value }));
  }

  function setItem(key: number, field: keyof Omit<DraftItem, "key">, value: string) {
    set(
      "items",
      draft.items.map((item) => (item.key === key ? { ...item, [field]: value } : item)),
    );
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    confirm.mutate(toUpdate(draft));
  }

  // Live preview of the arithmetic, in integer cents; the server recomputes it on save.
  const amounts = draft.items.map((i) => lineAmountCents(i.quantity, i.unit_price));
  const itemsCents = amounts.every((a) => a !== null) ? amounts.reduce<number>((s, a) => s + (a ?? 0), 0) : null;
  const taxCents = draft.tax.trim() === "" ? 0 : toCents(draft.tax);
  const totalCents = toCents(draft.total);
  const withTaxCents = itemsCents !== null && taxCents !== null ? itemsCents + taxCents : null;
  const canReconcile = draft.items.length > 0 && itemsCents !== null && totalCents !== null;
  const matches =
    canReconcile &&
    (Math.abs(itemsCents - totalCents) <= 1 || (withTaxCents !== null && Math.abs(withTaxCents - totalCents) <= 1));
  const currency = draft.currency.trim().toUpperCase();

  return (
    <form onSubmit={onSubmit}>
      <div className="form-section">
        <div className="section-label">Details</div>
        <div className="form-grid">
          <label className="field span-6">
            <span className="field-label">Merchant</span>
            <input
              className="input"
              required
              maxLength={200}
              placeholder="Store or business name"
              value={draft.merchant}
              onChange={(e) => set("merchant", e.target.value)}
            />
          </label>
          <label className="field span-2">
            <span className="field-label">Date</span>
            <input
              className="input"
              required
              type="date"
              value={draft.purchased_at}
              onChange={(e) => set("purchased_at", e.target.value)}
            />
          </label>
          <label className="field span-2">
            <span className="field-label">Currency</span>
            <input
              className="input"
              required
              pattern="[A-Za-z]{3}"
              title="3-letter ISO code, e.g. USD"
              placeholder="USD"
              maxLength={3}
              value={draft.currency}
              onChange={(e) => set("currency", e.target.value.toUpperCase())}
            />
          </label>
          <label className="field span-2">
            <span className="field-label">Category</span>
            <select
              className="select"
              required
              value={draft.category}
              onChange={(e) => set("category", e.target.value as Category)}
            >
              <option value="">Choose…</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_META[c].label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="form-section">
        <div className="section-label">Line items</div>
        {draft.items.length > 0 && (
          <table className="items-table">
            <thead>
              <tr>
                <th>Description</th>
                <th className="right" style={{ width: 76 }}>
                  Qty
                </th>
                <th className="right" style={{ width: 112 }}>
                  Unit price
                </th>
                <th className="right" style={{ width: 92 }}>
                  Amount
                </th>
                <th style={{ width: 38 }} />
              </tr>
            </thead>
            <tbody>
              {draft.items.map((item, index) => (
                <tr key={item.key}>
                  <td className="cell-desc">
                    <input
                      className="input"
                      required
                      aria-label="Item description"
                      value={item.description}
                      onChange={(e) => setItem(item.key, "description", e.target.value)}
                    />
                  </td>
                  <td className="cell-qty" data-label="Qty">
                    <input
                      className="input num"
                      required
                      aria-label="Quantity"
                      inputMode="decimal"
                      pattern="\d+(\.\d{1,3})?"
                      value={item.quantity}
                      onChange={(e) => setItem(item.key, "quantity", e.target.value)}
                    />
                  </td>
                  <td className="cell-price" data-label="Unit price">
                    <input
                      className="input num"
                      required
                      aria-label="Unit price"
                      inputMode="decimal"
                      pattern={UNIT_PRICE_PATTERN}
                      placeholder="0.00"
                      value={item.unit_price}
                      onChange={(e) => setItem(item.key, "unit_price", e.target.value)}
                    />
                  </td>
                  <td className="right line-amount cell-amount" data-label="Amount">
                    {amounts[index] === null ? "" : formatCents(amounts[index])}
                  </td>
                  <td className="cell-remove">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm btn-icon"
                      aria-label="Remove item"
                      onClick={() =>
                        set(
                          "items",
                          draft.items.filter((i) => i.key !== item.key),
                        )
                      }
                    >
                      <X size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {draft.items.length === 0 && (
          <p className="muted" style={{ marginBottom: 10 }}>
            No line items. Add them if you want the totals cross-checked.
          </p>
        )}
        <button type="button" className="btn btn-sm" style={{ marginTop: 6 }} onClick={() => set("items", [...draft.items, newItem()])}>
          <Plus size={15} /> Add item
        </button>
      </div>

      <div className="form-section">
        <div className="section-label">Totals</div>
        <div className="totals-box">
          <div className="totals-row">
            <span className="muted">Items subtotal</span>
            <span className="amount">
              {itemsCents !== null && draft.items.length > 0 ? `${formatCents(itemsCents)} ${currency}`.trim() : "Not calculated"}
            </span>
          </div>
          <div className="totals-row">
            <label htmlFor="tax" className="muted">
              Tax
            </label>
            <MoneyInput id="tax" currency={currency} placeholder="none" value={draft.tax} onChange={(v) => set("tax", v)} />
          </div>
          <div className="totals-row grand">
            <label htmlFor="total">Total paid</label>
            <MoneyInput
              id="total"
              currency={currency}
              placeholder="0.00"
              required
              value={draft.total}
              onChange={(v) => set("total", v)}
            />
          </div>
          {canReconcile && (
            <div className="totals-row">
              {matches ? (
                <span className="reconcile ok">
                  <CircleCheck size={15} /> Items{taxCents ? " + tax" : ""} add up to the total
                </span>
              ) : (
                <>
                  <span className="reconcile warn">
                    <TriangleAlert size={15} /> Items don&apos;t add up to the total
                  </span>
                  {withTaxCents !== null && (
                    <button type="button" className="btn btn-sm" onClick={() => set("total", formatCents(withTaxCents))}>
                      Use {formatCents(withTaxCents)}
                    </button>
                  )}
                </>
              )}
            </div>
          )}
        </div>
        {confirm.isError && (
          <div className="notice error" style={{ marginTop: 14 }}>
            <CircleAlert size={16} />
            {confirm.error.message}
          </div>
        )}
      </div>

      <div className="form-footer">
        <span className="muted" style={{ fontSize: 13 }}>
          {saved ? (
            <span className="reconcile ok">
              <CircleCheck size={15} /> Saved · counted in your totals
            </span>
          ) : receipt.status === "confirmed" ? (
            "Changes update your totals immediately."
          ) : (
            "Confirming stores these values as the source of truth."
          )}
        </span>
        <button type="submit" className="btn btn-primary btn-lg" disabled={confirm.isPending}>
          {confirm.isPending ? "Saving…" : receipt.status === "confirmed" ? "Save changes" : "Confirm receipt"}
        </button>
      </div>
    </form>
  );
}

/** An amount with up to two decimals, prefixed with the currency code once one is set. */
function MoneyInput({
  id,
  currency,
  value,
  onChange,
  placeholder,
  required,
}: {
  id: string;
  currency: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  required?: boolean;
}) {
  return (
    <div className={currency ? "input-affix" : undefined}>
      {currency && <span className="affix">{currency}</span>}
      <input
        id={id}
        className="input num"
        required={required}
        inputMode="decimal"
        pattern="\d+(\.\d{1,2})?"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
