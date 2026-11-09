// Run actions on one run of an experiment (PRODUCT-SPEC §3.9, §6.1-6.2).
// Plain request functions; sections/common/Runs/useRunActions.js adds the
// confirms, toasts and cache refresh.
import axios, { endpoints } from "src/utils/axios";
import { isPrototypeMode } from "src/utils/prototype";
import {
  stopRequestBody,
  supportsSelectedRunStop,
} from "./experiment-stop-request";

/** Whether this stop endpoint accepts a selection of run IDs. */
export const perRunStopSupported = (prototype = isPrototypeMode()) =>
  supportsSelectedRunStop(prototype);

/** Never widen a selected-run stop into a whole-experiment stop. */
export const stopRuns = async (experimentId, runIds) => {
  const body = stopRequestBody(runIds, perRunStopSupported());
  return axios.post(endpoints.develop.experiment.stop(experimentId), body);
};

/** E2 POST experiments/v2/{id}/resume/ `{run_ids}`. */
export const resumeRuns = (experimentId, runIds) =>
  axios.post(endpoints.develop.datasetRuns.resume(experimentId), {
    run_ids: runIds,
  });

/** POST experiments/v2/re-run/ `{experiment_ids}` (never `select_all`). */
export const rerunExperiments = (experimentIds) =>
  axios.post(endpoints.develop.experiment.rerun, {
    experiment_ids: experimentIds,
  });

/**
 * POST experiments/v2/{id}/rerun-cells/:
 *   whole runs `{source_ids}`, failed rows `{source_ids, failed_only: true}`,
 *   re-score `{user_eval_metric_ids}`.
 */
export const rerunCells = (experimentId, body) =>
  axios.post(
    endpoints.develop.experiment.reRunExperimentCell(experimentId),
    body,
  );

/**
 * E3 POST experiments/v2/{id}/runs/{run_id}/results/. With no results and
 * `complete: true` the prototype plays in every example (Simulate upload).
 */
export const uploadRunResults = (experimentId, runId, body) =>
  axios.post(
    endpoints.develop.datasetRuns.uploadResults(experimentId, runId),
    body,
  );
