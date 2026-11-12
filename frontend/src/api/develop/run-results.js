// Results of runs on a dataset (PRODUCT-SPEC §6.2 R3, R6): the results grid
// and its header summary, one row in full for the result drawer, the ids of
// the filtered set (select all), and adding result rows to a dataset.
import { useMemo } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { pollIntervalFor } from "src/sections/common/Runs/runStatus";
import { serializePills } from "src/sections/common/Results/filterPills";
import {
  hidesEmpty,
  rowsWithOutput,
  withHideEmpty,
} from "src/sections/common/filters/hideEmpty";
import { fetchReadPages, readEnvelope } from "src/api/develop/runs";

export const runResultsKey = (datasetId) => [
  "develop",
  "run-results",
  datasetId,
];

/** Grid transport page size; fetchReadPages consumes every declared page. */
export const RESULTS_PAGE_SIZE = 1000;

/**
 * R3 query params from the view state.
 * @param {{runIds: string[], baselineId?: string, pills?: object[],
 *   show?: string, search?: string, sort?: string, agg?: string,
 *   mixVersions?: boolean}} view
 */
export function runResultsParams({
  runIds,
  baselineId,
  pills,
  show,
  search,
  sort,
  agg,
  mixVersions,
}) {
  const params = { runs: (runIds ?? []).join(",") };
  // W1-3: deltas across eval versions only when the user opted in.
  if (mixVersions) params.mix_versions = 1;
  if (baselineId) params.baseline = baselineId;
  // `Hide rows with no output` is not a filter the endpoint accepts (it
  // refuses the expression with a 400): it never goes to the server, and
  // the reads below leave those rows out of the rows they load.
  const filters = serializePills(withHideEmpty(pills, false));
  if (filters) params.filters = filters;
  if (show && show !== "all") params.show = show;
  if (search) params.search = search;
  if (sort && sort !== "order") params.sort = sort;
  if (agg) params.agg = agg;
  return params;
}

/**
 * R3 complete bounded population plus its full-scope summary. Polls while a
 * run in view is active (pollIntervalFor: stops on every terminal status).
 * `data` = `{total, rows, summary}`. With `Hide rows with no output` on,
 * the loaded rows (every page of the view) keep only those where a run in
 * view has an output, and `total` counts them.
 */
export function useRunResults(datasetId, view, options = {}) {
  const { enabled = true, ...rest } = options;
  const params = {
    ...runResultsParams(view),
    page: 0,
    page_size: RESULTS_PAGE_SIZE,
  };
  const hideEmpty = hidesEmpty(view.pills);
  const inView = (view.runIds ?? []).join(",");
  const select = useMemo(
    () =>
      hideEmpty
        ? (res) =>
            rowsWithOutput(readEnvelope(res), inView ? inView.split(",") : [])
        : readEnvelope,
    [hideEmpty, inView],
  );
  return useQuery({
    queryKey: [...runResultsKey(datasetId), "grid", params],
    queryFn: ({ signal }) => fetchReadPages(
      endpoints.develop.datasetRuns.results(datasetId), params,
      { collection: "rows", signal },
    ),
    select,
    enabled: Boolean(datasetId) && view.runIds?.length > 0 && enabled,
    refetchInterval: (query) =>
      pollIntervalFor(query.state.data?.data?.result?.summary?.runs ?? []),
    ...rest,
  });
}

/**
 * R3 one row in full (`example_id`, `full=true`) for the result drawer:
 * untruncated outputs, `trials_detail`, `neighbors` in the view's order.
 */
export function useResultRow(datasetId, view, exampleId, options = {}) {
  const { enabled = true, ...rest } = options;
  const params = {
    ...runResultsParams(view),
    example_id: exampleId,
    full: true,
  };
  return useQuery({
    queryKey: [...runResultsKey(datasetId), "row", params],
    queryFn: ({ signal }) =>
      axios.get(endpoints.develop.datasetRuns.results(datasetId), { params, signal }),
    select: (res) => {
      const result = readEnvelope(res);
      if (result.rows?.length !== 1 || result.rows[0].example_id !== exampleId) {
        throw new Error("The requested result row is unavailable.");
      }
      return result.rows[0];
    },
    enabled:
      Boolean(datasetId && exampleId) && view.runIds?.length > 0 && enabled,
    ...rest,
  });
}

/**
 * R3 `ids_only=true`: every example id of the filtered set, in order. With
 * `Hide rows with no output` on, the rows are read instead (an id alone
 * does not say whether the row has an output) and those with none left out.
 */
