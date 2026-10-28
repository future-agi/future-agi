import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import {
  listScenarios,
  scenarioFromApi,
  amendScenarios,
  scenarioCoverage,
} from "src/api/simulate-environments/scenarios";

/**
 * The scenarios list read path, mirroring useMyEnvironments: 0-indexed page in
 * the UI, 1-indexed on the wire, `select: toPage`, and keepPreviousData so a
 * page/filter change holds the current rows on screen instead of flashing empty.
 *
 * The list is safe to poll (§6), so it refetches on a 15s tick; filters, page
 * and grouping all live in the key, so a poll never resets what the user chose.
 * The `["harness-scenarios", jobId]` prefix lets Phase 2 invalidate the suite
 * after an amend without knowing the current page.
 */

export const harnessScenariosListKey = (jobId) => ["harness-scenarios", jobId];

export const harnessScenariosKey = (
  jobId,
  { page = 0, pageSize = 25, search = "", groupBy, ordering, filters = {} } = {},
) => [
  ...harnessScenariosListKey(jobId),
  { page, pageSize, search, groupBy, ordering, filters },
];

// Map the RAW response to the page the seam reads: mapped rows plus the server's
// counts, sections and filter catalogue.
const toPage = (data) => ({
  rows: (Array.isArray(data?.results) ? data.results : []).map(scenarioFromApi),
  total: data?.count ?? 0,
  pageCount: data?.total_pages ?? 1,
  groups: Array.isArray(data?.groups) ? data.groups : [],
  groupBy: data?.group_by ?? "",
  groupings: Array.isArray(data?.groupings) ? data.groupings : [],
  fields: Array.isArray(data?.fields) ? data.fields : [],
  scenarioEditing: data?.scenario_editing ?? null,
  levelLabels: data?.level_labels ?? {},
});

export function useHarnessScenarios({
  jobId,
  page = 0,
  pageSize = 25,
  search = "",
  groupBy,
  ordering,
  filters = {},
} = {}) {
  return useQuery({
    queryKey: harnessScenariosKey(jobId, { page, pageSize, search, groupBy, ordering, filters }),
    queryFn: () =>
      listScenarios(jobId, {
        page: page + 1,
        limit: pageSize,
        search,
        group_by: groupBy,
        ordering,
        ...filters,
      }),
    select: toPage,
    placeholderData: keepPreviousData,
    refetchInterval: 15000,
    enabled: Boolean(jobId),
  });
}

// The coverage grid key sits UNDER the list prefix, so one invalidate of
// `harnessScenariosListKey(jobId)` after an amend refetches both the list and
// the coverage. The grid changes with the filter but not the page, so search +
// filters + the two axes are the only things in the key.
export const harnessScenarioCoverageKey = (
  jobId,
  { search = "", filters = {}, rowAxis, colAxis } = {},
) => [...harnessScenariosListKey(jobId), "coverage", { search, filters, rowAxis, colAxis }];

export function useScenarioCoverage(
  jobId,
  { search = "", filters = {}, rowAxis, colAxis } = {},
) {
  return useQuery({
    queryKey: harnessScenarioCoverageKey(jobId, { search, filters, rowAxis, colAxis }),
    queryFn: () =>
      scenarioCoverage(jobId, {
        search,
        // Send an axis only when the user picked one, so the server default
        // (task × overlay) stands until then.
        ...(rowAxis ? { row_axis: rowAxis } : {}),
        ...(colAxis ? { col_axis: colAxis } : {}),
        ...filters,
      }),
    placeholderData: keepPreviousData,
    enabled: Boolean(jobId),
  });
}

// Edit / delete scenarios through the amend route. The caller surfaces the
// receipts (including `why` on a refusal), so the global error toast is
// suppressed. A success invalidates the whole scenarios prefix — both the
// visible list page and the coverage grid — since a drop or a re-proof reshapes
// the suite and the counts.
export function useAmendScenarios(jobId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (body) => amendScenarios(jobId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: harnessScenariosListKey(jobId) });
    },
  });
}
