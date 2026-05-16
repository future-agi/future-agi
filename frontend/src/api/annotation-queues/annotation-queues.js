import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { apiPath } from "src/api/contracts/api-surface";
import { scoreKeys } from "src/api/scores/scores";

// ---------------------------------------------------------------------------
// Helper – extract response payload consistently across endpoints that may
// wrap data in `result`, `results`, or return it at the top level.
// ---------------------------------------------------------------------------
const extractData = (d, fallback = null) =>
  d.data?.result ?? d.data?.results ?? d.data ?? fallback;

export const extractErrorMessage = (error, fallback) => {
  const payload = error?.response?.data || error;
  const nestedError = payload?.error;
  const nestedErrorDetail = nestedError?.detail;
  const message =
    payload?.result ||
    payload?.detail ||
    payload?.message ||
    nestedError?.message ||
    (typeof nestedErrorDetail === "string" ? nestedErrorDetail : null) ||
    nestedError ||
    payload?.non_field_errors ||
    fallback;

  if (Array.isArray(message)) return message.join(", ");
  if (message && typeof message === "object") return JSON.stringify(message);
  return message || fallback;
};

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------
export const annotationQueueEndpoints = {
  list: apiPath("/model-hub/annotation-queues/"),
  create: apiPath("/model-hub/annotation-queues/"),
  detail: (id) => apiPath("/model-hub/annotation-queues/{id}/", { id }),
  restore: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/restore/", { id }),
  hardDelete: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/hard-delete/", { id }),
  updateStatus: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/update-status/", { id }),
  forSource: apiPath("/model-hub/annotation-queues/for-source/"),
  getOrCreateDefault: apiPath(
    "/model-hub/annotation-queues/get-or-create-default/",
  ),
  addLabel: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/add-label/", { id }),
  removeLabel: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/remove-label/", { id }),
  agreement: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/agreement/", { id }),
  analytics: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/analytics/", { id }),
  exportFields: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/export-fields/", { id }),
  exportToDataset: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/export-to-dataset/", { id }),
  export: (id) => apiPath("/model-hub/annotation-queues/{id}/export/", { id }),
  progress: (id) =>
    apiPath("/model-hub/annotation-queues/{id}/progress/", { id }),
  automationRules: (queueId) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/", {
      queue_id: queueId,
    }),
  automationRuleDetail: (queueId, id) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/", {
      queue_id: queueId,
      id,
    }),
  automationRuleEvaluate: (queueId, id) =>
    apiPath(
      "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/evaluate/",
      {
        queue_id: queueId,
        id,
      },
    ),
  items: (queueId) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/", {
      queue_id: queueId,
    }),
  addItems: (queueId) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/add-items/", {
      queue_id: queueId,
    }),
  assignItems: (queueId) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/assign/", {
      queue_id: queueId,
    }),
  bulkRemoveItems: (queueId) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/bulk-remove/", {
      queue_id: queueId,
    }),
  nextItem: (queueId) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/next-item/", {
      queue_id: queueId,
    }),
  itemDetail: (queueId, id) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/{id}/", {
      queue_id: queueId,
      id,
    }),
  annotateDetail: (queueId, id) =>
    apiPath(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
      {
        queue_id: queueId,
        id,
      },
    ),
  itemAnnotations: (queueId, id) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/", {
      queue_id: queueId,
      id,
    }),
  submitAnnotations: (queueId, id) =>
    apiPath(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
      {
        queue_id: queueId,
        id,
      },
    ),
  completeItem: (queueId, id) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/{id}/complete/", {
      queue_id: queueId,
      id,
    }),
  skipItem: (queueId, id) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/{id}/skip/", {
      queue_id: queueId,
      id,
    }),
  reviewItem: (queueId, id) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/{id}/review/", {
      queue_id: queueId,
      id,
    }),
  discussion: (queueId, id) =>
    apiPath("/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/", {
      queue_id: queueId,
      id,
    }),
  discussionResolve: (queueId, id, threadId) =>
    apiPath(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/{thread_id}/resolve/",
      {
        queue_id: queueId,
        id,
        thread_id: threadId,
      },
    ),
  discussionReopen: (queueId, id, threadId) =>
    apiPath(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/{thread_id}/reopen/",
      {
        queue_id: queueId,
        id,
        thread_id: threadId,
      },
    ),
  discussionReaction: (queueId, id, commentId) =>
    apiPath(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/reaction/",
      {
        queue_id: queueId,
        id,
        comment_id: commentId,
      },
    ),
};

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------
export const annotationQueueKeys = {
  all: ["annotation-queues"],
  list: (filters) => ["annotation-queues", "list", filters],
  detail: (id) => ["annotation-queues", "detail", id],
  exportFields: (id) => ["annotation-queues", "export-fields", id],
  progress: (queueId) => ["annotation-queues", "progress", queueId],
  analytics: (queueId) => ["annotation-queues", "analytics", queueId],
  agreement: (queueId) => ["annotation-queues", "agreement", queueId],
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export const useAnnotationQueuesList = (filters = {}, options = {}) => {
  return useQuery({
    queryKey: annotationQueueKeys.list(filters),
    queryFn: () =>
      axios.get(annotationQueueEndpoints.list, { params: filters }),
    select: (d) => d.data,
    staleTime: 1000 * 60 * 2,
    ...options,
  });
};

export const useAnnotationQueueDetail = (id, options = {}) => {
  return useQuery({
    queryKey: annotationQueueKeys.detail(id),
    queryFn: () => axios.get(annotationQueueEndpoints.detail(id)),
    select: (d) => extractData(d),
    enabled: !!id,
    ...options,
  });
};

export const useCreateAnnotationQueue = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data) => axios.post(annotationQueueEndpoints.create, data),
    onSuccess: () => {
      enqueueSnackbar("Queue created successfully", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
    },
    onError: (error) => {
      const msg = extractErrorMessage(error, "Failed to create queue");
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useUpdateAnnotationQueue = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }) =>
      axios.patch(annotationQueueEndpoints.detail(id), data),
    onSuccess: (_, variables) => {
      enqueueSnackbar("Queue updated successfully", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.detail(variables.id),
      });
    },
    onError: (error) => {
      const msg = extractErrorMessage(error, "Failed to update queue");
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

// "Delete" in the UI is a soft archive — the queue gets `deleted=true` and
// rules attached to it go dormant. Restoration brings them back. For
// truly destructive removal use `useHardDeleteAnnotationQueue` below.
export const useArchiveAnnotationQueue = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => axios.delete(annotationQueueEndpoints.detail(id)),
    onSuccess: () => {
      enqueueSnackbar("Queue archived. Rules paused; you can restore later.", {
        variant: "info",
      });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
    },
    onError: () => {
      enqueueSnackbar("Failed to archive queue", { variant: "error" });
    },
  });
};

