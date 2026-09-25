export const ENV_STATUS = {
  BUILDING: "building",
  NOT_RUN: "not_run",
  RUNNING: "running",
  FINALIZING: "finalizing",
  CANCELLING: "cancelling",
  CANCELLED: "cancelled",
  PASSED: "passed",
  FAILED: "failed",
  COMPLETED: "completed",
};

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

// Semantic status colours the designer chose for the run-state pill. Kept as
// literals in this one module — error.main is a different red
// and accent tokens shift in dark mode, so neither substitutes cleanly.
export const STATUS_META = {
  building: { label: "Building", color: "#7857FC" },
  not_run: { label: "Not run yet", color: "#9CA3AF" },
  running: { label: "Running…", color: "#2563EB" },
  finalizing: { label: "Finalizing", color: "#7857FC" },
  cancelling: { label: "Cancelling", color: "#DC2626" },
  cancelled: { label: "Cancelled", color: "#9CA3AF" },
  passed: { label: "Passed", color: "#16A34A" },
  failed: { label: "Failed", color: "#DC2626" },
  completed: { label: "Completed", color: "#CA8A04" },
  // Preflight checks that don't apply to the current source (e.g. a repo source
  // has no hosted provider to reach). Same muted grey as an un-run row.
  skipped: { label: "Skipped", color: "#9CA3AF" },
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

// The delete button's red — kept out of JSX so it never drifts (see C7).
export const DELETE_TONE = { main: "#DC2626", hover: "#B91C1C" };

export const EMPTY_MESSAGE = "No environments yet";
export const ROW_HEIGHT = 52;
export const DEFAULT_PAGE_SIZE = 25;
