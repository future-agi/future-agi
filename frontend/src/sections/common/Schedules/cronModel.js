// Cadence model for repeats (notify DESIGN §2.2, §2.9; BUILD-BRIEF §4.0).
// One scheduler serves experiments and Simulate test runs (OWNER-DECISIONS
// 10), so nothing here knows what repeats. Pure: no clock reads, no I/O;
// Intl.DateTimeFormat only (cronZones.js).
//
// Cadence: {preset: "hourly"|"every_n_hours"|"daily"|"weekdays"|"weekly"|
//   "cron", cron, interval_min, anchor_at, minute, tz, utc_locked} | null.
// Every day, Weekdays, Every week and Custom are wall-clock crons in `tz`.
// Every hour and every N hours are elapsed-time intervals in UTC anchored to
// the first run (`anchor_at`, or the next `minute` past the hour), so a
// fall-back night never leaves a two-hour gap.
// Shared cases: api_contracts/schedules/next-runs.golden.json.
// Bad input never computes (cadenceCheck.js): nextRuns, describe and
// toUtcCron throw in plain words on a bad zone, minute, anchor, interval or
// preset; validateCadence gives the same words as {ok: false} for forms.
import {
  fixedOffset,
  localToUtc,
  normalizeZone,
  offsetMs,
  wallOf,
  zoneAbbr,
} from "./cronZones";
import { validate } from "./cronParse";
import {
  CADENCE_ERRORS,
  INTERVALS_MIN,
  assertFields,
  isInterval,
  present,
} from "./cadenceCheck";

export { normalizeZone } from "./cronZones";
export { CRON_ERRORS, validate } from "./cronParse";
export {
  CADENCE_ERRORS,
  INTERVALS_MIN,
  PRESETS,
  validateCadence,
} from "./cadenceCheck";

const MIN = 60_000;
const DAY = 86_400_000;
// Three 29 Februaries even across 2100, which has none.
const SEARCH_DAYS = 366 * 16;
const DAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];
const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

// Callers check the fields first (assertFields), so a zone here is valid.
const zoneOf = (cadence) =>
  cadence?.utc_locked || !present(cadence?.tz)
    ? "UTC"
    : normalizeZone(cadence.tz);

function dayMatches(v, y, m, d) {
  const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  if (!v.fields.month.includes(m)) return false;
  const dom = v.fields.day.includes(d);
  const wd = v.fields.weekday.includes(dow);
  return v.dayRestricted && v.weekdayRestricted ? dom || wd : dom && wd;
}

function cronRuns(v, tz, fromMs, n) {
  const out = [];
  const start = wallOf(fromMs, tz);
  const times = v.fields.hour.flatMap((h) =>
    v.fields.minute.map((mi) => [h, mi]),
  );
  let dayMs = Date.UTC(start.y, start.m - 1, start.d) - DAY;
  for (let i = 0; i < SEARCH_DAYS && out.length < n; i += 1, dayMs += DAY) {
    const date = new Date(dayMs);
    const [y, m, d] = [
      date.getUTCFullYear(),
      date.getUTCMonth() + 1,
      date.getUTCDate(),
    ];
    if (!dayMatches(v, y, m, d)) continue;
    times.forEach(([h, mi]) => {
      const r = localToUtc(y, m, d, h, mi, tz);
      if (r.ms <= fromMs || out.some((x) => x.at_ms === r.ms)) return;
      const wanted = `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`;
      out.push({ at_ms: r.ms, shifted_from: r.shifted ? wanted : null });
    });
    out.sort((a, b) => a.at_ms - b.at_ms);
  }
  return out.slice(0, n);
}

/** The first instant after `fromMs` whose minute in `tz` is `minute`. */
function nextAtMinute(fromMs, minute, tz) {
  let t = Math.floor(fromMs / MIN) * MIN + MIN;
  for (let i = 0; i < 180; i += 1, t += MIN)
    if (wallOf(t, tz).mi === minute) return t;
  return null;
}

function intervalOf(cadence) {
  const minutes =
    cadence.preset === "hourly" ? 60 : Number(cadence.interval_min);
  if (!INTERVALS_MIN.includes(minutes))
    throw new Error(CADENCE_ERRORS.interval());
  return minutes * MIN;
}

function intervalRuns(cadence, fromMs, n) {
  const step = intervalOf(cadence);
  let first;
  if (present(cadence.anchor_at)) {
    const anchor = Date.parse(cadence.anchor_at);
    const k = anchor > fromMs ? 0 : Math.floor((fromMs - anchor) / step) + 1;
    first = anchor + k * step;
  } else {
    first = nextAtMinute(fromMs, cadence.minute ?? 0, zoneOf(cadence));
    if (first == null) throw new Error(CADENCE_ERRORS.minute(cadence.minute));
  }
  return Array.from({ length: n }, (_, i) => ({
    at_ms: first + i * step,
    shifted_from: null,
  }));
}

