import {
  formatUsdCost,
  minimumUsdCostFractionDigits,
} from "src/utils/format-usd-cost";
import { formatMoneyDelta } from "./money";

// One formatter for every number the Datasets + Experiments screens print
// (R3 G0 §B4, AS#9). The rules are the contract; fmt.test.js pins every
// example. Pure: no React, no I/O. Never `toFixed` in a screen: call these.
//
//   percent  fmtPct(0.866)            86.6%   (0 → 0%, 1 → 100%)
//   rate     fmtRate(59, 90)          65.6% (59/90)
//   pts      fmtPts(20)               +20.0 pts · −12.2 pts · ±0.0 pts
//   cost     fmtCost(0.0041)          $0.0041 · $0.011 · $0.21 · $6.30 · $1240.00
//   multiple fmtMultiple(0.21, 0.011) 19× · 1.7×
//   duration fmtMs(842)               842 ms · 9.8 s · 74 s · 12 m 05 s
//   tokens   fmtTokens(1017)          1,017 tok
//   score    fmtScore(0.7423)         0.74
//   count    fmtCount(1248, "row")    1,248 rows · 1 row
//   time     fmtTimeRelative(iso)     52s ago · 2h ago · 4d ago · Oct 12
//            fmtTimeAbsolute(iso)     Oct 12, 2026, 4:08 PM IST (UTC+5:30)
import { plural } from "src/sections/common/vocab";

/** The missing-aggregate glyph (cells say `Not scored` instead). */
export const EMPTY = "—";
/** U+2212, the minus sign every negative number uses. */
export const MINUS = "−";
/** sx for numeric table cells: tabular figures, right-aligned, one line. */
export const NUM_SX = Object.freeze({
  fontVariantNumeric: "tabular-nums",
  textAlign: "right",
  whiteSpace: "nowrap",
});

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const num = (v) => {
  if (isNum(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v)))
    return Number(v);
  return null;
};
// `-0.0` never reaches the screen.
const unNegZero = (s) => (/^-0(\.0+)?$/.test(s) ? s.slice(1) : s);
const sign = (v, text) =>
  v > 0 ? `+${text}` : v < 0 ? `${MINUS}${text}` : text;

// ------------------------------------------------------------ percent

/**
 * A 0–1 value as a percent, 1 dp: `86.6%`, `86.0%`; exactly 0 → `0%`,
 * exactly 1 → `100%`; missing → `—`.
 * @param {number|null|undefined} v  0–1
 * @param {{dp?: number}} [opts]
 */
export function fmtPct(v, { dp = 1 } = {}) {
  const n = num(v);
  if (n == null) return EMPTY;
  if (n === 0) return "0%";
  if (n === 1) return "100%";
  return `${unNegZero((n * 100).toFixed(dp)).replace("-", MINUS)}%`;
}

/** `65.6% (59/90)`; `—` when the total is 0 or missing. */
export function fmtRate(passed, total) {
  const p = num(passed);
  const t = num(total);
  if (p == null || t == null || t <= 0) return EMPTY;
  return `${fmtPct(p / t)} (${p}/${t})`;
}

// ------------------------------------------------------------ pts

/** Round to 1 dp without float garbage (0.1 + 0.2 → 0.3). */
const round1 = (v) => Math.round(v * 10 + Number.EPSILON * Math.sign(v)) / 10;

/**
 * Percentage points between two 0–1 rates, as the screen shows them: the
 * difference of the two DISPLAYED 1-dp percents, so `91.0%` vs `65.6%`
 * reads `+25.4 pts` (not the raw 25.46). Use it whenever the API sends no
 * `delta`; an API delta is formatted as it is.
 * @returns {number|null} points (25.4)
 */
export function ptsBetween(cand, base) {
  const c = num(cand);
  const b = num(base);
  if (c == null || b == null) return null;
  return round1(round1(c * 100) - round1(b * 100));
}

/**
 * A delta in percentage points: sign always, 1 dp, `pts`; |d| < 0.05 →
 * `±0.0 pts`; never `-0.00`, never `pp`.
 * @param {number} points  percentage points (20 = 20 pts). For a 0–1
 *   fraction delta pass `{fraction: true}` (0.2 → `+20.0 pts`).
 * @param {{fraction?: boolean}} [opts]
 */
export function fmtPts(points, { fraction = false } = {}) {
  const n = num(points);
  if (n == null) return EMPTY;
  const p = fraction ? n * 100 : n;
  if (Math.abs(p) < 0.05) return "±0.0 pts";
  return sign(p, `${Math.abs(p).toFixed(1)} pts`);
}

