// Display helpers for `Runs across experiments` (N7 items). Pure.
import { paths } from "src/routes/paths";
import {
  COMPARE_CHECK_FAILED,
  groupUnnamed,
  inOneComparisonGroup,
  setUpsDifferLine,
} from "src/sections/experiment-detail/matrix/comparableRuns";
import { formatValue } from "src/sections/common/Metrics/metricFormat";
import { OPS_DEFS } from "./rowModel";
import { fmtScore } from "src/sections/common/format/fmt";

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
  const higher = metric?.higher_is_better;
  const passFail = metric?.output_type === "pass_fail";
  const threshold = metric?.pass_threshold;
  let status = "neutral";
  // Aggregate and trial verdicts come from each run's pin, not shared metadata.
  // An explicit null is authoritative too; only older payloads use the fallback.
  if (Object.prototype.hasOwnProperty.call(ev, "passed")) {
    status =
      ev.passed === true ? "pass" : ev.passed === false ? "fail" : "neutral";
  } else if (passFail && (v === 0 || v === 1)) {
    status = v === 1 ? "pass" : "fail";
  } else if (typeof higher === "boolean" && Number.isFinite(threshold)) {
    const good = higher ? v >= threshold : v <= threshold;
    status = good ? "pass" : "fail";
  }
  let display;
  if (trials > 1) display = `${fmtScore(v)} · ${trials} trials`;
  else if (passFail && status !== "neutral")
    display = status === "pass" ? "Pass" : "Fail";
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

/**
 * The dataset's experiments with no recorded runs (`summary.
 * classic_experiments`): their results can't be traced to this row, so they
 * are not listed. Says only what the server counts, never whether this row
 * was in them. Null when there are none.
 */
export function unrecordedExperimentsText(summary) {
  const n = summary?.classic_experiments;
  if (!Number.isInteger(n) || n <= 0) return null;
  return n === 1
    ? "1 experiment on this dataset has no recorded runs, so its results aren't listed here."
    : `${n} experiments on this dataset have no recorded runs, so their results aren't listed here.`;
}

