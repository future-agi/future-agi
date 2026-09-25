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
export const useGetTraceDetail = (traceId, projectId) => {
  return useQuery({
    queryKey: projectId
      ? ["trace-detail", traceId, projectId]
      : ["trace-detail", traceId],
    queryFn: () =>
      axios.get(endpoints.project.getTrace(traceId), {
        params: projectId ? { project_id: projectId } : undefined,
      }),
    select: (d) => d.data?.result,
    enabled: !!traceId,
    staleTime: 30_000,
  });
};