// Backwards-compat alias — call sites still use this name.
export const useDeleteAnnotationQueue = useArchiveAnnotationQueue;

export const useHardDeleteAnnotationQueue = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }) =>
      axios.post(annotationQueueEndpoints.hardDelete(id), {
        force: true,
        confirm_name: name,
      }),
    onSuccess: () => {
      enqueueSnackbar("Queue permanently deleted.", { variant: "warning" });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
    },
    onError: (error) => {
      enqueueSnackbar(extractErrorMessage(error, "Failed to delete queue"), {
        variant: "error",
      });
    },
  });
};

export const useRestoreAnnotationQueue = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => axios.post(annotationQueueEndpoints.restore(id)),
    onSuccess: () => {
      enqueueSnackbar("Queue restored. Rule cadence reset.", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
    },
    onError: () => {
      enqueueSnackbar("Failed to restore queue", { variant: "error" });
    },
  });
};

export const useUpdateAnnotationQueueStatus = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }) =>
      axios.post(annotationQueueEndpoints.updateStatus(id), { status }),
    onMutate: async ({ id, status }) => {
      // Optimistically update cached queue lists so the UI reflects the new
      // status immediately (prevents stale menu options on re-open).
      await queryClient.cancelQueries({ queryKey: annotationQueueKeys.all });
      queryClient.setQueriesData(
        { queryKey: annotationQueueKeys.all },
        (old) => {
          if (!old) return old;
          const data = old?.data?.result || old?.data || old;
          const results = data?.results;
          if (!Array.isArray(results)) return old;
          return {
            ...old,
            data: {
              ...(old?.data || {}),
              result: {
                ...data,
                results: results.map((q) =>
                  q.id === id ? { ...q, status } : q,
                ),
              },
            },
          };
        },
      );
    },
    onSuccess: (_, variables) => {
      enqueueSnackbar("Queue status updated", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.detail(variables.id),
      });
    },
    onError: (error) => {
      // Revert optimistic update on error
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
      const msg = extractErrorMessage(error, "Failed to update status");
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

// ---------------------------------------------------------------------------
// Queue Items hooks
// ---------------------------------------------------------------------------
export const queueItemKeys = {
  all: (queueId) => ["queue-items", queueId],
  list: (queueId, filters) => ["queue-items", queueId, "list", filters],
};

export const useQueueItems = (queueId, filters = {}, options = {}) => {
  const { page, limit, ...restFilters } = filters;
  return useInfiniteQuery({
    queryKey: queueItemKeys.list(queueId, restFilters),
    queryFn: ({ pageParam = 1 }) =>
      axios.get(annotationQueueEndpoints.items(queueId), {
        params: { ...restFilters, page: pageParam, limit: limit || 25 },
      }),
    getNextPageParam: (lastPage) => {
      const data = lastPage.data;
      const currentPage = data?.current_page ?? 1;
      const totalPages = data?.total_pages ?? 1;
      return currentPage < totalPages ? currentPage + 1 : undefined;
    },
    select: (d) => {
      const allResults = d.pages.flatMap((p) => p.data?.results ?? []);
      const lastPageData = d.pages[d.pages.length - 1]?.data;
      return {
        results: allResults,
        count: lastPageData?.count ?? allResults.length,
      };
    },
    enabled: !!queueId,
    staleTime: 1000 * 60 * 2,
    ...options,
  });
};

export const useAddQueueItems = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, items, selection }) =>
      axios.post(
        annotationQueueEndpoints.addItems(queueId),
        selection ? { selection } : { items },
      ),
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.all,
      });
    },
    onError: (error) => {
      // Filter-mode bulk add can exceed the backend cap; surface the
      // structured error so the user sees the exact count and limit.
      const structured = error?.response?.data?.error;
      if (structured?.type === "selection_too_large") {
        enqueueSnackbar(structured.message, { variant: "error" });
        return;
      }
      const msg = extractErrorMessage(error, "Failed to add items");
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useRemoveQueueItem = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId }) =>
      axios.delete(annotationQueueEndpoints.itemDetail(queueId, itemId)),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.all,
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to remove item", { variant: "error" });
    },
  });
};

