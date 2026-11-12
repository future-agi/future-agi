import { keepPreviousData, useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { compareRunsKeyPrefix } from "./runEvals";
import { buildTraceColumns, mapCallRow, mapGroupAggregates } from "./runCalls";

/**
 * Two to eight finished runs of one environment, scenario by scenario. The
 * first run is the baseline. Search, filters and group-by are the run detail
 * calls table's; pages count scenario rows. Not gated on versioning.
 */

export const COMPARE_PAGE_SIZE = 25;
export const MAX_COMPARE_RUNS = 8;

// Built per request: a path missing from the contract throws, and the page
// should only fail when it asks.
const comparePath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/compare-runs/", {
    id: envId,
  });

/**
 * @param {{ page?: number, pageSize?: number, search?: string, filters?: Object, groupBy?: string, ordering?: string }} [opts]
 */
const withDefaults = ({
  page = 1,
  pageSize = COMPARE_PAGE_SIZE,
  search = "",
  filters = {},
  groupBy = "goal",
  ordering = "movement",
} = {}) => ({ page, pageSize, search, filters, groupBy, ordering });

/**
 * The request's query params, as the server reads them.
 * @param {string[]} runIds
 * @param {Object} [opts]
 */
export function compareRunsParams(runIds, opts) {
  const { page, pageSize, search, filters, groupBy, ordering } =
    withDefaults(opts);
  return {
    runs: runIds.join(","),
    page,
    page_size: pageSize,
    search,
    filters: JSON.stringify(filters),
    ...(groupBy ? { group_by: groupBy } : {}),
    ordering,
  };
}

export const getRunComparison = async (envId, runIds, opts) =>
  (
    await axios.get(comparePath(envId), {
      params: compareRunsParams(runIds, opts),
    })
  ).data;

// Under the prefix the run page and the evals drawer invalidate when grades
// change.
export const compareRunsKey = (envId, runIds, opts) => {
  const { page, pageSize, search, filters, groupBy, ordering } =
    withDefaults(opts);
  return [
    ...compareRunsKeyPrefix,
    envId,
    runIds.join(","),
    page,
    pageSize,
    search,
    filters,
    groupBy,
    ordering,
  ];
};

/**
 * One run of the comparison: its identity and whole-run pooled pass rate
 * (`execution.summary`, as the runs list shows it), plus its figures over the
 * matching calls.
 * @param {Object} raw  A `runs[]` item.
 */
export function mapCompareRun(raw) {
  const ex = raw?.execution ?? {};
  const wholeRun = ex.summary ?? {};
  return {
    id: ex.id,
    ordinal: ex.ordinal ?? null,
    isBaseline: !!raw?.is_baseline,
    status: ex.status ?? null,
    agentType: ex.agent_type ?? null,
    agentLabel: ex.pinned_agent_version?.label ?? ex.agent_version ?? null,
    environmentLabel: ex.environment_version?.label ?? null,
    trials: ex.trials ?? 1,
    passRate:
      wholeRun.pass_rate == null ? null : Math.round(wholeRun.pass_rate),
    measured: wholeRun.measured ?? 0,
    passed: wholeRun.outcomes?.passed ?? 0,
    total: wholeRun.total ?? 0,
    summary: raw?.summary ?? null,
    statusCounts: raw?.status_counts ?? {},
    histograms: raw?.histograms ?? {},
  };
}

/**
 * The whole response, adapted for the compare page. Every call goes through
 * the run detail's `mapCallRow`, so a call reads the same on both pages.
 * @param {Object} data
 */
export function mapCompareResponse(data) {
  const evalColumns = data?.evaluation_columns ?? [];
  const runs = (data?.runs ?? []).map(mapCompareRun);
  const rows = (data?.rows ?? []).map((row) => ({
    rowId: row.row_id,
    scenarioKey: row.scenario_key,
    scenario: row.scenario ?? null,
    useCase: row.use_case ?? null,
    persona: row.persona ?? null,
    situation: row.situation ?? null,
    idealOutcome: row.ideal_outcome ?? null,
    movement: row.movement ?? "same",
    calls: Object.fromEntries(
      runs.map((run) => [
        run.id,
        (row.calls?.[run.id] ?? []).map((call) =>
          mapCallRow(call, evalColumns),
        ),
      ]),
    ),
  }));
  const groups = (data?.groups ?? []).map((g) => ({
    key: g.key,
    label: g.label,
    rowIds: g.row_ids ?? [],
    // Every row of the group, not only this page's.
    rowCount: g.row_count ?? g.row_ids?.length ?? 0,
    runs: Object.fromEntries(
      Object.entries(g.runs ?? {}).map(([id, entry]) => [
        id,
        mapGroupAggregates(entry),
      ]),
    ),
  }));
  return {
    baselineId: data?.baseline_execution_id ?? runs[0]?.id ?? null,
    runs,
    rows,
    groups,
    evalColumns,
    columns: buildTraceColumns(evalColumns),
    facets: data?.facets ?? {},
    levelLabels: data?.level_labels ?? {},
    changedCount: data?.changed_count ?? 0,
    count: data?.count ?? 0,
    page: data?.page ?? 1,
    pageSize: data?.page_size ?? COMPARE_PAGE_SIZE,
    totalPages: data?.total_pages ?? 1,
  };
}

const errorStatus = (error) => error?.statusCode ?? error?.response?.status;
// A refused comparison answers the same way every time; only a server or
// network failure is worth asking again.
const retryServerFailures = (failureCount, error) => {
  const status = errorStatus(error);
  return (status === undefined || status >= 500) && failureCount < 2;
};

/**
 * @param {string} envId
 * @param {string[]} runIds  First = baseline.
 * @param {{ page?: number, pageSize?: number, search?: string, filters?: Object, groupBy?: string, ordering?: string }} [opts]
 */
export function compareRunsQueryOptions(envId, runIds, opts) {
  return {
    queryKey: compareRunsKey(envId, runIds, opts),
    queryFn: () => getRunComparison(envId, runIds, opts),
    select: mapCompareResponse,
    enabled: Boolean(envId) && runIds.length >= 2,
    placeholderData: keepPreviousData,
    retry: retryServerFailures,
    staleTime: 1000 * 60 * 5,
    meta: { errorHandled: true },
  };
}

export function useCompareRuns(envId, runIds, opts) {
  return useQuery(compareRunsQueryOptions(envId, runIds, opts));
}
