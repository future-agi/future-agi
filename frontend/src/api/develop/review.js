// Human review of experiment results (PRODUCT-SPEC §3.11 Review tab, §3.12,
// C9). A review session is an annotation queue whose `metadata.kind` is
// "experiment_review" (one queue per run pair, or per run for a rubric);
// starting one reuses the existing queue create + add-items calls. In-drawer
// votes go through V2 `POST datasets/{id}/result-reviews/`, which finds or
// creates that queue and item and submits.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import {
  annotationQueueEndpoints,
  annotationQueueKeys,
} from "src/api/annotation-queues/annotation-queues";
import {
  REVIEW_KIND,
  matchingReviewQueues,
  reviewQueueFor,
} from "src/sections/develop-detail/Review/reviewModel";

export const reviewKeys = {
  all: ["develop", "experiment-review"],
  queues: (datasetId) => ["develop", "experiment-review", "queues", datasetId],
  ids: (datasetId, params) => [
    "develop",
    "experiment-review",
    "ids",
    datasetId,
    params,
  ],
};

/**
 * Experiment-review queues on a dataset (every annotation queue whose
 * metadata is `{kind: "experiment_review", dataset_id}`), newest first.
 * Reads the existing queue list; `metadata` rides along on each queue.
 */
export function useExperimentReviewQueues(datasetId, options = {}) {
  const { enabled = true, ...queryOptions } = options;
  return useQuery({
    queryKey: reviewKeys.queues(datasetId),
    queryFn: () =>
      axios.get(annotationQueueEndpoints.list, {
        params: { limit: 200, include_counts: true },
      }),
    select: (res) =>
      (res?.data?.results ?? []).filter(
        (q) =>
          q?.metadata?.kind === REVIEW_KIND &&
          (!datasetId || q.metadata.dataset_id === datasetId),
      ),
    enabled: Boolean(datasetId) && enabled,
    staleTime: 10_000,
    ...queryOptions,
  });
}

/** The review queue for a pair (pairwise, either order) or a run (rubric). */
export function useReviewQueueFor(datasetId, { mode, runA, runB, runId }) {
  const query = useExperimentReviewQueues(datasetId);
  const queue = reviewQueueFor(query.data ?? [], { mode, runA, runB, runId });
  return { ...query, queue };
}

export { matchingReviewQueues };

/**
 * R3 with `ids_only=true`: the example ids of a results view, in a sort.
 * params: { runs:[id], baseline, filters:[pill], sort, search }.
 * Resolves `{ total, example_ids }`.
 */
export async function fetchResultExampleIds(datasetId, params) {
  const { runs, baseline, filters, sort, search } = params;
  const res = await axios.get(
    endpoints.develop.datasetRuns.results(datasetId),
    {
      params: {
        runs: (runs ?? []).join(","),
        baseline,
        filters: filters?.length ? JSON.stringify(filters) : undefined,
        sort: sort || undefined,
        search: search || undefined,
        ids_only: true,
      },
    },
  );
  const result = res?.data?.result ?? {};
  return {
    total: result.total ?? result.example_ids?.length ?? 0,
    example_ids: result.example_ids ?? [],
  };
}

/** Counted row sources for StartReviewDialog (R3 ids_only, cached). */
export function useResultExampleIds(datasetId, params, options = {}) {
  const { enabled = true, ...queryOptions } = options;
  return useQuery({
    queryKey: reviewKeys.ids(datasetId, params),
    queryFn: () => fetchResultExampleIds(datasetId, params),
    enabled: Boolean(datasetId) && (params?.runs?.length ?? 0) > 0 && enabled,
    staleTime: 15_000,
    ...queryOptions,
  });
}

/**
 * R3 page 0 with one row: `summary.metrics` (and the total) of a view, used
 * to pick the primary metric for "Regressions first".
 */