/**
 * The next `n` runs strictly after `fromMs`, with `shifted_from` ("02:30")
 * when a wall time didn't exist that day and the run moved to the end of
 * the gap. A null cadence (events only) has none.
 * @returns {{at_ms: number, shifted_from: string|null}[]}
 */
export function nextRunsDetail(cadence, fromMs, n = 3) {
  if (!cadence) return [];
  assertFields(cadence);
  if (isInterval(cadence)) return intervalRuns(cadence, fromMs, n);
  const v = validate(cadence.cron);
  if (!v.ok) throw new Error(v.error);
  return cronRuns(v, zoneOf(cadence), fromMs, n);
}

/** The next `n` run times (epoch ms) strictly after `fromMs`. */
export const nextRuns = (cadence, fromMs, n = 3) =>
  nextRunsDetail(cadence, fromMs, n).map((r) => r.at_ms);

// ------------------------------------------------------------- describe
const pad2 = (n) => String(n).padStart(2, "0");
const hhmm = (h, mi) => `${pad2(h)}:${pad2(mi)}`;
const listOf = (items) =>
  items.length < 2
    ? items.join("")
    : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
const mondayFirst = (days) =>
  [...days].sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7));
const contiguous = (sorted) =>
  sorted.every((x, i) => i === 0 || x === sorted[i - 1] + 1);

function weekdayText(days) {
  const order = mondayFirst(days).map((d) => (d + 6) % 7);
  if (days.length >= 3 && contiguous(order))
    return `${DAY_NAMES[(order[0] + 1) % 7]} to ${DAY_NAMES[(order.at(-1) + 1) % 7]}`;
  return `only on ${listOf(mondayFirst(days).map((d) => DAY_NAMES[d]))}`;
}

function monthText(months) {
  if (months.length >= 3 && contiguous(months))
    return `${MONTH_NAMES[months[0] - 1]} to ${MONTH_NAMES[months.at(-1) - 1]}`;
  return `in ${listOf(months.map((m) => MONTH_NAMES[m - 1]))}`;
}

function timeText({ hour, minute }) {
  const [mi] = minute;
  if (hour.length === 24) return `Every hour at minute :${pad2(mi)}`;
  if (hour.length <= 6) return `At ${listOf(hour.map((h) => hhmm(h, mi)))}`;
  const step = hour[1] - hour[0];
  if (
    hour.every((h, i) => h === hour[0] + i * step) &&
    hour.at(-1) + step >= 24
  )
    return `Every ${step} hours at minute :${pad2(mi)}, from ${hhmm(hour[0], mi)}`;
  return `At minute :${pad2(mi)}, ${hour.length} times a day`;
}

/** A cron read back in words: `41 3 * * 1-5` → `At 03:41, Monday to Friday`. */
export function describeCron(cron) {
  const v = validate(cron);
  if (!v.ok) return null;
  const { fields } = v;
  const dom = `on ${fields.day.length > 1 ? "days" : "day"} ${listOf(fields.day.map(String))} of the month`;
  let day = null;
  if (v.dayRestricted && v.weekdayRestricted)
    day = `${dom} and on every ${listOf(mondayFirst(fields.weekday).map((d) => DAY_NAMES[d]))}`;
  else if (v.dayRestricted) day = dom;
  else if (v.weekdayRestricted) day = weekdayText(fields.weekday);
  const time = timeText(fields);
  const parts = [time];
  if (day) parts.push(day);
  else if (!time.startsWith("Every")) parts.push("every day");
  if (fields.month.length < 12) parts.push(monthText(fields.month));
  return parts.join(", ");
}

function presetText(cadence, v) {
  const [mi] = v.fields.minute;
  const at = `at ${listOf(v.fields.hour.map((h) => hhmm(h, mi)))}`;
  if (cadence.preset === "daily") return `Every day ${at}`;
  if (cadence.preset === "weekdays") return `Every weekday ${at}`;
  return `Every ${listOf(mondayFirst(v.fields.weekday).map((d) => DAY_NAMES[d]))} ${at}`;
}

function intervalMinute(cadence) {
  if (present(cadence.anchor_at))
    return wallOf(Date.parse(cadence.anchor_at), zoneOf(cadence)).mi;
  return cadence.minute ?? 0;
}

/** Whether a preset's label fits its cron (else Custom's words are used). */
function presetFits(preset, v) {
  const months = v.fields.month.length === 12;
  if (!months || v.dayRestricted) return false;
  if (preset === "daily") return !v.weekdayRestricted;
  if (preset === "weekdays")
    return v.fields.weekday.join() === [1, 2, 3, 4, 5].join();
  return preset === "weekly" && v.weekdayRestricted;
}

/**
 * A cadence in words: `Every day at 03:41`, `Every hour at minute :41`,
 * `Every 6 hours at minute :41`, `At 03:41, Monday to Friday` (Custom).
 * `{zone: true, atMs}` appends the zone's short name (`… at 03:41 IST`).
 * Null for an events-only repeat.
 */
