import { useMemo } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { startOfDay, endOfDay, startOfMinute, subDays } from "date-fns";
import axios, { endpoints } from "src/utils/axios";

/**
 * Compute explicit start/end dates for date options that map to calendar
 * ranges (Today, Yesterday) or custom pickers, so the backend receives the
 * actual window rather than a coarse period string.
 */
function getDateParams(dateOption, dateFilter) {
  if (dateOption === "Today") {
    return {
      start_date: startOfDay(new Date()).toISOString(),
      // Floor to the minute so the query key is stable across renders.
      end_date: startOfMinute(new Date()).toISOString(),
    };
  }
  if (dateOption === "Yesterday") {
    const yesterday = subDays(new Date(), 1);
    return {
      start_date: startOfDay(yesterday).toISOString(),
      end_date: endOfDay(yesterday).toISOString(),
    };
  }
  if (dateOption === "Custom" && dateFilter?.[0] && dateFilter?.[1]) {
    return {
      start_date: new Date(dateFilter[0]).toISOString(),
      end_date: endOfDay(new Date(dateFilter[1])).toISOString(),
    };
  }
  return {};
}

/**
 * Fetch the complete usage payload once. The backend response already contains
 * stats, chart data, and paginated logs, so separate chart/log queries would
 * repeat the same expensive backend aggregation.
 */
export function useEvalUsageLogs(
  templateId,
  { page = 0, pageSize = 25, period = "30d", dateOption, dateFilter } = {},
) {
  const dateParams = useMemo(
    () => getDateParams(dateOption, dateFilter),
    [dateOption, dateFilter],
  );
  return useQuery({
    queryKey: [
      "evals",
      "usage-logs",
      templateId,
      period,
      page,
      pageSize,
      dateParams,
    ],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getEvalUsage(templateId),
        { params: { page, page_size: pageSize, period, ...dateParams } },
      );
      const result = data?.result || {};
      return {
        stats: result.stats || {},
        chart: result.chart || [],
        table: result.table || [],
        pagination: result.logs || {},
      };
    },
    enabled:
      !!templateId &&
      !(dateOption === "Custom" && !(dateFilter?.[0] && dateFilter?.[1])),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}
