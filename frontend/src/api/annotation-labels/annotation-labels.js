import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
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
    select: (d) => d,
    staleTime: 1000 * 60 * 2,
    ...options,
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
      const msg = error?.result || error?.detail || "Failed to create label";
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
      const msg = error?.result || error?.detail || "Failed to update label";
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
