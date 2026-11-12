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

// A partial fetch is never returned as a complete run/results population.
export function readEnvelope(response) {
  const body = response?.data;
  if (body?.status !== true || !body.result || typeof body.result !== "object") {
    throw new Error(body?.result?.message ?? "Invalid Results response. Retry the read.");
  }
  return body.result;
}

export async function fetchReadPages(url, params, {
  collection = "results", signal, maxItems = 10000,
} = {}) {
  const catalog = collection === "results";
  const positionKey = catalog ? "offset" : "page";
  let position = 0;
  let first;
  let total;
  let bytes = 0;
  let lastPagination;
  const records = [];
  const seen = new Set();
  const metricDefs = new Map();
  for (let page = 0; page < 100; page += 1) {
    const response = await axios.get(url, {
      params: { ...params, [positionKey]: position }, signal,
    });
    const result = readEnvelope(response);
    const pagination = catalog ? result : result.pagination;
    const population = catalog ? result.count : result.total;
    const chunk = result[collection];
    if (!Array.isArray(chunk) || !Number.isSafeInteger(population) || population < 0 ||
        typeof pagination?.has_more !== "boolean" || pagination[positionKey] !== position) {
      throw new Error("Invalid Results pagination contract.");
    }
    if (population > maxItems) {
      throw new Error(`This view exceeds ${maxItems} records. Narrow the selection; no partial data was shown.`);
    }
    if (!catalog && result.scope?.complete !== true) {
      throw new Error(result.scope?.reason ?? "Results population is incomplete. Narrow the selection.");
    }
    if (!first) {
      first = response;
      total = population;
    } else if (population !== total ||
        result.scope?.selection_id !== first.data.result.scope?.selection_id ||
        (catalog && result.pinned_run_id !== first.data.result.pinned_run_id)) {
      throw new Error("Results changed while loading. Retry to read a consistent selection.");
    }
    bytes += new TextEncoder().encode(JSON.stringify(result)).byteLength;
    if (bytes > 16 * 1024 * 1024) {
      throw new Error("This view exceeds the 16 MiB read limit. Narrow the selection.");
    }
    for (const row of chunk) {
      const id = collection === "example_ids" ? row : row.run_id ?? row.example_id ?? row.row_id;
      if (typeof id !== "string" || seen.has(id)) {
        throw new Error("Duplicate or invalid identity across Results pages. Retry the read.");
      }
      seen.add(id);
      records.push(row);
    }
    for (const def of result.metric_defs ?? []) {
      const previous = metricDefs.get(def.key);
      const versions = { ...previous?.versions, ...def.versions };
      metricDefs.set(def.key, { ...def, versions,
        runs: [...new Set([...(previous?.runs ?? []), ...(def.runs ?? [])])],
        versions_differ: new Set(Object.values(versions)).size > 1,
      });
    }
    lastPagination = pagination;
    if (!pagination.has_more) {
      if (records.length !== total) throw new Error("Incomplete Results population. Retry the read.");
      const merged = { ...first.data.result, [collection]: records,
        scope: { ...first.data.result.scope, complete: true, loaded: records.length },
      };
      if (catalog) Object.assign(merged, { metric_defs: [...metricDefs.values()],
        has_more: false, next: null, offset: 0 });
      else merged.pagination = { ...lastPagination, page: 0, page_size: records.length,
        has_more: false, next: null };
      return { ...first, data: { ...first.data, result: merged } };
    }
    if (!chunk.length || !Number.isSafeInteger(pagination.next) || pagination.next <= position) {
      throw new Error("Results continuation did not advance.");
    }
    const expected = catalog ? position + chunk.length : position + 1;
    if (pagination.next !== expected) throw new Error("Results continuation skipped a page.");
    position = pagination.next;
  }
  throw new Error("Results continuation exceeded 100 pages. Narrow the selection.");
}

export const fetchDatasetRuns = (datasetId, params, signal) =>
  fetchReadPages(endpoints.develop.datasetRuns.list(datasetId), params, {
    collection: "results", signal, maxItems: 10000,
  });

// Query params of R1. Metrics describe assessed evidence, not invented deltas.
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
    queryFn: ({ signal }) => fetchDatasetRuns(datasetId, params, signal),
    select: readEnvelope,
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
    queryFn: async ({ signal }) => {
      const response = await axios.get(
        endpoints.develop.experiment.getExperimentDetails(experimentId), { signal },
      );
      const detail = readEnvelope(response);
      if (!Array.isArray(detail.runs) || typeof detail.runs_has_more !== "boolean") {
        throw new Error("Canonical experiment run catalog is unavailable. Use classic results.");
      }
      if (!detail.runs_has_more) {
        if (detail.runs.length !== detail.runs_total) throw new Error("Incomplete experiment run catalog.");
        return response;
      }
      const all = readEnvelope(await fetchDatasetRuns(detail.dataset_id, {
        limit: 500, include: "config,lineage", experiment_id: experimentId,
      }, signal));
      return { ...response, data: { ...response.data, result: {
        ...detail, runs: all.results, runs_total: all.count,
        runs_has_more: false, runs_next: null,
      } } };
    },
    select: readEnvelope,
    enabled: Boolean(experimentId) && enabled,
    ...queryOptions,
  });
}
