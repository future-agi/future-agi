import { useEffect, useRef } from "react";
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
  validateScenarios,
  getScenarioValidation,
  getRunningScenarioValidation,
  archiveScenario,
  restoreScenario,
  getSourceChanges,
} from "src/api/simulate-environments/scenarios";
import { harnessEnvironmentKey } from "src/api/simulate-environments/environment";
import {
  environmentVersionsKey,
  refreshDrafts,
} from "src/api/simulate-environments/versions";

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
  // Proved, kept as written and not proved over the whole suite; the lifecycle counts carry the
  // same three keys plus the four states and replace the proof counts when present.
  proofCounts: data?.lifecycle_counts ?? data?.proof_counts ?? null,
  // The scenario fields only the grader reads, decided by the environment's rules.
  privateFields: Array.isArray(data?.private_fields) ? data.private_fields : [],
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
// the suite and the counts. An edit to a versioned environment also records a
// new scenario version, so the version history is refreshed too.
export function useAmendScenarios(jobId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (body) => amendScenarios(jobId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: harnessScenariosListKey(jobId) });
      // The header and tab badge read the scenario count from the detail.
      queryClient.invalidateQueries({ queryKey: harnessEnvironmentKey(jobId) });
      // Only the list: a saved version's own diff and scenarios never change.
      queryClient.invalidateQueries({
        queryKey: environmentVersionsKey(jobId),
        exact: true,
      });
      // With versioning on, the edit lands in the unsaved changes.
      refreshDrafts(queryClient, jobId);
    },
  });
}

// A scenario validation can run up to its deadline, so it is read every 15s while it runs.
export const VALIDATION_POLL_MS = 15_000;

// Under the list prefix, so whatever refreshes the list (a validation ending, an archive) reads it again.
export const runningValidationKey = (envId) => [
  ...harnessScenariosListKey(envId),
  "running-validation",
];

// The default pair's validation still running, so a reload or another page follows it. Read again while it runs;
// when it ends, the scenarios and the run plan are read again, since what can run changed.
export function useRunningScenarioValidation(envId) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: runningValidationKey(envId),
    queryFn: () => getRunningScenarioValidation(envId),
    enabled: Boolean(envId),
    refetchInterval: (q) => (q.state.data?.job_id ? VALIDATION_POLL_MS : false),
    meta: { errorHandled: true },
  });
  const jobId = query.data?.job_id ?? null;
  const was = useRef(jobId);
  useEffect(() => {
    if (was.current && !jobId) {
      queryClient.invalidateQueries({
        queryKey: harnessScenariosListKey(envId),
      });
      queryClient.invalidateQueries({
        queryKey: [...environmentVersionsKey(envId), "run-plan"],
      });
    }
    was.current = jobId;
  }, [jobId, envId, queryClient]);
  return query;
}

export function useValidateScenarios(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (args = {}) => validateScenarios(envId, args),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: runningValidationKey(envId),
        exact: true,
      });
    },
  });
}

// Poll one validation until it settles, then refetch the list so each case shows its new state.
export function useScenarioValidation(envId, validationJobId) {
  const queryClient = useQueryClient();
  return useQuery({
    // Outside the list prefix: a key under it would be refetched by the invalidate below, forever.
    queryKey: ["harness-scenario-validation", envId, validationJobId],
    queryFn: async () => {
      const status = await getScenarioValidation(envId, validationJobId);
      if (status.state !== "validating") {
        queryClient.invalidateQueries({
          queryKey: harnessScenariosListKey(envId),
        });
      }
      return status;
    },
    enabled: Boolean(envId && validationJobId),
    refetchInterval: (query) =>
      !query.state.data || query.state.data.state === "validating"
        ? VALIDATION_POLL_MS
        : false,
    meta: { errorHandled: true },
  });
}

// Archive or restore one scenario; the caller reads `live` and `lifecycle` from the answer.
export function useArchiveScenario(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ key, reason, restore = false }) =>
      restore
        ? restoreScenario(envId, key)
        : archiveScenario(envId, key, reason),
    onSuccess: (data) => {
      queryClient.invalidateQueries({
        queryKey: harnessScenariosListKey(envId),
      });
      queryClient.invalidateQueries({ queryKey: harnessEnvironmentKey(envId) });
      // A restore that isn't live lands in the unsaved changes.
      if (data?.live === false) refreshDrafts(queryClient, envId);
    },
  });
}

// The rules and files an environment version changed that scenarios cite, before and after.
export function useSourceChanges(envId, versionId, { enabled = true } = {}) {
  return useQuery({
    queryKey: [...harnessScenariosListKey(envId), "source-changes", versionId],
    queryFn: () => getSourceChanges(envId, versionId),
    meta: { errorHandled: true },
    enabled: Boolean(enabled && envId && versionId),
  });
}
