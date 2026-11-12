// W5-1 A2 · what the results door of `Filter by expression` shows, built
// from the runs, metrics and rows in view (never fixed text): the
// catalogue, value hints with counts, examples and quick picks. Pure.
import { fieldName, quoteName, resultsCatalog } from "./fieldCatalog";
import { expressionPill } from "./expressionPill";

const runIdOf = (r) => r.run_id ?? r.id;

/** The eval the examples speak about: the first pass/fail eval, else the first eval. */
export function primaryEval(metrics = []) {
  const evals = metrics.filter((m) => !m.kind || m.kind === "eval");
  return evals.find((m) => m.output_type === "pass_fail") ?? evals[0] ?? null;
}

/** The p-th quantile (0..1) of numbers, linear between ranks; null when empty. */
export function quantile(xs, p) {
  const v = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const i = (v.length - 1) * p;
  const lo = Math.floor(i);
  const hi = Math.ceil(i);
  return v[lo] + (v[hi] - v[lo]) * (i - lo);
}

/** The FE results catalogue for a view, the R3 rows in it and the dataset's custom columns. */
export function resultsCatalogForView(
  view = {},
  rows = [],
  customColumns = [],
) {
  const first = rows.find((r) => r?.inputs) ?? {};
  const inputs = Object.keys(first.inputs ?? {}).map((name) => ({ name }));
  const expected = Object.keys(first.expected?.value ?? {}).map((name) => ({
    name,
  }));
  const metadataKeys = [
    ...new Set([
      ...(view.metadataKeys ?? []),
      ...rows.flatMap((r) => Object.keys(r.metadata ?? {})),
    ]),
  ];
  const humanLabels = (view.humanMetrics ?? []).map((m) => ({
    id: String(m.key).replace(/^human:/, ""),
    name: m.name ?? m.key,
  }));
  return resultsCatalog({
    runs: (view.runs ?? [])
      .filter((r) => !r.not_found)
      .map((r) => ({ run_id: runIdOf(r), label: r.label })),
    baselineId: view.baselineId ?? null,
    metricDefs: (view.metrics ?? []).filter(
      (m) => !m.kind || m.kind === "eval",
    ),
    inputColumns: inputs,
    expectedColumns: expected,
    metadataKeys,
    humanLabels,
    customColumns,
  });
}

/** Autocomplete values with counts: metadata from the rows, split names. */
export function valueHints(view = {}, rows = []) {
  const values = {};
  const metaKeys = new Set([
    ...(view.metadataKeys ?? []),
    ...rows.flatMap((r) => Object.keys(r.metadata ?? {})),
  ]);
  metaKeys.forEach((k) => {
    const counts = new Map();
    rows.forEach((r) => {
      const v = r.metadata?.[k];
      if (v == null || v === "") return;
      counts.set(v, (counts.get(v) ?? 0) + 1);
    });
    values[`metadata.${k}`] = [...counts]
      .sort((a, b) => b[1] - a[1])
      .map(([value, count]) => ({ value, count }));
  });
  values.split = (view.splits ?? []).map((s) => ({
    value: s.name,
    count: s.row_count,
  }));
  return { values };
}

/**
 * Examples from the runs in view (W5-SCOPE §5 A2): movement, run vs
 * baseline, outputs present, cost above the view's p90.
 */
export function resultsExamples(view = {}, rows = []) {
  const runs = (view.runs ?? []).filter((r) => !r.not_found);
  const base = runs.find((r) => runIdOf(r) === view.baselineId) ?? null;
  const cand =
    runs.find((r) => runIdOf(r) === view.candidateId) ??
    runs.find((r) => runIdOf(r) !== view.baselineId) ??
    null;
  const ev = primaryEval(view.metrics);
  const n = ev ? quoteName(fieldName(ev.name ?? ev.key)) : null;
  const out = [];
  if (cand && base && n && runs.length >= 2) {
    out.push({
      label: `Rows where ${cand.label} regressed on ${ev.name}`,
      expr: `${cand.label}.${n}.movement = regressed`,
    });
    out.push({
      label: `${cand.label} scores below ${base.label}`,
      expr: `${cand.label}.${n} < ${base.label}.${n}`,
    });
  }
  if (runs.length)
    out.push({
      label: "Some run has an output",
      expr: "not (all.output is empty)",
    });
  const costs = rows
    // R3 wire rows carry per-run results under `results`.
    .flatMap((r) =>
      runs.map((run) => (r.results ?? r.runs)?.[runIdOf(run)]?.cost),
    )
    .filter((c) => Number.isFinite(c));
  const p90 = quantile(costs, 0.9);
  if (p90 != null && p90 > 0) {
    const shown = Number(p90.toPrecision(2));
    out.push({
      label: "Cost above the view's p90",
      expr:
        runs.length >= 2 ? `any.cost_usd > ${shown}` : `cost_usd > ${shown}`,
    });
  }
  return out;
}

/** L7c g1 (expr:results): the results door's placeholder when no run is in view. */
export const RESULTS_PLACEHOLDER = "Type an expression over the runs in view";

/**
 * L7c g1 (expr:results): the input's placeholder is the page's own first
 * example (it names this page's runs and evals, so typed as written it
 * parses and counts), never a constant naming runs or evals the page may
 * not have.
 */
export const resultsPlaceholder = (examples = []) =>
  examples.find((e) => e?.expr)?.expr ?? RESULTS_PLACEHOLDER;

/** Quick picks: Hide rows with no output (AX-09); every run fails the primary eval. */
export function resultsQuickPicks(view = {}) {
  const runs = (view.runs ?? []).filter((r) => !r.not_found);
  const ev = primaryEval(view.metrics);
  const out = [];
  if (runs.length)
    out.push({
      label: "Hide rows with no output",
      expr: "not (all.output is empty)",
    });
  if (ev && runs.length)
    out.push({
      label: `Only rows every run fails on ${ev.name}`,
      expr: `all.${quoteName(fieldName(ev.name ?? ev.key))} = fail`,
    });
  return out;
}

/** The R3 view of the count / API call: the page's view with the candidate pill. */
export function candidateView(resultsView, otherPills, expr) {
  return {
    ...resultsView,
    pills: [...(otherPills ?? []), expressionPill(expr)],
  };
}
