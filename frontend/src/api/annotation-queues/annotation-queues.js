import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { scoreKeys } from "src/api/scores/scores";

// ---------------------------------------------------------------------------
// Helper – extract response payload consistently across endpoints that may
// wrap data in `result`, `results`, or return it at the top level.
// ---------------------------------------------------------------------------
const extractData = (d, fallback = null) =>
  d.data?.result ?? d.data?.results ?? d.data ?? fallback;

export const extractErrorMessage = (error, fallback) => {
  const payload = error?.response?.data || error;
  const message =
    payload?.result ||
    payload?.detail ||
    payload?.message ||
    payload?.error ||
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
  list: "/model-hub/annotation-queues/",
  create: "/model-hub/annotation-queues/",
  detail: (id) => `/model-hub/annotation-queues/${id}/`,
  restore: (id) => `/model-hub/annotation-queues/${id}/restore/`,
  hardDelete: (id) => `/model-hub/annotation-queues/${id}/hard-delete/`,
  updateStatus: (id) => `/model-hub/annotation-queues/${id}/update-status/`,
};

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------
export const annotationQueueKeys = {
  all: ["annotation-queues"],
  list: (filters) => ["annotation-queues", "list", filters],
  detail: (id) => ["annotation-queues", "detail", id],
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
      const msg = error?.result || error?.detail || "Failed to create queue";
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
    onSuccess: () => {
      enqueueSnackbar("Queue updated successfully", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
    },
    onError: (error) => {
      const msg = error?.result || error?.detail || "Failed to update queue";
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
      enqueueSnackbar(
        extractErrorMessage(error, "Failed to delete queue"),
        { variant: "error" },
      );
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
    onSuccess: () => {
      enqueueSnackbar("Queue status updated", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
    },
    onError: (error) => {
      // Revert optimistic update on error
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
      const msg = error?.result || error?.detail || "Failed to update status";
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
      axios.get(`/model-hub/annotation-queues/${queueId}/items/`, {
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
        `/model-hub/annotation-queues/${queueId}/items/add-items/`,
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
      const msg = error?.result || error?.detail || "Failed to add items";
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
      axios.delete(`/model-hub/annotation-queues/${queueId}/items/${itemId}/`),
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
      axios.post(`/model-hub/annotation-queues/${queueId}/items/bulk-remove/`, {
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
    queryFn: () =>
      axios.get(`/model-hub/annotation-queues/${queueId}/progress/`),
    select: (d) => extractData(d),
    enabled: !!queueId,
    staleTime: 1000 * 30,
    ...options,
  });
};

export const useAssignQueueItems = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemIds, userId, userIds, action }) =>
      axios.post(`/model-hub/annotation-queues/${queueId}/items/assign/`, {
        item_ids: itemIds,
        ...(userIds
          ? { user_ids: userIds, action: action || "add" }
          : { user_id: userId }),
      }),
    onSuccess: (data, variables) => {
      enqueueSnackbar("Assignees updated", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to assign items", { variant: "error" });
    },
  });
};

export const useUpdateQueueItemStatus = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId, status }) =>
      axios.patch(`/model-hub/annotation-queues/${queueId}/items/${itemId}/`, {
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
  detail: (queueId, itemId) => ["annotate-detail", queueId, itemId],
  nextItem: (queueId) => ["annotate-next-item", queueId],
  annotations: (queueId, itemId) => ["item-annotations", queueId, itemId],
};

export const useAnnotateDetail = (queueId, itemId, options = {}) => {
  return useQuery({
    queryKey: annotateKeys.detail(queueId, itemId),
    queryFn: () =>
      axios.get(
        `/model-hub/annotation-queues/${queueId}/items/${itemId}/annotate-detail/`,
      ),
    select: (d) => extractData(d),
    enabled: !!queueId && !!itemId,
    placeholderData: keepPreviousData,
    ...options,
  });
};

export const useNextItem = (queueId, options = {}) => {
  return useQuery({
    queryKey: annotateKeys.nextItem(queueId),
    queryFn: () =>
      axios.get(`/model-hub/annotation-queues/${queueId}/items/next-item/`),
    select: (d) => extractData(d)?.item,
    enabled: !!queueId,
    ...options,
  });
};

export const useSubmitAnnotations = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId, annotations, notes }) =>
      axios.post(
        `/model-hub/annotation-queues/${queueId}/items/${itemId}/annotations/submit/`,
        { annotations, notes },
      ),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: annotateKeys.detail(variables.queueId, variables.itemId),
      });
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
      const msg =
        error?.result || error?.detail || "Failed to submit annotations";
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useCompleteItem = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemId, exclude }) =>
      axios.post(
        `/model-hub/annotation-queues/${queueId}/items/${itemId}/complete/`,
        exclude ? { exclude } : undefined,
      ),
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
    mutationFn: ({ queueId, itemId, exclude }) =>
      axios.post(
        `/model-hub/annotation-queues/${queueId}/items/${itemId}/skip/`,
        exclude ? { exclude } : undefined,
      ),
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
    queryFn: () =>
      axios.get(`/model-hub/annotation-queues/${queueId}/analytics/`),
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
    mutationFn: ({ queueId, itemId, action, notes }) =>
      axios.post(
        `/model-hub/annotation-queues/${queueId}/items/${itemId}/review/`,
        { action, notes },
      ),
    onSuccess: (data, variables) => {
      const action = variables.action;
      enqueueSnackbar(
        action === "approve" ? "Item approved" : "Item rejected",
        { variant: action === "approve" ? "success" : "warning" },
      );
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
    queryFn: () =>
      axios.get(`/model-hub/annotation-queues/${queueId}/automation-rules/`),
    select: (d) => extractData(d),
    enabled: !!queueId,
    ...options,
  });
};

