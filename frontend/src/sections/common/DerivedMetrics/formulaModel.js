import { formatExactMoney } from "src/sections/common/format/money";
// Run metrics (W5-4 B2, AG-12): formulas over a run's numbers, written in
// the ONE expression language (common/filters/expression.js). Pure. The
// formula scope is a run's aggregates:
//   <eval>        share of rows passing (pass/fail; a row passes when at
//                 least half its tries pass) or its mean score over rows
//   <eval>.n      rows it scored
//   cost_usd      mean cost per row · latency_p50 / latency_p90 (ms)
//   tokens        mean tokens per row · rows · errors · error_rate
// plus min(), max(), abs(), round(). The mock (w5DerivedMetrics.js) feeds the
// R1 aggregates in; the dialog and the API share this one evaluator.
import {
  compileExpression,
  evaluateExpression,
} from "src/sections/common/filters/expression";
import { fieldName } from "src/sections/common/filters/catalogFields";

export const METRIC_FORMATS = Object.freeze([
  { key: "percent", label: "Percent" },
  { key: "number", label: "Number" },
  { key: "usd", label: "Money (USD)" },
  { key: "ms", label: "Milliseconds" },
]);
export const FORMULA_FUNCTIONS = Object.freeze(["min", "max", "abs", "round"]);
const OTHER_FUNCTIONS = ["len", "lower", "upper", "coalesce"];
const NAME_RE = /^[a-z][a-z0-9_]{0,39}$/;

export const RUN_FIELDS = Object.freeze([
  { name: "cost_usd", read: "cost_usd", doc: "Mean cost per row (USD)" },
  { name: "latency_p50", read: "latency_p50", doc: "Median latency (ms)" },
  {
    name: "latency_p90",
    read: "latency_p90",
    doc: "90th percentile latency (ms)",
  },
  { name: "tokens", read: "tokens", doc: "Mean tokens per row" },
  { name: "rows", read: "rows", doc: "Rows the run covers" },
  { name: "errors", read: "errors", doc: "Rows that errored" },
  { name: "error_rate", read: "error_rate", doc: "Share of rows that errored" },
]);

/** The eval defs a formula can name (R1 metric_defs of kind eval). */
export const formulaEvals = (metricDefs = []) =>
  metricDefs.filter(
    (d) => d?.kind === "eval" && ["pass_fail", "score"].includes(d.output_type),
  );

/**
 * L7c g6 (metric:new, RELABEL): what an eval field reads, exactly. A
 * pass/fail eval reads the share of ROWS that pass, a row passing when at
 * least half of its tries pass; on a multi-try run that is not the per-try
 * pass rate the Summary and Matrix show (59 of 90 tries = 65.6% vs 20 of 30
 * rows = 0.667), so the help says so.
 */
export function evalFieldWhat(def) {
  if (def?.output_type === "pass_fail")
    return "share of rows that pass (a row passes when at least half of its tries pass; not the per-try pass rate)";
  return "mean score over rows (each row's mean over its tries)";
}

/** The catalogue doc of an eval field: `<eval name>: <what it reads>`. */
export const evalFieldDoc = (def) => `${def?.name}: ${evalFieldWhat(def)}`;

/** `{name, what}` for every eval field a formula can name (dialog help). */
export function evalFieldDocs(metricDefs = []) {
  return formulaEvals(metricDefs).map((def) => ({
    name: fieldName(def.name ?? def.key),
    what: evalFieldWhat(def),
  }));
}

/** The formula catalogue (the expression language's catalogue shape). */
export function formulaCatalog(metricDefs = []) {
  const f = (name, read, doc) => ({
    name,
    scope: "row",
    type: "number",
    read,
    doc,
  });
  const fields = RUN_FIELDS.map((x) => f(x.name, { k: x.read }, x.doc));
  formulaEvals(metricDefs).forEach((def) => {
    const n = fieldName(def.name ?? def.key);
    fields.push(
      f(n, { k: "metric", key: def.key }, evalFieldDoc(def)),
      f(`${n}.n`, { k: "metric_n", key: def.key }, `${def.name} rows scored`),
    );
  });
  return {
    kind: "run_metrics",
    fields,
    prefixes: [],
    runs: [],
    baselineId: null,
    custom: [],
  };
}

const usedCalls = (node, out = []) => {
  if (!node || typeof node !== "object") return out;
  if (node.t === "call") out.push(node);
  Object.values(node).forEach((v) => {
    if (Array.isArray(v)) v.forEach((x) => usedCalls(x, out));
    else if (v && typeof v === "object") usedCalls(v, out);
  });
  return out;
};

/** {ast, refs, catalog} or {error: {message, at, length}}. */
export function compileFormula(formula, metricDefs = []) {
  const text = String(formula ?? "").trim();
  if (!text)
    return { error: { message: "Write a formula.", at: 0, length: 1 } };
  const catalog = formulaCatalog(metricDefs);
  const compiled = compileExpression(text, catalog);
  if (compiled.error) return compiled;
  const bad = usedCalls(compiled.ast).find((c) =>
    OTHER_FUNCTIONS.includes(String(c.name ?? c.fn ?? "").toLowerCase()),
  );
  if (bad)
    return {
      error: {
        message: `A run metric uses ${FORMULA_FUNCTIONS.join(", ")} only.`,
        at: bad.at ?? 0,
        length: bad.length ?? 1,
      },
    };
  return { ...compiled, catalog };
}

