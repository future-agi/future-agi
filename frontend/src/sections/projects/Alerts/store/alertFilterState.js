// Shared between the alerts list and the alert-detail issues table: both hold
// FilterPanel's `{field: [values]}` shape and translate it for their endpoint.

const EMPTY = new Set();

// Only `project_id` goes to the monitor-list endpoint as a repeated param.
export const ALERT_LIST_MULTI_VALUE_FIELDS = new Set(["project_id"]);

export const hasAnyValue = (filters) =>
  !!filters && Object.values(filters).some((v) => v?.length > 0);

/**
 * FilterPanel emits `{field: [values]}`. Translate that into query params,
 * unwrapping to a scalar unless the field goes to the API as a repeated param.
 */
export const buildFilterParams = (activeFilters, multiValueFields = EMPTY) => {
  if (!activeFilters) return null;

  const params = Object.entries(activeFilters).reduce(
    (acc, [field, values]) => {
      if (!values?.length) return acc;
      acc[field] = multiValueFields.has(field) ? values : values[0];
      return acc;
    },
    {},
  );

  return Object.keys(params).length > 0 ? params : null;
};

/** The state both stores hold, for spreading into `create`. */
export const createFilterSlice = (set) => ({
  // `{field: [values]}` as produced by FilterPanel, or null when cleared.
  activeFilters: null,
  hasValidFilters: false,

  setActiveFilters: (filters) =>
    set({
      activeFilters: hasAnyValue(filters) ? filters : null,
      hasValidFilters: hasAnyValue(filters),
    }),
});

export const CLEARED_FILTER_STATE = {
  activeFilters: null,
  hasValidFilters: false,
};
