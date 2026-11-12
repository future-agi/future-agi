// C-ANALYSIS S1: exact nested arrays and explicit unavailable evidence.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

export const analysisKey = (datasetId) => ["develop", "analysis", datasetId];
const object = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const uuid = (v) => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const number = (v) => typeof v === "number" && Number.isFinite(v);
const count = (v) => Number.isSafeInteger(v) && v >= 0;
const nullable = (v, check) => v === null || check(v);
const text = (v) => typeof v === "string";
function requireField(ok, field) {
  if (!ok) throw new Error(`Invalid analysis response: ${field}`);
}
function checkAvailability(value, field) {
  requireField(object(value) && ["available", "unavailable", "incompatible"].includes(value.state), field);
  requireField(value.state === "available" ? value.reason === null : text(value.reason) && value.reason.length > 0, `${field}.reason`);
}

/** Reject malformed success rather than converting a failed read into empty arrays. */
export function adaptRunAnalysis(envelope) {
  requireField(envelope?.status === true && object(envelope.result), "envelope");
  const a = envelope.result;
  requireField(uuid(a.dataset_id) && typeof a.running === "boolean", "dataset/running");
  for (const key of ["runs", "metrics", "verdicts", "default_evals", "breakdown_options"])
    requireField(Array.isArray(a[key]), key);
  requireField(a.default_evals.every(text) && a.breakdown_options.every(text), "option items");
  const runIds = new Set();
  a.runs.forEach((r) => {
    requireField(object(r) && uuid(r.run_id) && !runIds.has(r.run_id), "runs.run_id");
    runIds.add(r.run_id);
    requireField(r.dataset_id === a.dataset_id && uuid(r.dataset_version_id), "runs.scope");
    requireField(nullable(r.experiment_id, uuid) && text(r.label) && nullable(r.name, text) && text(r.status) && text(r.task_kind), "runs.fields");
    requireField([r.planned_trials, r.recorded_trials, r.missing_trials].every(count) && r.recorded_trials + r.missing_trials === r.planned_trials, "runs.counts");
  });
  requireField(nullable(a.baseline_run_id, (id) => runIds.has(id)), "baseline_run_id");
  requireField(nullable(a.baseline_label, text), "baseline_label");
  requireField(nullable(a.candidate_run_id, (id) => runIds.has(id) && id !== a.baseline_run_id && a.baseline_run_id !== null), "candidate_run_id");
  requireField(count(a.examples_in_view) && count(a.examples_total) && a.examples_in_view <= a.examples_total, "examples");
  requireField(a.scope?.complete === true && a.scope?.population === "full_filtered_plan", "scope");
  const metricIds = new Set();
  a.metrics.forEach((m) => {
    requireField(object(m) && text(m.key) && !metricIds.has(m.key) && text(m.name) && m.family === "eval", "metrics.identity");
    metricIds.add(m.key);
    requireField(m.aggregation === "assessed_trial_mean" && m.denominator_unit === "example_trial", "metrics.aggregation");
    requireField(nullable(m.higher_is_better, (v) => typeof v === "boolean") && nullable(m.pass_threshold, number), "metrics.policy");
    requireField(Array.isArray(m.per_run) && Array.isArray(m.vs_baseline), "metrics.arrays");
    const seen = new Set();
    m.per_run.forEach((p) => {
      requireField(object(p) && runIds.has(p.run_id) && !seen.has(p.run_id), "per_run.run_id");
      seen.add(p.run_id);
      requireField(nullable(p.value, number) && [p.n, p.expected_n, p.missing_n].every(count) && p.n + p.missing_n <= p.expected_n, "per_run.value/counts");
      requireField((p.value === null) === (p.n === 0), "per_run.denominator");
      requireField(p.value === null ? text(p.unavailable_reason) && p.unavailable_reason.length > 0 : p.unavailable_reason === null, "per_run.unavailable_reason");
      requireField(nullable(p.evaluator_version_id, uuid) && p.ci95 === null, "per_run.evidence");
      requireField(object(p.score_status_counts) && Object.values(p.score_status_counts).every(count), "per_run.score_status_counts");
    });
    requireField(seen.size === runIds.size, "per_run.complete");
    const compared = new Set();
    m.vs_baseline.forEach((p) => {
      requireField(object(p) && a.baseline_run_id !== null && p.baseline_run_id === a.baseline_run_id && runIds.has(p.run_id) && p.run_id !== a.baseline_run_id && !compared.has(p.run_id), "vs_baseline.identity");
      compared.add(p.run_id);
      requireField(nullable(p.delta, number) && count(p.n) && count(p.expected_n) && p.n <= p.expected_n, "vs_baseline.values");
      requireField(p.ci95 === null && p.p_value === null && p.significant === null, "vs_baseline.uncertainty");
      checkAvailability(p.availability, "vs_baseline.availability");
      requireField(p.availability.state === "available" ? p.delta !== null && p.n > 0 : p.delta === null && p.n === 0, "vs_baseline.unavailable");
    });
    requireField(compared.size === (a.baseline_run_id === null ? 0 : runIds.size - 1), "vs_baseline.complete");
  });
  a.verdicts.forEach((v) => {
    requireField(object(v) && runIds.has(v.run_id) && v.baseline_run_id === a.baseline_run_id && v.run_id !== a.baseline_run_id && metricIds.has(v.metric), "verdicts.identity");
    requireField(["observed_difference", "unavailable"].includes(v.status) && nullable(v.delta, number) && count(v.n) && text(v.reason) && v.significant === null, "verdicts.fields");
    requireField(v.status === "unavailable" ? v.delta === null && v.n === 0 : v.delta !== null && v.n > 0, "verdicts.availability");
  });
  for (const key of ["preference", "human", "agent", "worklist", "breakdown", "pattern", "root_causes"])
    requireField(a[key] === null, key);
  requireField(object(a.availability), "availability");
  for (const key of ["uncertainty", "candidate", "root_causes", "ops", "actions", "preference", "human", "agent", "worklist", "breakdown"])
    checkAvailability(a.availability[key], `availability.${key}`);
  if (a.observed_failures !== null) {
    const observed = a.observed_failures;
    requireField(object(observed) && Array.isArray(observed.groups) && count(observed.total) && typeof observed.has_more === "boolean", "observed_failures");
    checkAvailability(observed.availability, "observed_failures.availability");
    observed.groups.forEach((g) => {
      requireField(object(g) && runIds.has(g.run_id) && metricIds.has(g.metric) && g.causal === false, "observed_failures.identity");
      requireField(count(g.failed_trials) && count(g.assessed_trials) && g.failed_trials <= g.assessed_trials && count(g.examples_total), "observed_failures.counts");
      requireField(Array.isArray(g.example_ids) && g.example_ids.every(uuid) && g.example_ids.length <= 50 && typeof g.examples_has_more === "boolean", "observed_failures.examples");
    });
  }
  return a;
}

