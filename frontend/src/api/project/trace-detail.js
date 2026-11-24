import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * Fetch full trace detail — trace + span tree + evals + annotations + summary + graph.
 * Uses the enhanced GET /tracer/trace/{id}/ endpoint (Phase 8).
 *
 * The same trace id can exist in several projects (replays, re-imports), so
 * pass the project the trace was opened from whenever the caller knows it;
 * without it the backend serves the newest copy in the caller's scope.
 */
export const useGetTraceDetail = (
  traceId,
  { projectId, testExecutionId, clusterId } = {},
) => {
  const params = {
    ...(projectId ? { project_id: projectId } : {}),
    ...(testExecutionId ? { test_execution_id: testExecutionId } : {}),
    ...(clusterId ? { cluster_id: clusterId } : {}),
  };
  return useQuery({
    // Keep the trace-ID prefix used by narrow mutation invalidations.
    queryKey: [
      "trace-detail",
      traceId,
      projectId,
      testExecutionId,
      clusterId,
      "capture-coverage-v1",
    ].filter(Boolean),
    queryFn: () =>
      axios.get(endpoints.project.getTrace(traceId), {
        params: Object.keys(params).length ? params : undefined,
      }),
    select: (d) => d.data?.result,
    placeholderData: (prev, prevQuery) =>
      prevQuery?.queryKey?.[1] === traceId ? prev : undefined,
    enabled: !!traceId,
    staleTime: 30_000,
  });
};
