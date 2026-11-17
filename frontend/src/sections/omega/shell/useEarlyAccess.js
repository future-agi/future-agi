import { useQuery } from "@tanstack/react-query";

import { apiPath } from "src/api/contracts/api-surface";
import axios from "src/utils/axios";

import { isOmegaConsoleEnabled } from "./flags";

// Settings → Early access (READINESS 3.13, RC-01, RC-05). The server lists
// the Omega products this organization may see there: the ones it is admitted
// to and the ones taking waitlist requests (omega_rollout/early_access_api.py).
// The nav entry and the page exist only while at least one listed product is
// NOT admitted, i.e. while there is something to ask for or a request to
// follow. An org admitted to everything it can see, or one that sees nothing
// (every product internal, S0), gets no entry and the standard 404: no
// waitlist page with nothing to request (CP:D12, AUDIT R1). Loading or a
// failed read shows nothing.

export const EARLY_ACCESS_KEY = Object.freeze([
  "omega-rollout",
  "early-access",
]);

export const fetchEarlyAccess = () =>
  axios.get(apiPath("/omega-rollout/early-access/"));

/**
 * The Early access listing `{ can_request, products }`, read only when the
 * Omega master switch is on. Pass `staleTime` to re-read less often (the nav
 * does; the page re-reads on mount).
 */
export function useEarlyAccess({ enabled = true, staleTime } = {}) {
  return useQuery({
    queryKey: EARLY_ACCESS_KEY,
    queryFn: fetchEarlyAccess,
    select: (res) => res.data,
    enabled: enabled && isOmegaConsoleEnabled(),
    ...(staleTime === undefined ? {} : { staleTime }),
  });
}

/**
 * True when the listing names at least one product this org is not admitted
 * to: one it may ask for, or one whose request it is waiting on. Admitted
 * rows alone are not a reason to show the page (HIDE-UNFINISHED-FA round 2).
 */
export const hasRequestable = (listing) =>
  Array.isArray(listing?.products) &&
  listing.products.some((row) => row && row.admitted === false);