export const useBulkRemoveQueueItems = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemIds }) =>
      axios.post(annotationQueueEndpoints.bulkRemoveItems(queueId), {
        item_ids: itemIds,
      }),
    onSuccess: (data, variables) => {
      const removed = data?.data?.result?.removed || 0;
      enqueueSnackbar(`${removed} item${removed !== 1 ? "s" : ""} removed`, {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.all,
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to remove items", { variant: "error" });
    },
  });
};

export const useQueueProgress = (queueId, options = {}) => {
  return useQuery({
    queryKey: annotationQueueKeys.progress(queueId),
    queryFn: () => axios.get(annotationQueueEndpoints.progress(queueId)),
    select: (d) => extractData(d),
    enabled: !!queueId,
    staleTime: 1000 * 30,
    ...options,
  });
};

const getAssignmentUserId = (user) => user?.id ?? user?.user_id ?? user?.userId;

const normalizeAssignmentUser = (user, fallbackId) => {
  const id = String(getAssignmentUserId(user) ?? fallbackId ?? "");
  if (!id) return null;
  return {
    ...(user || {}),
    id,
    user_id: user?.user_id ?? id,
    name: user?.name || user?.email || id,
  };
};

const optimisticAssignmentUsers = (variables, assignedUsers = []) => {
  const ids = (
    variables.userIds || (variables.userId ? [variables.userId] : [])
  )
    .map((id) => String(id))
    .filter(Boolean);
  const assignees = [...(variables.assignees || []), ...assignedUsers];

  return ids
    .map((id) => {
      const assignee = assignees.find(
        (candidate) => String(getAssignmentUserId(candidate)) === id,
      );
      return normalizeAssignmentUser(assignee, id);
    })
    .filter(Boolean);
};

const applyOptimisticAssignment = (assignedUsers = [], variables) => {
  const action = variables.action || "add";
  const nextUsers = optimisticAssignmentUsers(variables, assignedUsers);

  if (action === "set") return nextUsers;

  const nextUserIds = new Set(nextUsers.map((user) => String(user.id)));
  if (!nextUserIds.size) return assignedUsers;

  const usersById = new Map();
  assignedUsers.forEach((user) => {
    const normalized = normalizeAssignmentUser(user);
    if (normalized) usersById.set(String(normalized.id), normalized);
  });

  if (action === "remove") {
    nextUserIds.forEach((id) => usersById.delete(id));
  } else {
    nextUsers.forEach((user) => usersById.set(String(user.id), user));
  }

  return Array.from(usersById.values());
};

const patchAssignmentItem = (item, variables) => {
  if (!item?.id) return item;
  const targetIds = new Set((variables.itemIds || []).map((id) => String(id)));
  if (!targetIds.has(String(item.id))) return item;

  const assignedUsers = applyOptimisticAssignment(
    item.assigned_users || [],
    variables,
  );
  return {
    ...item,
    assigned_users: assignedUsers,
    assigned_to_name:
      assignedUsers
        .map((user) => user.name || user.email)
        .filter(Boolean)
        .join(", ") || null,
  };
};

