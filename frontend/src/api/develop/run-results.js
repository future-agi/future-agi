// Results of runs on a dataset (PRODUCT-SPEC §6.2 R3, R6): the results grid
// and its header summary, one row in full for the result drawer, the ids of
// the filtered set (select all), and adding result rows to a dataset.
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { pollIntervalFor } from "src/sections/common/Runs/runStatus";
import { serializePills } from "src/sections/common/Results/filterPills";

export const runResultsKey = (datasetId) => [
  "develop",
  "run-results",
  datasetId,
];

/** Grid page size: one request carries every row in view (≤ ~1k, §8). */
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
  const filters = serializePills(pills);
  if (filters) params.filters = filters;
  if (show && show !== "all") params.show = show;
  if (search) params.search = search;
  if (sort && sort !== "order") params.sort = sort;
  if (agg) params.agg = agg;
  return params;
}

/**
 * R3 page 0 with every row in view and the `summary` block. Polls while a
 * run in view is active (pollIntervalFor: stops on every terminal status).
 * `data` = `{total, rows, summary}`.
 */
export function useRunResults(datasetId, view, options = {}) {
  const { enabled = true, ...rest } = options;
  const params = {
    ...runResultsParams(view),
    page: 0,
    page_size: RESULTS_PAGE_SIZE,
  };
  return useQuery({
    queryKey: [...runResultsKey(datasetId), "grid", params],
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.results(datasetId), { params }),
    select: (res) => res.data?.result,
    enabled: Boolean(datasetId) && view.runIds?.length > 0 && enabled,
    placeholderData: keepPreviousData,
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
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.results(datasetId), { params }),
    select: (res) => res.data?.result?.rows?.[0] ?? null,
    enabled:
      Boolean(datasetId && exampleId) && view.runIds?.length > 0 && enabled,
    placeholderData: keepPreviousData,
    ...rest,
  });
}

/** R3 `ids_only=true`: every example id of the filtered set, in order. */
export async function fetchResultIds(datasetId, view) {
  const res = await axios.get(
    endpoints.develop.datasetRuns.results(datasetId),
    { params: { ...runResultsParams(view), ids_only: true } },
  );
  return res.data?.result?.example_ids ?? [];
}

/** R6 · POST datasets/{target}/rows/from-results/. mutate(body). */
export function useAddResultsToDataset(targetDatasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ targetId, ...body }) =>
      axios.post(
        endpoints.develop.datasetRows.fromResults(targetId ?? targetDatasetId),
        body,
      ),
    ...options,
    onSuccess: (res, vars, ctx) => {
      queryClient.invalidateQueries({ queryKey: ["develop"] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
      options.onSuccess?.(res, vars, ctx);
    },
  });
}

/**
 * Re-run result cells: POST experiments/v2/{id}/rerun-cells/
 * `{cells:[{column_id, row_id}], failed_only:true}` per experiment. `cells`
 * carry the run (`experiment_id`, `output_column_id`) and the example id.
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
          .push({ column_id: run.output_column_id, row_id: exampleId });
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