export function useResultSummary(datasetId, { runs, baseline }, options = {}) {
  const { enabled = true, ...queryOptions } = options;
  return useQuery({
    queryKey: [...reviewKeys.all, "summary", datasetId, runs, baseline],
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.results(datasetId), {
        params: {
          runs: (runs ?? []).join(","),
          baseline,
          page: 0,
          page_size: 1,
        },
      }),
    select: (res) => res?.data?.result?.summary ?? null,
    enabled: Boolean(datasetId) && (runs?.length ?? 0) > 0 && enabled,
    staleTime: 30_000,
    ...queryOptions,
  });
}

// Results grids, analysis and run reads that show human preference.
const refreshesAfterVote = (queryKey) => {
  const key = JSON.stringify(queryKey ?? []);
  return (
    key.includes("results") ||
    key.includes("analysis") ||
    key.includes("dataset-runs") ||
    key.includes("experiment-review")
  );
};

/**
 * V2 · POST datasets/{id}/result-reviews/. mutate(body) where body =
 * `{example_id, mode:"pairwise", run_a, run_b, values:[{label_id, value}],
 * notes, blind, left}` or `{example_id, mode:"rubric", run_id, values,
 * notes}`. `values: [{label_id, value: null}]` clears the viewer's vote.
 */
export function useSubmitResultReview(datasetId, options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.develop.datasetRuns.resultReviews(datasetId), body),
    ...options,
    onSuccess: (res, vars, ctx) => {
      queryClient.invalidateQueries({
        predicate: (q) => refreshesAfterVote(q.queryKey),
      });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
      options.onSuccess?.(res?.data?.result ?? null, vars, ctx);
    },
    onError: (error, vars, ctx) => {
      const msg =
        error?.response?.data?.result ||
        error?.result ||
        "Could not save the review";
      enqueueSnackbar(typeof msg === "string" ? msg : "Could not save", {
        variant: "error",
      });
      options.onError?.(error, vars, ctx);
    },
  });
}

/** The viewer's next item in a queue (`items/next-item/`), or null. */
export async function fetchNextReviewItemId(queueId) {
  try {
    const res = await axios.get(annotationQueueEndpoints.nextItem(queueId));
    const data = res?.data?.result ?? res?.data;
    return data?.item?.id ?? null;
  } catch {
    return null;
  }
}

// ------------------------------------------------ W4 C1 · review corrections
// A reviewer's corrected expected answer is a suggested change to the
// dataset (MOCK/CONTRACT.md "W4" ReviewCorrection); the owner accepts or
// dismisses each one and saves the accepted ones as a version (N2).
export const correctionKeys = {
  all: ["develop", "review-corrections"],
  list: (datasetId, params) => [
    "develop",
    "review-corrections",
    datasetId,
    params ?? {},
  ],
};

/** `{corrections, counts, can_apply}` of a dataset (optionally one queue). */
export function useReviewCorrections(datasetId, params = {}, options = {}) {
  const { enabled = true, ...queryOptions } = options;
  return useQuery({
    queryKey: correctionKeys.list(datasetId, params),
    queryFn: () =>
      axios.get(endpoints.develop.w4.corrections(datasetId), {
        params: {
          status: params.status || undefined,
          queue_id: params.queueId || undefined,
        },
      }),
    select: (res) => res?.data?.result ?? null,
    enabled: Boolean(datasetId) && enabled,
    staleTime: 5_000,
    ...queryOptions,
  });
}

/** POST a suggestion: `{row_id, column_id, value, source, kind?, run_id?}`. */
export async function postCorrection(datasetId, body) {
  const res = await axios.post(
    endpoints.develop.w4.corrections(datasetId),
    body,
  );
  return res?.data?.result ?? null;
}

/** PATCH one correction: `{status: "applied", version_number}` | `{status}`. */
export async function patchCorrection(datasetId, correctionId, patch) {
  const res = await axios.patch(
    endpoints.develop.w4.correction(datasetId, correctionId),
    patch,
  );
  return res?.data?.result ?? null;
}

/** Refresh every read that shows corrections. */
export const refreshCorrections = (queryClient) =>
  queryClient.invalidateQueries({ queryKey: correctionKeys.all });
