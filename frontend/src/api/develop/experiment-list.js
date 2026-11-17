// Experiments tab leaderboard (PRODUCT-SPEC §3.7): R1 with metrics, live
// polling while runs are active, and the list's bulk writes (delete,
// rename, rerun, download). One run's own actions live in useRunActions.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { pollIntervalFor } from "src/sections/common/Runs/runStatus";
import { datasetRunsKey, useDatasetRuns } from "./runs";
import {
  refreshAfterRename,
  renameErrorOf,
  renameSuccessText,
} from "./experiment-metadata";

/**
 * R1 `include=metrics` for the leaderboard. Polls every 5 s while a run is
 * queued, running or scoring, every 30 s while only external runs wait for
 * results, and stops when every run is terminal.
 * @param {string} datasetId
 * @param {{baseline?: string, version?: string}} opts version = "all" or a
 *   version number
 */
export function useLeaderboardRuns(datasetId, { baseline, version } = {}) {
  return useDatasetRuns(datasetId, {
    include: "metrics",
    baseline: baseline || undefined,
    versionId: version && version !== "all" ? String(version) : undefined,
    refetchInterval: (query) =>
      pollIntervalFor(query.state.data?.data?.result?.results ?? []),
    refetchOnWindowFocus: false,
    placeholderData: (prev) => prev,
  });
}

function useRefreshLists(datasetId) {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: datasetRunsKey(datasetId) });
    queryClient.invalidateQueries({ queryKey: ["experiment-list"] });
    queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
  };
}

const errorToast = (fallback) => (error) =>
  enqueueSnackbar(getRequestErrorMessage(error, fallback), {
    variant: "error",
  });

/**
 * DELETE experiments/v2/delete/ `{experiment_ids}` (dev's route answers
 * DELETE only; a POST is 405). mutate({ids, runCount}).
 */
export const deleteExperiments = (ids) =>
  axios.delete(endpoints.develop.experiment.delete, {
    data: { experiment_ids: ids },
  });

export function useDeleteExperiments(datasetId, options = {}) {
  const refresh = useRefreshLists(datasetId);
  return useMutation({
    mutationFn: ({ ids }) => deleteExperiments(ids),
    onSuccess: (res, { ids, runCount }) => {
      refresh();
      enqueueSnackbar(
        `Deleted ${ids.length} ${ids.length === 1 ? "experiment" : "experiments"} (${runCount} ${runCount === 1 ? "run" : "runs"})`,
        { variant: "success" },
      );
      options.onSuccess?.(res);
    },
    onError: errorToast("Could not delete the experiments"),
    meta: { errorHandled: true },
  });
}

/**
 * exp.rename: PUT experiments/v2/{id}/ with only what changed: `{name}`,
 * `{description}` or both (a field left undefined never travels). Dev saves
 * them on their own, so no run starts and the runs and results stay.
 * mutate({id, name?, description?}). A refusal is not toasted: the Edit
 * dialog shows it next to its field (experiment-metadata renameErrorOf).
 */
export function useRenameExperiment(datasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name, description }) =>
      axios.put(endpoints.develop.experiment.update(id), {
        name,
        description,
      }),
    onSuccess: (res, sent) => {
      refreshAfterRename(queryClient, { datasetId, experimentId: sent.id });
      enqueueSnackbar(renameSuccessText(res, sent), { variant: "success" });
      options.onSuccess?.(res);
    },
    onError: (error, sent) => {
      // No answer: the save may have landed, so the lists read again.
      if (renameErrorOf(error).unknown)
        refreshAfterRename(queryClient, { datasetId, experimentId: sent?.id });
      options.onError?.(error);
    },
    meta: { errorHandled: true },
  });
}

/**
 * Rerun several runs at once. mode:
 *   "all"     POST experiments/v2/re-run/ {experiment_ids} (whole experiments)
 *   "failed"  POST experiments/v2/{id}/rerun-cells/ {source_ids, failed_only}
 *   "rescore" POST experiments/v2/{id}/rerun-cells/ {user_eval_metric_ids}
 * mutate({mode, runs, evalIds}).
 */
export function useBulkRerun(datasetId, options = {}) {
  const refresh = useRefreshLists(datasetId);
  return useMutation({
    mutationFn: async ({ mode, runs, evalIds = [] }) => {
      const byExp = new Map();
      runs.forEach((r) => {
        if (!byExp.has(r.experiment_id)) byExp.set(r.experiment_id, []);
        byExp.get(r.experiment_id).push(r.run_id);
      });
      if (mode === "all")
        return axios.post(endpoints.develop.experiment.rerun, {
          experiment_ids: [...byExp.keys()],
        });
      return Promise.all(
        [...byExp.entries()].map(([expId, runIds]) =>
          axios.post(
            endpoints.develop.experiment.reRunExperimentCell(expId),
            mode === "failed"
              ? { source_ids: runIds, failed_only: true }
              : { user_eval_metric_ids: evalIds },
          ),
        ),
      );
    },
    onSuccess: (res, { mode, runs }) => {
      refresh();
      const n = runs.length;
      const what = n === 1 ? runs[0].label : `${n} runs`;
      enqueueSnackbar(
        mode === "failed"
          ? `Re-running failed rows of ${what}`
          : mode === "rescore"
            ? `Re-scoring ${what}`
            : `Re-running ${what}`,
        { variant: "success" },
      );
      options.onSuccess?.(res);
    },
    onError: errorToast("Could not start the rerun"),
    meta: { errorHandled: true },
  });
}

/** GET experiments/v2/{id}/download/ as a CSV file. mutate({id, name}). */
export function useDownloadExperimentCsv() {
  return useMutation({
    mutationFn: ({ id }) =>
      axios.get(endpoints.develop.experiment.downloadExperiment(id), {
        responseType: "blob",
      }),
    onSuccess: (response, { id, name }) => {
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      const safe = String(name ?? id)
        .replace(/[^\w.-]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .toLowerCase();
      link.setAttribute("download", `${safe || "experiment"}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      enqueueSnackbar("CSV downloaded", { variant: "success" });
    },
    onError: errorToast("Could not download the CSV"),
    meta: { errorHandled: true },
  });
}
