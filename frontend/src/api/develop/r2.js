// Round 2 hooks (R2-SPEC §6; wire shapes in src/_mock/prototype/CONTRACT.md
// "Round 2", store records in fixtures/world/r2Shapes.js). Every R2 screen
// reads and writes through these; they touch only R1, R3, I1, S1, IM1 and
// the saved-views endpoints.
//
// Frozen API (R2 manifest row 46): useRunConfigs, useMatrix, useWorkbench,
// useByDataset, useIterate, useLineage, useCodeSession, useCodeSessionAction,
// useImportPreview, useImportCommit, useSavedWorkbenchViews, useTryOnOneRow,
// the query keys and the pure helpers below.
import { useMemo } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { absentError, absentTemplateOf } from "src/launch/absentPathGuard";
import { isLaunchBuild } from "src/launch/launchSwitch";
import {
  datasetRunsKey, datasetRunsParams, fetchDatasetRuns, fetchReadPages, readEnvelope,
} from "src/api/develop/runs";
import { runResultsKey } from "src/api/develop/run-results";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import {
  useCreateWorkspaceSavedView,
  useDeleteWorkspaceSavedView,
  useGetWorkspaceSavedViews,
} from "src/api/project/saved-views";
import { configKeyOf } from "src/sections/common/filters/configKey";
import { experimentReadPolicy } from "src/api/develop/experiment-read-policy";

// ------------------------------------------------------------------ keys

/** R1 with config + lineage (shares the R1 prefix, so R1 invalidations hit it). */
export const runConfigsKey = (datasetId, params) => [
  ...datasetRunsKey(datasetId),
  params ?? "config",
];
/** R3 `shape=matrix` (shares the R3 prefix). */
export const matrixKey = (datasetId, params) => [
  ...runResultsKey(datasetId),
  "matrix",
  params ?? {},
];
export const codeSessionKey = (experimentId, runId) => [
  "develop",
  "code-session",
  experimentId,
  runId,
];
export const WORKBENCH_VIEW_TAB = "dataset_workbench";

export const R1_CONFIG_INCLUDE = "metrics,config,lineage";
export const MATRIX_PAGE_SIZE = 500;

/** Invalidate R1 / R3 (and the dataset list counts) of a dataset. */
export function invalidateR2Reads(queryClient, datasetId) {
  if (datasetId) {
    queryClient.invalidateQueries({ queryKey: datasetRunsKey(datasetId) });
    queryClient.invalidateQueries({ queryKey: runResultsKey(datasetId) });
  } else {
    queryClient.invalidateQueries({ queryKey: ["develop", "dataset-runs"] });
    queryClient.invalidateQueries({ queryKey: ["develop", "run-results"] });
  }
  queryClient.invalidateQueries({ queryKey: ["experiment"] });
  queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
}

// ------------------------------------------------------------ pure helpers

// The "same setup" key moved to common/filters/configKey.js (one rule for
// By dataset and the runs catalogue's `config_key`); re-exported unchanged.
export { configKeyOf };

/** Runs of one lineage, root first (R1 `include=lineage` runs). */
export function lineageOf(runs, runId) {
  const list = runs ?? [];
  const run = list.find((r) => r.run_id === runId);
  if (!run) return { root: null, chain: [] };
  const rootId =
    run.iteration?.root_run_id ?? run.parent_run_id ?? run.run_id ?? null;
  const chain = list
    .filter(
      (r) =>
        r.run_id === rootId ||
        r.iteration?.root_run_id === rootId ||
        r.parent_run_id === rootId,
    )
    .sort((a, b) => (a.iteration?.n ?? 0) - (b.iteration?.n ?? 0));
  return { root: list.find((r) => r.run_id === rootId) ?? null, chain };
}

// --------------------------------------------------------------------- R1

/**
 * R1 with `include=metrics,config,lineage`: every run on a dataset with its
 * `config` snapshot, `iteration` and `parent_run_id`.
 * `data` = `{count, baseline, metric_defs, results}`.
 */
