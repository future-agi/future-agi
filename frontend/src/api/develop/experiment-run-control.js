import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { resumeRuns, stopRuns } from "./experiment-run-actions";

const runControlPath = (experimentId) =>
  apiPath("/model-hub/experiments/v2/{experiment_id}/run-control/", {
    experiment_id: experimentId,
  });

export const readRunControl = (experimentId, signal) =>
  axios.get(runControlPath(experimentId), { signal });

export const changeRunControl = (experimentId, runId, stopped) =>
  stopped ? stopRuns(experimentId, [runId]) : resumeRuns(experimentId, [runId]);
