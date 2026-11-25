import { getSafeActionErrorMessage } from "src/utils/errorUtils";

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

export const FAILED_MESSAGE =
  "The last analysis could not be finished. Press Refresh to try again.";

export const FAILED_WITH_EARLIER_MESSAGE =
  "The last analysis could not be finished; these are the issues found before it. Press Refresh to try again.";

export const REFRESH_NOT_STARTED_MESSAGE =
  "The analysis could not be started. Please try again.";

// A simulation run's last requested summary failed. Any summary stored
// before it is kept and shown under the failed note.
export const isCriticalIssueFailed = (mode, status) =>
  mode === "simulate" && status === CriticalIssueStatus.FAILED;

// A refused refresh: the server's own reason when it is one a person can act
// on (the run is still being graded), else that the analysis did not start.
export const refreshErrorMessage = (error) =>
  getSafeActionErrorMessage(error, REFRESH_NOT_STARTED_MESSAGE);

// A stored summary with "pending" was made before the run's verdicts last
// changed (a rerun or a late verdict); nobody has asked for a new one.
export const isCriticalIssueOutOfDate = (mode, status, result) =>
  mode === "simulate" &&
  status === CriticalIssueStatus.PENDING &&
  result !== null &&
  result !== undefined;
