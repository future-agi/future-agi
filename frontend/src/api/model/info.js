import useSWR from "swr";
import { useMemo } from "react";

import axios, { fetcher, endpoints } from "src/utils/axios";

export function useGetModels() {
  const URL = endpoints.model.list;

  const { data, isLoading, error, isValidating } = useSWR(URL, fetcher);

  const memoizedValue = useMemo(
    () => ({
      models: data?.results || [],
      modelsLoading: isLoading,
      modelsError: error,
      modelsValidating: isValidating,
      modelsEmpty: !isLoading && !data?.results.length,
      next: data?.next,
      previous: data?.previous,
      count: data?.count,
      totalPages: data?.total_pages,
      currentPage: data?.current_page,
    }),
    [data, error, isLoading, isValidating],
  );

  return memoizedValue;
}

export function useGetModel(modelId) {
  const URL = endpoints.model.details(modelId);

  const { data, isLoading, error, isValidating } = useSWR(URL, fetcher);

  const memoizedValue = useMemo(
    () => ({
      model: data,
      modelLoading: isLoading,
      modelError: error,
      modelValidating: isValidating,
    }),
    [data, error, isLoading, isValidating],
  );

  return memoizedValue;
}

export async function updateModelPerformanceMetric(modelId, details) {
  const URL = endpoints.model.updateMetric(modelId);
  const data = details;
  await axios.post(URL, data);
}