/** The same count, in the empty state of a row no recorded run includes. */
export function unrecordedExperimentsEmptyText(summary) {
  const n = summary?.classic_experiments;
  if (!Number.isInteger(n) || n <= 0) return null;
  return n === 1
    ? "1 experiment on this dataset has no recorded runs and isn't listed here."
    : `${n} experiments on this dataset have no recorded runs and aren't listed here.`;
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

/**
 * Open this run's result by its example id, falling back to the row id.
 * Attached runs keep their experiment Data page and Run history group.
 * Standalone runs use dataset results with only that run as the baseline,
 * so an unrelated pinned baseline cannot change the selected population.
 * Without dataset/run context a standalone result has no destination.
 */
export function runResultHref(
  run,
  rowId,
  { trajectory = false, datasetId } = {},
) {
  if (!run.experiment_id) {
    if (!datasetId || !run.run_id) return null;
    const href = compareRunsHref(datasetId, [run.run_id], {
      rowId: run.example_id ?? rowId,
    });
    const q = new URLSearchParams({ run: run.run_id });
    if (trajectory) q.set("dtab", "trajectory");
    return `${href}&${q.toString()}`;
  }
  const q = new URLSearchParams({
    row: run.example_id ?? rowId,
    run: run.run_id,
  });
  if (run.run_id) q.set("history", run.run_id);
  if (trajectory) q.set("dtab", "trajectory");
  return `/dashboard/develop/experiment/${run.experiment_id}/data?${q.toString()}`;
}

// ---- R3 (P2): the side-by-side door and the Simple summary -------------

/** Max lanes the side-by-side workspace shows as columns. */
export const SIDE_BY_SIDE_MAX = 4;

/** What a side-by-side door says when nothing can check its runs. */
export const SIDE_BY_SIDE_UNAVAILABLE =
  "Side-by-side compare needs a server read this release doesn't have yet.";

/**
 * The `catalogs` key of the dataset's run catalog. Runs that are not all in
 * one experiment (runs of other experiments, or of none) are checked against
 * it and open in the dataset's Compare Data instead of one experiment's
 * Results.
 */
export const DATASET_RUNS_CATALOG = "dataset";

/**
 * Which catalog says whether these runs open side by side: their experiment's
 * when every run is in that one experiment, else the dataset's.
 */
export function sideBySideCatalogKey(runIds = [], runs = []) {
  const selected = runIds.map((id) => runs.find((run) => run.run_id === id));
  const experimentId = selected[0]?.experiment_id;
  return experimentId &&
    selected.every((run) => run?.experiment_id === experimentId)
    ? experimentId
    : DATASET_RUNS_CATALOG;
}

/**
 * What a side-by-side door says when a ticked run is not among the runs the
 * row lists now (Show changed which runs are listed).
 */
export const SIDE_BY_SIDE_NOT_SHOWN =
  "A ticked run isn't in this view. Show all runs to see or untick it.";

/** What a side-by-side door says while its experiment's run catalog is read. */
export const SIDE_BY_SIDE_CHECKING =
  "Checking whether these runs can be compared…";

/**
 * What a side-by-side door says when the catalog read failed, or the catalog
 * does not list one of its runs or name its comparison group. A Try again
 * beside it reads it again.
 */
export const SIDE_BY_SIDE_CHECK_FAILED = COMPARE_CHECK_FAILED;

/**
 * Why runs can't open side by side, else null. Runs of one experiment open
 * in its Results when its run catalog puts them in one comparison group;
 * any other runs (of other experiments, or of none) open in the dataset's
 * Compare Data when the dataset's run catalog does. `catalogs` maps an
 * experiment id, or DATASET_RUNS_CATALOG, to its catalog read:
 * `{ runs, failed, fetching }`, `runs` null until it answers, records with
 * `comparison_group`. The runs open only once that catalog lists every one
 * of them in one named group: until then, when the read fails, or when it
 * names no group for one of them, they stay off and say so. While the
 * catalog is read again, its last answer stands. The line names the runs
 * baseline first, as they open. A ticked run the row no longer lists keeps
 * them off, so none is left out of the comparison without a word.
 */
export function sideBySideBlocked(runIds = [], runs = [], catalogs = {}) {
  if (runIds.some((id) => !runs.some((run) => run.run_id === id)))
    return SIDE_BY_SIDE_NOT_SHOWN;
  if (runIds.length < 2) return null;
  const catalog = catalogs?.[sideBySideCatalogKey(runIds, runs)];
  const records = runIds.map((id) =>
    (catalog?.runs ?? []).find((run) => run.run_id === id),
  );
  const known =
    Boolean(catalog?.runs) && !catalog.failed && records.every(Boolean);
  if (!known)
    return catalog?.fetching || (!catalog?.runs && !catalog?.failed)
      ? SIDE_BY_SIDE_CHECKING
      : SIDE_BY_SIDE_CHECK_FAILED;
  if (inOneComparisonGroup(records)) return null;
  if (groupUnnamed(records)) return SIDE_BY_SIDE_CHECK_FAILED;
  return setUpsDifferLine(
    orderForSideBySide(runIds, runs).map((id) => {
      const run = runs.find((r) => r.run_id === id);
      return run?.label || run?.name;
    }),
  );
}

/**
 * Where runs that share a set-up open side by side, baseline first: their
 * experiment's Results when they are all in one experiment, else the
 * dataset's Compare Data at this row's example.
 */
export function sideBySideResultsHref(
  runIds = [],
  runs = [],
  catalogs = {},
  { datasetId, rowId } = {},
) {
  if (!runIds.length || runIds.length > SIDE_BY_SIDE_MAX) return null;
  if (sideBySideBlocked(runIds, runs, catalogs)) return null;
  const experimentId = sideBySideCatalogKey(runIds, runs);
  const ordered = orderForSideBySide(runIds, runs);
  if (experimentId === DATASET_RUNS_CATALOG) {
    if (!datasetId) return null;
    const base = runs.find((run) => run.run_id === ordered[0]);
    return compareRunsHref(datasetId, ordered, {
      baselineId: ordered[0],
      rowId: base?.example_id ?? rowId,
    });
  }
  const q = new URLSearchParams({
    runs: ordered.join(","),
    baseline: ordered[0],
    history: ordered[0],
  });
  return `${paths.dashboard.develop}/experiment/${encodeURIComponent(experimentId)}/data?${q.toString()}`;
}

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
