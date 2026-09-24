import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export default function useKpis(executionId, options = {}) {
  const queryKey = ["test-execution-detail", "KPIS", executionId];

  const query = useQuery({
    queryKey,
    // Cache the body, not the AxiosResponse: `useRunsSummary` (environments
    // workspace) shares this query key and caches `res.data` too, so caching
    // the raw response here would let whichever observer mounts second read
    // the wrong shape off the shared cache entry.
    queryFn: () => axios.get(endpoints.testExecutions.kpis(executionId)).then((res) => res.data),
    enabled: options.enabled ?? !!executionId,
    refetchInterval: options.refetch ? 5000 : false,
    staleTime: 1000 * 60 * 5, // 5 minutes
    ...options,
  });

  return {
    ...query,
    isPending: query.isFetching || query.isLoading,
  };
}
