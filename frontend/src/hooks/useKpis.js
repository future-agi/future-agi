import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * The one definition of the run-KPIs query: its key, its fetch and how long it
 * stays fresh.
 *
 * Two places observe this key — this hook and the environments workspace's
 * `useRunsSummary`, which needs the options rather than the hook because
 * `useQueries` cannot call one. Written out twice they drifted: what is cached
 * is the response BODY, so an observer that cached the raw AxiosResponse would
 * hand whichever one mounted second the wrong shape off the same cache entry.
 * Both spread this instead, and a caller adds only its own `select` on top.
 *
 * @param {string} executionId
 */
export const kpisQueryOptions = (executionId) => ({
  queryKey: ["test-execution-detail", "KPIS", executionId],
  queryFn: () => axios.get(endpoints.testExecutions.kpis(executionId)).then((res) => res.data),
  staleTime: 1000 * 60 * 5, // 5 minutes
});

export default function useKpis(executionId, options = {}) {
  const query = useQuery({
    ...kpisQueryOptions(executionId),
    enabled: options.enabled ?? !!executionId,
    refetchInterval: options.refetch ? 5000 : false,
    ...options,
  });

  return {
    ...query,
    isPending: query.isFetching || query.isLoading,
  };
}
