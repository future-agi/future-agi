import { useMemo } from "react";
import { endpoints, fetchWithPost } from "src/utils/axios";
import useSWR from "swr";

export function useModelPerformance(modelId, details) {
  const URL = endpoints.model.performance(modelId);

  const { data, isLoading, error, isValidating } = useSWR(
    [URL, details],
    fetchWithPost,
  );

  const memoizedValue = useMemo(
    () => ({
      performanceData: data || [],
      performanceLoading: isLoading,
      performanceError: error,
      performanceValidating: isValidating,
      performanceEmpty: !isLoading && !data,
    }),
    [data, error, isLoading, isValidating],
  );

  return memoizedValue;
}
