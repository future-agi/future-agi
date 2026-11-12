// Every on-screen string for experiments that open in the original results
// view, in one place: the Experiments tab's second group and the original
// Summary's line when it cannot be computed. A wording change is one edit here.
export const OTHER_EXPERIMENTS_COPY = Object.freeze({
  heading: "Other experiments",
  line: "These experiments open in the original results view, where all of their variants show. This includes experiments created before this update.",
  // The group's Search by name when no experiment matches, as the
  // Experiments list said.
  noMatch: "No experiments match your search",
  // ErrorState reads "Couldn't load <thing>."
  loadThing: "other experiments",
  // Why Compare is disabled for them in the group's selection bar.
  compareReason:
    "Compare works on runs; these experiments open in the original results view instead.",
  // The original Summary of an experiment that has no snapshot dataset.
  summaryNotAvailable: "A summary is not available for this experiment.",
  // On a run row of one of these experiments, in the runs view: "In <name>",
  // the name a link that opens the experiment, with this tooltip.
  runExperimentPrefix: "In",
  runExperimentTip:
    "This experiment opens in the original results view, where all of its variants show.",
});