// ------------------------------------------------------------ cost

const groupInt = (value) =>
  Math.round(value).toLocaleString("en-US", { maximumFractionDigits: 0 });

/** Exact USD display; legacy padding is retained without a precision ceiling. */
export function fmtCost(usd) {
  return formatUsdCost(usd, EMPTY);
}

/** A signed cost delta: `+$0.20`, `−$0.0041`, `±$0.00`. */
export function fmtCostDelta(usd) {
  return formatMoneyDelta(usd, {
    minimumFractionDigits: minimumUsdCostFractionDigits(usd),
  });
}

// ------------------------------------------------------------ multiple

/**
 * `a / b` as a multiple: 1 dp below 10 (`1.7×`), whole above (`19×`).
 * One argument = an already computed ratio. `—` when undefined.
 */
export function fmtMultiple(a, b) {
  const x = num(a);
  const y = b === undefined ? 1 : num(b);
  if (x == null || y == null || y === 0) return EMPTY;
  const r = x / y;
  if (!Number.isFinite(r)) return EMPTY;
  const one = Number(r.toFixed(1));
  if (one < 10) return `${one.toFixed(1)}×`;
  return `${groupInt(r)}×`;
}

// ------------------------------------------------------------ durations

/**
 * Milliseconds: under 1 s integer ms (`842 ms`); under 10 s one decimal
 * (`9.8 s`); under 600 s whole seconds (`74 s`); else `12 m 05 s`.
 */
export function fmtMs(ms) {
  const n = num(ms);
  if (n == null) return EMPTY;
  const neg = n < 0;
  const a = Math.abs(n);
  let text;
  if (Math.round(a) < 1000) text = `${Math.round(a)} ms`;
  else if (Number((a / 1000).toFixed(1)) < 10)
    text = `${(a / 1000).toFixed(1)} s`;
  else if (Math.round(a / 1000) < 600) text = `${Math.round(a / 1000)} s`;
  else {
    const total = Math.round(a / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    text = `${m} m ${String(s).padStart(2, "0")} s`;
  }
  return neg ? `${MINUS}${text}` : text;
}

/** Seconds → the same rules (`fmtSec(74)` = `74 s`). */
export const fmtSec = (s) => {
  const n = num(s);
  return n == null ? EMPTY : fmtMs(n * 1000);
};

/**
 * A time delta (candidate − baseline, ms).
 * - `style: "signed"` (default): `+64 s`, `−1.2 s`, `±0 ms`
 * - `style: "words"`: `64 s slower`, `1.2 s faster`, `same time`
 */
export function fmtDeltaMs(deltaMs, { style = "signed" } = {}) {
  const n = num(deltaMs);
  if (n == null) return EMPTY;
  const body = fmtMs(Math.abs(n));
  if (style === "words") {
    if (Math.round(Math.abs(n)) === 0) return "same time";
    return `${body} ${n > 0 ? "slower" : "faster"}`;
  }
  if (Math.round(Math.abs(n)) === 0) return "±0 ms";
  return sign(n, body);
}

// ------------------------------------------------------------ counts

/** `1,017` (en-US grouping); decimals kept to 1 dp. `—` when missing. */
export function fmtNumber(v) {
  const n = num(v);
  if (n == null) return EMPTY;
  const digits = Number.isInteger(n) ? 0 : 1;
  const text = n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return unNegZero(text).replace("-", MINUS);
}

/** `1,017 tok`. */
export function fmtTokens(n) {
  const v = num(n);
  return v == null ? EMPTY : `${groupInt(v)} tok`;
}

/** A 0–1 score, 2 dp: `0.74`. */
export function fmtScore(v) {
  const n = num(v);
  return n == null ? EMPTY : unNegZero(n.toFixed(2)).replace("-", MINUS);
}

/**
 * `1,248 rows` / `1 row` (vocab.plural). Without a word, the grouped number.
 * @param {number} n
 * @param {string} [word]  singular
 * @param {string} [pluralWord]  explicit plural
 */
export function fmtCount(n, word, pluralWord) {
  const v = num(n);
  if (v == null) return EMPTY;
  if (!word) return groupInt(v);
  return plural(Math.round(v), word, pluralWord);
}

// ------------------------------------------------------------ time

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

const toMs = (iso) => {
  if (iso instanceof Date) return iso.getTime();
  if (isNum(iso)) return iso;
  const t = Date.parse(iso ?? "");
  return Number.isFinite(t) ? t : null;
};

/** The viewer's IANA zone (falls back to UTC). */
export const viewerTimeZone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};

