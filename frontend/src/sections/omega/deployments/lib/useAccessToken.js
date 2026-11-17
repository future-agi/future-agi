import { useSyncExternalStore } from "react";

import { heldAccessToken, subscribeAccessTokens } from "./accessTokens";

/** The token this tab holds for a deployment, or null. */
export function useAccessToken(id) {
  return useSyncExternalStore(subscribeAccessTokens, () =>
    id ? heldAccessToken(id) : null,
  );
}