export function useRunConfigs(datasetId, options = {}) {
  const { enabled = true, versionId, ...rest } = options;
  const params = datasetRunsParams({ include: R1_CONFIG_INCLUDE, versionId });
  // apiPath throws on a missing id; queryFn never runs without one.
  const url = datasetId ? endpoints.develop.datasetRuns.list(datasetId) : null;
  // `held`: this build refuses the run list (launch hold list / absent-path
  // guard). Views show "Not available in this release" instead of waiting.
  const absent = url && isLaunchBuild() ? absentTemplateOf(url) : null;
  const q = useQuery({
    queryKey: runConfigsKey(datasetId, params),
    queryFn: ({ signal }) => {
      // Required Edit/Duplicate readers must leave the loading state. Reject
      // locally without axios, the transport, or an interceptor refusal.
      if (absent) throw absentError(absent, { url, method: "get" });
      return fetchDatasetRuns(datasetId, params, signal);
    },
    select: readEnvelope,
    enabled: Boolean(datasetId) && enabled,
    ...experimentReadPolicy,
    ...rest,
  });
  return { ...q, held: Boolean(absent) };
}

/**
 * The lineage of one run (root → iterations), from R1 `include=lineage`.
 * `data` = `{root, chain}`.
 */
export function useLineage({ datasetId, runId }, options = {}) {
  const q = useRunConfigs(datasetId, {
    enabled: Boolean(runId) && (options.enabled ?? true),
  });
  const data = useMemo(
    () => (q.data ? lineageOf(q.data.results, runId) : undefined),
    [q.data, runId],
  );
  return { ...q, data };
}

/**
 * By dataset: R1 on every dataset, joined client side. With `configKey`,
 * each dataset keeps only the runs of that setup.
 * `data` = `[{dataset, runs}]` (datasets without matching runs dropped when
 * a configKey is given).
 */
export function useByDataset({ configKey } = {}, options = {}) {
  const { enabled = true } = options;
  const { data: datasets, isLoading: listLoading } = useDevelopDatasetList(
    undefined,
    undefined,
    { enabled },
  );
  const params = datasetRunsParams({ include: R1_CONFIG_INCLUDE });
  const results = useQueries({
    queries: (datasets ?? []).map((d) => ({
      queryKey: runConfigsKey(d.datasetId, params),
      queryFn: ({ signal }) => fetchDatasetRuns(d.datasetId, params, signal),
      select: readEnvelope,
      enabled: enabled && Boolean(d.datasetId),
    })),
  });
  const isLoading = listLoading || results.some((r) => r.isLoading);
  const isError = results.some((r) => r.isError);
  const stamp = results.map((r) => r.dataUpdatedAt).join(",");
  const data = useMemo(
    () => {
      if (!datasets) return undefined;
      return datasets
        .map((dataset, i) => {
          const runs = results[i]?.data?.results ?? [];
          return {
            dataset,
            runs: configKey
              ? runs.filter((r) => configKeyOf(r) === configKey)
              : runs,
          };
        })
        .filter((entry) => !configKey || entry.runs.length > 0);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [datasets, stamp, configKey],
  );
  return {
    data: isLoading || isError ? undefined : data, isLoading, isError,
    error: results.find((query) => query.isError)?.error,
  };
}

// --------------------------------------------------------------------- R3

/**
 * R3 `shape=matrix`: rows × runs × trials with per-cell `trials[]`,
 * `trace_summary[]` and `values{<column name>: raw}` per row.
 * @param {{datasetId: string, runIds: string[], baselineId?: string,
 *   measure?: string, filters?: string, enabled?: boolean}} opts
 */
/**
 * options-truth L3 G9 (fix 7): does a matrix still wait for tries? A try is
 * pending while it has no outcome and has not run yet (no trace) or is
 * still running; a finished try with no score is not pending. Pure.
 */
export function matrixPending(result) {
  const PENDING = new Set(["queued", "running", "none"]);
  return (result?.rows ?? []).some((row) =>
    Object.values(row?.cells ?? {}).some((cell) =>
      (cell?.trials ?? []).some(
        (t, i) =>
          t == null &&
          (PENDING.has(cell.trace_summary?.[i]?.status) ||
            (cell.trace_summary?.[i]?.status == null && cell.trace_ids?.[i] == null)),
      ),
    ),
  );
}

/** How often a matrix with pending tries refetches (ms). */
export const MATRIX_PENDING_POLL_MS = 2000;

export function useMatrix(
  { datasetId, runIds, baselineId, measure, filters },
  options = {},
) {
  const { enabled = true, ...rest } = options;
  const params = {
    runs: (runIds ?? []).join(","),
    shape: "matrix",
    page: 0,
    page_size: MATRIX_PAGE_SIZE,
  };
  if (baselineId) params.baseline = baselineId;
  if (measure) params.measure = measure;
  if (filters) params.filters = filters;
  return useQuery({
    queryKey: matrixKey(datasetId, params),
    queryFn: ({ signal }) => fetchReadPages(
      endpoints.develop.datasetRuns.results(datasetId), params,
      { collection: "rows", signal },
    ),
    select: readEnvelope,
    enabled: Boolean(datasetId) && (runIds ?? []).length > 0 && enabled,
    // L3 G9 (fix 7): while any try in view is pending the matrix refetches,
    // so verdicts and Failed counts arrive when the run completes (the last
    // fetch is the finished run). A caller's own interval wins.
    refetchInterval: (q) =>
      matrixPending(q?.state?.data?.data?.result)
        ? MATRIX_PENDING_POLL_MS
        : false,
    ...rest,
  });
}

/** The Workbench reads the matrix too (alias kept for L4). */
export const useWorkbench = useMatrix;

// --------------------------------------------------------------------- I1

/**
 * I1 · POST experiments/v2/{id}/runs/: iterate a run.
 * mutate({experimentId, datasetId, parentRunId, scope, rowIds, change,
 *   config, trials}) → `{run, experiment_id, compare_url}` (CONTRACT.md).
 */
export function useIterate(options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ experimentId, datasetId: _datasetId, ...body }) =>
      axios
        .post(endpoints.develop.r2.iterate(experimentId), toIterateBody(body))
        .then((res) => res.data?.result),
    ...options,
    onSuccess: (result, vars, ctx) => {
      invalidateR2Reads(queryClient, vars?.datasetId);
      options.onSuccess?.(result, vars, ctx);
    },
  });
}