const patchAssignmentCacheValue = (value, variables) => {
  if (!value || typeof value !== "object") return value;

  if (Array.isArray(value)) {
    return value.map((entry) => patchAssignmentCacheValue(entry, variables));
  }

  if (Array.isArray(value.pages)) {
    return {
      ...value,
      pages: value.pages.map((page) =>
        patchAssignmentCacheValue(page, variables),
      ),
    };
  }

  if (value.data) {
    return {
      ...value,
      data: patchAssignmentCacheValue(value.data, variables),
    };
  }

  if (value.result) {
    return {
      ...value,
      result: patchAssignmentCacheValue(value.result, variables),
    };
  }

  if (Array.isArray(value.results)) {
    return {
      ...value,
      results: value.results.map((item) =>
        patchAssignmentItem(item, variables),
      ),
    };
  }

  if (value.item) {
    return {
      ...value,
      item: patchAssignmentItem(value.item, variables),
    };
  }

  return patchAssignmentItem(value, variables);
};

export const useAssignQueueItems = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemIds, userId, userIds, action }) =>
      axios.post(annotationQueueEndpoints.assignItems(queueId), {
        item_ids: itemIds,
        ...(userIds
          ? { user_ids: userIds, action: action || "add" }
          : { user_id: userId }),
      }),
    onMutate: async (variables) => {
      const itemIds = variables.itemIds || [];

      await Promise.all([
        queryClient.cancelQueries({
          queryKey: queueItemKeys.all(variables.queueId),
          exact: false,
        }),
        ...itemIds.map((itemId) =>
          queryClient.cancelQueries({
            queryKey: annotateKeys.detail(variables.queueId, itemId),
            exact: false,
          }),
        ),
      ]);

      const previousQueueItems = queryClient.getQueriesData({
        queryKey: queueItemKeys.all(variables.queueId),
        exact: false,
      });
      const previousDetails = itemIds.flatMap((itemId) =>
        queryClient.getQueriesData({
          queryKey: annotateKeys.detail(variables.queueId, itemId),
          exact: false,
        }),
      );

      queryClient.setQueriesData(
        { queryKey: queueItemKeys.all(variables.queueId), exact: false },
        (old) => patchAssignmentCacheValue(old, variables),
      );
      itemIds.forEach((itemId) => {
        queryClient.setQueriesData(
          {
            queryKey: annotateKeys.detail(variables.queueId, itemId),
            exact: false,
          },
          (old) => patchAssignmentCacheValue(old, variables),
        );
      });

      return { previousQueueItems, previousDetails };
    },
    onSuccess: (data, variables) => {
      enqueueSnackbar("Assignees updated", { variant: "success" });
      (variables.itemIds || []).forEach((itemId) => {
        invalidateAnnotateItem(queryClient, variables.queueId, itemId);
      });
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotateKeys.nextItem(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.progress(variables.queueId),
      });
    },
    onError: (_error, _variables, context) => {
      context?.previousQueueItems?.forEach(([queryKey, data]) => {
        queryClient.setQueryData(queryKey, data);
      });
      context?.previousDetails?.forEach(([queryKey, data]) => {
        queryClient.setQueryData(queryKey, data);
      });
      enqueueSnackbar("Failed to assign items", { variant: "error" });
    },
  });
};

export const useUpdateQueueItemStatus = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId, status }) =>
      axios.patch(annotationQueueEndpoints.itemDetail(queueId, itemId), {
        status,
      }),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to update item status", { variant: "error" });
    },
  });
};

// ---------------------------------------------------------------------------
// Phase 3A: Annotation workspace hooks
// ---------------------------------------------------------------------------

export const annotateKeys = {
  detail: (queueId, itemId, annotatorId, filters) => {
    const key = annotatorId
      ? ["annotate-detail", queueId, itemId, annotatorId]
      : ["annotate-detail", queueId, itemId];
    return filters && Object.keys(filters).length ? [...key, filters] : key;
  },
  discussion: (queueId, itemId) => ["annotate-discussion", queueId, itemId],
  nextItem: (queueId, filters) =>
    filters && Object.keys(filters).length
      ? ["annotate-next-item", queueId, filters]
      : ["annotate-next-item", queueId],
  annotations: (queueId, itemId) => ["item-annotations", queueId, itemId],
};

const invalidateAnnotateItem = (queryClient, queueId, itemId) => {
  if (!queueId || !itemId) return;
  queryClient.invalidateQueries({
    queryKey: annotateKeys.detail(queueId, itemId),
  });
  queryClient.invalidateQueries({
    queryKey: annotateKeys.annotations(queueId, itemId),
  });
  queryClient.invalidateQueries({
    queryKey: annotateKeys.discussion(queueId, itemId),
  });
};

