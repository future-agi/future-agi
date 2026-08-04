import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { normalizeGatewayMetadataField } from "../../utils/metadataDisplay";

const SESSION_KEY = "agentcc-sessions";

export function useSessions(params = {}) {
  return useQuery({
    queryKey: [SESSION_KEY, params],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.sessions.list, {
        params,
      });
      return data.results || data.result || [];
    },
    staleTime: 30000,
    placeholderData: keepPreviousData,
  });
}

export function useSessionDetail(id) {
  return useQuery({
    queryKey: [SESSION_KEY, "detail", id],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.sessions.detail(id));
      return normalizeGatewayMetadataField(data.result);
    },
    enabled: Boolean(id),
    staleTime: 15000,
  });
}

export function useSessionRequests(id) {
  return useQuery({
    queryKey: [SESSION_KEY, "requests", id],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.sessions.requests(id));
      return data.result || [];
    },
    enabled: Boolean(id),
    staleTime: 15000,
  });
}

export function useCloseSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.post(endpoints.gateway.sessions.close(id));
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [SESSION_KEY] }),
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.delete(
        endpoints.gateway.sessions.delete(id),
      );
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [SESSION_KEY] }),
  });
}
