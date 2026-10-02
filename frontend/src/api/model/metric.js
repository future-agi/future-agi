import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const useGetMetricOptions = (id, metricId = null, options = {}) => {
  return useQuery({
    queryKey: ["dataset-options", id, metricId],
    queryFn: () =>
      axios.get(endpoints.dataset.options(id), {
        params: { metric_id: metricId },
      }),
    select: (d) => d.data,
    staleTime: 30 * 60 * 1000,
    ...options,
  });
};

export const useGetAllCustomMetrics = (id, options) => {
  return useQuery({
    ...options,
    queryKey: ["all-custom-metric", id],
    queryFn: () => axios.get(endpoints.customMetric.all(id)),
    staleTime: 30 * 60 * 1000, // 30 min stale time
    select: (d) => d.data?.metrics,
  });
};

export const useGetMetricTagOptions = (id, options) => {
  return useQuery({
    ...options,
    queryKey: ["metric-tag-options", id],
    queryFn: () => axios.get(endpoints.customMetric.tagOptions(id)),
    staleTime: 30 * 60 * 1000, // 30 min stale time
    select: (d) => d.data,
  });
};
