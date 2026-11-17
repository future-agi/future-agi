import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const SESSION_KEY = "agentcc-sessions";

// One page of sessions. The list API pages by 10 unless asked, so the page
// asks for SESSIONS_PAGE_SIZE at a time and keeps the total it reports.
export const SESSIONS_PAGE_SIZE = 100;

export function useSessions({ status, search, page = 1 } = {}) {
  const params = { limit: SESSIONS_PAGE_SIZE, page };
  if (status) params.status = status;
  if (search) params.search = search;
  return useQuery({
    queryKey: [SESSION_KEY, params],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.sessions.list, {
        params,
      });
      const results = data.results || data.result || [];
      return { results, count: data.count ?? results.length };
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
      return data.result;
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
