// L7c merge (the capability contract f, one source of truth): the ONE name of each machine
// a `Simulate …` stand-in button stands in for. connectCopy (tags, status
// lines) and runLedger (RunBar / run-row Simulate doors, by label) both read
// it, so one button reads the same on every screen. No imports: a leaf, so
// Runs and Connect can both use it without an import cycle.

/** The machine each stand-in stands in for, by machine. */
export const STAND_IN_EVENTS = Object.freeze({
  terminal: "your terminal sending results",
  ci_job: "the CI job sending results",
  // The upload run waits for `results` (runLedger WAIT_NOUNS.upload); it is
  // never named `your code` (a removed source, the capability contract e).
  upload: "the upload sending results",
  // A sandbox run then runs inside Future AGI: only its start stands in.
  sandbox: "the sandbox starting",
});

/** The same names, by the Simulate button's label. */
export const STAND_IN_EVENTS_BY_LABEL = Object.freeze({
  "Simulate the upload": STAND_IN_EVENTS.upload,
  "Simulate your terminal": STAND_IN_EVENTS.terminal,
  "Simulate the CI job": STAND_IN_EVENTS.ci_job,
});
