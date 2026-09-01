import {
  useInfiniteQuery,
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { apiPath } from "src/api/contracts/api-surface";
import {
  modelHubAnnotationsLabelsCreate,
  modelHubAnnotationsLabelsDelete,
  modelHubAnnotationsLabelsList,
  modelHubAnnotationsLabelsRestore,
  modelHubAnnotationsLabelsUpdate,
} from "src/generated/api-contracts/api";

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------
export const annotationLabelEndpoints = {
  list: apiPath("/model-hub/annotations-labels/"),
  create: apiPath("/model-hub/annotations-labels/"),
  detail: (id) => apiPath("/model-hub/annotations-labels/{id}/", { id }),
  restore: (id) =>
    apiPath("/model-hub/annotations-labels/{id}/restore/", { id }),
};

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------
export const annotationLabelKeys = {
  all: ["annotation-labels"],
  list: (filters) => ["annotation-labels", "list", filters],
  detail: (id) => ["annotation-labels", "detail", id],
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export const useAnnotationLabelsList = (filters = {}, options = {}) => {
  return useQuery({
    queryKey: annotationLabelKeys.list(filters),
    queryFn: () => modelHubAnnotationsLabelsList(filters),
    select: (d) => d?.data || d,
    staleTime: 1000 * 60 * 2,
    ...options,
  });
};

export const useInfiniteAnnotationLabelsList = (filters = {}, options = {}) => {
  const { page: _page, limit = 50, ...queryFilters } = filters;
  const { meta: optionMeta, ...queryOptions } = options;

  return useInfiniteQuery({
    queryKey: [...annotationLabelKeys.list(queryFilters), "infinite", limit],
    initialPageParam: 1,
    queryFn: ({ pageParam, signal }) =>
      modelHubAnnotationsLabelsList(
        {
          ...queryFilters,
          page: pageParam,
          limit,
        },
        { signal },
      ),
    getNextPageParam: (lastPage, allPages) => {
      const lastPageData = lastPage?.data || lastPage || {};
      const pageResults = Array.isArray(lastPageData.results)
        ? lastPageData.results
        : [];

      // The server's `next` cursor is authoritative. Counts can lag while labels
      // are created or archived, and must never make the client request empty
      // terminal pages indefinitely.
      if (pageResults.length === 0 || !lastPageData.next) return undefined;
      return allPages.length + 1;
    },
    select: (data) => {
      const pages = data.pages.map((page) => page?.data || page || {});
      const labelsById = new Map();
      pages.forEach((page) => {
        (page.results || []).forEach((label) =>
          labelsById.set(label.id, label),
        );
      });
      const results = Array.from(labelsById.values());
      const lastPage = pages[pages.length - 1] || {};
      return {
        results,
        count: Number(lastPage.count) || results.length,
      };
    },
    retry: false,
    meta: { ...optionMeta, errorHandled: true },
    staleTime: 1000 * 60 * 2,
    ...queryOptions,
  });
};

export const useCreateAnnotationLabel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data) => modelHubAnnotationsLabelsCreate(data),
    onSuccess: () => {
      enqueueSnackbar("Label created successfully", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: annotationLabelKeys.all });
    },
    onError: (error) => {
      const body = error?.response?.data || {};
      const msg =
        body.result ||
        body.detail ||
        body.message ||
        error?.message ||
        "Failed to create label";
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useUpdateAnnotationLabel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }) => modelHubAnnotationsLabelsUpdate(id, data),
    onSuccess: () => {
      enqueueSnackbar("Label updated successfully", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: annotationLabelKeys.all });
    },
    onError: (error) => {
      const body = error?.response?.data || {};
      const msg =
        body.result ||
        body.detail ||
        body.message ||
        error?.message ||
        "Failed to update label";
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });
};

export const useDeleteAnnotationLabel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => modelHubAnnotationsLabelsDelete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: annotationLabelKeys.all });
    },
    onError: () => {
      enqueueSnackbar("Failed to archive label", { variant: "error" });
    },
  });
};

export const useRestoreAnnotationLabel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => modelHubAnnotationsLabelsRestore(id, {}),
    onSuccess: () => {
      enqueueSnackbar("Label restored", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: annotationLabelKeys.all });
    },
    onError: () => {
      enqueueSnackbar("Failed to restore label", { variant: "error" });
    },
  });
};
