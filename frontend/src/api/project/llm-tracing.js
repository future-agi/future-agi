import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { tracerObservationSpanRootSpans } from "src/generated/api-contracts/api";

/**
 * Fetch root span IDs for the given trace IDs. Root span = the span whose
 * parent_span_id is NULL. Sent as repeated ?trace_ids= query params.
 *
 * @param {string[]} traceIds
 * @param {string[]} [projectIds] optional — prunes the ClickHouse scan
 * @returns {Promise<Record<string, string>>} map of trace_id → root span_id
 */
export async function fetchRootSpans(traceIds, projectIds = []) {
  if (!traceIds || traceIds.length === 0) return {};
  const params = { trace_ids: traceIds };
  if (projectIds?.length) params.project_ids = projectIds;
  const res = await tracerObservationSpanRootSpans(params);
  return res?.data?.result || {};
}

export const useGetTraceProperties = () => {
  return useQuery({
    queryKey: ["trace-properties"],
    queryFn: () => axios.get(endpoints.project.getTraceProperties),
    select: (d) => d.data?.result,
    staleTime: 1 * 60 * 1000, // 1 min stale time
  });
};

export const useGetTraceEvals = (projectId, search) => {
  return useQuery({
    queryKey: ["trace-evals", projectId, search],
    queryFn: () =>
      axios.get(endpoints.project.getTraceEvals(), {
        params: {
          name: search?.length ? search : null,
          project_id: projectId,
        },
      }),
    select: (d) => d.data?.result,
  });
};
