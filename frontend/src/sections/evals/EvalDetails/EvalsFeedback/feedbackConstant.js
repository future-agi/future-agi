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

// Action-type vocabularies by surface. Each surface's BE accepts a different
// set; the FE wrapper picks the right one. TH-5604 will canonicalize the
// dataset BE handler onto FeedbackActionType, after which the LEGACY set can
// be deleted and every wrapper can use ACTION_TYPES.
//
// ACTION_TYPES — mirrors FeedbackActionType on the BE
// (model_hub/models/choices.py:309-311). Used by the Observe submit_feedback /
// submit_feedback_action_type endpoints, enforced at the serializer layer via
// SubmitFeedbackActionTypeSerializer.action_type = ChoiceField(...).
export const ACTION_TYPES = {
  RETUNE: "retune",
  RECALCULATE: "recalculate",
  RETUNE_RECALCULATE: "retune_recalculate",
};

// LEGACY_DATASET_ACTION_TYPES — what model_hub/views/develop_dataset.py's
// submit_feedback_action handler currently accepts in its inline valid_actions
// list (`:10971`). Used by the dataset + experiment wrappers until TH-5604
// PR B canonicalizes the handler.
export const LEGACY_DATASET_ACTION_TYPES = {
  RETUNE: "retune",
  RECALCULATE_ROW: "recalculate_row",
  RECALCULATE_DATASET: "recalculate_dataset",
};
