// Display helpers for `Runs across experiments` (N7 items). Pure.
import { formatValue } from "src/sections/common/Metrics/metricFormat";
import { OPS_DEFS } from "./rowModel";
import { fmtScore } from "src/sections/common/format/fmt";

const PASS = 0.5;

/**
 * Chip verdict of one run's eval: `Pass` / `Fail`, `0.72`, and with trials
 * `0.67 · 3 trials` (trial mean). D4 colours: good = pass, bad = fail.
 */
export function runEvalVerdict(ev, metric, trials = 1) {
  // ir-5: skipped is never a pass and says why.
  if (ev?.status === "skipped")
    return {
      status: "empty",
      value: null,
      display: `Skipped · ${ev.skip_reason || "not scored"}`,
    };
  if (!ev || !Number.isFinite(ev.value))
    return { status: "empty", value: null, display: "Not scored" };
  const v = ev.value;
  const higher = metric?.higher_is_better !== false;
  const passFail = metric?.output_type === "pass_fail";
  const threshold = passFail ? PASS : metric?.pass_threshold;
  let status = "neutral";
  if (Number.isFinite(threshold)) {
    const good = higher ? v >= threshold : v < threshold;
    status = good ? "pass" : "fail";
  }
  let display;
  if (trials > 1) display = `${fmtScore(v)} · ${trials} trials`;
  else if (passFail) display = v >= PASS ? "Pass" : "Fail";
  else display = fmtScore(v);
  return { status, value: v, display };
}

/** `Ran in 9 runs · 7 experiments · Passes Correctness in 6/9`. */
export function runsSummaryText(summary) {
  if (!summary || !summary.runs) return "";
  const parts = [
    `Ran in ${summary.runs} run${summary.runs === 1 ? "" : "s"}`,
    `${summary.experiments} experiment${summary.experiments === 1 ? "" : "s"}`,
  ];
  if (summary.primary_metric_name && summary.primary_scored)
    parts.push(
      `Passes ${summary.primary_metric_name} in ${summary.primary_pass}/${summary.primary_scored}`,
    );
  return parts.join(" · ");
}

/** Flags under the summary: hard case, flaky, expected changed. */
export function runsFlags(summary) {
  const f = summary?.flags ?? {};
  const out = [];
  if (f.hard_case) {
    const [fails, of] = f.fails_of_runs ?? [];
    out.push({
      key: "hard",
      tone: "red",
      label: Number.isFinite(fails)
        ? `Hard case · fails ${fails} of ${of} runs`
        : "Hard case",
      tooltip: `Fails ${summary.primary_metric_name ?? "the primary metric"} in at least 70% of 3 or more runs`,
    });
  }
  if (f.flaky)
    out.push({
      key: "flaky",
      tone: "amber",
      label: "Flaky",
      tooltip: "Trials of one run disagree on this row",
    });
  if (f.expected_changed_after_runs > 0)
    out.push({
      key: "changed",
      tone: "amber",
      label: `Expected changed after ${f.expected_changed_after_runs} run${
        f.expected_changed_after_runs === 1 ? "" : "s"
      }`,
      tooltip: "These runs scored the row against an older expected value",
    });
  return out;
}

export const formatLatency = (ms) =>
  Number.isFinite(ms) ? formatValue(OPS_DEFS.latency, ms) : "—";

export const formatCost = (usd) =>
  Number.isFinite(usd) && usd > 0 ? formatValue(OPS_DEFS.cost, usd) : "—";

/** The compare page link for the selected runs (2–8), baseline first. */
export function compareRunsHref(datasetId, runIds, { baselineId, rowId } = {}) {
  const ids = [...runIds];
  const base = baselineId && ids.includes(baselineId) ? baselineId : ids[0];
  const ordered = [base, ...ids.filter((id) => id !== base)];
  const q = new URLSearchParams({
    runs: ordered.join(","),
    baseline: base,
    ...(rowId && { row: rowId }),
  });
  return `/dashboard/develop/${datasetId}/compare/data?${q.toString()}`;
}

/** Experiment page link for one run's result on this row. */
export function runResultHref(run, rowId, { trajectory = false } = {}) {
  const q = new URLSearchParams({ row: rowId, run: run.run_id });
  if (trajectory) q.set("dtab", "trajectory");
  return `/dashboard/develop/experiment/${run.experiment_id}/data?${q.toString()}`;
}

// ---- R3 (P2): the side-by-side door and the Simple summary -------------

/** Max lanes the side-by-side workspace shows as columns. */
export const SIDE_BY_SIDE_MAX = 4;

/** `pass` / `fail` / null of a run's primary metric on this row. */
export function primaryVerdictOf(run, metric) {
  if (!metric) return null;
  const v = runEvalVerdict(run?.evals?.[metric.key], metric, 1);
  return v.status === "pass" || v.status === "fail" ? v.status : null;
}

/**
 * RV#21: the 9-segment bar under the summary — one segment per run that has
 * a primary verdict, in table order.
 */
export function verdictSegments(runs = [], metric) {
  return runs
    .map((r) => ({
      runId: r.run_id,
      label: r.label,
      status: primaryVerdictOf(r, metric),
    }))
    .filter((s) => s.status);
}

/** Runs kept by the Simple `Show` filter (`all` · `pass` · `fail`). */
export function filterRunsByVerdict(runs = [], metric, verdict) {
  if (!verdict || verdict === "all" || !metric) return runs;
  return runs.filter((r) => primaryVerdictOf(r, metric) === verdict);
}

/**
 * The runs `Compare these runs side by side` opens when nothing is ticked:
 * the dataset baseline (else the newest run) plus runs whose primary
 * verdict disagrees with it, then the rest, up to `max`.
 */
export function defaultSideBySideRuns(
  runs = [],
  metric,
  max = SIDE_BY_SIDE_MAX,
) {
  if (!runs.length) return [];
  const base = runs.find((r) => r.is_dataset_baseline) ?? runs[0];
  const bv = primaryVerdictOf(base, metric);
  const rest = runs.filter((r) => r.run_id !== base.run_id);
  const differ = rest.filter((r) => {
    const v = primaryVerdictOf(r, metric);
    return v && bv && v !== bv;
  });
  const others = rest.filter((r) => !differ.includes(r));
  return [base, ...differ, ...others].slice(0, max).map((r) => r.run_id);
}

/** Baseline of a side-by-side set: the dataset baseline when picked. */
export function sideBySideBaseline(runIds = [], runs = []) {
  const base = runs.find((r) => r.is_dataset_baseline)?.run_id;
  return base && runIds.includes(base) ? base : runIds[0] ?? null;
}

/** Baseline first, then the picks in table order. */
export function orderForSideBySide(runIds = [], runs = []) {
  const base = sideBySideBaseline(runIds, runs);
  const order = runs.map((r) => r.run_id).filter((id) => runIds.includes(id));
  return base ? [base, ...order.filter((id) => id !== base)] : order;
}
