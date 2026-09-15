export const ENV_STATUS = {
  BUILDING: "building",
  NOT_RUN: "not_run",
  RUNNING: "running",
  PASSED: "passed",
  FAILED: "failed",
  COMPLETED: "completed",
};

// Semantic status colours the designer chose for the run-state pill. Kept as
// literals in this one module (see TH-7961 C7) — error.main is a different red
// and accent tokens shift in dark mode, so neither substitutes cleanly.
export const STATUS_META = {
  building: { label: "Building", color: "#7857FC" },
  not_run: { label: "Not run yet", color: "#9CA3AF" },
  running: { label: "Running…", color: "#2563EB" },
  passed: { label: "Passed", color: "#16A34A" },
  failed: { label: "Failed", color: "#DC2626" },
  completed: { label: "Completed", color: "#CA8A04" },
};

export const ROW_ACTION = { RUN: "run", DELETE: "delete" };

export const ROW_ACTION_LABEL = {
  run: "Run simulation",
  rerun: "Re-run simulation",
  delete: "Delete",
};

export const BUILDING_TOOLTIP = "Wait for setup to finish";

export const DELETE_DIALOG_COPY = {
  title: "Delete environment?",
  body: "and everything derived from it — scenarios, personas, evals and run history — will be removed from this workspace. This cannot be undone.",
  cancel: "Cancel",
  confirm: "Delete",
};

// The delete button's red — kept out of JSX so it never drifts (see C7).
export const DELETE_TONE = { main: "#DC2626", hover: "#B91C1C" };

export const EMPTY_MESSAGE = "No environments yet";
export const ROW_HEIGHT = 52;
export const DEFAULT_PAGE_SIZE = 25;
