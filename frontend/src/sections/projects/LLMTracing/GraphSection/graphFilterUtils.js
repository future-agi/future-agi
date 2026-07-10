import { FILTER_FOR_HAS_EVAL } from "../common";

// Shared by PrimaryGraph.jsx and GraphSection.jsx so the "created_at"
// literal and the default-date-entry construction exist in exactly one place.
export const CREATED_AT = "created_at";

export const isCreatedAtFilter = (f) => f?.column_id === CREATED_AT;

// Default created_at entry derived from the date picker, added only when the
// combined filters don't already carry an explicit created_at filter.
export const buildDefaultDateEntry = (existingFilters, dateFilter) => {
  const hasDateFilter = (existingFilters || []).some(isCreatedAtFilter);
  const startDate = dateFilter?.dateFilter?.[0];
  const endDate = dateFilter?.dateFilter?.[1];
  if (hasDateFilter || !startDate || !endDate) return [];
  return [
    {
      column_id: CREATED_AT,
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: [
          new Date(startDate).toISOString(),
          new Date(endDate).toISOString(),
        ],
      },
    },
  ];
};

/**
 * Combine filters for the graph POST body.
 *
 * Two modes depending on whether the caller provides `extraFilters`:
 *   Trace/Span mode (`extraFilters` passed, LLMTracingView): strip `filters`
 *     down to the date entry only — other col-level filters (name, status, …)
 *     carry no col_type and the trace/span graph endpoint rejects them. All
 *     non-date graph filters come via `extraFilters` from the toolbar.
 *   Users/Sessions mode (`extraFilters` omitted → undefined): the caller
 *     already merged everything into `filters`; pass them all through
 *     unchanged so the users/sessions graph stays in sync with its table.
 *
 * The mode check is strict prop presence (`undefined`), NOT emptiness: an
 * empty extraFilters array is a valid trace-mode state (toolbar filters
 * cleared) and must still strip col-level filters.
 *
 * UI-only keys (the FE React-key `id`, etc.) are NOT stripped here — callers
 * pass the result through `toBackendFilters` (../common) at the POST
 * boundary, the single place that owns that concern.
 */
export const combineGraphFilters = ({
  filters,
  extraFilters,
  dateFilter,
  hasEvalFilter,
}) => {
  const isTracingMode = extraFilters !== undefined;
  const baseFilters = isTracingMode
    ? (filters || []).filter(isCreatedAtFilter)
    : filters || [];
  const base = [...baseFilters, ...(extraFilters || [])];

  return [
    ...base,
    ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
    ...buildDefaultDateEntry(base, dateFilter),
  ];
};

// Which filter list hydrates the shared filter panel (ObserveToolbar):
// editing the Compare Graph's filters must show/overwrite compare filters,
// never the primary ones.
export const selectPanelGraphFilters = (
  filterTarget,
  extraFilters,
  compareExtraFilters,
) => (filterTarget === "compare" ? compareExtraFilters : extraFilters);
