import assert from "node:assert/strict";
import { test } from "node:test";

import { isReceiptId, receiptPath } from "./ids.ts";

test("only real receipt ids become API paths", () => {
  const id = "3f6c1d2e-8a4b-4c5d-9e0f-1a2b3c4d5e6f";
  assert.equal(receiptPath(id), `/receipts/${id}`);
  assert.equal(receiptPath(id, "/image"), `/receipts/${id}/image`);
  for (const crafted of ["../auth/me", "..%2Fauth%2Fme", `${id}/../../auth/me`, `${id}?x=1`, ""]) {
    assert.equal(isReceiptId(crafted), false);
    assert.throws(() => receiptPath(crafted), /Receipt not found/);
  }
});
