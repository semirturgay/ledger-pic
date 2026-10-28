import assert from "node:assert/strict";
import { test } from "node:test";

import { AccountChanged, uploadPinned, type UploadIO } from "./pinnedUpload.ts";

const jwt = (sub: string) => ["e30", Buffer.from(JSON.stringify({ sub })).toString("base64url"), "sig"].join(".");
const A = jwt("user-a");
const B = jwt("user-b");
const transientError = { transient: true };

function io(overrides: Partial<UploadIO<string>> = {}) {
  const calls = { sent: [] as string[], queued: [] as (string | null)[] };
  const base: UploadIO<string> = {
    send: async (token) => {
      calls.sent.push(token);
      return "receipt";
    },
    queue: async (owner) => {
      calls.queued.push(owner);
    },
    unqueue: async () => {
      calls.queued.pop();
    },
    currentToken: () => A,
    online: () => true,
    transient: (err) => err === transientError,
    unreachable: (err) => err === transientError,
    wait: async () => {},
  };
  return { io: { ...base, ...overrides }, calls };
}

test("uploads with the token it started with", async () => {
  const { io: deps, calls } = io();
  assert.equal(await uploadPinned(A, deps), "receipt");
  assert.deepEqual(calls.sent, [A]);
});

test("a sign-in as someone else during the retry stops the upload instead of sending it to them", async () => {
  let stored = A;
  const { io: deps, calls } = io({
    send: async (token) => {
      calls.sent.push(token);
      stored = B; // another tab signs in as B while the first attempt fails
      throw transientError;
    },
    currentToken: () => stored,
  });
  await assert.rejects(uploadPinned(A, deps), AccountChanged);
  assert.deepEqual(calls.sent, [A]); // never sent with B's token
  assert.deepEqual(calls.queued, []); // and never queued under B
});

test("when the server can't be reached it is queued for the account that started it", async () => {
  let stored = A;
  const { io: deps, calls } = io({
    send: async () => {
      throw transientError;
    },
    currentToken: () => stored,
  });
  assert.equal(await uploadPinned(A, deps), "queued");
  assert.deepEqual(calls.queued, ["user-a"]);

  stored = B; // same failure, but the account changed before queueing
  await assert.rejects(uploadPinned(A, deps), AccountChanged);
  assert.deepEqual(calls.queued, ["user-a"]);
});

test("offline goes straight to the queue without a request", async () => {
  const { io: deps, calls } = io({ online: () => false });
  assert.equal(await uploadPinned(A, deps), "queued");
  assert.deepEqual(calls.sent, []);
  assert.deepEqual(calls.queued, ["user-a"]);
});

test("a sign-in while the photo is being saved takes it back out of the queue", async () => {
  let stored = A;
  const { io: deps, calls } = io({
    send: async () => {
      throw transientError;
    },
    queue: async (owner) => {
      calls.queued.push(owner);
      stored = B; // another tab signs in as B during the save
    },
    currentToken: () => stored,
  });
  await assert.rejects(uploadPinned(A, deps), AccountChanged);
  assert.deepEqual(calls.queued, []); // removed again
});
