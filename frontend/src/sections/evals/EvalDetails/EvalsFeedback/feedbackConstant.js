export const OUTPUT_TYPES = {
  TEXT: "text",
  BOOL: "bool",
  FLOAT: "float",
  INT: "int",
  STR_LIST: "str_list",
};

export const RADIO_VALUES = {
  PASSED: "passed",
  FAILED: "failed",
};

export const ACTION_TYPES = {
  RETUNE: "retune",
  RECALCULATE: "recalculate",
  RETUNE_RECALCULATE: "retune_recalculate",
  RECALCULATE_ROW: "recalculate_row",
};

// Feedback left on a trace span: Observe, or a prototype run (source
// "experiment" keyed by span, so no experiment id resolves). The tracer saves
// its value, explanation and follow-up action, only for the person who left it.
export const isTraceFeedback = (feedback) =>
  feedback?.source === "observe" ||
  (feedback?.source === "experiment" &&
    !feedback?.experiment_id &&
    Boolean(feedback?.custom_eval_config_id));

// The note the person wrote. Trace feedback keeps it in feedback_improvement
// and the eval's own explanation in explanation; other feedback keeps the
// person's note in explanation.
export const feedbackNote = (feedback) => {
  if (!feedback) return "";
  if (isTraceFeedback(feedback)) return feedback.feedback_improvement || "";
  return feedback.explanation || "";
};

// The eval's own explanation, when the row still carries it: only trace
// feedback stores it, until an edit replaces it with the person's note.
export const feedbackEvalExplanation = (feedback) => {
  if (!isTraceFeedback(feedback)) return "";
  const explanation = feedback.explanation || "";
  return explanation === feedbackNote(feedback) ? "" : explanation;
};

// Why Edit Feedback can't save this row, or null when it can. Trace feedback
// is saved by the tracer only for its author; experiment feedback needs the
// experiment it was left on. Rows that don't carry an author id aren't blocked;
// the server still checks the author.
export const feedbackEditBlockedReason = (row, currentUserId) => {
  if (!row) return null;
  if (isTraceFeedback(row)) {
    const authorKnown = Object.prototype.hasOwnProperty.call(row, "user_id");
    if (authorKnown && String(row.user_id) !== String(currentUserId ?? "")) {
      return "Only the person who left this feedback can edit it.";
    }
    return null;
  }
  if (row.source === "experiment" && !row.experiment_id) {
    return "Edit this feedback from the experiment it was left on.";
  }
  return null;
};
