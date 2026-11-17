// Cadence field checks for repeats (notify DESIGN §2.2, §2.9; Step 0 review
// must-fix 5). Bad input never computes: a misspelled zone, a minute outside
// 0-59, an anchor that isn't a time, an interval that doesn't divide the
// day or an unknown preset is refused in plain words. Only a missing `tz`
// means UTC. Pure; cronModel.js re-exports validateCadence, PRESETS,
// INTERVALS_MIN and CADENCE_ERRORS, so callers import from there.
import { validate } from "./cronParse";
import { normalizeZone } from "./cronZones";

export const PRESETS = Object.freeze([
  "hourly",
  "every_n_hours",
  "daily",
  "weekdays",
  "weekly",
  "cron",
]);
/** Every hour and every N hours: the intervals that divide a day. */
export const INTERVALS_MIN = Object.freeze([60, 120, 180, 240, 360, 480, 720]);

export const isInterval = (cadence) =>
  cadence?.preset === "hourly" || cadence?.preset === "every_n_hours";
export const present = (v) => v != null && String(v).trim() !== "";

/** The plain-words reasons a cadence can't be computed. */
export const CADENCE_ERRORS = Object.freeze({
  preset: (p) => `“${p}” isn't a repeat option. Use ${PRESETS.join(", ")}.`,
  zone: (tz) =>
    `“${tz}” isn't a time zone. Use a name like Asia/Kolkata or America/New_York.`,
  interval: () =>
    `interval_min must be one of ${INTERVALS_MIN.join(", ")} (every 1, 2, 3, 4, 6, 8 or 12 hours).`,
  minute: (m) =>
    `The minute past the hour must be a whole number from 0 to 59, not “${m}”.`,
  anchor: (a) => `The first run time “${a}” isn't a date and time.`,
});

const isMinute = (m) => Number.isInteger(m) && m >= 0 && m <= 59;

/** Every field but the cron text: {code, error} or null. */
function fieldProblem(cadence) {
  const fail = (code, value) => ({ code, error: CADENCE_ERRORS[code](value) });
  if (!PRESETS.includes(cadence.preset)) return fail("preset", cadence.preset);
  if (present(cadence.tz) && !normalizeZone(cadence.tz))
    return fail("zone", cadence.tz);
  if (!isInterval(cadence)) return null;
  const minutes =
    cadence.preset === "hourly" ? 60 : Number(cadence.interval_min);
  if (!INTERVALS_MIN.includes(minutes)) return fail("interval");
  if (cadence.minute != null && !isMinute(cadence.minute))
    return fail("minute", cadence.minute);
  if (
    present(cadence.anchor_at) &&
    !Number.isFinite(Date.parse(cadence.anchor_at))
  )
    return fail("anchor", cadence.anchor_at);
  return null;
}

/** Throws the first field problem (with its `code`); cron text aside. */
export function assertFields(cadence) {
  const p = fieldProblem(cadence);
  if (p) throw Object.assign(new Error(p.error), { code: p.code });
}

/**
 * A cadence checked whole, for forms: {ok: true}, or {ok: false, code,
 * error} with the error in plain words. Null (events only) is fine.
 */
export function validateCadence(cadence) {
  if (!cadence) return { ok: true };
  const p = fieldProblem(cadence);
  if (p) return { ok: false, ...p };
  if (!isInterval(cadence)) {
    const v = validate(cadence.cron);
    if (!v.ok) return { ok: false, code: v.code, error: v.error };
  }
  return { ok: true };
}