export const useAnnotateDetail = (
  queueId,
  itemId,
  {
    annotatorId,
    includeCompleted,
    viewMode,
    reviewStatus,
    excludeReviewStatus,
    ...options
  } = {},
) => {
  const params = {
    ...(annotatorId ? { annotator_id: annotatorId } : {}),
    ...(includeCompleted ? { include_completed: true } : {}),
    ...(viewMode ? { view_mode: viewMode } : {}),
    ...(reviewStatus ? { review_status: reviewStatus } : {}),
    ...(excludeReviewStatus
      ? { exclude_review_status: excludeReviewStatus }
      : {}),
  };
  const requestOptions = Object.keys(params).length ? { params } : undefined;
  const detailFilters = {
    ...(includeCompleted ? { include_completed: true } : {}),
    ...(viewMode ? { view_mode: viewMode } : {}),
    ...(reviewStatus ? { review_status: reviewStatus } : {}),
    ...(excludeReviewStatus
      ? { exclude_review_status: excludeReviewStatus }
      : {}),
  };
  return useQuery({
    queryKey: annotateKeys.detail(queueId, itemId, annotatorId, detailFilters),
    queryFn: () =>
      axios.get(
        annotationQueueEndpoints.annotateDetail(queueId, itemId),
        requestOptions,
      ),
    select: (d) => extractData(d),
    enabled: !!queueId && !!itemId,
    placeholderData: keepPreviousData,
    ...options,
  });
};

export const useNextItem = (queueId, options = {}) => {
  const {
    viewMode,
    reviewStatus,
    excludeReviewStatus,
    includeCompleted,
    ...queryOptions
  } = options;
  const params = {
    ...(viewMode ? { view_mode: viewMode } : {}),
    ...(reviewStatus ? { review_status: reviewStatus } : {}),
    ...(excludeReviewStatus
      ? { exclude_review_status: excludeReviewStatus }
      : {}),
    ...(includeCompleted ? { include_completed: true } : {}),
  };
  const requestOptions = Object.keys(params).length ? { params } : undefined;
  return useQuery({
    queryKey: annotateKeys.nextItem(queueId, params),
    queryFn: () =>
      axios.get(annotationQueueEndpoints.nextItem(queueId), requestOptions),
    select: (d) => extractData(d)?.item,
    enabled: !!queueId,
    staleTime: 0,
    refetchOnMount: "always",
    ...queryOptions,
  });
};

export const useSubmitAnnotations = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId, annotations, notes, itemNotes }) => {
      const payload = { annotations };
      if (notes !== undefined) payload.notes = notes;
      if (itemNotes !== undefined) payload.item_notes = itemNotes;
      return axios.post(
        annotationQueueEndpoints.submitAnnotations(queueId, itemId),
        payload,
      );
    },
    onSuccess: (_, variables) => {
      invalidateAnnotateItem(queryClient, variables.queueId, variables.itemId);
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.progress(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.all,
      });
      queryClient.invalidateQueries({ queryKey: scoreKeys.all });
    },
    onError: (error) => {
      const msg = extractErrorMessage(error, "Failed to submit annotations");
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useCompleteItem = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      queueId,
      itemId,
      exclude,
      excludeReviewStatus,
      includeCompleted,
    }) => {
      const payload = {
        ...(exclude ? { exclude } : {}),
        ...(excludeReviewStatus
          ? { exclude_review_status: excludeReviewStatus }
          : {}),
        ...(includeCompleted ? { include_completed: true } : {}),
      };
      return axios.post(
        annotationQueueEndpoints.completeItem(queueId, itemId),
        Object.keys(payload).length ? payload : undefined,
      );
    },
    onSuccess: (_, variables) => {
      invalidateAnnotateItem(queryClient, variables.queueId, variables.itemId);
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotateKeys.nextItem(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.progress(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.all,
      });
      queryClient.invalidateQueries({ queryKey: scoreKeys.all });
    },
    onError: () => {
      enqueueSnackbar("Failed to complete item", { variant: "error" });
    },
  });
};

export const useSkipItem = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      queueId,
      itemId,
      exclude,
      excludeReviewStatus,
      includeCompleted,
    }) => {
      const payload = {
        ...(exclude ? { exclude } : {}),
        ...(excludeReviewStatus
          ? { exclude_review_status: excludeReviewStatus }
          : {}),
        ...(includeCompleted ? { include_completed: true } : {}),
      };
      return axios.post(
        annotationQueueEndpoints.skipItem(queueId, itemId),
        Object.keys(payload).length ? payload : undefined,
      );
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotateKeys.nextItem(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.all,
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to skip item", { variant: "error" });
    },
  });
};

export const useQueueAnalytics = (queueId, options = {}) => {
  return useQuery({
    queryKey: annotationQueueKeys.analytics(queueId),
    queryFn: () => axios.get(annotationQueueEndpoints.analytics(queueId)),
    select: (d) => extractData(d),
    enabled: !!queueId,
    staleTime: 1000 * 60,
    ...options,
  });
};

