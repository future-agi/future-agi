import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export default function useKpis(executionId, options = {}) {
  const queryKey = ["test-execution-detail", "KPIS", executionId];

  const query = useQuery({
    queryKey,
    // Cache the body, not the AxiosResponse. `useRunsSummary` (environments
    // workspace) shares this exact query key and caches `res.data` — if this
    // hook cached the raw response instead, whichever observer mounted second
    // would read the wrong shape back off the shared cache entry (M2,
    // final-review-r4.md).
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
