// Lifecycle status of a single eval result. Mirrors the backend
// `EvalEntryStatus` (tracer EvalLogger.status): pending -> running ->
// completed | errored | skipped. The read APIs surface the non-score states
// (pending/running/skipped) so the UI can render a loading / queued / skipped
// cell instead of a misleading blank or 0.
export const EVAL_STATUS = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  ERRORED: "errored",
  SKIPPED: "skipped",
};

const NON_SCORE_LABELS = {
  [EVAL_STATUS.PENDING]: "Queued",
  [EVAL_STATUS.RUNNING]: "Evaluating…",
  [EVAL_STATUS.SKIPPED]: "Skipped",
};

// Normalize a backend status string to one of the non-score lifecycle states
// the UI renders specially, or null when it is a real/terminal result.
export const getEvalNonScoreStatus = (status) => {
  const s = String(status || "").toLowerCase();
  return s in NON_SCORE_LABELS ? s : null;
};

// Pull a non-score lifecycle marker out of a backend eval cell value, which may
// be a scalar score, an `{ error: true }` marker, or a `{ status: ... }` marker.
export const getEvalNonScoreStatusFromValue = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return getEvalNonScoreStatus(value.status || value.eval_status);
};

export const getEvalStatusLabel = (status) =>
  NON_SCORE_LABELS[getEvalNonScoreStatus(status)] || "";
