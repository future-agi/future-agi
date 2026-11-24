// pending running completed failed
export const CriticalIssueStatus = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  INSUFFICIENT_DATA: "insufficient_data",
};

const CriticalIssueRefetchStates = [
  CriticalIssueStatus.PENDING,
  CriticalIssueStatus.RUNNING,
];

// A simulation run's summary is made only when a person presses Refresh,
// which marks it running. "pending" there means nobody has asked since the
// run last finished, so the pane waits only on "running".
const SimulateCriticalIssueRefetchStates = [CriticalIssueStatus.RUNNING];

export const NOT_ANALYSED_MESSAGE =
  "Critical issues have not been analysed for this run yet. Press Refresh to analyse it.";

export const isCriticalIssueInProgress = (mode, status) =>
  (mode === "simulate"
    ? SimulateCriticalIssueRefetchStates
    : CriticalIssueRefetchStates
  ).includes(status);

export const isCriticalIssueNotAnalysed = (mode, status, result) =>
  mode === "simulate" &&
  status === CriticalIssueStatus.PENDING &&
  (result === null || result === undefined);

export const OUT_OF_DATE_MESSAGE =
  "Found before the run's latest grading. Press Refresh to update them.";

// A stored summary with "pending" was made before the run's verdicts last
// changed (a rerun or a late verdict); nobody has asked for a new one.
export const isCriticalIssueOutOfDate = (mode, status, result) =>
  mode === "simulate" &&
  status === CriticalIssueStatus.PENDING &&
  result !== null &&
  result !== undefined;
