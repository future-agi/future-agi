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
// the mock run itself). Tokens and cost have no field on the runs list, so they
// are null here and render as a dashed cell, never a fabricated number.

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
    runState: run?.runState ?? run?.status,
    stoppable: !!run?.stoppable,
    at: run?.finishedAt || run?.startedAt || null,
    finishedAt: run?.finishedAt ?? null,
    tasks: total,
    passed,
    failed: run?.failed ?? Math.max(total - passed, 0),
    scenarioCount: run?.scenarioCount ?? null,
    scenarioIds: run?.scenarioIds ?? [],
    trials: run?.trials ?? 1,
    passRate: total ? Math.round((passed / total) * 100) : 0,
    durationS: run?.durationS ?? null,
    scores: scores || {},
    tokens: null,
    cost: null,
  };
}

// Distinct scenarios the runs covered. Runs that carry no ids (older or mock
// runs) can't add to the union, so they are skipped; only when no run has ids
// does the environment total stand in.
export function countCoveredScenarios(rows, fallback) {
  const keyed = (rows || []).filter((r) => r?.scenarioIds?.length);
  if (!keyed.length) return fallback;
  return new Set(keyed.flatMap((r) => r.scenarioIds)).size;
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

// A run the pass-rate chart plots: it finished with a verdict on at least one
// call. Running and stopped runs are left off, since their pass rate counts
// calls that never ran.
export function isFinishedRun(run) {
  return (
    !!run?.finishedAt &&
    (run?.total ?? 0) > 0 &&
    (run?.status === "passed" || run?.status === "failed")
  );
}
