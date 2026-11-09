// Metric display rules (PRODUCT-SPEC §4, C5, D4). The frontend never
// recomputes movement or significance; it formats the API's fields.
//
// A metric def is the R1 `metric_defs[]` item:
//   { key, name, kind: "eval"|"system"|"agent"|"human"|"preference",
//     output_type: "pass_fail"|"score"|"choice"|"ms"|"usd"|"count"|
//                  "thumbs"|"stars"|"pairwise",
//     higher_is_better: true|false|null, pass_threshold?, min?, max? }
//
// R3 (G0, AS#9): every number goes through common/format/fmt.js, the one
// formatter. These exports keep their names and signatures and delegate.
import { interpolateColorBasedOnScore } from "src/utils/utils";
import {
  formatExactMoney,
  moneySign,
  roundMoneyPercent,
} from "src/sections/common/format/money";
import {
  EMPTY,
  MINUS as FMT_MINUS,
  fmtCost,
  fmtCostDelta,
  fmtDeltaMs,
  fmtMs,
  fmtNumber,
  fmtPct,
  fmtPts,
  fmtScore,
} from "src/sections/common/format/fmt";

/** Fewer shared rows than this get a grey `n=20` suffix (C5). */
export const LOW_SAMPLE_N = 30;

/** The movement rule, word for word, for tooltips (area-results §0, C4). */
export const MOVEMENT_RULE_TEXT =
  "A row moves when a pass/fail result flips, a score changes by more than 0.05, a label value changes, or latency, cost, tokens or step counts change by 10% or more. Trials are averaged first. Rows without a value on both runs are not comparable and are not counted.";

export const EMPTY_VALUE = EMPTY;
const MINUS = FMT_MINUS;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** Kinds whose values are rates on 0–1 and are shown as percentages. */
const isRateType = (t) =>
  t === "pass_fail" || t === "thumbs" || t === "pairwise";

const pct = (v) => fmtPct(v);

const fmtCount = (v, agg) => {
  if (Number.isInteger(v) || agg === "sum") return fmtNumber(Math.round(v));
  return fmtNumber(v);
};

/**
 * Format a value or an aggregate.
 * @param {object} def metric def
 * @param {number|string|null} v
 * @param {{agg?: "avg"|"median"|"pass_rate"|"p50"|"p99"|"sum"|"value"}} [opts]
 *   agg "value" = one row's value (pass/fail 1 -> "Pass", 0 -> "Fail")
 * @returns {string} `84%`, `0.82`, `1.84 s`, `$0.0023`, `412`, or "—"
 */
export function formatValue(def, v, { agg } = {}) {
  if (v == null || v === "") return EMPTY_VALUE;
  if (def?.output_type === "usd" && agg !== "na_rate") return fmtCost(v);
  if (typeof v === "string") return v;
  if (typeof v === "boolean") return v ? "Pass" : "Fail";
  if (!isNum(v)) return EMPTY_VALUE;
  // W1-4: an N/A rate is a share of rows, whatever the eval's type; it
  // says what it is, so it never reads as a pass rate.
  if (agg === "na_rate") return `${pct(v)} N/A`;
  const type = def?.output_type;
  switch (type) {
    case "pass_fail":
      if (agg === "value" && (v === 0 || v === 1)) return v ? "Pass" : "Fail";
      return pct(v);
    case "thumbs":
      if (agg === "value" && (v === 0 || v === 1)) return v ? "Yes" : "No";
      return pct(v);
    case "pairwise":
      return pct(v);
    case "score":
      return agg === "pass_rate" ? pct(v) : fmtScore(v);
    case "stars":
      return fmtNumber(v);
    case "ms":
      return fmtMs(v);
    case "usd":
      return fmtCost(v);
    case "count":
      return fmtCount(v, agg);
    default:
      return Number.isInteger(v) ? String(v) : fmtScore(v);
  }
}

const signed = (text, d) =>
  d > 0 ? `+${text}` : d < 0 ? `${MINUS}${text}` : text;

/**
 * Format a delta (candidate − baseline, in the metric's units).
 * Rates are percentage points; latency in seconds; cost and tokens become a
 * relative % when `base` (the baseline value) is given. `agg: "pass_rate"`
 * makes a score delta percentage points.
 * @returns {string} `+7.1 pts`, `−0.04`, `+38%`, `+0.8 s`, or "—"
 */
