import assert from "node:assert/strict";
import { test } from "node:test";

import { planFlush, tokenSubject } from "./uploadOwner.ts";

const jwt = (payload: object) =>
  ["e30", Buffer.from(JSON.stringify(payload)).toString("base64url"), "signature"].join(".");

test("reads the user id from a token", () => {
  assert.equal(tokenSubject(jwt({ sub: "user-a", exp: 1 })), "user-a");
  assert.equal(tokenSubject(null), null);
  assert.equal(tokenSubject("not-a-jwt"), null);
  assert.equal(tokenSubject(jwt({ exp: 1 })), null);
});

test("only the queuing account's photos are sent; others are dropped, never uploaded", () => {
  const items = [
    { key: "1", owner: "user-a" },
    { key: "2", owner: "user-b" },
    { key: "3" }, // queued before photos were tagged with an owner
  ];
  const { send, discard } = planFlush(items, "user-a");
  assert.deepEqual(send.map((i) => i.key), ["1"]);
  assert.deepEqual(discard.map((i) => i.key), ["2", "3"]);
});

test("nothing is sent when no one is signed in", () => {
  assert.deepEqual(planFlush([{ key: "1", owner: "user-a" }], null).send, []);
});
