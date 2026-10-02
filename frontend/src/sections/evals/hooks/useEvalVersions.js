import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export function useEvalVersions(templateId) {
  return useQuery({
    queryKey: ["evals", "versions", templateId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getEvalVersions(templateId),
      );
      return data?.result;
    },
    enabled: !!templateId,
  });
}

export function useCreateEvalVersion(templateId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload = {}) => {
      const { data } = await axios.post(
        endpoints.develop.eval.createEvalVersion(templateId),
        payload,
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["evals", "versions", templateId],
      });
    },
  });
}

export function useSetDefaultVersion(templateId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (versionId) => {
      const { data } = await axios.put(
        endpoints.develop.eval.setDefaultVersion(templateId, versionId),
        {},
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["evals", "versions", templateId],
      });
      queryClient.invalidateQueries({
        queryKey: ["evals", "detail", templateId],
      });
    },
  });
}

export function useRestoreVersion(templateId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (versionId) => {
      const { data } = await axios.post(
        endpoints.develop.eval.restoreVersion(templateId, versionId),
        {},
      );
      return data?.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["evals", "versions", templateId],
      });
      queryClient.invalidateQueries({
        queryKey: ["evals", "detail", templateId],
      });
    },
  });
}