export function formatDelta(def, d, { base, agg } = {}) {
  if (def?.output_type === "usd") {
    if (moneySign(base) == null || moneySign(base) === 0)
      return fmtCostDelta(d);
    const sign = moneySign(d);
    if (sign == null) return EMPTY_VALUE;
    const absolute = (value) =>
      formatExactMoney(value, { prefix: "", minimumFractionDigits: 0 }).replace(
        /^-/,
        "",
      );
    const percent = roundMoneyPercent(absolute(d), absolute(base));
    return percent == null ? EMPTY_VALUE : signed(`${percent}%`, sign);
  }
  if (!isNum(d)) return EMPTY_VALUE;
  const a = Math.abs(d);
  const type = def?.output_type;
  if (isRateType(type) || (type === "score" && agg === "pass_rate"))
    return fmtPts(d, { fraction: true });
  switch (type) {
    case "score":
      return signed(fmtScore(a), d);
    case "stars":
      return signed(fmtNumber(a), d);
    case "ms":
      return fmtDeltaMs(d);
    case "count":
      if (isNum(base) && base !== 0 && def?.kind === "system")
        return signed(`${Math.round((a / Math.abs(base)) * 100)}%`, d);
      return signed(fmtNumber(a), d);
    default:
      return signed(fmtScore(a), d);
  }
}

/** "±3.8 pts", "±0.02", "±0.3 s" for a CI half-width; "" when unknown. */
export function formatCi(def, ci95) {
  if (def?.output_type === "usd") {
    const text = fmtCostDelta(ci95);
    return text === EMPTY_VALUE ? "" : `±${text.replace(/^[+±−]/, "")}`;
  }
  if (!isNum(ci95)) return "";
  return `±${formatDelta(def, Math.abs(ci95)).replace(/^[+±]/, "")}`;
}

/**
 * Tone of a delta.
 * - "noise": `significant === false` (grey, no glyph)
 * - "neutral": no direction, zero or missing
 * - "good" / "bad": follows higher_is_better
 * @returns {"good"|"bad"|"neutral"|"noise"}
 */
export function deltaTone(def, d, { significant } = {}) {
  if (!isNum(d) || d === 0) return "neutral";
  if (significant === false) return "noise";
  const hib = def?.higher_is_better;
  if (hib == null) return "neutral";
  return (hib ? d > 0 : d < 0) ? "good" : "bad";
}

/** Theme colour for a tone (sx-ready palette path). */
// GS-01 (options-truth L3 G10): green text at ≥ 4.5:1 — green.700
// (#007C3E, 5.3:1 on white) in light, green.400 on the dark background.
export const TONE_COLOR = {
  good: (t) =>
    t.palette.mode === "dark" ? t.palette.green[400] : t.palette.green[700],
  bad: "red.600",
  neutral: "text.secondary",
  noise: "text.disabled",
};

/** Value normalised to 0–1 for heat, or null when the metric has no heat. */
export function normalizeForHeat(def, v) {
  if (!isNum(v)) return null;
  const type = def?.output_type;
  if (type === "pass_fail" || type === "score" || type === "thumbs")
    return Math.max(0, Math.min(1, v));
  if (type === "stars") {
    const min = def?.min ?? 1;
    const max = def?.max ?? 5;
    return max > min ? Math.max(0, Math.min(1, (v - min) / (max - min))) : null;
  }
  return null;
}

/**
 * Cell heat: the eval-cell scale (`--score-*`, already tinted) through
 * interpolateColorBasedOnScore, reversed when lower is better. Latency,
 * cost, counts and direction-less metrics get no heat (null).
 */
export function heatColor(def, v) {
  if (def?.higher_is_better == null) return null;
  const norm = normalizeForHeat(def, v);
  if (norm == null) return null;
  return interpolateColorBasedOnScore(norm * 100, 100, !def.higher_is_better);
}

/** Display word for a movement direction. */
export const MOVEMENT_LABEL = {
  improved: "improved",
  regressed: "regressed",
  unchanged: "unchanged",
  not_comparable: "not comparable",
};

/**
 * The header/DeltaBadge tooltip line (M6):
 * "Within noise (95% CI ±3.8 pts) · 14 improved · 7 regressed ·
 *  215 unchanged · 12 not comparable · Partial · 142 of 248 rows"
 */
export function movementSummaryText(
  def,
  {
    ci95,
    significant,
    improved,
    regressed,
    unchanged,
    notComparable,
    n,
    partial,
    done,
    total,
  } = {},
) {
  const parts = [];
  const ci = formatCi(def, ci95);
  if (significant === false)
    parts.push(ci ? `Within noise (95% CI ${ci})` : "Within noise");
  else if (significant === true && ci) parts.push(`95% CI ${ci}`);
  if (isNum(improved)) parts.push(`${improved} improved`);
  if (isNum(regressed)) parts.push(`${regressed} regressed`);
  if (isNum(unchanged)) parts.push(`${unchanged} unchanged`);
  if (isNum(notComparable)) parts.push(`${notComparable} not comparable`);
  if (isNum(n) && n < LOW_SAMPLE_N)
    parts.push(`Low sample: fewer than ${LOW_SAMPLE_N} rows`);
  if (partial)
    parts.push(
      isNum(done) && isNum(total)
        ? `Partial · ${done} of ${total} rows`
        : "Partial",
    );
  return parts.join(" · ");
}
