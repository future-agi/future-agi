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
  "Only rows that failed a check, errored or were stopped run again, in a new run. Rows that passed are carried over.";

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

const KEPT_OUTPUTS_LINE = "Kept runs keep their outputs.";

/**
 * An Edit save that changes a kept evaluation scores it again on every run's
 * outputs. Alone, no output is run again. With re-run prompts and a kept
 * agent (a mixed save), the re-run runs score it in their own runs and each
 * kept run is scored again: a prompt with a saved version by an eval-only
 * run, the others by eval-only batches.
 */
export const EDITED_EVALUATION_LINE =
  "The edited evaluation is scored again on every run.";

/**
 * The sentence after a footer amount when the save also scores kept runs (an
 * evaluation edited or added while runs start and another run is kept): the
 * amount covers only the runs that start. It never shows without an amount.
 */
export const KEPT_SCORING_NOT_ESTIMATED =
  "Scoring on kept runs is not included in this estimate.";

/** An Edit save that only adds an evaluation scores it on every run. */
export const ADDED_EVALUATION_LINE =
  "The added evaluation is scored on every run.";

/**
 * An Edit save that edits one evaluation and adds another, with no run
 * re-run or added, scores both on every run's outputs; no output is run
 * again.
 */
export const EDITED_AND_ADDED_EVALUATIONS_LINE =
  "The edited and added evaluations are scored on every run.";

/**
 * Which kind of Edit save this is for the confirm and the footer total:
 * "evaluation" (a kept evaluation edited, no run re-run or added),
 * "editedAndAdded" (an evaluation edited and another added, no run re-run
 * or added), "mixed" (an evaluation edited, none added, prompt runs re-run,
 * no agent run re-run and an agent run kept), "added" (an evaluation added,
 * nothing edited, no run re-run or added), or null for any other save,
 * whose words stay as they were.
 * @param {{rerun?: string[], added?: string[], keptAgents?: string[],
 *   rerunAgents?: string[], evalsEdited?: string[], evalsAdded?: string[]}}
 *   change  editChangeSummary's runs re-run and added, its kept and re-run
 *   agent runs, and its evaluations edited and added (evalsNew)
 */
export function editSaveKind({
  rerun = [],
  added = [],
  keptAgents = [],
  rerunAgents = [],
  evalsEdited = [],
  evalsAdded = [],
} = {}) {
  if (added?.length) return null;
  const edited = Boolean(evalsEdited?.length);
  const adding = Boolean(evalsAdded?.length);
  const rerunning = Boolean(rerun?.length);
  if (edited && adding) return rerunning ? null : "editedAndAdded";
  if (edited && !rerunning) return "evaluation";
  if (edited)
    return keptAgents?.length && !rerunAgents?.length ? "mixed" : null;
  if (adding && !rerunning) return "added";
  return null;
}

/**
 * Whether an Edit save also scores kept runs, outside the runs it starts:
 * an evaluation is edited or added, a run is re-run or added, and another
 * run is kept. The footer amount covers only the runs that start.
 * @param {{rerun?: string[], added?: string[], kept?: string[],
 *   evalsEdited?: string[], evalsAdded?: string[]}} change  editRunScope's
 *   runs re-run, added and kept, and its evaluations edited and added
 *   (evalsNew)
 */
export function editScoresKeptRuns({
  rerun = [],
  added = [],
  kept = [],
  evalsEdited = [],
  evalsAdded = [],
} = {}) {
  const scoresChange = Boolean(evalsEdited?.length || evalsAdded?.length);
  const runsStart = Boolean(rerun?.length || added?.length);
  return scoresChange && runsStart && Boolean(kept?.length);
}

/**
 * For the Edit confirm: what a save that also scores kept runs
 * (editScoresKeptRuns, the same rule) changed, as the key of its sentence:
 * "evaluation" (an evaluation edited), "added" (one added) or
 * "editedAndAdded" (one edited and another added). Null when the save
 * scores no kept run.
 * @param {object} change  as editScoresKeptRuns takes it
 */
export function editKeptScoring(change = {}) {
  if (!editScoresKeptRuns(change)) return null;
  const edited = Boolean(change?.evalsEdited?.length);
  const adding = Boolean(change?.evalsAdded?.length);
  if (edited && adding) return "editedAndAdded";
  return edited ? "evaluation" : "added";
}

