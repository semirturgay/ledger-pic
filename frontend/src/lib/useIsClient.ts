import { useSyncExternalStore } from "react";

const noSubscription = () => () => {};

/** False while rendering on the server and during hydration, true in the browser afterwards. */
export function useIsClient(): boolean {
  return useSyncExternalStore(noSubscription, () => true, () => false);
}