/** I1 body from camelCase vars (unknown keys pass through as given). */
export function toIterateBody({
  parentRunId,
  scope,
  rowIds,
  change,
  cause,
  config,
  trials,
  kind,
  ...rest
} = {}) {
  const body = { ...rest };
  if (kind) body.kind = kind;
  if (parentRunId) body.parent_run_id = parentRunId;
  if (scope) body.scope = scope;
  if (rowIds?.length) body.row_ids = rowIds;
  if (change) body.change = change;
  if (cause) body.cause = cause;
  if (config) body.config = config;
  if (trials) body.trials = trials;
  return body;
}

/**
 * Try on one row: a parentless I1 quick test (`kind:"quick_test"`). Posts
 * to the dataset's scratch experiment (`dataset.scratch_experiment_id`).
 * mutate({experimentId, datasetId, config, rowId, trials}) →
 * `{attempts:[attempt]}` (attempt shape: r2Shapes.js QUICK_TEST_ATTEMPT).
 * Writes nothing a grid reads, so nothing is invalidated.
 */
export function useTryOnOneRow(options = {}) {
  return useMutation({
    mutationFn: ({ experimentId, datasetId, config, rowId, trials = 1 }) =>
      axios
        .post(endpoints.develop.r2.iterate(experimentId), {
          kind: "quick_test",
          dataset_id: datasetId,
          row_id: rowId ?? null,
          trials,
          config,
        })
        .then((res) => res.data?.result),
    ...options,
  });
}

// --------------------------------------------------------------------- S1

const LIVE_SESSION = new Set(["waiting", "connected", "streaming"]);

/**
 * S1 · GET the live session of a My code run. With `{poll:true}` it polls
 * every second while the session is waiting / connected / streaming.
 * `data` = code session (r2Shapes.js CODE_SESSION).
 */
