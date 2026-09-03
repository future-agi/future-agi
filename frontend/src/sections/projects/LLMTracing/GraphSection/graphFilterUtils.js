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
 * Keep the merge order stable so every visualization describes the same row
 * set as its grid: validated grid/saved-view filters, explicit graph filters,
 * Display filters, eval-only, then the date constraint. The first explicit
 * created_at filter wins by that same source precedence; all other created_at
 * entries are omitted so the graph never receives conflicting date ranges.
 *
 * UI-only keys (the FE React-key `id`, etc.) are NOT stripped here — callers
 * pass the result through `toBackendFilters` (../common) at the POST
 * boundary, the single place that owns that concern.
 */
export const combineGraphFilters = ({
  filters,
  extraFilters,
  metricFilters,
  dateFilter,
  hasEvalFilter,
}) => {
  const merged = [
    ...(filters || []),
    ...(extraFilters || []),
    ...(metricFilters || []),
  ];
  const explicitDateFilter = merged.find(isCreatedAtFilter);
  const nonDateFilters = merged.filter((filter) => !isCreatedAtFilter(filter));

  return [
    ...nonDateFilters,
    ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
    ...(explicitDateFilter
      ? [explicitDateFilter]
      : buildDefaultDateEntry([], dateFilter)),
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

/**
 * Return the one project selected by a positive Project filter.
 *
 * Cross-project user detail has no route-level project id. Its property,
 * retained-attribute, and value catalogs can become project-scoped only after
 * the user chooses Project in the filter panel. Do not guess a scope for a
 * multi-project, negative, or otherwise non-equality predicate.
 */
export const singleProjectIdFromFilters = (filters) => {
  const projectFilters = (filters || []).filter(
    (filter) => filter?.column_id === "project_id",
  );
  if (projectFilters.length !== 1) return null;

  const config = projectFilters[0]?.filter_config || {};
  const operator = config.filter_op;
  if (!new Set(["equals", "is", "in"]).has(operator)) return null;

  const rawValue = config.filter_value;
  const values = (Array.isArray(rawValue) ? rawValue : [rawValue]).filter(
    (value) => typeof value === "string" && value.length > 0,
  );
  return values.length === 1 ? values[0] : null;
};

/**
 * Resolve topology scopes independently for the two compare panes.
 *
 * Project Observe routes have one authoritative route project. Cross-project
 * user detail has no such route scope, so each pane must supply its own single
 * positive Project filter. Never let the currently edited filter panel choose
 * the other pane's project.
 */
export const resolveAgentGraphProjectScopes = ({
  routeProjectId,
  primaryFilters,
  compareFilters,
}) => ({
  primaryProjectId:
    routeProjectId || singleProjectIdFromFilters(primaryFilters),
  compareProjectId:
    routeProjectId || singleProjectIdFromFilters(compareFilters),
});