export const useCreateAutomationRule = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, ...data }) =>
      axios.post(
        `/model-hub/annotation-queues/${queueId}/automation-rules/`,
        data,
      ),
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
        `/model-hub/annotation-queues/${queueId}/automation-rules/${ruleId}/`,
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
        `/model-hub/annotation-queues/${queueId}/automation-rules/${ruleId}/`,
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
        `/model-hub/annotation-queues/${queueId}/automation-rules/${ruleId}/evaluate/`,
      ),
    onSuccess: (data, variables) => {
      const result = data?.data?.result || data?.data;
      enqueueSnackbar(
        `Rule evaluated: ${result?.added || 0} items added, ${result?.duplicates || 0} duplicates skipped`,
        { variant: "success" },
      );
      queryClient.invalidateQueries({
        queryKey: automationRuleKeys.all(variables.queueId),
      });
      queryClient.invalidateQueries({
        queryKey: queueItemKeys.all(variables.queueId),
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to evaluate rule", { variant: "error" });
    },
  });
};

export const useExportToDataset = () => {
  return useMutation({
    mutationFn: ({ queueId, ...data }) =>
      axios.post(
        `/model-hub/annotation-queues/${queueId}/export-to-dataset/`,
        data,
      ),
    onSuccess: (data) => {
      const result = data?.data?.result || data?.data;
      enqueueSnackbar(
        `${result?.rows_created || 0} rows exported to dataset "${result?.dataset_name}"`,
        { variant: "success" },
      );
    },
    onError: (error) => {
      const msg =
        error?.result || error?.detail || "Failed to export to dataset";
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useQueueAgreement = (queueId, options = {}) => {
  return useQuery({
    queryKey: annotationQueueKeys.agreement(queueId),
    queryFn: () =>
      axios.get(`/model-hub/annotation-queues/${queueId}/agreement/`),
    select: (d) => extractData(d),
    enabled: !!queueId,
    staleTime: 1000 * 60 * 2,
    ...options,
  });
};

export const useItemAnnotations = (queueId, itemId, options = {}) => {
  return useQuery({
    queryKey: annotateKeys.annotations(queueId, itemId),
    queryFn: () =>
      axios.get(
        `/model-hub/annotation-queues/${queueId}/items/${itemId}/annotations/`,
      ),
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
    staleTime: 1000 * 60 * 5,
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
    staleTime: 1000 * 60 * 5,
    ...options,
  });
};

// ---------------------------------------------------------------------------
// Queue items for a given source (for annotation sidebar)
// ---------------------------------------------------------------------------
/**
 * Fetch annotation queue items for one or more sources.
 * @param {Array<{sourceType: string, sourceId: string}>} sources
 */
export const useQueueItemsForSource = (sources = [], options = {}) => {
  // Filter out entries with missing values
  const validSources = sources.filter((s) => s.sourceType && s.sourceId);

  return useQuery({
    queryKey: ["annotation-queues", "for-source", validSources],
    queryFn: () =>
      axios.get("/model-hub/annotation-queues/for-source/", {
        params: {
          sources: JSON.stringify(
            validSources.map((s) => ({
              source_type: s.sourceType,
              source_id: s.sourceId,
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
      axios.post("/model-hub/annotation-queues/get-or-create-default/", {
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
      const msg =
        error?.result || error?.detail || "Failed to get default queue";
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
      axios.post(`/model-hub/annotation-queues/${queueId}/add-label/`, {
        label_id: labelId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
      queryClient.invalidateQueries({
        queryKey: ["annotation-queues", "for-source"],
      });
    },
    onError: (error) => {
      const msg =
        error?.result || error?.detail || "Failed to add label to queue";
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
      axios.post(`/model-hub/annotation-queues/${queueId}/remove-label/`, {
        label_id: labelId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
      queryClient.invalidateQueries({
        queryKey: ["annotation-queues", "for-source"],
      });
    },
    onError: (error) => {
      const msg =
        error?.result || error?.detail || "Failed to remove label from queue";
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};
