import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";
import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { DEFAULT_USAGE_PERIOD } from "../constants";

export const TASK_USAGE_REQUEST_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

const taskUsageResponseError = () => {
  const error = new Error("Task usage returned an invalid response");
  error.code = "task_usage_invalid_response";
  return error;
};

const requestTaskUsage = (params, upstreamSignal) =>
  awaitAggregationRequestWithDeadline(
    (signal) =>
      axios.get(endpoints.project.getEvalTaskUsage(), {
        params,
        signal,
        timeout: TASK_USAGE_REQUEST_TIMEOUT_MS,
      }),
    {
      timeoutMs: TASK_USAGE_REQUEST_TIMEOUT_MS,
      signal: upstreamSignal,
    },
  );

/**
 * Task usage hooks — mirrors useEvalUsage but hits the eval-task endpoint
 * (`GET /tracer/eval-task/get_usage/`). The response shape is intentionally
 * identical to the eval-template usage endpoint so the same UsageChart
 * + DataTable + DetailPanel components render unchanged.
 *
 * Splitting chart and logs into two queries lets the chart cache for 30s
 * (cheap, doesn't change as the user paginates) while logs refetch on
 * every page change.
 */

const buildParams = ({
  evalTaskId,
  period,
  evalId,
  dateRange,
  endInclusive,
}) => {
  const params = { eval_task_id: evalTaskId, period };
  if (evalId) params.eval_id = evalId;
  if (dateRange?.[0] && dateRange?.[1]) {
    params.start_date = new Date(dateRange[0]).toISOString();
    const endDate = new Date(dateRange[1]);
    if (endInclusive) endDate.setDate(endDate.getDate() + 1);
    params.end_date = endDate.toISOString();
  }
  return params;
};

/**
 * Fetch chart + stats + configured-evals list. Independent of pagination.
 */
export function useTaskUsageChart(
  evalTaskId,
  {
    period = DEFAULT_USAGE_PERIOD,
    evalId,
    dateRange,
    endInclusive = false,
  } = {},
) {
  return useQuery({
    queryKey: [
      "tasks",
      "usage-chart",
      evalTaskId,
      period,
      evalId || null,
      dateRange?.[0] || null,
      dateRange?.[1] || null,
      endInclusive,
    ],
    queryFn: async ({ signal }) => {
      const { data } = await requestTaskUsage(
        {
          ...buildParams({
            evalTaskId,
            period,
            evalId,
            dateRange,
            endInclusive,
          }),
          // The chart hook ignores the paginated `logs` block; we still
          // need to send valid pagination params or the BE serializer
          // 400s. page is 1-indexed (DRF PageNumberPagination).
          page: 1,
          page_size: 1,
        },
        signal,
      );
      const result = data?.result;
      if (
        !result ||
        !result.stats ||
        typeof result.stats !== "object" ||
        Array.isArray(result.stats) ||
        !Array.isArray(result.chart) ||
        !Array.isArray(result.evals)
      ) {
        throw taskUsageResponseError();
      }
      return {
        stats: result.stats,
        chart: result.chart,
        evals: result.evals,
        // Backend echoes the exact bounded window it applied. Empty windows
        // stay empty; they are never widened to all task history.
        periodUsed: result.period_used,
        periodRequested: result.period_requested,
        querySampled: !!result.query_sampled,
        queryStatus: result.query_status,
      };
    },
    enabled: !!evalTaskId,
    staleTime: 30_000,
  });
}

/**
 * Fetch paginated logs. Keeps previous data while loading next page so
 * the table doesn't flash empty during pagination.
 */
export function useTaskUsageLogs(
  evalTaskId,
  {
    page = 0,
    pageSize = 25,
    period = DEFAULT_USAGE_PERIOD,
    evalId,
    dateRange,
    endInclusive = false,
  } = {},
) {
  return useQuery({
    queryKey: [
      "tasks",
      "usage-logs",
      evalTaskId,
      period,
      evalId || null,
      dateRange?.[0] || null,
      dateRange?.[1] || null,
      endInclusive,
      page,
      pageSize,
    ],
    queryFn: async ({ signal }) => {
      const { data } = await requestTaskUsage(
        {
          ...buildParams({
            evalTaskId,
            period,
            evalId,
            dateRange,
            endInclusive,
          }),
          page: page + 1,
          page_size: pageSize,
          include_summary: false,
        },
        signal,
      );
      const logs = data?.result?.logs;
      if (
        !logs ||
        !Array.isArray(logs.results) ||
        !Number.isSafeInteger(logs.count) ||
        logs.count < 0 ||
        typeof logs.has_more !== "boolean"
      ) {
        throw taskUsageResponseError();
      }
      return logs;
    },
    enabled: !!evalTaskId,
    keepPreviousData: true,
  });
}