export function useCodeSession({ experimentId, runId }, options = {}) {
  const { poll = false, enabled = true, ...rest } = options;
  return useQuery({
    queryKey: codeSessionKey(experimentId, runId),
    queryFn: () =>
      axios.get(endpoints.develop.r2.codeSession(experimentId, runId)),
    select: (res) => res.data?.result,
    enabled: Boolean(experimentId && runId) && enabled,
    refetchInterval: poll
      ? (query) =>
          LIVE_SESSION.has(query.state.data?.data?.result?.status)
            ? 1000
            : false
      : false,
    ...rest,
  });
}

/**
 * S1 · POST a session action (`connect`, `start`, `stop`, `finish`).
 * mutate({experimentId, runId, datasetId, action, ...}).
 */
export function useCodeSessionAction(options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ experimentId, runId, datasetId: _d, ...body }) =>
      axios
        .post(endpoints.develop.r2.codeSession(experimentId, runId), body)
        .then((res) => res.data?.result),
    ...options,
    onSuccess: (result, vars, ctx) => {
      queryClient.invalidateQueries({
        queryKey: codeSessionKey(vars?.experimentId, vars?.runId),
      });
      if (result?.status === "done")
        invalidateR2Reads(queryClient, vars?.datasetId);
      options.onSuccess?.(result, vars, ctx);
    },
  });
}

// -------------------------------------------------------------------- IM1

/**
 * IM1 · POST datasets/imports/ `{op:"preview", source, target, shape}` →
 * `{items:[import preview item], columns, counts}` (r2Shapes.js).
 */
export function useImportPreview(options = {}) {
  return useMutation({
    mutationFn: (body) =>
      axios
        .post(endpoints.develop.r2.imports, { ...body, op: "preview" })
        .then((res) => res.data?.result),
    ...options,
  });
}

/**
 * IM1 · POST datasets/imports/ `{op:"commit", …}` → `{dataset_id,
 * version, rows_added}`; invalidates the target's R1 / R3 and dataset reads.
 */
export function useImportCommit(options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      axios
        .post(endpoints.develop.r2.imports, { ...body, op: "commit" })
        .then((res) => res.data?.result),
    ...options,
    onSuccess: (result, vars, ctx) => {
      invalidateR2Reads(
        queryClient,
        result?.dataset_id ?? vars?.target?.dataset_id,
      );
      queryClient.invalidateQueries({ queryKey: ["develop"] });
      options.onSuccess?.(result, vars, ctx);
    },
  });
}

// ------------------------------------------------------------ saved views

/**
 * Saved Workbench views of a dataset (`/tracer/saved-views/`,
 * `tab_type=dataset_workbench`, `config.display.dataset_id`), through the existing
 * workspace saved-view hooks.
 * @returns {{views: object[], isLoading: boolean,
 *   create: (name: string, config: object) => Promise,
 *   remove: (id: string) => Promise}}
 */
export function useSavedWorkbenchViews(datasetId) {
  const list = useGetWorkspaceSavedViews(WORKBENCH_VIEW_TAB);
  const create = useCreateWorkspaceSavedView(WORKBENCH_VIEW_TAB);
  const remove = useDeleteWorkspaceSavedView(WORKBENCH_VIEW_TAB);
  const views = useMemo(() => {
    const raw = list.data;
    const all = Array.isArray(raw)
      ? raw
      : raw?.custom_views ?? raw?.results ?? [];
    // R3 (R2D-42): the dataset id lives under `config.display` (the saved-view
    // serializer rejects unknown top-level keys); older views kept it at the
    // top level.
    return all.filter(
      (v) =>
        (v?.config?.display?.dataset_id ?? v?.config?.dataset_id) === datasetId,
    );
  }, [list.data, datasetId]);
  return {
    views,
    isLoading: list.isLoading,
    create: (name, config = {}) =>
      create.mutateAsync({
        name,
        config: {
          ...config,
          display: { ...(config.display ?? {}), dataset_id: datasetId },
        },
      }),
    remove: (id) => remove.mutateAsync(id),
    isSaving: create.isPending,
  };
}