export async function fetchResultIds(datasetId, view) {
  if (hidesEmpty(view?.pills)) {
    const read = await fetchReadPages(
      endpoints.develop.datasetRuns.results(datasetId),
      { ...runResultsParams(view), page_size: RESULTS_PAGE_SIZE },
      { collection: "rows" },
    );
    return rowsWithOutput(readEnvelope(read), view.runIds).rows.map(
      (row) => row.example_id,
    );
  }
  const res = await fetchReadPages(
    endpoints.develop.datasetRuns.results(datasetId),
    { ...runResultsParams(view), ids_only: true, page_size: RESULTS_PAGE_SIZE },
    { collection: "example_ids" },
  );
  return readEnvelope(res).example_ids;
}

/**
 * R6 · POST datasets/{target | new}/rows/from-results/ (copy result rows into
 * another or a new dataset). mutate({targetId, ...body}); `targetId` is the
 * dataset id or `new` and never goes in the body. 200 = every row added,
 * 207 = some added (the rest in `result.failures`), 422 = none added.
 * Never retried on its own: a repeated request can add the rows twice. The
 * caller says every failure itself (no global error toast). With no answer
 * (no `statusCode` on the client's rejection) or a server failure, the rows
 * or a new dataset may exist, so the dataset lists are read again.
 */
export function useAddResultsToDataset(targetDatasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ targetId, ...body }) =>
      axios.post(
        endpoints.develop.datasetRows.fromResults(targetId ?? targetDatasetId),
        body,
      ),
    ...options,
    retry: false,
    meta: { ...options.meta, errorHandled: true },
    onSuccess: (res, vars, ctx) => {
      queryClient.invalidateQueries({ queryKey: ["develop"] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
      options.onSuccess?.(res, vars, ctx);
    },
    onError: (error, ...rest) => {
      const status = Number(error?.statusCode ?? error?.response?.status);
      if (!Number.isInteger(status) || status >= 500) {
        queryClient.invalidateQueries({ queryKey: ["develop"] });
        queryClient.invalidateQueries({ queryKey: ["datasets"] });
      }
      options.onError?.(error, ...rest);
    },
  });
}

/**
 * Re-run result cells: POST experiments/v2/{id}/rerun-cells/
 * `{cells:[{column_id, run_id, example_id}], failed_only:true}` per experiment.
 * The server resolves canonical run/example identities to immutable snapshot rows.
 * mutate([{run, exampleId}]).
 */
export function useRerunResultCells(datasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (cells) => {
      const byExperiment = new Map();
      cells.forEach(({ run, exampleId }) => {
        if (!run?.experiment_id || !run.output_column_id) return;
        if (!byExperiment.has(run.experiment_id))
          byExperiment.set(run.experiment_id, []);
        byExperiment
          .get(run.experiment_id)
          .push({ column_id: run.output_column_id, run_id: run.run_id, example_id: exampleId });
      });
      return Promise.all(
        [...byExperiment.entries()].map(([experimentId, list]) =>
          axios.post(
            endpoints.develop.experiment.reRunExperimentCell(experimentId),
            { cells: list, failed_only: true },
          ),
        ),
      );
    },
    ...options,
    onSuccess: (res, vars, ctx) => {
      queryClient.invalidateQueries({ queryKey: runResultsKey(datasetId) });
      queryClient.invalidateQueries({ queryKey: ["experiment"] });
      options.onSuccess?.(res, vars, ctx);
    },
  });
}

/**
 * Re-score one eval on the runs in view: POST experiments/v2/{id}/rerun-cells/
 * `{user_eval_metric_ids:[…]}` per experiment. mutate({runs, def}) where
 * `def.uem_ids` maps run id -> user eval metric id.
 */
export function useRescoreEval(datasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runs, def }) => {
      const byExperiment = new Map();
      runs.forEach((run) => {
        const uem = def?.uem_ids?.[run.run_id];
        if (!uem || !run.experiment_id) return;
        if (!byExperiment.has(run.experiment_id))
          byExperiment.set(run.experiment_id, new Set());
        byExperiment.get(run.experiment_id).add(uem);
      });
      return Promise.all(
        [...byExperiment.entries()].map(([experimentId, ids]) =>
          axios.post(
            endpoints.develop.experiment.reRunExperimentCell(experimentId),
            { user_eval_metric_ids: [...ids] },
          ),
        ),
      );
    },
    ...options,
    onSuccess: (res, vars, ctx) => {
      queryClient.invalidateQueries({ queryKey: runResultsKey(datasetId) });
      options.onSuccess?.(res, vars, ctx);
    },
  });
}
