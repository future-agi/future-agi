// The "Scenarios to generate" field's constants + validity, shared by the field
// component and the build panels that gate submit on it.

export const MAX_SCENARIOS = 500;
export const DEFAULT_SCENARIOS = "10";

// Valid = a whole number 1..500. Empty / 0 / over-cap / non-numeric is invalid,
// which blocks the build form's submit (the backend wants a concrete count).
export function isValidScenarioCount(value) {
  const s = String(value ?? "").trim();
  if (!/^\d+$/.test(s)) return false;
  const n = Number(s);
  return n >= 1 && n <= MAX_SCENARIOS;
}
