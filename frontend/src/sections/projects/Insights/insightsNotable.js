import {
  formatCost,
  formatLatency,
  formatPercent,
  formatSentiment,
  humanizeKey,
} from "./insightsFormat";

// A "notable" difference is a statistic where a category differs from the rest
// of the report and the difference survives a false-discovery correction
// across every comparison the report made (Benjamini–Hochberg q < 0.1).
//   rate:   {kind: "rate", metric, label, value, rest, q}  (shares 0..1)
//   median: {kind: "median", metric, label, value, rest, q}

// Which way is bad, per metric. Attributes have no known direction.
const HIGHER_IS_WORSE = new Set([
  "failure",
  "unresolved",
  "negative",
  "error",
  "escalated",
  "latency_ms",
  "turns",
  "cost",
]);
const HIGHER_IS_BETTER = new Set(["sentiment"]);

const SHORT_LABEL = {
  failure: "Failure",
  unresolved: "Unresolved",
  negative: "Negative",
  error: "Errors",
  escalated: "Escalated",
  latency_ms: "Latency",
  turns: "Turns",
  sentiment: "Sentiment",
  cost: "Cost",
};

/** "worse" | "better" | "neutral" for the category against the rest. */
export function notableDirection(note) {
  if (!note || note.value === note.rest) return "neutral";
  const higher = note.value > note.rest;
  if (HIGHER_IS_WORSE.has(note.metric)) return higher ? "worse" : "better";
  if (HIGHER_IS_BETTER.has(note.metric)) return higher ? "better" : "worse";
  return "neutral";
}

function formatValue(note, value) {
  if (note.kind === "rate") return formatPercent(value);
  if (note.metric === "latency_ms") return formatLatency(value);
  if (note.metric === "cost") return formatCost(value);
  if (note.metric === "sentiment") return formatSentiment(value);
  if (note.metric === "turns")
    return Number.isInteger(value) ? String(value) : value.toFixed(1);
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** One plain-language sentence, e.g. "100% had an agent failure vs 59% elsewhere". */
export function notableText(note) {
  const value = formatValue(note, note.value);
  const rest = formatValue(note, note.rest);
  if (note.kind === "rate")
    return `${value} ${note.label} vs ${rest} elsewhere`;
  if (note.metric === "latency_ms")
    return `median latency ${value} vs ${rest} elsewhere`;
  if (note.metric === "turns")
    return `median ${value} turns vs ${rest} elsewhere`;
  if (note.metric === "sentiment")
    return `median sentiment ${value} vs ${rest} elsewhere`;
  if (note.metric === "cost")
    return `median cost ${value} vs ${rest} elsewhere`;
  return `${note.label} ${value} vs ${rest} elsewhere`;
}

/** Compact chip text for the table, e.g. "Unresolved 0% vs 59%". */
export function notableShort(note) {
  const label = SHORT_LABEL[note.metric] || humanizeKey(note.metric);
  return `${label} ${formatValue(note, note.value)} vs ${formatValue(note, note.rest)}`;
}

export function formatQ(q) {
  if (!Number.isFinite(q)) return "–";
  if (q < 0.001) return "< 0.001";
  return q < 0.01 ? q.toFixed(3) : q.toFixed(2);
}

// q under 0.05 is a clear result; 0.05–0.1 passes the report's 10% bar but is
// worth checking against the examples.
export const isBorderline = (note) => Number(note?.q) >= 0.05;

export function notableTooltip(note) {
  const q = formatQ(note.q);
  const base = `Holds after correcting for every comparison in this report (q ${q.startsWith("<") ? q : `= ${q}`}).`;
  return isBorderline(note)
    ? `${base} Borderline: check it against the examples.`
    : base;
}

// Strongest first: clear before borderline, then by q.
export const sortNotable = (notes = []) =>
  [...notes].sort((a, b) => (a.q ?? 1) - (b.q ?? 1));
