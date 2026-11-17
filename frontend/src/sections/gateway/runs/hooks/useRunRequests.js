import { useQuery } from "@tanstack/react-query";

import axiosInstance, { endpoints } from "src/utils/axios";

// The request logs behind the Runs pages. There is no runs endpoint, so the
// list reads the newest RUNS_WINDOW requests and groups them by run id, and a
// run's page reads up to RUNS_WINDOW of its requests through the run_id
// filter. Both say on the page when there is more than one read holds.

export const RUNS_WINDOW = 500;

const read = (data) => ({
  results: data?.result?.results ?? data?.results ?? [],
  count: data?.result?.count ?? data?.count ?? 0,
});

/** The newest requests, for the runs list. */
export function useRecentRequests() {
  return useQuery({
    queryKey: ["gateway-runs", "recent"],
    queryFn: async () => {
      const res = await axiosInstance.get(endpoints.gateway.requestLogs, {
        params: { page: 1, limit: RUNS_WINDOW, ordering: "-started_at" },
      });
      return read(res.data);
    },
    staleTime: 30000,
  });
}

/** One run's requests, oldest first. */
export function useRunRequests(runId) {
  return useQuery({
    queryKey: ["gateway-runs", "run", runId],
    queryFn: async () => {
      const res = await axiosInstance.get(endpoints.gateway.requestLogs, {
        params: {
          run_id: runId,
          page: 1,
          limit: RUNS_WINDOW,
          ordering: "started_at",
        },
      });
      return read(res.data);
    },
    enabled: Boolean(runId),
    staleTime: 30000,
  });
}
