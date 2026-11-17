// IT-R2 G2 P1: links land where the answer is. An experiment opens on the
// Matrix when it holds 3+ runs (one look at every run, task by task), else on
// its Data tab; a dataset with experiments opens on its Experiments tab.
//
// The dataset list's payload names an experiment or a run but not how many
// runs it holds, so a list link resolves on click from R1 (`GET
// datasets/{id}/runs/`, the same cached read the Experiments tab makes):
//   resolveExperimentHref(queryClient, {datasetId, experimentId?, runId?})
// The anchor keeps the plain Data URL as its href (middle-click, new tab).
import axios, { endpoints } from "src/utils/axios";
import { datasetRunsKey, datasetRunsParams } from "src/api/develop/runs";
import { defaultRunsTab } from "./runKind";

/** The experiment page URL; `cand` = the run the page's verdict is about. */
export function experimentPageHref(
  experimentId,
  { datasetId, tab = "data", cand } = {},
) {
  const parts = [];
  if (datasetId) parts.push(`datasetId=${datasetId}`);
  if (cand) parts.push(`cand=${cand}`);
  return `/dashboard/develop/experiment/${experimentId}/${tab}${parts.length ? `?${parts.join("&")}` : ""}`;
}

/** Same params the Experiments tab reads, so the list shares its cache. */
const LIST_PARAMS = datasetRunsParams({ include: "metrics,config" });

/** R1 runs of a dataset through the shared query cache (fetches once). */
export async function fetchDatasetRuns(queryClient, datasetId) {
  if (!datasetId) return [];
  const res = await queryClient.fetchQuery({
    queryKey: [...datasetRunsKey(datasetId), LIST_PARAMS],
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.list(datasetId), {
        params: LIST_PARAMS,
      }),
    staleTime: 30_000,
  });
  // The hook selects `res.data.result`; a cached entry is the raw response.
  const result = res?.data?.result ?? res?.result ?? res;
  return Array.isArray(result?.results) ? result.results : [];
}

/** Warm the cache on hover, so the click resolves at once. */
export function prefetchDatasetRuns(queryClient, datasetId) {
  fetchDatasetRuns(queryClient, datasetId).catch(() => {});
}

/**
 * Where an experiment (or a run's experiment) opens.
 * @param {object[]} runs  R1 runs of the dataset
 * @param {{experimentId?: string, runId?: string, datasetId?: string}} o
 * @returns {string|null}
 */
export function experimentHrefFromRuns(
  runs,
  { experimentId, runId, datasetId },
) {
  const run = runId ? (runs ?? []).find((r) => r.run_id === runId) : null;
  const expId = experimentId ?? run?.experiment_id ?? null;
  if (!expId) return null;
  const mine = (runs ?? []).filter(
    (r) => r.experiment_id === expId && r.listed !== false,
  );
  const tab = defaultRunsTab(mine);
  return experimentPageHref(expId, {
    datasetId,
    tab,
    // The verdict on the page follows the run the link named (rule 12).
    cand: run && tab === "matrix" ? run.run_id : undefined,
  });
}

/** Resolve on click; falls back to `fallback` when R1 cannot answer. */
export async function resolveExperimentHref(
  queryClient,
  { datasetId, experimentId, runId, fallback = null },
) {
  try {
    const runs = await fetchDatasetRuns(queryClient, datasetId);
    return (
      experimentHrefFromRuns(runs, { experimentId, runId, datasetId }) ??
      fallback
    );
  } catch {
    return fallback;
  }
}
