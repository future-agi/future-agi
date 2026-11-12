// Text of Explore's per-group numbers (W5-2 B1): the interval under a value
// and the Δ vs the baseline line. Pure.
import { fmtValue, fmtDelta } from "./workbenchFormat";
import {
  isMoneyValue,
  halfMoneyValue,
  subtractMoneyValues,
} from "src/sections/common/format/money";
const valid = (measure, v) =>
  measure?.unit === "usd" ? isMoneyValue(v) : ok(v);

const MINUS = "−";
const ok = (v) => typeof v === "number" && Number.isFinite(v);
const signed = (v, digits = 0) => {
  const r = Number(Math.abs(v).toFixed(digits));
  if (r === 0) return "0";
  return `${v > 0 ? "+" : MINUS}${r.toFixed(digits)}`;
};

/** `61–85%` for a rate interval, `$0.003–$0.005` / `0.41–0.62` otherwise. */
export function rangeText(measure, ci) {
  if (!ci || !valid(measure, ci.low) || !valid(measure, ci.high)) return null;
  if (measure?.kind === "rate")
    return `${Math.round(ci.low * 100)}–${Math.round(ci.high * 100)}%`;
  return `${fmtValue(measure, ci.low)}–${fmtValue(measure, ci.high)}`;
}

/** A mean difference in the measure's own unit, signed. */
function signedValue(measure, v) {
  if (measure?.unit === "usd")
    return fmtDelta(measure, v, { precise: true }) ?? "—";
  if (!ok(v)) return "—";
  const body = fmtValue(measure, Math.abs(v), { precise: true });
  if (/^[$]?0(\.0+)?( s)?$/.test(body)) return body;
  return `${v > 0 ? "+" : v < 0 ? MINUS : ""}${body}`;
}

/**
 * `Δ +12 pts (−3 to +27) · significant` and the tooltip sentence.
 * @param {object} measure
 * @param {{kind: "rate"|"mean", delta: number, low: number|null,
 *   high: number|null, significant: boolean, n: number}} d
 * @param {string} [baselineLabel]
 */
export function deltaLine(measure, d, baselineLabel) {
  if (!d || !valid(measure, d.delta)) return null;
  const sig = d.significant ? " · significant" : "";
  const vs = baselineLabel ? ` vs ${baselineLabel}` : " vs baseline";
  if (d.kind === "rate") {
    const range =
      ok(d.low) && ok(d.high)
        ? `${signed(d.low, 1)} to ${signed(d.high, 1)}`
        : null;
    return {
      short: `Δ ${signed(d.delta, 1)} pts${range ? ` (${range})` : ""}${sig}`,
      full: `Δ${vs} ${signed(d.delta, 1)} pts${range ? `, 95% range ${range} pts` : ""} on ${d.n} paired rows${d.significant ? ", significant (the range excludes 0)" : ", not significant"}`,
    };
  }
  const half =
    valid(measure, d.low) && valid(measure, d.high)
      ? fmtValue(
          measure,
          measure?.unit === "usd"
            ? halfMoneyValue(subtractMoneyValues(d.high, d.low))
            : (d.high - d.low) / 2,
        )
      : null;
  return {
    short: `Δ ${signedValue(measure, d.delta)}${half ? ` (±${half})` : ""}${sig}`,
    full: `Δ${vs} ${signedValue(measure, d.delta)}${half ? `, 95% interval ±${half}` : ""} on ${d.n} paired rows${d.significant ? ", significant (the interval excludes 0)" : ", not significant"}`,
  };
}