export function describe(cadence, { zone = false, atMs } = {}) {
  if (!cadence) return null;
  assertFields(cadence);
  let text;
  if (isInterval(cadence)) {
    const hours = intervalOf(cadence) / 3_600_000;
    text = `Every ${hours === 1 ? "hour" : `${hours} hours`} at minute :${pad2(intervalMinute(cadence))}`;
  } else {
    const v = validate(cadence.cron);
    if (!v.ok) return null;
    text = presetFits(cadence.preset, v)
      ? presetText(cadence, v)
      : describeCron(cadence.cron);
  }
  if (!zone) return text;
  const at =
    atMs ?? (present(cadence.anchor_at) ? Date.parse(cadence.anchor_at) : null);
  if (at == null) throw new Error("describe needs atMs to name the zone");
  return `${text} ${zoneAbbr(zoneOf(cadence), at)}`;
}

// ------------------------------------------------------------ to UTC
/**
 * The same cadence as a UTC cron, for GitHub's `schedule:` (DESIGN §2.2,
 * §3.2). The conversion uses the zone's offset at `atMs` (required when
 * the zone changes its clocks, or for an unanchored every-N-hours repeat).
 * Null when one UTC line can't say it (a restricted day that moves across
 * midnight by different amounts, or a day of the month that moves).
 */
export function toUtcCron(cadence, atMs) {
  if (!cadence) return null;
  assertFields(cadence);
  const tz = zoneOf(cadence);
  if (isInterval(cadence)) {
    const anchor = present(cadence.anchor_at)
      ? Date.parse(cadence.anchor_at)
      : atMs != null
        ? nextRuns(cadence, atMs, 1)[0]
        : null;
    if (anchor == null)
      throw new Error("toUtcCron needs atMs for an unanchored interval");
    const d = new Date(anchor);
    const stepH = intervalOf(cadence) / 3_600_000;
    if (stepH === 1) return `${d.getUTCMinutes()} * * * *`;
    const hours = Array.from(
      { length: 24 / stepH },
      (_, i) => (d.getUTCHours() + i * stepH) % 24,
    );
    return `${d.getUTCMinutes()} ${hours.sort((a, b) => a - b).join(",")} * * *`;
  }
  const v = validate(cadence.cron);
  if (!v.ok) return null;
  const parts = String(cadence.cron).trim().split(/\s+/);
  if (tz === "UTC") return parts.join(" ");
  if (atMs == null && !fixedOffset(tz, Date.UTC(2026, 0, 1)))
    throw new Error(`toUtcCron needs atMs: ${tz} changes its clocks`);
  const off = Math.round(offsetMs(atMs ?? Date.UTC(2026, 0, 1), tz) / MIN);
  const [mi] = v.fields.minute;
  const moved = v.fields.hour.map((h) => {
    const total = h * 60 + mi - off;
    return {
      shift: Math.floor(total / 1440),
      h: Math.floor((((total % 1440) + 1440) % 1440) / 60),
      mi: ((total % 60) + 60) % 60,
    };
  });
  const shifts = new Set(moved.map((x) => x.shift));
  const monthRestricted = v.fields.month.length < 12;
  const anyDayRule = v.dayRestricted || v.weekdayRestricted || monthRestricted;
  if (shifts.size > 1 && anyDayRule) return null;
  const [shift] = shifts;
  if (shift !== 0 && (v.dayRestricted || monthRestricted)) return null;
  const hours = [...new Set(moved.map((x) => x.h))].sort((a, b) => a - b);
  const weekday =
    v.weekdayRestricted && shift !== 0
      ? rangesOf(v.fields.weekday.map((d) => (d + shift + 7) % 7))
      : parts[4];
  return `${moved[0].mi} ${hours.length === 24 ? "*" : rangesOf(hours)} ${parts[2]} ${parts[3]} ${weekday}`;
}

/** `[0,1,2,3,4,9]` → `0-4,9` (cron list syntax). */
function rangesOf(values) {
  const sorted = [...new Set(values)].sort((a, b) => a - b);
  const out = [];
  sorted.forEach((v, i) => {
    if (i > 0 && v === sorted[i - 1] + 1) out[out.length - 1][1] = v;
    else out.push([v, v]);
  });
  return out
    .map(([a, b]) =>
      b - a >= 2 ? `${a}-${b}` : a === b ? `${a}` : `${a},${b}`,
    )
    .join(",");
}

// ------------------------------------------------------- default minute
/**
 * The minute a new repeat starts at, from its id: stable, spread out, never
 * :00 or :30 ("Repeats in this workspace start at different minutes so they
 * don't all queue at once.").
 */
export function defaultMinute(id) {
  let h = 0x811c9dc5;
  for (const ch of String(id ?? "")) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  const minutes = Array.from({ length: 60 }, (_, i) => i).filter(
    (m) => m % 30 !== 0,
  );
  return minutes[h % minutes.length];
}
