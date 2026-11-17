import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const QUERY_KEY = ["agentcc", "request-log-retention"];

export function useRequestLogRetention() {
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.requestLogRetention);
      return data.result;
    },
  });
}

export function useRequestLogRetentionPreview(retentionDays) {
  return useQuery({
    queryKey: [...QUERY_KEY, "preview", retentionDays],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.requestLogRetention, {
        params: { retention_days: retentionDays },
      });
      return data.result;
    },
    enabled: retentionDays !== null,
    retry: false,
  });
}

export function useSaveRequestLogRetention() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (retentionDays) => {
      const { data } = await axios.put(endpoints.gateway.requestLogRetention, {
        retention_days: retentionDays,
      });
      return data.result;
    },
    onSuccess: (result) => {
      queryClient.setQueryData(QUERY_KEY, result);
      queryClient.invalidateQueries({ queryKey: QUERY_KEY, exact: true });
    },
  });
}
