import { EVALS_COPY } from "../../evals/evals.constants";

// Only a harness call rerun can refresh a non-regradable row's score, so both
// its checkbox and its run action are locked with the same explanation.
export const HARNESS_ONLY_TOOLTIP = "Only a call rerun refreshes this";
export const NOT_EDITABLE_TOOLTIP = EVALS_COPY.notEditable;
// Shown when a ticked eval has no mapping of its own: on this page that is one
// of the harness's suite evals, whose scores the platform will replace.
export const HARNESS_NOTE =
  "Scores the harness gave will be replaced by the platform's.";
export const NOT_COMPLETED_TOOLTIP =
  "Only a completed run can be graded again.";
export const GRADING_TOOLTIP = EVALS_COPY.gradingLocked;
// A run's columns come from its stored results, so an eval removed from the
// environment after the run keeps its column but has nothing to act on.
export const EVAL_GONE_TOOLTIP =
  "This evaluation isn't on this environment any more.";
