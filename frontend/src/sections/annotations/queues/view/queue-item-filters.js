export const ITEM_STATUS_FILTER_OPTIONS = [
  { value: "pending", label: "Pending Annotation" },
  { value: "in_review", label: "In Review" },
  { value: "needs_changes", label: "Needs Changes" },
  { value: "resubmitted", label: "Resubmitted" },
  { value: "completed", label: "Completed" },
  { value: "skipped", label: "Skipped" },
];

export const ITEM_SOURCE_FILTER_OPTIONS = [
  { value: "dataset_row", label: "Dataset Row" },
  { value: "trace", label: "Trace" },
  { value: "observation_span", label: "Span" },
  { value: "trace_session", label: "Session" },
  // { value: "prototype_run", label: "Prototype" },
  { value: "call_execution", label: "Simulation" },
];

export const ALL_STATUS_VALUES = ITEM_STATUS_FILTER_OPTIONS.map(
  (option) => option.value,
);
export const ALL_SOURCE_VALUES = ITEM_SOURCE_FILTER_OPTIONS.map(
  (option) => option.value,
);
export const NO_FILTER_MATCH_VALUE = "__none__";

export const buildQueueItemQueryFilters = (filters) => {
  const normalizeMultiFilter = (selectedValues, allValues) => {
    if (!Array.isArray(selectedValues)) return selectedValues || undefined;
    if (selectedValues.length === allValues.length) return undefined;
    return selectedValues.length ? selectedValues : NO_FILTER_MATCH_VALUE;
  };

  return {
    status: normalizeMultiFilter(filters.status, ALL_STATUS_VALUES),
    source_type: normalizeMultiFilter(filters.source_type, ALL_SOURCE_VALUES),
    assigned_to: filters.assigned_to || undefined,
    review_status: filters.review_status || undefined,
  };
};
