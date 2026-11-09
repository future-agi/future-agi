// Analysis reads and writes (PRODUCT-SPEC §3.13–3.15): R5 for the
// experiment and compare Summary, the Dataset Summary reads with their
// additive fields, and the eval-on-a-dataset writes (direction, pass
// threshold, `Use in new experiments`). The frontend never recomputes
// movement or significance; it displays these fields.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

export const analysisKey = (datasetId) => ["develop", "analysis", datasetId];

/** R5 query params (`runs`, `baseline`, `filters`, `group_by`, `bmetric`, `cand`, `wmetric`). */
export function analysisParams({
  runIds,
  baselineId,
  filters,
  groupBy,
  bmetric,
  cand,
  wmetric,
} = {}) {
  const params = { runs: (runIds ?? []).join(",") };
  if (baselineId) params.baseline = baselineId;
  if (filters?.length) params.filters = JSON.stringify(filters);
  if (groupBy) params.group_by = groupBy;
  if (bmetric) params.bmetric = bmetric;
  if (cand) params.cand = cand;
  if (wmetric) params.wmetric = wmetric;
  return params;
}

/**
 * R5 · GET datasets/{id}/results/analysis/. Refetches every 5 s while a run
 * in view is running (area-analysis §A states).
 */
export function useRunAnalysis(datasetId, opts = {}) {
  const { enabled = true, ...rest } = opts;
  const params = analysisParams(rest);
  return useQuery({
    queryKey: [...analysisKey(datasetId), params],
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.analysis(datasetId), { params }),
    select: (res) => res.data?.result,
    enabled: Boolean(datasetId) && enabled && Boolean(params.runs),
    refetchInterval: (query) =>
      query.state.data?.data?.result?.running ? 5000 : false,
    placeholderData: (prev) => prev,
  });
}

/**
 * The page's candidate run (R3 analysis fix: one candidate per page). `cand`
 * when it is a non-baseline run in view, else the R5 default
 * (`candidate_run_id`: the best verdict), else the first non-baseline run.
 * Reads R5 only with two or more candidates and no valid `cand`; pass the
 * same `analysisBaselineId` the Summary sends so both share one cache entry.
 * @returns {string|null}
 */
export function useCandidateRunId(
  datasetId,
  { runIds = [], baselineId, analysisBaselineId, cand, enabled = true } = {},
) {
  const valid =
    cand && cand !== baselineId && runIds.includes(cand) ? cand : null;
  const others = runIds.filter((id) => id !== baselineId);
  const needs = enabled && !valid && Boolean(baselineId) && others.length > 1;
  const { data } = useRunAnalysis(datasetId, {
    runIds,
    baselineId: analysisBaselineId,
    enabled: needs,
  });
  if (valid) return valid;
  if (!baselineId) return null;
  const fromApi =
    needs && data?.baseline_run_id === baselineId
      ? data?.candidate_run_id
      : null;
  return fromApi && others.includes(fromApi) ? fromApi : others[0] ?? null;
}

const scopeParams = ({ version, splitIds } = {}) => {
  const params = {};
  if (version != null) params.version = version;
  if (splitIds?.length) params.split_ids = splitIds.join(",");
  return params;
};

/**
 * Dataset Summary · Human review and the Evals overview (annotation-summary
 * with `judge_agreement`, `review` and `evals_overview`).
 */
export function useDatasetAnalysisSummary(datasetId, scope = {}) {
  const params = scopeParams(scope);
  return useQuery({
    queryKey: ["develop", "dataset-analysis-summary", datasetId, params],
    queryFn: () =>
      axios.get(endpoints.dataset.annotationSummary(datasetId), { params }),
    select: (res) => res.data?.result,
    enabled: Boolean(datasetId),
  });
}

/** Re-score a run on the latest eval version: rerun-cells `{user_eval_metric_ids}`. */
export function useRescoreRun(options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ experimentId, metricName }) => {
      const detail = await axios.get(
        endpoints.develop.experiment.getExperimentDetails(experimentId),
      );
      const metrics = detail?.data?.result?.user_eval_metrics ?? [];
      const ids = metrics
        .filter(
          (m) =>
            !metricName ||
            String(m.name ?? m.eval_template_name ?? "")
              .toLowerCase()
              .includes(metricName.toLowerCase()),
        )
        .map((m) => m.id)
        .filter(Boolean);
      return axios.post(
        endpoints.develop.experiment.reRunExperimentCell(experimentId),
        {
          user_eval_metric_ids: ids.length ? ids : metrics.map((m) => m.id),
        },
      );
    },
    ...options,
    onSuccess: (res, vars, ctx) => {
      queryClient.invalidateQueries({ queryKey: ["develop", "analysis"] });
      enqueueSnackbar(
        `Re-scoring ${vars.runLabel ?? "the run"} on ${vars.version ?? "the latest version"}`,
        { variant: "success" },
      );
      options.onSuccess?.(res, vars, ctx);
    },
  });
}

/**
 * `Use in new experiments` (C20): PUT edit_dataset_behavior with the
 * passthrough key `experiment_default_eval_ids`.
 */
export function useSetExperimentDefaults(datasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (evalIds) =>
      axios.put(endpoints.develop.updateDataset(datasetId), {
        experiment_default_eval_ids: evalIds,
      }),
    ...options,
    onSuccess: (res, evalIds, ctx) => {
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-analysis-summary", datasetId],
      });
      options.onSuccess?.(res, evalIds, ctx);
    },
  });
}

/**
 * Direction and pass threshold of a dataset eval (C20): the existing
 * edit_and_run_user_eval with `run:false` and passthrough keys.
 */
export function useEditDatasetEvalConfig(datasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ evalId, higherIsBetter, passThreshold, extra = {} }) =>
      axios.post(endpoints.develop.eval.editEval(datasetId, evalId), {
        ...extra,
        run: false,
        higher_is_better: higherIsBetter,
        ...(passThreshold !== undefined && { pass_threshold: passThreshold }),
      }),
    ...options,
    onSuccess: (res, vars, ctx) => {
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-analysis-summary", datasetId],
      });
      options.onSuccess?.(res, vars, ctx);
    },
  });
}
