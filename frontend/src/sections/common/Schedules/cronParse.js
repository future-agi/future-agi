// Five-field cron parsing and checks for repeats (notify DESIGN §2.2;
// BUILD-BRIEF §4.0). Pure; cronModel.js re-exports `validate` and
// `CRON_ERRORS`, so callers import from there.

export const CRON_ERRORS = Object.freeze({
  fields: "Use five fields: minute hour day month weekday.",
  unsupported: "L, W, # and ? aren't supported.",
  tooOften: "Runs at most once an hour. Pick one minute, or use Every hour.",
  never: "This never runs: no month has that day.",
});
const MONTHS = [
  "JAN",
  "FEB",
  "MAR",
  "APR",
  "MAY",
  "JUN",
  "JUL",
  "AUG",
  "SEP",
  "OCT",
  "NOV",
  "DEC",
];
const DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const FIELDS = [
  { key: "minute", label: "Minute", min: 0, max: 59 },
  { key: "hour", label: "Hour", min: 0, max: 23 },
  { key: "day", label: "Day of the month", min: 1, max: 31 },
  { key: "month", label: "Month", min: 1, max: 12, names: MONTHS, base: 1 },
  { key: "weekday", label: "Weekday", min: 0, max: 7, names: DAYS, base: 0 },
];
const MONTH_DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

class CronError extends Error {
  constructor(code, message) {
    super(message ?? CRON_ERRORS[code]);
    this.code = code;
  }
}

function atom(text, spec) {
  const upper = text.toUpperCase();
  const named = spec.names?.indexOf(upper) ?? -1;
  if (named >= 0) return named + spec.base;
  if (!/^\d+$/.test(text))
    throw new CronError("syntax", `${spec.label} can't be “${text}”.`);
  const n = Number(text);
  if (n < spec.min || n > spec.max)
    throw new CronError(
      "range",
      `${spec.label} must be between ${spec.min} and ${spec.max}.`,
    );
  return n;
}

function parseField(text, spec) {
  const out = new Set();
  text.split(",").forEach((part) => {
    if (!part)
      throw new CronError("syntax", `${spec.label} has an empty item.`);
    const [range, stepText, extra] = part.split("/");
    if (extra !== undefined)
      throw new CronError("syntax", `${spec.label} can't be “${part}”.`);
    const step =
      stepText === undefined
        ? 1
        : atom(stepText, {
            ...spec,
            label: `${spec.label} step`,
            names: null,
            min: 1,
            max: spec.max,
          });
    let lo;
    let hi;
    if (range === "*") [lo, hi] = [spec.min, spec.max];
    else if (range.includes("-")) {
      const [a, b] = range.split("-");
      [lo, hi] = [atom(a, spec), atom(b, spec)];
      // `FRI-SUN`: SUN ends a weekday range as 7 (cron's `5-7`).
      if (spec.key === "weekday" && hi === 0 && lo > 0 && /^sun$/i.test(b))
        hi = 7;
      if (lo > hi)
        throw new CronError(
          "range",
          `${spec.label} range “${range}” runs backwards.`,
        );
    } else {
      lo = atom(range, spec);
      hi = stepText === undefined ? lo : spec.max;
    }
    for (let v = lo; v <= hi; v += step) out.add(v);
  });
  if (spec.key === "weekday" && out.has(7)) {
    out.delete(7);
    out.add(0);
  }
  return [...out].sort((a, b) => a - b);
}

/**
 * Check a five-field cron. Faster than hourly, `L`, `W`, `#` and `?` are
 * rejected. Day and weekday combine as OR when both are restricted
 * (Vixie cron, croniter `day_or=True`); a field is restricted when it
 * doesn't cover its whole range.
 * @returns {{ok: true, fields: object, dayRestricted: boolean,
 *   weekdayRestricted: boolean} | {ok: false, code: string, error: string}}
 */
export function validate(cron) {
  try {
    const parts = String(cron ?? "")
      .trim()
      .split(/\s+/);
    if (parts.length !== 5 || !parts[0]) throw new CronError("fields");
    if (
      /[LW#?]/i.test(
        parts
          .join(" ")
          .replace(/[A-Z]{3}/gi, (w) =>
            [...MONTHS, ...DAYS].includes(w.toUpperCase()) ? "0" : w,
          ),
      )
    )
      throw new CronError("unsupported");
    const fields = {};
    FIELDS.forEach((spec, i) => {
      fields[spec.key] = parseField(parts[i], spec);
    });
    if (fields.minute.length !== 1) throw new CronError("tooOften");
    const dayRestricted = fields.day.length < 31;
    const weekdayRestricted = fields.weekday.length < 7;
    if (
      dayRestricted &&
      !weekdayRestricted &&
      !fields.month.some((m) => fields.day.some((d) => d <= MONTH_DAYS[m - 1]))
    )
      throw new CronError("never");
    return { ok: true, fields, dayRestricted, weekdayRestricted };
  } catch (e) {
    if (e instanceof CronError)
      return { ok: false, code: e.code, error: e.message };
    throw e;
  }
}
