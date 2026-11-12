import { useCapabilities } from "src/hooks/useCapabilities";

import { isOmegaConsoleEnabled } from "./flags";
import { ROLLOUT_STALE_MS, isPartReady } from "./rollout";

/**
 * Whether one part of a hosted Omega product works today (OMEGA_PART in
 * rollout.js; decided by the server in omega_rollout/readiness.py). A part
 * that does not is hidden, never shown as "soon" or "unavailable" (HUB_PUBLISH
 * excepted: a Hub with no checker is a configuration error its Publish page
 * states, hub/lib/uploadsChecked.js). False while
 * the answer loads, when it cannot be read, and with the master switch off.
 * Backed by the same cached /api/capabilities/ query as the product gates.
 */
export function useOmegaPart(part) {
  const { data, isLoading, isError } = useCapabilities({
    staleTime: isOmegaConsoleEnabled() ? ROLLOUT_STALE_MS : Infinity,
  });
  if (!isOmegaConsoleEnabled()) return false;
  return isPartReady(data, part, { isLoading, isError });
}

export default useOmegaPart;
