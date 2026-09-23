// The "Scenarios to generate" field's constants + validity, shared by the field
// component and the build panels that gate submit on it.

// The backend's admission ceiling is min(ALK_MAX_SCENARIOS_PER_REQUEST, db-limit)
// and defaults to 1000; the UI mirrors that so a submit that would be rejected is
// caught before the round-trip.
export const MAX_SCENARIOS = 1000;
export const DEFAULT_SCENARIOS = "10";

// Valid = a whole number 1..1000. Empty / 0 / over-cap / non-numeric is invalid,
// which blocks the build form's submit.
export function isValidScenarioCount(value) {
  const s = String(value ?? "").trim();
  if (!/^\d+$/.test(s)) return false;
  const n = Number(s);
  return n >= 1 && n <= MAX_SCENARIOS;
}
