import { BUILD_TONES } from "../../../buildEnvironment/buildTones";

// Eval identity colours, indexed by the eval's position in the derived set —
// green first, purple second, matching the designer's Task-success / Policy
// graph. Mapped onto BUILD_TONES so this module carries no raw hex.
export const EVAL_COLORS = [
  BUILD_TONES.green,
  BUILD_TONES.accent,
  BUILD_TONES.blue,
  BUILD_TONES.amber,
  BUILD_TONES.orange,
  BUILD_TONES.pink,
  BUILD_TONES.tealDeep,
  BUILD_TONES.indigo,
];

// A backend score key ("task_success") → a display name ("Task success"). The
// kpis payload keys the eval metrics by snake_case; the graph legend and the
// table header want the human label. Sentence case (first word capitalised
// only), matching the designer.
function humanizeEvalKey(key) {
  const s = String(key).replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Pure adapters for the Runs summary — the populated Runs tab's graph + table.
//
// A summary row is one completed run, read against the environment's applied
// evals. Real columns (pass rate, tasks, avg duration) come from the executions
// row; per-eval scores come from that run's kpis (or, under the QA switch, from
// the mock run itself). Tokens / cost / said-not-done / mean-return have no
// backend field yet, so they are null here and rendered as a dashed "Dummy"
// cell — never a fabricated number.

// One run → the summary table's row shape. `scores` is the run's {evalId: 0–100}
// map (kpis-derived for a real run, inline for a mock one); absent → {}.
export function buildSummaryRow(run, scores) {
  const total = run?.total ?? 0;
  const passed = run?.passed ?? 0;
  return {
    id: run?.id,
    executionId: run?.executionId ?? run?.id,
    ordinal: run?.ordinal,
    label: run?.label,
    agentVersion: run?.agentVersion ?? null,
    status: run?.status,
    at: run?.finishedAt || run?.startedAt || null,
    tasks: total,
    passRate: total ? Math.round((passed / total) * 100) : 0,
    durationS: run?.durationS ?? null,
    scores: scores || {},
    // No backend field — shown as a dashed "Dummy" cell, not a made-up value.
    tokens: null,
    cost: null,
    saidNotDone: null,
    meanReturn: null,
  };
}

// The environment's eval set for the summary, derived from the union of the
// runs' score keys (first-seen order) — so the graph lines and the table's eval
// columns come from the same source the scores do, and always agree. Each entry
// carries a humanised name and a stable identity colour. No scores → no evals.
export function deriveEvals(rows) {
  const seen = [];
  (rows || []).forEach((r) => {
    Object.keys(r?.scores || {}).forEach((key) => {
      if (!seen.includes(key)) seen.push(key);
    });
  });
  return seen.map((id, i) => ({
    id,
    name: humanizeEvalKey(id),
    color: EVAL_COLORS[i % EVAL_COLORS.length],
  }));
}

// The graph series: one line per applied eval, its data the eval's score for
// each run in chronological order (a null gap where a run never scored it, so
// the line breaks rather than inventing a point). No applied evals → no series.
export function buildEvalSeries(rowsChrono, evals) {
  return (evals || []).map((e) => ({
    id: e.id,
    name: e.name,
    color: e.color,
    data: (rowsChrono || []).map((r) => {
      const v = r?.scores?.[e.id];
      return v == null ? null : v;
    }),
  }));
}