// ---------------------------------------------------------------------------
// Review hooks
// ---------------------------------------------------------------------------
export const useReviewItem = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId, action, notes, labelComments = [] }) =>
      axios.post(annotationQueueEndpoints.reviewItem(queueId, itemId), {
        action,
        notes,
        label_comments: labelComments,
      }),
    onSuccess: (data, variables) => {
      const action = variables.action;
      const requestedChanges =
        action === "request_changes" || action === "reject";
      enqueueSnackbar(
        action === "approve"
          ? "Item approved"
          : requestedChanges
            ? "Changes requested"
            : "Review comment saved",
        {
          variant:
            action === "approve"
              ? "success"
              : requestedChanges
                ? "warning"
                : "info",
        },
      );
      invalidateAnnotateItem(queryClient, variables.queueId, variables.itemId);
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotateKeys.nextItem(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.all,
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to review item", { variant: "error" });
    },
  });
};

export const useItemDiscussion = (queueId, itemId, options = {}) => {
  return useQuery({
    queryKey: annotateKeys.discussion(queueId, itemId),
    queryFn: () =>
      axios.get(annotationQueueEndpoints.discussion(queueId, itemId)),
    select: (d) => {
      const payload = extractData(d, {
        review_comments: [],
        review_threads: [],
      });
      // Older backend responses returned the discussion endpoint as a bare
      // comments array. Normalize both shapes so the collaboration drawer can
      // poll this endpoint without caring which backend build served it.
      if (Array.isArray(payload)) {
        return { review_comments: payload, review_threads: [] };
      }
      return {
        review_comments: payload?.review_comments || [],
        review_threads: payload?.review_threads || [],
      };
    },
    enabled: !!queueId && !!itemId,
    staleTime: 0,
    refetchOnWindowFocus: true,
    ...options,
  });
};

export const useCreateDiscussionComment = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      queueId,
      itemId,
      comment,
      threadId,
      labelId,
      targetAnnotatorId,
      mentionedUserIds = [],
    }) =>
      axios.post(annotationQueueEndpoints.discussion(queueId, itemId), {
        comment,
        ...(threadId ? { thread_id: threadId } : {}),
        ...(labelId ? { label_id: labelId } : {}),
        ...(targetAnnotatorId
          ? { target_annotator_id: targetAnnotatorId }
          : {}),
        mentioned_user_ids: mentionedUserIds,
      }),
    onSuccess: (_, variables) => {
      enqueueSnackbar("Comment added", { variant: "success" });
      invalidateAnnotateItem(queryClient, variables.queueId, variables.itemId);
    },
    onError: (error) => {
      enqueueSnackbar(extractErrorMessage(error, "Failed to add comment"), {
        variant: "error",
      });
    },
  });
};

export const useResolveDiscussionThread = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId, threadId, comment }) =>
      axios.post(
        annotationQueueEndpoints.discussionResolve(queueId, itemId, threadId),
        { ...(comment ? { comment } : {}) },
      ),
    onSuccess: (_, variables) => {
      enqueueSnackbar("Thread resolved", { variant: "success" });
      invalidateAnnotateItem(queryClient, variables.queueId, variables.itemId);
    },
    onError: (error) => {
      enqueueSnackbar(extractErrorMessage(error, "Failed to resolve thread"), {
        variant: "error",
      });
    },
  });
};

export const useReopenDiscussionThread = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId, threadId, comment }) =>
      axios.post(
        annotationQueueEndpoints.discussionReopen(queueId, itemId, threadId),
        { ...(comment ? { comment } : {}) },
      ),
    onSuccess: (_, variables) => {
      enqueueSnackbar("Thread reopened", { variant: "info" });
      invalidateAnnotateItem(queryClient, variables.queueId, variables.itemId);
    },
    onError: (error) => {
      enqueueSnackbar(extractErrorMessage(error, "Failed to reopen thread"), {
        variant: "error",
      });
    },
  });
};

export const useToggleDiscussionReaction = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId, commentId, emoji }) =>
      axios.post(
        annotationQueueEndpoints.discussionReaction(queueId, itemId, commentId),
        { emoji },
      ),
    onSuccess: (_, variables) => {
      invalidateAnnotateItem(queryClient, variables.queueId, variables.itemId);
    },
    onError: (error) => {
      enqueueSnackbar(extractErrorMessage(error, "Failed to update reaction"), {
        variant: "error",
      });
    },
  });
};

// ---------------------------------------------------------------------------
// Automation Rules hooks
// ---------------------------------------------------------------------------
export const automationRuleKeys = {
  all: (queueId) => ["automation-rules", queueId],
  list: (queueId) => ["automation-rules", queueId, "list"],
};

