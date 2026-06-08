import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * Fetch chart + stats for a period. Does NOT depend on page/pageSize.
 */
export function useEvalUsageChart(templateId, period = "30d") {
  return useQuery({
    queryKey: ["evals", "usage-chart", templateId, period],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getEvalUsage(templateId),
        { params: { page: 0, page_size: 1, period } },
      );
      const result = data?.result;
      return { stats: result?.stats, chart: result?.chart };
    },
    enabled: !!templateId,
    staleTime: 30_000, // cache chart for 30s
  });
}

/**
 * Fetch paginated logs. Keeps previous data while loading next page.
 */
export function useEvalUsageLogs(
  templateId,
  { page = 0, pageSize = 25, period = "30d", refetchInterval = false } = {},
) {
  return useQuery({
    queryKey: ["evals", "usage-logs", templateId, period, page, pageSize],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getEvalUsage(templateId),
        { params: { page, page_size: pageSize, period } },
      );
      return data?.result?.logs;
    },
    enabled: !!templateId,
    keepPreviousData: true,
    refetchInterval,
  });
}
