import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

/**
 * Fetch root span IDs for the given trace IDs.
 * Root span = the span whose parent_span_id is NULL for that trace.
 *
 * @param {string[]} traceIds
 * @returns {Promise<Record<string, string>>} map of trace_id → root span_id
 */
export async function fetchRootSpans(traceIds) {
  if (!traceIds || traceIds.length === 0) return {};
  const params = new URLSearchParams();
  traceIds.forEach((id) => params.append("trace_ids", id));
  const res = await axios.get(
    `${apiPath("/tracer/observation-span/root-spans/")}?${params.toString()}`,
  );
  return res.data?.result || {};
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
