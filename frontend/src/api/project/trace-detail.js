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
  { projectId, testExecutionId } = {},
) => {
  const params = {
    ...(projectId ? { project_id: projectId } : {}),
    ...(testExecutionId ? { test_execution_id: testExecutionId } : {}),
  };
  return useQuery({
    queryKey: ["trace-detail", traceId, projectId, testExecutionId].filter(
      Boolean,
    ),
    queryFn: () =>
      axios.get(endpoints.project.getTrace(traceId), {
        params: Object.keys(params).length ? params : undefined,
      }),
    select: (d) => d.data?.result,
    enabled: !!traceId,
    staleTime: 30_000,
  });
};
