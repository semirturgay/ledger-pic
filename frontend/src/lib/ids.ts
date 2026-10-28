// Receipt ids are UUIDs. The review page takes its id from the URL, so it is checked before it goes into
// an API path: a crafted link can't steer the signed-in user's request to another endpoint.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const isReceiptId = (id: string): boolean => UUID.test(id);

export function receiptPath(id: string, suffix = ""): string {
  if (!isReceiptId(id)) throw new Error("Receipt not found");
  return `/receipts/${id}${suffix}`;
}
