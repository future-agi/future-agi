export const ENV_STATUS = {
  BUILDING: "building",
  NOT_RUN: "not_run",
  RUNNING: "running",
  FINALIZING: "finalizing",
  CANCELLING: "cancelling",
  CANCELLED: "cancelled",
  PASSED: "passed",
  FAILED: "failed",
  READY: "ready",
};

// The unfinished values of the list endpoint's own status (building | running |
// completed | failed, from harness_environment.status_for). A cancel in flight
// stays building/running until it lands as failed, so these two cover it.
export const LIVE_ENV_STATUSES = new Set([
  ENV_STATUS.BUILDING,
  ENV_STATUS.RUNNING,
]);

// An environment's build lifecycle, distinct from the run-state pill (ENV_STATUS
// above): "ready" once authoring finished, "failed" on a terminal build failure
// (a failed/canceled job stage), "building" while it is still deriving. The
// workspace and its LivePill key off this — see buildStatusFor() in
// helpers/harnessJobToRow.js for the single derivation from a job stage.
export const BUILD_STATUS = {
  READY: "ready",
  BUILDING: "building",
  FAILED: "failed",
};

// The label and status role (theme.palette.status.<role>) of each run-state
// pill; StatusBadge turns the role into colours.
export const STATUS_META = {
  building: { label: "Building", role: "info" },
  not_run: { label: "Not run yet", role: "neutral" },
  running: { label: "Running…", role: "info" },
  finalizing: { label: "Finalizing", role: "info" },
  cancelling: { label: "Cancelling", role: "neutral" },
  cancelled: { label: "Cancelled", role: "neutral" },
  passed: { label: "Passed", role: "success" },
  failed: { label: "Failed", role: "danger" },
  // Built and runnable.
  ready: { label: "Ready", role: "success" },
  // Preflight checks that don't apply to the current source (e.g. a repo source
  // has no hosted provider to reach). Same neutral as an un-run row.
  skipped: { label: "Skipped", role: "neutral" },
};

export const ROW_ACTION = { RUN: "run", DELETE: "delete" };

export const ROW_ACTION_LABEL = {
  open: "Open",
  run: "Run simulation",
  rerun: "Re-run simulation",
  delete: "Delete",
};

export const BUILDING_TOOLTIP = "Wait for setup to finish";

export const DELETE_DIALOG_COPY = {
  title: "Delete environment?",
  body: "and everything derived from it (scenarios, personas, evals, and run history) will be removed from this workspace. This cannot be undone.",
  cancel: "Cancel",
  confirm: "Delete",
};

// The destructive-action colour, from the theme's error palette.
export const DELETE_TONE = { main: "status.danger.fg", hover: "status.danger.solid" };

export const EMPTY_MESSAGE = "No environments yet";
export { ROW_HEIGHT } from "src/theme/sizes";
export const DEFAULT_PAGE_SIZE = 25;
