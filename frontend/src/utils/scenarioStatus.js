export const SCENARIO_STATUS = {
  PROCESSING: "Processing",
  RUNNING: "Running",
  COMPLETED: "Completed",
  FAILED: "Failed",
};

export const SCENARIO_IN_PROGRESS_STATUSES = [
  SCENARIO_STATUS.PROCESSING,
  SCENARIO_STATUS.RUNNING,
];

const normalize = (status) => status?.toString().toLowerCase();

export const isScenarioInProgress = (status) =>
  SCENARIO_IN_PROGRESS_STATUSES.some((s) => normalize(s) === normalize(status));

export const isScenarioFailed = (status) =>
  normalize(status) === normalize(SCENARIO_STATUS.FAILED);

export const isScenarioCompleted = (status) =>
  normalize(status) === normalize(SCENARIO_STATUS.COMPLETED);
