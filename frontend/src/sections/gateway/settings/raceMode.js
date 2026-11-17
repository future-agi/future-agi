// Race mode (routing.fastest, or strategy "fastest"), as this gateway treats
// it: it races no request (its hedge plan is always empty), so a stored race
// setting is kept with the config and not applied. Org config > Routing
// shows it read-only; a summary says it is stored and not run.

// The gateway's CanonicalStrategy: "Fastest" and "fastest" are one.
export const isFastestStrategy = (strategy) =>
  typeof strategy === "string" &&
  strategy.trim().toLowerCase().replace(/_/g, "-") === "fastest";

const isObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/** routing.fastest, {} when it is not stored. */
export const fastestOf = (routing) =>
  isObject(routing?.fastest) ? routing.fastest : {};

/** Whether a race setting is stored as on. */
export function raceModeOn(routing) {
  return (
    Boolean(fastestOf(routing).enabled) || isFastestStrategy(routing?.strategy)
  );
}

/** Race mode in one line, for a summary of what applies. */
export function raceSummary(routing) {
  return raceModeOn(routing) ? "Stored, not run on this gateway" : "Off";
}
