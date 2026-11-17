import {
  formatExactMoney,
  formatMoneyDelta,
} from "src/sections/common/format/money";
// Number formatting for the Workbench (quality bar: `61%` in cells, `61.1%
// (11/18)` in tooltips, money `$0.004`, durations `2.9 s` / `74 s` /
// `2 min`, missing aggregates `—`). Pure.
export const EMPTY = "—";
const MINUS = "−";
const ok = (v) => typeof v === "number" && Number.isFinite(v);

const moneyMinimum = (v) => {
  const a = Math.abs(Number(v));
  return a > 0 && a < 0.001 ? 0 : a < 0.01 ? 3 : 2;
};
export function fmtMoney(v) {
  return formatExactMoney(v, { minimumFractionDigits: moneyMinimum(v) });
}

/** Exact monetary ticks; no inaccessible rounded amount in an axis label. */
export function fmtTick(measure, v) {
  return fmtValue(measure, v);
}

export function fmtSeconds(v) {
  if (!ok(v)) return EMPTY;
  const a = Math.abs(v);
  if (a < 10) return `${a.toFixed(1)} s`;
  if (a < 120) return `${Math.round(a)} s`;
  return `${Math.round(a / 60)} min`;
}

const trim = (x, d) => {
  const s = x.toFixed(d);
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
};

/**
 * A Workbench value. `precise` = tooltips (1 decimal on rates).
 * @param {{kind: string, unit?: string}} measure
 */
export function fmtValue(measure, v, { precise = false } = {}) {
  if (measure?.unit === "usd") return fmtMoney(v);
  if (!ok(v)) return EMPTY;
  const kind = measure?.kind;
  if (kind === "rate")
    return precise ? `${(v * 100).toFixed(1)}%` : `${Math.round(v * 100)}%`;
  if (kind === "count") return Math.round(v).toLocaleString("en-US");
  if (kind === "score") return v.toFixed(2);
  if (measure?.unit === "s") return fmtSeconds(v);
  return trim(v, precise ? 2 : 1);
}

/** Cell delta vs baseline: `+39` (pts) for rates, signed units otherwise. */
export function fmtDelta(measure, d, { precise = false } = {}) {
  if (measure?.unit === "usd")
    return formatMoneyDelta(d, {
      zeroSign: "",
      missing: null,
      minimumFractionDigits: moneyMinimum(d),
    });
  if (!ok(d)) return null;
  const sign = d > 0 ? "+" : d < 0 ? MINUS : "";
  if (measure?.kind === "rate") {
    const pts = Math.abs(d * 100);
    return precise
      ? `${sign}${pts.toFixed(1)} pts`
      : `${Math.round(pts) === 0 ? "" : sign}${Math.round(pts)}`;
  }
  return `${sign}${fmtValue(measure, Math.abs(d), { precise })}`;
}

/** `61.1% (11/18)` / `$0.004 · n 90` for tooltips. */
export function fmtCellTip(measure, cell) {
  if (
    !cell ||
    (measure?.unit === "usd" ? fmtMoney(cell.value) === EMPTY : !ok(cell.value))
  )
    return "No scored attempts";
  if (measure?.kind === "rate" && cell.pass != null)
    return `${fmtValue(measure, cell.value, { precise: true })} (${cell.pass}/${cell.n})`;
  return `${fmtValue(measure, cell.value, { precise: true })} · ${cell.n.toLocaleString("en-US")} attempts`;
}

/** `95% CI 72–99%` for rates, `95% CI $0.003–$0.005` otherwise. */
export function fmtCi(measure, ci) {
  if (
    !ci ||
    (measure?.unit === "usd"
      ? fmtMoney(ci.low) === EMPTY || fmtMoney(ci.high) === EMPTY
      : !ok(ci.low) || !ok(ci.high))
  )
    return null;
  if (measure?.kind === "rate")
    return `95% CI ${Math.round(ci.low * 100)}–${Math.round(ci.high * 100)}%`;
  return `95% CI ${fmtValue(measure, ci.low)}–${fmtValue(measure, ci.high)}`;
}

/** Tone of a delta: good / bad / neutral by the measure's direction. */
export function deltaToneOf(measure, d) {
  if (!ok(d) || Math.abs(d) < 1e-9) return "neutral";
  if (measure?.kind === "rate" && Math.round(Math.abs(d) * 100) === 0)
    return "neutral";
  if (
    measure?.kind !== "rate" &&
    /^[$]?0(\.0+)?( s)?$/.test(fmtValue(measure, Math.abs(d)))
  )
    return "neutral";
  const up = d > 0;
  return measure?.higherIsBetter === false
    ? up
      ? "bad"
      : "good"
    : up
      ? "good"
      : "bad";
}

export const TONE = {
  good: "green.600",
  bad: "red.600",
  neutral: "text.secondary",
};
