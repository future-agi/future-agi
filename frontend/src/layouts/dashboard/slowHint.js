/** When the route skeleton's loading hint appears, and when it says why. */
export const SLOW_HINT_MS = 3000;
export const VERY_SLOW_HINT_MS = 10000;

/** The hint under the skeleton toolbar: none, then two steps of copy. */
export function slowHintText(elapsedMs) {
  if (elapsedMs < SLOW_HINT_MS) return null;
  if (elapsedMs < VERY_SLOW_HINT_MS) return "Still loading this page…";
  return "Still loading. The first visit after an update takes longer; the next ones are quick.";
}

/**
 * L7c g1 (state:route-skeleton): how long a skeleton has already waited when
 * it mounts. Only the skeleton of the page load itself (mounted before the
 * person clicked or typed anything) counts from the navigation; a skeleton
 * of an in-app navigation, even the first one after a long idle, counts
 * from its own mount. Pure.
 */
export function alreadyWaitedMs({ firstSinceLoad, interacted, sinceLoadMs }) {
  if (!firstSinceLoad || interacted) return 0;
  const ms = Number(sinceLoadMs);
  return Number.isFinite(ms) && ms > 0 ? ms : 0;
}
