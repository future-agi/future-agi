// Warning types an eval run can attach to EvalLogger.output_metadata.warnings.
// Keep in sync with model_hub/utils/eval_input_validation.py (partial_input)
// and tracer/utils/eval.py (ground_truth_not_applied).

export const PARTIAL_INPUT_WARNING_TYPE = "partial_input";
export const GROUND_TRUTH_NOT_APPLIED_WARNING_TYPE = "ground_truth_not_applied";

export const WARNING_TYPE_LABELS = {
  [PARTIAL_INPUT_WARNING_TYPE]: "Partial inputs",
  [GROUND_TRUTH_NOT_APPLIED_WARNING_TYPE]: "Ground Truth not applied",
};

// Only for types the server can emit without copy. ground_truth_not_applied is
// not one: the task-logs endpoint always fills its message from the backend
// table, so duplicating the text here would be the drift this module removes.
export const WARNING_TYPE_FALLBACK_MESSAGES = {
  [PARTIAL_INPUT_WARNING_TYPE]:
    "Eval ran with some inputs empty. Result may be less reliable. Ignore if this is intentional.",
};

export const warningTypeLabel = (type) =>
  WARNING_TYPE_LABELS[type] || "Warning";

export const warningMessage = (warning) =>
  warning?.message || WARNING_TYPE_FALLBACK_MESSAGES[warning?.type] || "";
