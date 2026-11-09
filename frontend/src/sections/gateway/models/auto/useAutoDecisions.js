import { useQuery } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

const DECISIONS_KEY = "agentcc-auto-decisions";

// How many of the latest requests the page reads for auto decisions.
export const AUTO_SCAN_LIMIT = 200;

// The latest requests, newest first. GET /agentcc/request-logs/ has no tags
// filter in the API contract (the app refuses unknown query keys), so the
// page keeps the auto-routed ones itself (autoDecisions.decisionsFromLogs).
export function useAutoDecisions() {
  return useQuery({
    queryKey: [DECISIONS_KEY],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.requestLogs, {
        params: { limit: AUTO_SCAN_LIMIT, ordering: "-started_at" },
        timeout: 30000,
      });
      return data;
    },
    staleTime: 30000,
    meta: { errorHandled: true },
  });
}
