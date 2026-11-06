// Shared lifecycle mapping for the runs list and detail header.
const RUN_STATE = {
  pending: "queued",
  running: "running",
  // Every call has ended and their evals are still grading.
  evaluating: "grading",
  cancelling: "cancelling",
  completed: "finished",
  failed: "failed",
  cancelled: "cancelled",
};

export const runStateFor = (status) => RUN_STATE[status?.toLowerCase()];

// An eval's or CSAT's scoring state, as the v3 calls API sends it. `error` is
// what older payloads sent for a failed eval.
export const SCORING_STATUS = {
  PENDING: "pending",
  FAILED: "failed",
  ERROR: "error",
  TIMED_OUT: "timed_out",
  SKIPPED: "skipped",
};

// The statuses whose dot animates — a run in one of these phases is still
// moving, so the chip breathes.
export const LIVE_STATUSES = ["running", "booting", "grading", "cancelling"];

// TestExecution lifecycle states that can still advance and need polling.
export const ACTIVE_EXECUTION_STATUSES = new Set([
  "pending",
  "running",
  "cancelling",
  "evaluating",
]);

// The subset a user can still stop — `cancelling` is already on its way out,
// and grading (`evaluating`) finishes on its own.
export const STOPPABLE_EXECUTION_STATUSES = new Set(["pending", "running"]);

// Stable hook for the pulsing dot so callers (and tests) can target it without
// depending on emotion's generated class name.
export const PULSING_DOT_CLASS = "sim-status-dot--pulse";

// The run status vocabulary: a label and the status role (theme.palette.status)
// each state takes. `flaky` and `unmeasured` are deliberately not danger: a
// flaky scenario disagreed with itself (the scenario is the problem, not the
// agent), and an unmeasured run means nothing upstream of the agent worked, so
// there is no verdict to blame the agent for. Roles follow src/utils/runStatus.
export const STATUS_META = {
  in_progress: { role: "info", label: "In progress" },
  inconclusive: { role: "neutral", label: "Inconclusive" },
  queued: { role: "neutral", label: "Queued" },
  booting: { role: "info", label: "Provisioning" },
  running: { role: "info", label: "Running" },
  grading: { role: "info", label: "Grading" },
  passed: { role: "success", label: "Passed" },
  flaky: { role: "warning", label: "Flaky" },
  unmeasured: { role: "neutral", label: "Not measured" },
  // The run header's verdict for mixed results; an all-failed run reads
  // "Failed" instead.
  completed: { role: "neutral", label: "Completed" },
  // Shared lifecycle "Completed" for the runs list and detail header.
  finished: { role: "neutral", label: "Completed" },
  failed: { role: "danger", label: "Failed" },
  error: { role: "danger", label: "Error" },
  // A stopped run until the backend confirms its sandbox is gone.
  cancelling: { role: "neutral", label: "Cancelling" },
  cancelled: { role: "neutral", label: "Cancelled" },
};

// A category colour from the design system's data palette
// (theme.palette.data, 8 colours), wrapped by index. Used for run identity and
// eval series, which are categories rather than statuses.
export const dataColor = (theme, index = 0) => {
  const palette = theme.palette.data;
  if (!palette?.length) return theme.palette.text.secondary;
  return palette[Math.max(0, index) % palette.length];
};

// Run identity colours, indexed by a run's place in the sequence (its ordinal
// minus one, wrapped). They are data-palette tokens (theme.palette.data[i]),
// written as the sx path "data.<i>" so a screen can use them as `color` or
// `bgcolor` without a theme in hand. Keyed off the ordinal, not the list
// position, so a run keeps the same letter and colour on the history list, the
// detail header and a comparison.
export const RUN_COLORS = Array.from({ length: 8 }, (_, i) => `data.${i}`);

// The identity colour for a run at a given 1-based ordinal. A missing or
// non-finite ordinal falls back to the first colour, so a caller never gets
// `undefined`.
const runColorIndex = (ordinal) => {
  const n = Number.isFinite(ordinal) ? ordinal : 1;
  return (Math.max(1, n) - 1) % RUN_COLORS.length;
};

export const runColor = (ordinal) => RUN_COLORS[runColorIndex(ordinal)];

// The same colour as a value, for helpers like alpha() that can't read an sx path.
export const runColorValue = (theme, ordinal) =>
  theme.palette.data[runColorIndex(ordinal)];

// The pass bar's two segments, as status roles.
export const PASS_BAR_ROLES = { passed: "success", failed: "danger" };
