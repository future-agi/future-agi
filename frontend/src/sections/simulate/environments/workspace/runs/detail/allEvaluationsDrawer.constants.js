// Only a harness call rerun can refresh a non-regradable row's score, so both
// its checkbox and its run action are locked with the same explanation.
export const HARNESS_ONLY_TOOLTIP = "Only a call rerun refreshes this";
// Shown when a ticked eval has no mapping of its own: on this page that is one
// of the harness's suite evals, whose scores the platform will replace.
export const HARNESS_NOTE =
  "Scores the harness gave will be replaced by the platform's.";
