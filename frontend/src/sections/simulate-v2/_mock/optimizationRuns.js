/**
 * Optimization runs — the record, not the search.
 *
 * An optimization belongs to the environment and its agent, not to the one
 * simulation run that happened to prompt it. Filing them under a run would mean
 * a team's whole optimization history vanished the day somebody tidied up their
 * runs list, and it would make "how has this agent been tuned over time" a
 * question with no screen behind it. So they live on the environment, each one
 * remembering which run it was started from.
 *
 * Every record carries the diagnosis it was created against. A search is only
 * as good as the problem statement it was given, and six weeks later "why did
 * we try that" is answerable only if the evidence travelled with the attempt.
 */

import { OPTIMIZERS, optimizerById } from "./optimizer";

export const OPT_STATUS = {
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
};

/**
 * Models that can drive the search.
 *
 * This is the model doing the optimizing — reading failures and writing
 * candidate prompts — not the model under test. Worth keeping separate in
 * someone's head, because the strongest available model is usually the right
 * choice here even when the agent itself runs on something cheaper.
 */
export const OPTIMIZER_MODELS = [
  { id: "claude-opus-5", label: "Claude Opus 5", note: "Strongest at reading failures and writing the fix" },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5", note: "Faster, close behind on prompt rewriting" },
  { id: "gpt-5", label: "GPT-5", note: "Second opinion — a different model proposes differently" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", note: "Cheapest; fine for many small variations" },
];

export const optimizations = (envState) => envState?.optimizations || [];

/** Newest first, which is the only order a runs list is ever read in. */
export const optimizationList = (envState) =>
  [...optimizations(envState)].sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));

/** The ones a given simulation run produced. */
export const optimizationsFromRun = (envState, runId) =>
  optimizationList(envState).filter((o) => o.fromRunId === runId);

export const nextOptimizationName = (envState) =>
  `Optimization run ${optimizations(envState).length + 1}`;

export const optimizationId = (envState) =>
  `OPT-${String(optimizations(envState).length + 1).padStart(5, "0")}`;

/**
 * One row's worth of headline, so the list and the detail header cannot drift.
 *
 * `heldScore` is the number that leads, never the training best — a list that
 * ranks runs by what they scored on the split they were tuned against is a
 * leaderboard of overfitting.
 */
export const optimizationSummary = (record) => {
  const r = record?.result;
  if (!r) return { headline: "—", sub: "", tone: "text.disabled" };
  if (record.status === OPT_STATUS.RUNNING) {
    return { headline: "Running", sub: `${r.trials?.length || 0} trials`, tone: "#CA8A04" };
  }
  const lift = r.heldScore - r.heldBase;
  return {
    headline: `${r.heldScore}%`,
    sub: `held out · ${lift >= 0 ? "+" : ""}${lift} from ${r.heldBase}%`,
    tone: lift > 0 ? "#16A34A" : lift < 0 ? "#DC2626" : "text.secondary",
  };
};

/** What a run is worth acting on, stated once so every surface agrees. */
export const optimizationVerdict = (record) => {
  const r = record?.result;
  if (!r) return null;
  if (r.hollow?.length) {
    return {
      tone: "#DC2626",
      title: `${r.hollow.length} of the winner's fixes have no tool evidence`,
      body: "The search found wording that passes the checks on scenarios that failed for a missing tool call. That is the optimizer doing its job against a check that cannot tell the difference — fix the check before shipping this prompt.",
    };
  }
  if (r.overruled) {
    return {
      tone: "#CA8A04",
      title: `Trial ${r.overruled.n} scored higher and was rejected`,
      body: "It broke a scenario that blocks a release. The winner below is the best candidate that did not.",
    };
  }
  /*
    A held-out set with nothing failing in it cannot show an improvement.

    It reported 96% "from 100%" and the screen called that no improvement, as
    though the search had failed to lift something liftable. It had not: every
    held-out scenario already passed, so the only thing that set could ever
    report was a regression — and here it reported one, which is the more
    serious finding and was being read as the milder one.
  */
  if (r.heldBase >= 100) {
    const broke = (r.heldTasks || []).filter((t) => r.heldPerScenario?.[t.id] === "broke");
    return r.heldScore < r.heldBase
      ? {
        tone: "#DC2626",
        title: broke.length === 1
          ? `The winner broke “${broke[0].title}”`
          : `The winner broke ${broke.length || 1} held-out scenarios`,
        body: "Every held-out scenario passed before this change and that one does not now. The held-out set was too easy to show a gain, so a regression was the only thing it could ever report — and it reported one.",
      }
      : {
        tone: "#CA8A04",
        title: "The held-out set could not test this",
        body: "Every held-out scenario already passed, so there was nothing there for the winner to improve. The held-out number is evidence of no regression, not evidence of a gain — add harder scenarios before reading a lift into it.",
      };
  }
  if (r.heldScore < r.heldBase) {
    return {
      tone: "#DC2626",
      title: `Held-out rate fell ${r.heldBase - r.heldScore} points`,
      body: "The winner scored better on the scenarios it was tuned against and worse on the ones it was not. That is the definition of a prompt fitted to its training split.",
    };
  }
  if (r.gap > 12) {
    return {
      tone: "#CA8A04",
      title: `${r.gap} points between training and held out`,
      body: "The prompt learned the scenarios it was tuned on more than it learned the task. Worth more scenarios before trusting the lift.",
    };
  }
  if (r.heldScore > r.heldBase) {
    return {
      tone: "#16A34A",
      title: `Held-out rate improved by ${r.heldScore - r.heldBase} points`,
      body: "The winner does better on scenarios the search never saw, which is the only evidence that it learned the task rather than the split.",
    };
  }
  return {
    tone: "#DC2626",
    title: "No improvement on held-out scenarios",
    body: "Whatever the training split gained did not survive contact with scenarios the search had not seen. The reasoned changes are the better route here.",
  };
};

export { OPTIMIZERS, optimizerById };