export function analysisParams({ runIds, baselineId, filters, groupBy, bmetric, cand, wmetric, include } = {}) {
  const params = { runs: (runIds ?? []).join(",") };
  if (baselineId) params.baseline = baselineId;
  if (filters?.length) params.filters = JSON.stringify(filters);
  if (groupBy) params.group_by = groupBy;
  if (bmetric) params.bmetric = bmetric;
  if (cand) params.cand = cand;
  if (wmetric) params.wmetric = wmetric;
  if (include) params.include = include;
  return params;
}

export function useRunAnalysis(datasetId, opts = {}) {
  const { enabled = true, errorHandled = false, ...rest } = opts;
  const params = analysisParams(rest);
  return useQuery({
    queryKey: [...analysisKey(datasetId), params],
    meta: { errorHandled },
    queryFn: async () => {
      const res = await axios.get(endpoints.develop.datasetRuns.analysis(datasetId), { params });
      return adaptRunAnalysis(res.data);
    },
    enabled: Boolean(datasetId) && enabled && Boolean(params.runs),
    refetchInterval: (query) => query.state.data?.running ? 5000 : false,
    // Never carry results across a changed dataset/run/filter/baseline scope.
  });
}

export function useCandidateRunId(datasetId, { runIds = [], baselineId, analysisBaselineId, cand, enabled = true } = {}) {
  const valid = cand && cand !== baselineId && runIds.includes(cand) ? cand : null;
  const others = runIds.filter((id) => id !== baselineId);
  const needs = enabled && !valid && Boolean(baselineId) && others.length > 1;
  const { data } = useRunAnalysis(datasetId, { runIds, baselineId: analysisBaselineId, enabled: needs, errorHandled: true });
  if (valid) return valid;
  if (!baselineId || baselineId === "none") return null;
  const fromApi = needs && data?.baseline_run_id === baselineId ? data?.candidate_run_id : null;
  return fromApi && others.includes(fromApi) ? fromApi : others[0] ?? null;
}

const scopeParams = ({ version, splitIds } = {}) => {
  const params = {};
  if (version != null) params.version = version;
  if (splitIds?.length) params.split_ids = splitIds.join(",");
  return params;
};

// Existing dataset/classic mutations are retained; the S1 Summary does not mount them.
export function useDatasetAnalysisSummary(datasetId, scope = {}) {
  const params = scopeParams(scope);
  return useQuery({
    queryKey: ["develop", "dataset-analysis-summary", datasetId, params],
    queryFn: () => axios.get(endpoints.dataset.annotationSummary(datasetId), { params }),
    select: (res) => res.data?.result,
    enabled: Boolean(datasetId),
  });
}

export function useRescoreRun(options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ experimentId, metricName }) => {
      const detail = await axios.get(endpoints.develop.experiment.getExperimentDetails(experimentId));
      const metrics = detail?.data?.result?.user_eval_metrics ?? [];
      const ids = metrics.filter((m) => !metricName || String(m.name ?? m.eval_template_name ?? "").toLowerCase().includes(metricName.toLowerCase())).map((m) => m.id).filter(Boolean);
      return axios.post(endpoints.develop.experiment.reRunExperimentCell(experimentId), {
        user_eval_metric_ids: ids.length ? ids : metrics.map((m) => m.id),
      });
    },
    ...options,
    onSuccess: (res, vars, ctx) => {
      queryClient.invalidateQueries({ queryKey: ["develop", "analysis"] });
      enqueueSnackbar(`Re-scoring ${vars.runLabel ?? "the run"} on ${vars.version ?? "the latest version"}`, { variant: "success" });
      options.onSuccess?.(res, vars, ctx);
    },
  });
}

export function useSetExperimentDefaults(datasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (evalIds) => axios.put(endpoints.develop.updateDataset(datasetId), { experiment_default_eval_ids: evalIds }),
    ...options,
    onSuccess: (res, evalIds, ctx) => {
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-analysis-summary", datasetId] });
      options.onSuccess?.(res, evalIds, ctx);
    },
  });
}

export function useEditDatasetEvalConfig(datasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ evalId, higherIsBetter, passThreshold, extra = {} }) => axios.post(endpoints.develop.eval.editEval(datasetId, evalId), {
      ...extra, run: false, higher_is_better: higherIsBetter,
      ...(passThreshold !== undefined && { pass_threshold: passThreshold }),
    }),
    ...options,
    onSuccess: (res, vars, ctx) => {
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-analysis-summary", datasetId] });
      options.onSuccess?.(res, vars, ctx);
    },
  });
}
