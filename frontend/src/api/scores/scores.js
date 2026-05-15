import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { apiPath } from "src/api/contracts/api-surface";
import {
  modelHubScoresBulkCreate,
  modelHubScoresCreate,
  modelHubScoresDelete,
  modelHubScoresForSource,
} from "src/generated/api-contracts/api";

export const scoreEndpoints = {
  list: apiPath("/model-hub/scores/"),
  detail: (id) => apiPath("/model-hub/scores/{id}/", { id }),
  bulk: apiPath("/model-hub/scores/bulk/"),
  forSource: apiPath("/model-hub/scores/for-source/"),
};

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------
export const scoreKeys = {
  all: ["scores"],
  forSource: (sourceType, sourceId) => ["scores", sourceType, sourceId],
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/**
 * Fetch all scores for a given source (trace, span, session, etc.)
 */
export const useScoresForSource = (sourceType, sourceId, options = {}) => {
  return useQuery({
    queryKey: scoreKeys.forSource(sourceType, sourceId),
    queryFn: () =>
      modelHubScoresForSource({ source_type: sourceType, source_id: sourceId }),
    select: (d) => d?.result || d,
    enabled: !!sourceType && !!sourceId,
    staleTime: 1000 * 60,
    ...options,
  });
};

/**
 * Fetch span-level notes for an observation_span source.
 * Returns the span_notes array from the for-source endpoint.
 */
export const useSpanNotes = (spanId, options = {}) => {
  return useQuery({
    queryKey: ["span-notes", spanId],
    queryFn: () =>
      modelHubScoresForSource({
        source_type: "observation_span",
        source_id: spanId,
      }),
    select: (d) => d?.span_notes || [],
    enabled: !!spanId,
    staleTime: 1000 * 60,
    ...options,
  });
};

/**
 * Create a single score.
 */
export const useCreateScore = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      sourceType,
      sourceId,
      queueItemId,
      labelId,
      value,
      notes,
      scoreSource,
    }) =>
      modelHubScoresCreate({
        source_type: sourceType,
        source_id: sourceId,
        label_id: labelId,
        value,
        notes,
        score_source: scoreSource || "human",
        // queue_item_id pins the score to a specific queue review context;
        // see useBulkCreateScores for rationale.
        ...(queueItemId ? { queue_item_id: queueItemId } : {}),
      }),
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: scoreKeys.forSource(variables.sourceType, variables.sourceId),
      });
      // Invalidate queue items for this specific source in case queue items got auto-completed
      queryClient.invalidateQueries({
        queryKey: ["annotation-queues", "for-source"],
      });
    },
    onError: (error) => {
      const msg = error?.result || error?.detail || "Failed to save score";
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

/**
 * Create multiple scores on a single source (inline annotator).
 */
export const useBulkCreateScores = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      sourceType,
      sourceId,
      queueItemId,
      scores,
      notes,
      spanNotes,
      includeSpanNotes = false,
      spanNotesSourceId,
      scoreSource,
    }) => {
      const payload = {
        source_type: sourceType,
        source_id: sourceId,
        scores,
        notes: notes || "",
        score_source: scoreSource || "human",
      };
      // queue_item_id is the queue review context the caller wants the
      // scores attributed to. Required for per-queue scoring (one score
      // per (source, label, annotator, queue)) — otherwise the backend
      // falls back to the source's default queue.
      if (queueItemId) {
        payload.queue_item_id = queueItemId;
      }
      if (includeSpanNotes || spanNotes) {
        payload.span_notes = spanNotes || "";
        if (spanNotesSourceId) {
          payload.span_notes_source_id = spanNotesSourceId;
        }
      }
      return modelHubScoresBulkCreate(payload);
    },
    onSuccess: (data, variables) => {
      // Backend returns { scores: [...saved], errors: [...failed] } per
      // model_hub/views/scores.py:bulk_create. A 2xx response can hide
      // partial failures (e.g., label not found, validation error on one
      // label) — without inspecting `errors[]` the UI used to claim success
      // even when some labels were silently dropped. Surface partial
      // failures explicitly so the user can retry the failed ones.
      const result = data?.result || data || {};
      const errors = result.errors || [];
      const savedCount = (result.scores || []).length;

      if (errors.length > 0) {
        enqueueSnackbar(
          `Saved ${savedCount} annotation${savedCount === 1 ? "" : "s"}; ` +
            `${errors.length} failed: ${errors.slice(0, 3).join("; ")}` +
            (errors.length > 3 ? "…" : ""),
          { variant: "warning", autoHideDuration: 8000 },
        );
      } else {
        enqueueSnackbar("Annotations saved", { variant: "success" });
      }

      queryClient.invalidateQueries({
        queryKey: scoreKeys.forSource(variables.sourceType, variables.sourceId),
      });
      const spanNotesSourceId =
        variables.spanNotesSourceId ||
        (variables.sourceType === "observation_span"
          ? variables.sourceId
          : null);
      if (spanNotesSourceId) {
        queryClient.invalidateQueries({
          queryKey: ["span-notes", spanNotesSourceId],
        });
      }
      // Invalidate queue items for this specific source in case queue items got auto-completed
      queryClient.invalidateQueries({
        queryKey: ["annotation-queues", "for-source"],
      });
    },
    onError: (error) => {
      // Axios attaches backend error JSON at error.response.data; the older
      // pattern `error?.result || error?.detail` was always falling through
      // to the generic message because those keys live one level deeper.
      const body = error?.response?.data || {};
      const msg =
        body.result ||
        body.detail ||
        body.message ||
        error?.message ||
        "Failed to save annotations";
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

/**
 * Delete a score.
 */
export const useDeleteScore = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ scoreId }) => modelHubScoresDelete(scoreId),
    onSuccess: (data, variables) => {
      if (variables.sourceType && variables.sourceId) {
        queryClient.invalidateQueries({
          queryKey: scoreKeys.forSource(
            variables.sourceType,
            variables.sourceId,
          ),
        });
      } else {
        queryClient.invalidateQueries({ queryKey: scoreKeys.all });
      }
    },
    onError: () => {
      enqueueSnackbar("Failed to delete score", { variant: "error" });
    },
  });
};
