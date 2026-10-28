// One upload, bound to the account that started it. Pure logic with injected I/O, so it runs under
// `node --test`; src/lib/receipts.ts wires in the real API, queue and token.

import { tokenSubject } from "./uploadOwner.ts";

export type UploadIO<R> = {
  /** Send the file with this exact token (never "whatever token is stored now"). */
  send: (token: string) => Promise<R>;
  /** Save for later, tagged with the account it belongs to; `unqueue` takes it back out. */
  queue: (owner: string | null) => Promise<void>;
  unqueue: () => Promise<void>;
  /** The token stored right now: another tab may have signed in as someone else. */
  currentToken: () => string | null;
  online: () => boolean;
  /** May the request not have reached the server (or failed there), so a retry could help? */
  transient: (err: unknown) => boolean;
  /** Did it fail because the server can't be reached at all (offline)? */
  unreachable: (err: unknown) => boolean;
  wait: (ms: number) => Promise<void>;
};

export class AccountChanged extends Error {
  constructor() {
    super("You signed in as someone else in another tab, so this photo wasn't uploaded.");
  }
}

/**
 * Upload once, retry once after a second if the failure may be transient, otherwise queue it on this
 * device. Every attempt uses `token`; if the signed-in account changes meanwhile, it stops instead of
 * sending one account's receipt photo into another.
 */
export async function uploadPinned<R>(token: string, io: UploadIO<R>): Promise<R | "queued"> {
  const attempt = () => {
    if (io.currentToken() !== token) throw new AccountChanged();
    return io.send(token);
  };
  try {
    if (!io.online()) throw new Offline();
    return await attempt();
  } catch (err) {
    if (err instanceof AccountChanged) throw err;
    if (!(err instanceof Offline) && !io.transient(err)) throw err;
    if (io.online()) {
      await io.wait(1000);
      try {
        return await attempt();
      } catch (retryErr) {
        if (retryErr instanceof AccountChanged || !io.unreachable(retryErr)) throw retryErr;
      }
    }
    if (io.currentToken() !== token) throw new AccountChanged();
    await io.queue(tokenSubject(token));
    if (io.currentToken() !== token) {
      // Another tab signed in while it was being saved: don't leave this photo on a device someone else uses.
      await io.unqueue();
      throw new AccountChanged();
    }
    return "queued";
  }
}

class Offline extends Error {}
