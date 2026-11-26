// pending running completed failed
export const CriticalIssueStatus = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  INSUFFICIENT_DATA: "insufficient_data",
  // A simulation run's summary nobody has asked for since its verdicts last
  // changed; the server answers its stored "pending" this way.
  NOT_REQUESTED: "not_requested",
};

// Nobody has asked: no job is coming.
const NotAskedStates = [
  CriticalIssueStatus.NOT_REQUESTED,
  CriticalIssueStatus.PENDING,
];

const CriticalIssueRefetchStates = [
  CriticalIssueStatus.PENDING,
  CriticalIssueStatus.RUNNING,
];

// A simulation run's summary is made only when a person presses Refresh,
// which marks it running, so the pane waits only on "running".
const SimulateCriticalIssueRefetchStates = [CriticalIssueStatus.RUNNING];

export const NOT_ANALYSED_MESSAGE =
  "Critical issues have not been analysed for this run yet. Press Refresh to analyse it.";

export const OUT_OF_DATE_MESSAGE =
  "Found before the run's latest grading. Press Refresh to update them.";

export const FAILED_MESSAGE =
  "The last analysis could not be finished. Press Refresh to try again.";

export const isCriticalIssueInProgress = (mode, status) =>
  (mode === "simulate"
    ? SimulateCriticalIssueRefetchStates
    : CriticalIssueRefetchStates
  ).includes(status);

const hasResult = (result) => result !== null && result !== undefined;

export const isCriticalIssueNotAnalysed = (mode, status, result) =>
  mode === "simulate" && NotAskedStates.includes(status) && !hasResult(result);

// A stored summary nobody has asked to remake was made before the run's
// verdicts last changed (a rerun or a late verdict).
export const isCriticalIssueOutOfDate = (mode, status, result) =>
  mode === "simulate" && NotAskedStates.includes(status) && hasResult(result);

// The last summary a person asked for failed, or its job was lost.
export const isCriticalIssueFailed = (mode, status) =>
  mode === "simulate" && status === CriticalIssueStatus.FAILED;