export const useAutomationRules = (queueId, options = {}) => {
  return useQuery({
    queryKey: automationRuleKeys.list(queueId),
    queryFn: () => axios.get(annotationQueueEndpoints.automationRules(queueId)),
    select: (d) => extractData(d),
    enabled: !!queueId,
    ...options,
  });
};

export const useCreateAutomationRule = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, ...data }) =>
      axios.post(annotationQueueEndpoints.automationRules(queueId), data),
    onSuccess: (_, variables) => {
      enqueueSnackbar("Rule created", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: automationRuleKeys.all(variables.queueId),
      });
    },
    onError: (error) => {
      enqueueSnackbar(extractErrorMessage(error, "Failed to create rule"), {
        variant: "error",
      });
    },
  });
};

export const useUpdateAutomationRule = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, ruleId, ...data }) =>
      axios.patch(
        annotationQueueEndpoints.automationRuleDetail(queueId, ruleId),
        data,
      ),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: automationRuleKeys.all(variables.queueId),
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to update rule", { variant: "error" });
    },
  });
};

export const useDeleteAutomationRule = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, ruleId }) =>
      axios.delete(
        annotationQueueEndpoints.automationRuleDetail(queueId, ruleId),
      ),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: automationRuleKeys.all(variables.queueId),
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to delete rule", { variant: "error" });
    },
  });
};

export const useEvaluateRule = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, ruleId }) =>
      axios.post(
        annotationQueueEndpoints.automationRuleEvaluate(queueId, ruleId),
      ),
    onSuccess: (response, variables) => {
      // 200 → ran inline (≤ sync threshold). 202 → too large; backend
      // handed it to a worker and will email creator + queue managers
      // when done.
      const status = response?.status;
      const data = response?.data || {};
      if (status === 202) {
        enqueueSnackbar(
          data.message ||
            "We're preparing your data — you'll get an email when it's ready.",
          { variant: "info", autoHideDuration: 6000 },
        );
      } else {
        const result = data.result || data;
        if (result?.error) {
          enqueueSnackbar(result.error, { variant: "error" });
        } else {
          enqueueSnackbar(
            `Rule evaluated: ${result?.added || 0} items added, ${result?.duplicates || 0} duplicates skipped`,
            { variant: "success" },
          );
        }
      }
      queryClient.invalidateQueries({
        queryKey: automationRuleKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: annotationQueueKeys.progress(variables.queueId),
      });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
    },
    onError: (error) => {
      // 409 = "a run is already in progress" — show a friendly message
      // instead of a generic failure (the backend guards against duplicate
      // workflows fired within 30s of each other).
      if (error?.response?.status === 409 || error?.statusCode === 409) {
        const msg = extractErrorMessage(
          error,
          "A run is already in progress for this rule.",
        );
        enqueueSnackbar(typeof msg === "string" ? msg : "Run already running", {
          variant: "warning",
        });
        return;
      }
      enqueueSnackbar("Failed to evaluate rule", { variant: "error" });
    },
  });
};

