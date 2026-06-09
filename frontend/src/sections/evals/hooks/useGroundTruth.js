import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// ── List ground truth datasets for a template ──
export function useGroundTruthList(templateId) {
  return useQuery({
    queryKey: ["evals", "ground-truth", templateId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getGroundTruthList(templateId),
      );
      return data?.result;
    },
    enabled: !!templateId,
  });
}

// ── Upload ground truth (file via FormData, or JSON body for dataset import) ──
export function useUploadGroundTruth(templateId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const isFormData = payload instanceof FormData;
      const { data } = await axios.post(
        endpoints.develop.eval.uploadGroundTruth(templateId),
        payload,
        isFormData
          ? { headers: { "Content-Type": "multipart/form-data" } }
          : {},
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["evals", "ground-truth", templateId],
      });
    },
  });
}

// ── Get paginated data preview ──
export function useGroundTruthData(gtId, { page = 1, pageSize = 50 } = {}) {
  return useQuery({
    queryKey: ["evals", "ground-truth-data", gtId, page, pageSize],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.groundTruthData(gtId),
        {
          params: { page, page_size: pageSize },
        },
      );
      return data?.result;
    },
    enabled: !!gtId,
    keepPreviousData: true,
  });
}

// ── Get embedding status ──
export function useGroundTruthStatus(gtId, { enabled = true } = {}) {
  return useQuery({
    queryKey: ["evals", "ground-truth-status", gtId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.groundTruthStatus(gtId),
      );
      return data?.result;
    },
    enabled: !!gtId && enabled,
    refetchInterval: (data) => {
      // Poll every 3s while processing
      if (data?.state?.data?.embedding_status === "processing") return 3000;
      return false;
    },
  });
}

// ── Get ground truth config for template ──
export function useGroundTruthConfig(templateId) {
  return useQuery({
    queryKey: ["evals", "ground-truth-config", templateId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getGroundTruthConfig(templateId),
      );
      return data?.result?.ground_truth;
    },
    enabled: !!templateId,
  });
}

// ── Update ground truth config ──
export function useUpdateGroundTruthConfig(templateId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (config) => {
      const { data } = await axios.put(
        endpoints.develop.eval.updateGroundTruthConfig(templateId),
        config,
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["evals", "ground-truth-config", templateId],
      });
    },
  });
}

// ── Update role mapping ──
export function useUpdateRoleMapping() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ gtId, roleMapping }) => {
      const { data } = await axios.put(
        endpoints.develop.eval.groundTruthRoleMapping(gtId),
        {
          role_mapping: roleMapping,
        },
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["evals", "ground-truth"] });
    },
  });
}

// ── Update variable mapping ──
export function useUpdateVariableMapping() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ gtId, variableMapping }) => {
      const { data } = await axios.put(
        endpoints.develop.eval.groundTruthMapping(gtId),
        {
          variable_mapping: variableMapping,
        },
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["evals", "ground-truth"] });
    },
  });
}

// ── Delete ground truth ──
export function useDeleteGroundTruth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (gtId) => {
      const { data } = await axios.delete(
        endpoints.develop.eval.deleteGroundTruth(gtId),
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["evals", "ground-truth"] });
    },
  });
}

// ── Trigger embedding generation ──
export function useTriggerEmbedding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (gtId) => {
      const { data } = await axios.post(
        endpoints.develop.eval.groundTruthEmbed(gtId),
      );
      return data?.result;
    },
    onSuccess: (_, gtId) => {
      queryClient.invalidateQueries({ queryKey: ["evals", "ground-truth"] });
      queryClient.invalidateQueries({
        queryKey: ["evals", "ground-truth-status", gtId],
      });
    },
  });
}

// ── Search ground truth (test retrieval) ──
export function useSearchGroundTruth() {
  return useMutation({
    mutationFn: async ({ gtId, query, maxResults = 3 }) => {
      const { data } = await axios.post(
        endpoints.develop.eval.groundTruthSearch(gtId),
        {
          query,
          max_results: maxResults,
        },
      );
      return data?.result;
    },
  });
}
