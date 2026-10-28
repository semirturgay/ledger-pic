// Which account an offline-queued photo belongs to. Pure functions (no browser APIs beyond atob),
// so they run under `node --test` as well as in the app.

/** The user id (`sub`) inside a JWT. Not verified: only used to tell this device's accounts apart. */
export function tokenSubject(token: string | null): string | null {
  const payload = token?.split(".")[1];
  if (!payload) return null;
  try {
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const sub = (JSON.parse(json) as { sub?: unknown }).sub;
    return typeof sub === "string" ? sub : null;
  } catch {
    return null;
  }
}

/**
 * Split queued uploads into those to send for `owner` and those to drop. A photo queued by another
 * account (or before uploads were tagged) is never sent under this account's token.
 */
export function planFlush<T extends { owner?: string | null }>(items: T[], owner: string | null) {
  if (!owner) return { send: [] as T[], discard: [] as T[] };
  return {
    send: items.filter((item) => item.owner === owner),
    discard: items.filter((item) => item.owner !== owner),
  };
}
