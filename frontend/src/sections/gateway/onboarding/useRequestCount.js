import { useQuery } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

// How many requests this organization has ever sent through the gateway: the
// request log list's `count`, read with one row. "Send your first request" is
// done by any request, not only one in the last 24 hours.
export function useRequestCount({ enabled = true } = {}) {
  return useQuery({
    queryKey: ["agentcc-request-count"],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.requestLogs, {
        params: { limit: 1 },
      });
      const count = data?.count ?? data?.result?.count;
      if (Number.isFinite(Number(count))) return Number(count);
      const rows = data?.results ?? data?.result?.results ?? [];
      return Array.isArray(rows) ? rows.length : 0;
    },
    enabled,
    staleTime: 60000,
    refetchOnWindowFocus: false,
  });
}
