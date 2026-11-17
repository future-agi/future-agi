import { useQuery } from "@tanstack/react-query";

import { useOrganization } from "src/contexts/OrganizationContext";
import axios, { endpoints } from "src/utils/axios";

// Lifetime successful requests, not all attempts or just the last 24 hours.
// Keep the query object intact so Overview can pass status and manual refetch
// to GettingStarted. A malformed count is a retryable read error, never zero
// and never the length of the one-row page.
export function useRequestCount({ enabled = true } = {}) {
  const { currentOrganizationId } = useOrganization();
  return useQuery({
    queryKey: ["agentcc-request-count", currentOrganizationId, "successful"],
    queryFn: async ({ signal }) => {
      const { data } = await axios.get(endpoints.gateway.requestLogs, {
        params: { limit: 1, min_status_code: 200, max_status_code: 299, is_error: false },
        timeout: 30000,
        signal,
      });
      const count = data?.count ?? data?.result?.count;
      const numeric = typeof count === "number" ||
        (typeof count === "string" && /^\d+$/.test(count.trim()));
      if (numeric && Number.isSafeInteger(Number(count)) && Number(count) >= 0) {
        return Number(count);
      }
      throw new Error("Could not check successful requests. Check again to retry.");
    },
    enabled,
    staleTime: 60000,
    refetchOnWindowFocus: false,
    retry: false,
    meta: { errorHandled: true },
  });
}
