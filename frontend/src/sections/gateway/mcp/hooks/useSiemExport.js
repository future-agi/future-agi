import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";

import { MCP_API } from "../api";

// Org-scoped reads for members and viewers; Owner/Admin writes. Query only
// while the SIEM export drawer is open; never persist credentials locally.
export const SIEM_QUERY_KEY = "agentcc-siem";

export function useSiemDestinations(enabled) {
  return useQuery({
    queryKey: [SIEM_QUERY_KEY, "destinations"],
    queryFn: async () => {
      const { data } = await axios.get(MCP_API.siemDestinations);
      return data.result;
    },
    enabled,
    retry: false,
  });
}

export function useSiemDeliveries(enabled, status = "") {
  return useQuery({
    queryKey: [SIEM_QUERY_KEY, "deliveries", status],
    refetchInterval: enabled ? 5000 : false,
    queryFn: async () => {
      const { data } = await axios.get(MCP_API.siemDeliveries, {
        params: status ? { status } : undefined,
      });
      return data.result?.deliveries || [];
    },
    enabled,
    retry: false,
  });
}

// One write (create, update, retry, discard); every SIEM read is refetched after.
export function useSiemWrite(write) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input) => (await write(input)).data.result,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: [SIEM_QUERY_KEY] }),
  });
}

export const siemWrites = {
  create: (body) => axios.post(MCP_API.siemDestinations, body),
  update: ({ id, ...body }) => axios.patch(MCP_API.siemDestination(id), body),
  testConnection: (body) => axios.post(MCP_API.siemTestConnection, body),
  delete: (id) => axios.delete(MCP_API.siemDestination(id)),
  retry: (id) => axios.post(MCP_API.siemRetryDelivery(id), {}),
  discard: (id) => axios.post(MCP_API.siemDiscardDelivery(id), {}),
};
