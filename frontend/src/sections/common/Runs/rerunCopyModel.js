// W1-1 kept runs (notify DESIGN §1.2 "Copy"): every whole-run rerun starts a
// new run of each config (#2A·2) and the runs it started from keep their
// results. The one place the rerun dialogs and toasts are worded; the eight
// sites that said "results are replaced" read it. People see "runs", never
// the internal word for a numbered run of a config.
import { formatRunLabel, parseRunLabel } from "./runLabel";

const joinAnd = (xs) =>
  xs.length <= 1
    ? xs[0] ?? ""
    : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/**
 * The label the next run of a run's config gets: `#2A` → `#2A·2`, a config
 * with 3 runs → `·4` (R1 `attempts_count`; the newest run's number + 1).
 */
export function nextRunLabel(run) {
  const p = parseRunLabel(run?.label);
  if (!p) return "";
  const n = Math.max(
    Number(run?.attempts_count) || 1,
    Number(run?.attempt) || p.attempt || 1,
  );
  return formatRunLabel({ ...p, attempt: n + 1 });
}

const labelsOf = (runs = []) => runs.map((r) => r?.label).filter(Boolean);
const nextsOf = (runs = []) => runs.map(nextRunLabel).filter(Boolean);
const keep = (labels) =>
  `${joinAnd(labels)} ${labels.length === 1 ? "keeps its" : "keep their"} results.`;

/** The experiment header's Rerun button tooltip (ExperimentRow). */
export const RERUN_TOOLTIP =
  "Every rerun starts a new run; the runs it starts from keep their results. To change something and compare, use Test a fix.";

/** Experiment header `Rerun ▾` → Re-run all (ExperimentRow). */
export function rerunAllBody({ rowNoun = "row", allLine = "", runs = [] }) {
  const detail = allLine ? ` (${allLine})` : "";
  return `Every run runs again on every ${rowNoun}${detail} as a new run: ${joinAnd(nextsOf(runs))}. ${keep(labelsOf(runs))}`;
}

/** Re-run all when some runs can't be started here (external CI runs). */
export function rerunSomeBody({ rowNoun = "row", allLine = "", runs = [] }) {
  const detail = allLine ? ` (${allLine})` : "";
  return `${joinAnd(labelsOf(runs))} run again on every ${rowNoun}${detail} as new runs: ${joinAnd(nextsOf(runs))}. ${keep(labelsOf(runs))} The other runs keep theirs: Future AGI cannot start them again.`;
}

/** Experiment header `Rerun ▾` → Rerun failed rows (ExperimentRow). */
export function rerunFailedBody({ rowNoun = "row", runs = [] }) {
  const nexts = nextsOf(runs);
  const labels = labelsOf(runs);
  if (runs.length === 1)
    return `Only the ${rowNoun}s that failed a check or errored run again, in a new run ${nexts[0]}. The ${rowNoun}s that passed are carried over from ${labels[0]}, so ${nexts[0]} is complete and ${labels[0]} stays as it was.`;
  return `Only the ${rowNoun}s that failed a check or errored run again, in new runs ${joinAnd(nexts)}. The ${rowNoun}s that passed are carried over from ${joinAnd(labels)}, so the new runs are complete and ${joinAnd(labels)} stay as they were.`;
}

/** Experiment header `Rerun ▾` → Rerun changed configs (ExperimentRow). */
export function rerunChangedBody({
  secondary = "",
  rowNoun = "row",
  runs = [],
}) {
  const lead = secondary ? `${secondary}. ` : "";
  return `${lead}Each runs again on every ${rowNoun} as a new run (${joinAnd(nextsOf(runs))}); the other runs keep their results.`;
}

/** useRunActions rerunAll: the whole experiment. */
export const rerunExperimentBody = () =>
  "Every run of this experiment runs again on every example as a new run. The current runs keep their results.";

/** useRunActions rerunRun: one run on every example. */
export const rerunRunBody = (run) =>
  `Every example runs again in a new run, ${nextRunLabel(run)}. ${run?.label ?? "This run"} keeps its results.`;

/** Leaderboard row menu / selection bar: Re-run. */
export const leaderboardRerunBody = (runs = []) =>
  `Every row runs again in a new run and is scored again. ${runs.length > 1 ? "These runs keep their" : "This run keeps its"} results.`;

/** Leaderboard row menu / selection bar: Re-run failed rows. */
export const leaderboardRerunFailedBody = () =>
  "Only rows that errored or were stopped run again, in a new run. Completed rows are carried over.";

/**
 * The toast once the new runs started: "Started #2A·2 and #2B·2. #2A and
 * #2B keep their results." `labels` are the new runs' (re-run/ `labels`),
 * `from` the runs they started from.
 */
export function startedToast(labels = [], from = []) {
  if (!labels.length) return "";
  const kept = from.length ? ` ${keep(from)}` : "";
  return `Started ${joinAnd(labels)}.${kept}`;
}

/** Pull the new runs out of a re-run / rerun-cells response (or none). */
export function startedRunsOf(response) {
  const r = response?.data?.result ?? response?.result ?? response;
  if (!r || typeof r !== "object") return { runIds: [], labels: [] };
  return {
    runIds: Array.isArray(r.run_ids) ? r.run_ids : [],
    labels: Array.isArray(r.labels) ? r.labels : [],
  };
}

/**
 * W1-1: the Edit confirm's second line. A changed run never re-runs in
 * place: `A changed run starts a new run: #4A·2. #4A keeps its results.`
 * With nothing changed: `Results of kept runs stay as they are.`
 * @param {string[]} rerunLabels  the changed runs (editChangeSummary.rerun)
 * @param {object[]} runs  R1 runs of the experiment (attempts_count)
 */
export function editKeptLine(rerunLabels = [], runs = []) {
  const byLabel = new Map((runs ?? []).map((r) => [r?.label, r]));
  const from = (rerunLabels ?? []).filter(Boolean);
  if (!from.length) return "Results of kept runs stay as they are.";
  const next = from.map((l) => nextRunLabel(byLabel.get(l) ?? { label: l }));
  return `${from.length === 1 ? "A changed run starts a new run" : "Changed runs start new runs"}: ${joinAnd(next)}. ${keep(from)}`;
}
