/**
 * Workflow Execution States and Status Mapping
 */

// Node execution states
export const NODE_EXECUTION_STATE = {
  IDLE: "idle",
  RUNNING: "running",
  COMPLETED: "completed",
  ERROR: "error",
  SKIPPED: "skipped",
};

// Workflow execution states
export const WORKFLOW_STATE = {
  IDLE: "idle",
  RUNNING: "running",
  COMPLETED: "completed",
  ERROR: "error",
};

// API response execution statuses (lowercased for comparison)
export const EXECUTION_STATUS = {
  PENDING: "pending",
  RUNNING: "running",
  SUCCESS: "success",
  ERROR: "error",
  FAILED: "failed",
  SKIPPED: "skipped",
};

export const isSkippedStatus = (status) =>
  status?.toLowerCase() === EXECUTION_STATUS.SKIPPED;

/**
 * Map an API execution status string to the corresponding NODE_EXECUTION_STATE.
 * Returns null for unrecognized statuses.
 */
export function mapApiStatusToNodeState(apiStatus) {
  switch (apiStatus) {
    case EXECUTION_STATUS.SUCCESS:
      return NODE_EXECUTION_STATE.COMPLETED;
    case EXECUTION_STATUS.FAILED:
    case EXECUTION_STATUS.ERROR:
      return NODE_EXECUTION_STATE.ERROR;
    case EXECUTION_STATUS.RUNNING:
      return NODE_EXECUTION_STATE.RUNNING;
    case EXECUTION_STATUS.SKIPPED:
      return NODE_EXECUTION_STATE.SKIPPED;
    case EXECUTION_STATUS.PENDING:
      return null;
    default:
      return null;
  }
}