function partsIn(ms, tz) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  const out = {};
  fmt.formatToParts(new Date(ms)).forEach((p) => {
    out[p.type] = p.value;
  });
  return out;
}

/**
 * Relative short time: `just now`, `52s ago`, `5m ago`, `2h ago`, `4d ago`;
 * from 30 days the date (`Oct 12`, `Oct 12, 2025` in another year).
 * Future times read `in 5m`.
 * @param {string|number|Date} iso
 * @param {{now?: number, tz?: string}} [opts]
 */
export function fmtTimeRelative(iso, { now = Date.now(), tz } = {}) {
  const t = toMs(iso);
  if (t == null) return EMPTY;
  const diff = now - t;
  const a = Math.abs(diff);
  const s = Math.floor(a / 1000);
  const wrap = (x) => (diff < 0 ? `in ${x}` : `${x} ago`);
  if (s < 10) return "just now";
  if (s < 60) return wrap(`${s}s`);
  const m = Math.floor(s / 60);
  if (m < 60) return wrap(`${m}m`);
  const h = Math.floor(m / 60);
  if (h < 24) return wrap(`${h}h`);
  const d = Math.floor(h / 24);
  if (d < 30) return wrap(`${d}d`);
  const zone = tz ?? viewerTimeZone();
  const p = partsIn(t, zone);
  const n = partsIn(now, zone);
  const date = `${MONTHS[Number(p.month) - 1]} ${p.day}`;
  return p.year === n.year ? date : `${date}, ${p.year}`;
}

/** `Oct 12` (`Oct 12, 2025` in another year), in the viewer's zone. */
export function fmtDateShort(iso, { now = Date.now(), tz } = {}) {
  const t = toMs(iso);
  if (t == null) return EMPTY;
  const zone = tz ?? viewerTimeZone();
  const p = partsIn(t, zone);
  const n = partsIn(now, zone);
  const date = `${MONTHS[Number(p.month) - 1]} ${p.day}`;
  return p.year === n.year ? date : `${date}, ${p.year}`;
}

// Zones whose Intl short name is only `GMT+x` get their common abbreviation.
const ZONE_ABBR = {
  "Asia/Kolkata": "IST",
  "Asia/Calcutta": "IST",
  "Asia/Singapore": "SGT",
  "Asia/Tokyo": "JST",
  "Asia/Dubai": "GST",
  "Australia/Sydney": null,
  "Europe/Berlin": null,
  "Europe/Paris": null,
};

function zoneLabel(ms, tz) {
  let short = "";
  let offset = "";
  try {
    short =
      new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "short" })
        .formatToParts(new Date(ms))
        .find((p) => p.type === "timeZoneName")?.value ?? "";
    offset =
      new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        timeZoneName: "longOffset",
      })
        .formatToParts(new Date(ms))
        .find((p) => p.type === "timeZoneName")?.value ?? "";
  } catch {
    return "UTC";
  }
  // "GMT+05:30" → "UTC+5:30"; "GMT" → "UTC"
  const m = /^GMT([+-])(\d{2}):(\d{2})$/.exec(offset);
  const utc =
    m && !(m[2] === "00" && m[3] === "00")
      ? `UTC${m[1]}${Number(m[2])}${m[3] === "00" ? "" : `:${m[3]}`}`
      : "UTC";
  if (utc === "UTC") return "UTC";
  const abbr = /^GMT/.test(short) ? ZONE_ABBR[tz] ?? null : short;
  return abbr ? `${abbr} (${utc})` : utc;
}

/**
 * Absolute time for tooltips: `Oct 12, 2026, 4:08 PM IST (UTC+5:30)`, plus
 * ` · by Ravi Menon` when `by` is given.
 * @param {string|number|Date} iso
 * @param {{tz?: string, by?: string|null}} [opts]  tz defaults to the viewer's
 */
export function fmtTimeAbsolute(iso, { tz, by } = {}) {
  const t = toMs(iso);
  if (t == null) return EMPTY;
  const zone = tz ?? viewerTimeZone();
  const p = partsIn(t, zone);
  const base = `${MONTHS[Number(p.month) - 1]} ${p.day}, ${p.year}, ${p.hour}:${p.minute} ${p.dayPeriod} ${zoneLabel(t, zone)}`;
  return by ? `${base} · by ${by}` : base;
}