export const useExportToDataset = () => {
  return useMutation({
    mutationFn: ({ queueId, ...data }) =>
      axios.post(annotationQueueEndpoints.exportToDataset(queueId), data),
    onSuccess: (data) => {
      const result = data?.data?.result || data?.data;
      enqueueSnackbar(
        `${result?.rows_created || 0} rows exported to dataset "${result?.dataset_name}"`,
        { variant: "success" },
      );
    },
    onError: (error) => {
      const msg = extractErrorMessage(error, "Failed to export to dataset");
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useDownloadAnnotationQueueExport = () => {
  return useMutation({
    mutationFn: ({ queueId, status }) =>
      axios.get(annotationQueueEndpoints.export(queueId), {
        params: {
          export_format: "json",
          ...(status ? { status } : {}),
        },
      }),
    onSuccess: (response, variables) => {
      const payload = extractData(response, []);
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `annotation-queue-${variables.queueId}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      enqueueSnackbar("Annotation export downloaded", { variant: "success" });
    },
    onError: (error) => {
      enqueueSnackbar(extractErrorMessage(error, "Failed to download export"), {
        variant: "error",
      });
    },
  });
};

export const useQueueAgreement = (queueId, options = {}) => {
  return useQuery({
    queryKey: annotationQueueKeys.agreement(queueId),
    queryFn: () => axios.get(annotationQueueEndpoints.agreement(queueId)),
    select: (d) => extractData(d),
    enabled: !!queueId,
    staleTime: 1000 * 60 * 2,
    ...options,
  });
};

export const useAnnotationQueueExportFields = (queueId, options = {}) => {
  return useQuery({
    queryKey: annotationQueueKeys.exportFields(queueId),
    queryFn: () => axios.get(annotationQueueEndpoints.exportFields(queueId)),
    select: (d) => extractData(d, { fields: [], default_mapping: [] }),
    enabled: !!queueId,
    staleTime: 1000 * 60 * 2,
    ...options,
  });
};

export const useItemAnnotations = (queueId, itemId, options = {}) => {
  return useQuery({
    queryKey: annotateKeys.annotations(queueId, itemId),
    queryFn: () =>
      axios.get(annotationQueueEndpoints.itemAnnotations(queueId, itemId)),
    select: (d) => extractData(d),
    enabled: !!queueId && !!itemId,
    ...options,
  });
};

// ---------------------------------------------------------------------------
// Org members hook (for annotator picker)
// ---------------------------------------------------------------------------
export const useOrgMembers = (orgId, options = {}) => {
  return useQuery({
    queryKey: ["org-members", orgId],
    queryFn: () => axios.get(`/model-hub/organizations/${orgId}/users/`),
    select: (d) => extractData(d, []),
    enabled: !!orgId,
    staleTime: 0,
    refetchOnMount: "always",
    ...options,
  });
};

export const useOrgMembersInfinite = (orgId, search = "", options = {}) => {
  return useInfiniteQuery({
    queryKey: ["org-members-infinite", orgId, search],
    queryFn: ({ pageParam }) =>
      axios.get(`/model-hub/organizations/${orgId}/users/`, {
        params: { page: pageParam, limit: 30, ...(search && { search }) },
      }),
    initialPageParam: 1,
    getNextPageParam: (lastPage) => {
      const data = lastPage?.data;
      const currentPage = data?.current_page ?? 1;
      const totalPages = data?.total_pages ?? 1;
      return currentPage < totalPages ? currentPage + 1 : undefined;
    },
    select: (d) => d?.pages?.flatMap((p) => p?.data?.results ?? []) ?? [],
    enabled: !!orgId,
    staleTime: 0,
    refetchOnMount: "always",
    ...options,
  });
};

// ---------------------------------------------------------------------------
// Queue items for a given source (for annotation sidebar)
// ---------------------------------------------------------------------------
/**
 * Fetch annotation queue items for one or more sources.
 * @param {Array<{sourceType: string, sourceId: string, spanNotesSourceId?: string}>} sources
 */
export const useQueueItemsForSource = (sources = [], options = {}) => {
  // Filter out entries with missing values
  const validSources = sources.filter((s) => s.sourceType && s.sourceId);

  return useQuery({
    queryKey: ["annotation-queues", "for-source", validSources],
    queryFn: () =>
      axios.get(annotationQueueEndpoints.forSource, {
        params: {
          sources: JSON.stringify(
            validSources.map((s) => ({
              source_type: s.sourceType,
              source_id: s.sourceId,
              span_notes_source_id: s.spanNotesSourceId,
            })),
          ),
        },
      }),
    select: (d) => extractData(d, []),
    enabled: validSources.length > 0,
    staleTime: 1000 * 30,
    ...options,
  });
};

// ---------------------------------------------------------------------------
// Default queue hooks
// ---------------------------------------------------------------------------

export const useGetOrCreateDefaultQueue = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, datasetId, agentDefinitionId }) =>
      axios.post(annotationQueueEndpoints.getOrCreateDefault, {
        ...(projectId && { project_id: projectId }),
        ...(datasetId && { dataset_id: datasetId }),
        ...(agentDefinitionId && { agent_definition_id: agentDefinitionId }),
      }),
    onSuccess: (response) => {
      // Backend returns action ∈ {"created", "restored", "fetched"}.
      // "restored" means a previously archived default queue for this scope
      // came back online — surface it explicitly so users understand their
      // old rules + items are now visible again.
      const result = response?.data?.result || response?.data || {};
      if (result.action === "restored") {
        enqueueSnackbar(
          "Restored your archived default queue. Rules and items are back.",
          { variant: "info" },
        );
      }
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
    },
    onError: (error) => {
      const msg = extractErrorMessage(error, "Failed to get default queue");
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useAddLabelToQueue = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, labelId }) =>
      axios.post(annotationQueueEndpoints.addLabel(queueId), {
        label_id: labelId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
      queryClient.invalidateQueries({
        queryKey: ["annotation-queues", "for-source"],
      });
    },
    onError: (error) => {
      const msg = extractErrorMessage(error, "Failed to add label to queue");
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useRemoveLabelFromQueue = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, labelId }) =>
      axios.post(annotationQueueEndpoints.removeLabel(queueId), {
        label_id: labelId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
      queryClient.invalidateQueries({
        queryKey: ["annotation-queues", "for-source"],
      });
    },
    onError: (error) => {
      const msg = extractErrorMessage(
        error,
        "Failed to remove label from queue",
      );
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};
