import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  amendHarnessScenarios,
  getHarnessScenarioCoverage,
  listHarnessScenarios,
} from "src/api/harness/harness";

export const HARNESS_SCENARIOS_KEY = ["harness-scenarios"];
// The prefix every page of one run's suite shares, so invalidating it refetches whatever page,
// filter and grouping are on screen without resetting any of them.
export const scenariosListKey = (jobId) => [...HARNESS_SCENARIOS_KEY, jobId];
export const scenariosQueryKey = (jobId, params) => [
  ...scenariosListKey(jobId),
  params,
];

// Map the raw paginated payload to what the tab draws. `fields` is the filter panel's own
// catalogue and `scenario_editing` the drawer's, both counted and decided by the server, so the
// client keeps no copy of either.
const toPage = (data) => ({
  rows: Array.isArray(data?.results) ? data.results : [],
  total: data?.count ?? 0,
  totalPages: data?.total_pages ?? 1,
  fields: Array.isArray(data?.fields) ? data.fields : [],
  // The sections the server cut this page into, already in row order and counted.
  groups: Array.isArray(data?.groups) ? data.groups : [],
  // What the "group by" control offers, decided by the server.
  groupings: Array.isArray(data?.groupings) ? data.groupings : [],
  // Which grouping the server actually applied, so the control can show it before anyone picks.
  groupBy: data?.group_by ?? "",
  editing: data?.scenario_editing ?? null,
  // What each coverage level and noise bed is called. Served, never spelled here: renaming one is
  // a change in the backend and none in this app.
  levelLabels: data?.level_labels ?? {},
});

/**
 * One page of a run's authored suite. The search, the filters, the ordering, the grouping and the
 * paging all happen on the server, so a filter describes the whole suite rather than whichever
 * page had been downloaded.
 *
 * `page` is the table's 0-indexed page; the endpoint is 1-indexed. `placeholderData` holds the
 * current page on screen while the next loads, so paging and filtering never flash an empty table.
 */
export function useHarnessScenarios(
  jobId,
  {
    page = 0,
    pageSize = 25,
    search = "",
    groupBy,
    filters = {},
    rowAxis = "",
    colAxis = "",
    refetchInterval,
  } = {},
) {
  const params = {
    page: page + 1,
    limit: pageSize,
    search,
    ...(groupBy === null || groupBy === undefined ? {} : { group_by: groupBy }),
    row_axis: rowAxis,
    col_axis: colAxis,
    ...filters,
  };
  return useQuery({
    queryKey: scenariosQueryKey(jobId, params),
    queryFn: () => listHarnessScenarios(jobId, params),
    select: toPage,
    enabled: Boolean(jobId),
    placeholderData: keepPreviousData,
    refetchInterval,
  });
}

/**
 * Edit or drop scenarios. The harness answers one receipt per requested change rather than a
 * single verdict, so the caller reads the receipts and surfaces the refusals itself; the global
 * mutation toast is suppressed to avoid saying it twice.
 */
export function useAmendScenarios(jobId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ changes, rework }) =>
      amendHarnessScenarios(jobId, changes, { rework }),
    // Refetch the visible page: a dropped row shifts the rest up from later pages, which cannot
    // be done correctly by editing one page in place.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scenariosListKey(jobId) });
    },
  });
}

export const scenarioCoverageKey = (jobId, params) => [
  ...scenariosListKey(jobId),
  "coverage",
  params,
];

/**
 * The coverage grid, on its own route. It describes the whole suite rather than a page of it, so
 * it does not move when the reader pages or regroups, and asking for it separately keeps a
 * whole-suite cross-tab off the list's polling path.
 */
export function useHarnessScenarioCoverage(
  jobId,
  { search = "", filters = {}, rowAxis = "", colAxis = "" } = {},
) {
  // The grid does not move with the page, but it does move with the filter, so it carries the
  // list's search and filters and none of its paging.
  const params = { search, ...filters, row_axis: rowAxis, col_axis: colAxis };
  return useQuery({
    queryKey: scenarioCoverageKey(jobId, params),
    queryFn: () => getHarnessScenarioCoverage(jobId, params),
    enabled: Boolean(jobId),
    placeholderData: keepPreviousData,
  });
}
