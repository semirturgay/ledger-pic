// Live form preview only, in exact integers so 0.1 + 0.2 stays 0.30 and 17,384.64 x 0.14039 doesn't drift.
// The server recomputes everything from what it stores; these numbers are never sent.

const MONEY = /^-?\d+(\.\d{1,2})?$/;
const QUANTITY = /^\d+(\.\d{1,3})?$/;
// Unit prices keep sub-cent precision (0.14039 per kWh); only line amounts are rounded to cents.
const UNIT_PRICE = /^-?\d+(\.\d{1,6})?$/;

function toScaled(value: string, pattern: RegExp, digits: number): bigint | null {
  const s = value.trim();
  if (!pattern.test(s)) return null;
  const negative = s.startsWith("-");
  const [whole, fraction = ""] = s.replace("-", "").split(".");
  const scaled = BigInt(whole + fraction.padEnd(digits, "0"));
  return negative ? -scaled : scaled;
}

export const toCents = (value: string): number | null => {
  const cents = toScaled(value, MONEY, 2);
  return cents === null ? null : Number(cents);
};

/** quantity x unit_price rounded half up to cents (as the server and tills do), or null if invalid. */
export function lineAmountCents(quantity: string, unitPrice: string): number | null {
  const thousandths = toScaled(quantity, QUANTITY, 3);
  const micros = toScaled(unitPrice, UNIT_PRICE, 6);
  if (thousandths === null || micros === null) return null;
  const product = thousandths * micros; // scale 10^9; cents are scale 10^2
  const divisor = 10_000_000n;
  const half = product < 0n ? -divisor / 2n : divisor / 2n;
  return Number((product + half) / divisor);
}

/** Show a stored unit price without padding: "4.290000" -> "4.29", "0.140390" -> "0.14039". */
export function trimUnitPrice(value: string): string {
  if (!value.includes(".")) return value;
  const [whole, fraction] = value.split(".");
  const kept = fraction.replace(/0+$/, "");
  return `${whole}.${kept.padEnd(2, "0")}`;
}

export function formatCents(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}
