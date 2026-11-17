// Compare two runs by a key (W5-6 B7): the dialog's words, number formats
// and the largest drops. Pure. Every number comes from the mock's w5Match.js.
import { fmtMoney } from "src/sections/common/Analysis/workbench/workbenchFormat";

const MINUS = "−";

export const runName = (side) =>
  side
    ? `${side.label}${side.dataset_name ? ` · ${side.dataset_name}` : ""}`
    : "";

/** The two runs' names; a label both runs carry (each dataset counts from
 * #1A) gets its dataset's name, so `only in #1A` is never ambiguous. */
export function sideLabels(m) {
  return m.a.label === m.b.label
    ? { a: runName(m.a), b: runName(m.b) }
    : { a: m.a.label, b: m.b.label };
}

/** `238 rows matched · 3 only in #2B · 0 only in #1A · 2 duplicate keys in #2B (first kept)` */
export function summaryLine(m) {
  const { a, b } = sideLabels(m);
  const parts = [
    `${m.matched} rows matched`,
    `${m.only_b} only in ${b}`,
    `${m.only_a} only in ${a}`,
  ];
  if (m.duplicates_b)
    parts.push(`${m.duplicates_b} duplicate keys in ${b} (first kept)`);
  if (m.duplicates_a)
    parts.push(`${m.duplicates_a} duplicate keys in ${a} (first kept)`);
  if (m.no_key_b) parts.push(`${m.no_key_b} rows in ${b} have no key`);
  if (m.no_key_a) parts.push(`${m.no_key_a} rows in ${a} have no key`);
  return parts.join(" · ");
}

const kindOf = (metric) =>
  metric.key === "ops:cost"
    ? "usd"
    : metric.key === "ops:latency"
      ? "ms"
      : metric.key === "ops:tokens"
        ? "count"
        : metric.output_type === "pass_fail"
          ? "rate"
          : "score";

/** A metric's value: `86.6%`, `0.78`, `$0.0031`, `1,960 ms`, `332`. */
export function fmtMatchValue(metric, v) {
  if (kindOf(metric) === "usd") return fmtMoney(v);
  if (v == null || !Number.isFinite(v)) return "—";
  const k = kindOf(metric);
  if (k === "rate") return `${(v * 100).toFixed(1)}%`;
  if (k === "usd") return fmtMoney(v);
  if (k === "ms") return `${Math.round(v).toLocaleString("en-US")} ms`;
  if (k === "count") return Math.round(v).toLocaleString("en-US");
  return v.toFixed(2);
}

/** `+7.1 pts ± 4.0` / `+0.082 ± 0.012` / `+$0.0022 ± $0.000033`. */
export function fmtMatchDelta(metric, d, ci) {
  if (kindOf(metric) === "usd") {
    const text = fmtMoney(d);
    if (text === "—") return text;
    const sign = text.startsWith("-") ? MINUS : /[1-9]/.test(text) ? "+" : "";
    const half = fmtMoney(ci);
    return `${sign}${text.replace(/^-/, "")}${half === "—" ? "" : ` ± ${half.replace(/^-/, "")}`}`;
  }
  if (d == null || !Number.isFinite(d)) return "—";
  const sign = d > 0 ? "+" : d < 0 ? MINUS : "";
  const k = kindOf(metric);
  const abs = Math.abs(d);
  const one = (x) =>
    k === "rate"
      ? `${(x * 100).toFixed(1)}`
      : k === "usd"
        ? fmtMoney(x)
        : k === "ms"
          ? `${Math.round(x)} ms`
          : k === "count"
            ? x.toFixed(1)
            : x.toFixed(3);
  const unit = k === "rate" ? " pts" : "";
  const half = ci != null && Number.isFinite(ci) ? ` ± ${one(ci)}${unit}` : "";
  return `${sign}${one(abs)}${unit}${half}`;
}

/** Better / worse / no clear change, by the metric's own direction. */
export function verdictOf(metric) {
  if (!metric.significant || metric.direction == null) return "no clear change";
  return metric.direction === "better" ? "better" : "worse";
}

/** The matched rows where B dropped most on `metric` (worst first). */
export function topDrops(rows, metric, limit = 10) {
  const hib = metric?.higher_is_better !== false;
  return rows
    .filter((r) => typeof r.delta?.[metric.key] === "number")
    .filter((r) => (hib ? r.delta[metric.key] < 0 : r.delta[metric.key] > 0))
    .sort((x, y) =>
      hib
        ? x.delta[metric.key] - y.delta[metric.key]
        : y.delta[metric.key] - x.delta[metric.key],
    )
    .slice(0, limit);
}

/** `Column customer_message`, `Input hash`, `Row id`. */
export const matchByLabel = (v) =>
  v === "input_hash" ? "Input hash" : v === "row_id" ? "Row id" : `Column ${v}`;