// The words after `Kept runs keep their outputs.` (after the re-run line for
// a mixed save), in the confirm and the footer total. A mixed save says what
// an evaluation-only save says.
const KIND_SENTENCE = {
  evaluation: EDITED_EVALUATION_LINE,
  editedAndAdded: EDITED_AND_ADDED_EVALUATIONS_LINE,
  mixed: EDITED_EVALUATION_LINE,
  added: ADDED_EVALUATION_LINE,
};

/**
 * The footer total's text for an Edit save of `kind`, or null to keep
 * today's total. The one place this choice is made. A save that starts no
 * run (an evaluation edited, added, or both) shows its sentence with no
 * amount. A mixed save shows its sentence, then `estimate` (the total as it
 * shows the runs that start), then what that amount leaves out (the
 * scoring on kept runs). Any other save that also scores kept runs
 * (`scoresKeptRuns`) shows `estimate`, then the same words; the rest get
 * null. With no estimate there is no amount, so no words about one: a mixed
 * save shows its sentence alone and any other save gets null.
 * @param {string|null} kind  editSaveKind of the save
 * @param {{estimate?: string|null, scoresKeptRuns?: boolean}} [opts]
 */
export function editChipText(
  kind,
  { estimate = null, scoresKeptRuns = false } = {},
) {
  const sentence = KIND_SENTENCE[kind];
  if (sentence && kind !== "mixed") return sentence;
  // The sentence stands only beside a money amount; an estimate with none
  // ("~$ —") shows as it is.
  const priced = /\$\s*\d/.test(estimate ?? "");
  const amount =
    estimate && (kind === "mixed" || scoresKeptRuns)
      ? priced
        ? `${estimate}. ${KEPT_SCORING_NOT_ESTIMATED}`
        : estimate
      : null;
  if (kind === "mixed") return amount ? `${sentence} ${amount}` : sentence;
  return amount;
}

/**
 * W1-1: the Edit confirm's second line. A changed run never re-runs in
 * place: `A changed run starts a new run: #4A·2. #4A keeps its results.`
 * With no run changed: `Results of kept runs stay as they are.` A save of a
 * kind editSaveKind names reads `Kept runs keep their outputs.` and that
 * kind's sentence instead; a mixed save, which also starts a new run of each
 * changed prompt, reads the re-run line and then its sentence. Any other
 * save that also scores kept runs
 * (`keptScoring`, editKeptScoring) ends with the sentence for what changed:
 * after the re-run line when a run is re-run, else after `Kept runs keep
 * their outputs.`.
 * @param {string[]} rerunLabels  the changed runs (editChangeSummary.rerun)
 * @param {object[]} runs  R1 runs of the experiment (attempts_count)
 * @param {{kind?: string|null, keptScoring?: string|null}} [opts]
 *   editSaveKind and editKeptScoring of the save
 */
export function editKeptLine(
  rerunLabels = [],
  runs = [],
  { kind = null, keptScoring = null } = {},
) {
  const sentence = KIND_SENTENCE[kind];
  if (sentence && kind === "mixed" && (rerunLabels ?? []).some(Boolean))
    return `${editKeptLine(rerunLabels, runs)} ${sentence}`;
  if (sentence) return `${KEPT_OUTPUTS_LINE} ${sentence}`;
  const scored = KIND_SENTENCE[keptScoring];
  if (scored) {
    const lead = (rerunLabels ?? []).some(Boolean)
      ? editKeptLine(rerunLabels, runs)
      : KEPT_OUTPUTS_LINE;
    return `${lead} ${scored}`;
  }
  const byLabel = new Map((runs ?? []).map((r) => [r?.label, r]));
  const from = (rerunLabels ?? []).filter(Boolean);
  if (!from.length) return "Results of kept runs stay as they are.";
  const next = from.map((l) => nextRunLabel(byLabel.get(l) ?? { label: l }));
  return `${from.length === 1 ? "A changed run starts a new run" : "Changed runs start new runs"}: ${joinAnd(next)}. ${keep(from)}`;
}