/** The field values a formula reads, from a run's aggregates. */
export function readAggregate(agg, read) {
  if (!agg) return null;
  switch (read.k) {
    case "metric":
      return agg.metrics?.[read.key]?.value ?? null;
    case "metric_n":
      return agg.metrics?.[read.key]?.n ?? null;
    default:
      return agg[read.k] ?? null;
  }
}

/** Names the formula reads (each once, in catalogue order). */
export function refNames(compiled) {
  const names = new Set();
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.t === "field" && Array.isArray(node.path))
      names.add(node.path.join("."));
    Object.values(node).forEach((v) =>
      Array.isArray(v)
        ? v.forEach(walk)
        : v && typeof v === "object" && walk(v),
    );
  };
  walk(compiled.ast);
  return compiled.catalog.fields
    .map((x) => x.name)
    .filter((n) => [...names].some((m) => m.toLowerCase() === n));
}

/** {value, parts: {name: value}} of a compiled formula on one run. */
export function evaluateFormula(compiled, agg) {
  const scope = {
    catalog: compiled.catalog,
    runIds: [],
    baselineId: null,
    readRow: (field) => readAggregate(agg, field.read),
    readRun: () => null,
  };
  const raw = evaluateExpression(compiled.ast, scope);
  const value = typeof raw === "number" && Number.isFinite(raw) ? raw : null;
  const parts = Object.fromEntries(
    refNames(compiled).map((n) => {
      const fld = compiled.catalog.fields.find((x) => x.name === n);
      return [n, readAggregate(agg, fld.read)];
    }),
  );
  return { value, parts };
}

/** A definition's errors: {name?, formula?} (empty when it can be saved). */
export function definitionErrors(def, metricDefs, others = []) {
  const errors = {};
  const name = String(def?.name ?? "").trim();
  if (!NAME_RE.test(name))
    errors.name =
      "Use lowercase letters, digits and _ (start with a letter, max 40).";
  else if (
    others.some((o) => o.name === name && (o.id == null || o.id !== def.id))
  )
    errors.name = `A run metric named “${name}” already exists.`;
  else if (formulaCatalog(metricDefs).fields.some((x) => x.name === name))
    errors.name = `“${name}” is already a field; pick another name.`;
  const compiled = compileFormula(def?.formula, metricDefs);
  if (compiled.error) errors.formula = compiled.error.message;
  if (!METRIC_FORMATS.some((x) => x.key === def?.format))
    errors.format = "Pick a format.";
  return errors;
}

const trimNum = (v, d) => {
  const s = Number(v).toFixed(d);
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
};

/** A value in a metric's format: `71.2%`, `0.71`, `$0.0031`, `1,960 ms`. */
export function fmtMetric(v, format) {
  if (format === "usd") return formatExactMoney(v).replace(/^-/, "−");
  if (v == null || !Number.isFinite(v)) return "—";
  if (format === "percent") return `${(v * 100).toFixed(1)}%`;
  if (format === "ms") return `${Math.round(v).toLocaleString("en-US")} ms`;
  const a = Math.abs(v);
  return trimNum(v, a >= 100 ? 0 : a >= 1 ? 2 : 4).replace("-", "−");
}

const partText = (v) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a !== 0 && a < 0.01) return String(Number(v.toPrecision(2)));
  return trimNum(v, a >= 100 ? 0 : 3);
};

/**
 * `0.71 = 0.7 × 0.82 + 0.3 × 0.45 − 10 × 0.0031`: the formula with each
 * field replaced by the run's value (the longest names first).
 */
export function partsLine(formula, { value, parts }, format) {
  const names = Object.keys(parts).sort((a, b) => b.length - a.length);
  let text = String(formula ?? "");
  const marks = [];
  names.forEach((n, i) => {
    const re = new RegExp(
      `(?<![A-Za-z0-9_.])${n.replace(/\./g, "\\.")}(?![A-Za-z0-9_])`,
      "gi",
    );
    text = text.replace(re, `\u27e6${i}\u27e7`);
    marks[i] = partText(parts[n]);
  });
  text = text
    .replace(/\u27e6(\d+)\u27e7/g, (_, i) => marks[Number(i)])
    .replace(/\s*\*\s*/g, " × ")
    .replace(/\s+-\s+/g, " − ")
    .replace(/\s*\/\s*/g, " ÷ ");
  return `${fmtMetric(value, format)} = ${text}`;
}

/** Best and worst run of a metric by its direction. */
export function rankRuns(values, metric) {
  const xs = Object.entries(values ?? {})
    .map(([run, v]) => [run, v?.[metric.id]?.value])
    .filter(([, v]) => v != null);
  if (xs.length < 2) return { best: null, worst: null };
  const sorted = [...xs].sort((a, b) =>
    metric.higher_is_better === false ? a[1] - b[1] : b[1] - a[1],
  );
  return { best: sorted[0][0], worst: sorted[sorted.length - 1][0] };
}
