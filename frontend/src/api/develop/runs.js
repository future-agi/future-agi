// Runs on a dataset (PRODUCT-SPEC §4 "Runs hooks", §6.2 R1/R2) and the
// experiment detail read. One run = one variant of an experiment (`#4B`);
// every hook returns the canonical Run object (CONTRACT.md "Canonical Run").
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

export const datasetRunsKey = (datasetId) => [
  "develop",
  "dataset-runs",
  datasetId,
];

// Query params of R1. `include: "metrics"` adds per-run aggregates and
// deltas vs the baseline; without it the response is the picker shape.
export function datasetRunsParams({
  include,
  baseline,
  versionId,
  search,
  status,
  sameTargetAs,
  limit = 500,
} = {}) {
  const params = { limit };
  if (include) params.include = include;
  if (baseline) params.baseline = baseline;
  if (versionId) params.dataset_version_id = versionId;
  if (search) params.search = search;
  if (status) params.status = status;
  if (sameTargetAs) params.same_target_as = sameTargetAs;
  return params;
}

/**
 * R1 · GET datasets/{id}/runs/.
 * @param datasetId
 * @param opts { include, baseline, versionId ("all" | version id), search,
 *   status, sameTargetAs, limit } plus any react-query option
 *   (refetchInterval for polling running runs, enabled, ...).
 * @returns query whose `data` is `{count, baseline, metric_defs, results}`.
 */
export function useDatasetRuns(datasetId, opts = {}) {
  const {
    include,
    baseline,
    versionId,
    search,
    status,
    sameTargetAs,
    limit,
    enabled = true,
    ...queryOptions
  } = opts;
  const params = datasetRunsParams({
    include,
    baseline,
    versionId,
    search,
    status,
    sameTargetAs,
    limit,
  });
  return useQuery({
    queryKey: [...datasetRunsKey(datasetId), params],
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.list(datasetId), { params }),
    select: (res) => res.data?.result,
    enabled: Boolean(datasetId) && enabled,
    ...queryOptions,
  });
}

function useInvalidateRuns(datasetId) {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: datasetRunsKey(datasetId) });
    // The dataset list shows the baseline and best run.
    queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
  };
}

/** R2 · PUT datasets/{id}/baseline/ `{run_id}`. mutate(runId). */
export function usePinBaseline(datasetId, options = {}) {
  const invalidate = useInvalidateRuns(datasetId);
  return useMutation({
    mutationFn: (runId) =>
      axios.put(endpoints.develop.datasetRuns.baseline(datasetId), {
        run_id: runId,
      }),
    ...options,
    onSuccess: (res, runId, ctx) => {
      invalidate();
      const label = res?.data?.result?.label;
      enqueueSnackbar(
        label ? `${label} is the dataset baseline` : "Baseline pinned",
        { variant: "success" },
      );
      options.onSuccess?.(res, runId, ctx);
    },
  });
}

/** R2 · DELETE datasets/{id}/baseline/. mutate(). */
export function useUnpinBaseline(datasetId, options = {}) {
  const invalidate = useInvalidateRuns(datasetId);
  return useMutation({
    mutationFn: () =>
      axios.delete(endpoints.develop.datasetRuns.baseline(datasetId)),
    ...options,
    onSuccess: (res, vars, ctx) => {
      invalidate();
      enqueueSnackbar("Baseline unpinned. Deltas use the earliest run.", {
        variant: "success",
      });
      options.onSuccess?.(res, vars, ctx);
    },
  });
}

/**
 * GET experiments/v2/{id}/ with `runs[]` (canonical Run objects), ordinal,
 * dataset_version, splits and trials. Shares the query key of
 * hooks/useGetExperimentDetails.js so either invalidation refreshes both.
 */
export function useExperimentDetail(experimentId, options = {}) {
  const { enabled = true, ...queryOptions } = options;
  return useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () =>
      axios.get(
        endpoints.develop.experiment.getExperimentDetails(experimentId),
      ),
    select: (res) => res?.data?.result,
    enabled: Boolean(experimentId) && enabled,
    ...queryOptions,
  });
}
