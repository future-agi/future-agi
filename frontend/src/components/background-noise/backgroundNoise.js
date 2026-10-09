// The key a scenario carries when the caller is heard with no noise behind them.
export const QUIET_LINE = "quiet line";

// What the column header and the drawer chip explain on hover.
export const BACKGROUND_NOISE_HINT =
  "Where the scenario puts the caller. The sound plays under the caller's voice for the whole call.";

/**
 * A call's background noise from an API payload (a calls-list row or the call
 * detail), or null when the payload has none.
 * @param {?Object} payload
 * @returns {?{ key: string, label: string }}
 */
export function backgroundNoiseFrom(payload) {
  const key = payload?.background_noise;
  if (!key) return null;
  return { key, label: payload.background_noise_label || key };
}
