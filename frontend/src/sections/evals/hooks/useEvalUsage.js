import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { startOfDay, endOfDay, startOfMinute, subDays } from "date-fns";
import axios, { endpoints } from "src/utils/axios";
import {
  AGGREGATION_POLL_TIMEOUT_MS,
  getAggregationPollDelay,
  getAggregationRefreshState,
  getExactAggregationReadState,
  getQueryCompletedAt,
  isAggregationPollBudgetExhausted,
} from "src/utils/queryReadState";

const readAggregationResult = (data) => {
  const queryReadState = getExactAggregationReadState(data);
  const { isRefreshing, refreshFailed } = getAggregationRefreshState(data);
  if (queryReadState === "pending") {
    return {
      result: null,
      queryPending: true,
      queryRefreshing: isRefreshing,
      queryRefreshFailed: refreshFailed,
      queryCompletedAt: null,
    };
  }
  if (queryReadState !== "complete") {
    throw new Error("Exact evaluation usage data is not available");
  }
  return {
    result: data?.result || {},
    queryPending: false,
    queryRefreshing: isRefreshing,
    queryRefreshFailed: refreshFailed,
    queryCompletedAt: getQueryCompletedAt(data)?.toISOString() || null,
  };
};

function useAggregationPollBudget(identity) {
  const pollAttemptRef = useRef(0);
  const pollingRef = useRef(false);
  const pollStartedAtRef = useRef(null);
  const [pollingTimedOut, setPollingTimedOut] = useState(false);

  const reset = useCallback(() => {
    pollAttemptRef.current = 0;
    pollingRef.current = false;
    pollStartedAtRef.current = null;
    setPollingTimedOut(false);
  }, []);

  useEffect(() => reset(), [identity, reset]);

  const markTimedOut = useCallback(() => {
    pollingRef.current = false;
    setPollingTimedOut(true);
  }, []);

  const beforeRequest = useCallback(() => {
    if (pollingRef.current) pollAttemptRef.current += 1;
  }, []);

  const record = useCallback(
    ({ queryRefreshing, queryRefreshFailed }) => {
      const shouldPoll = queryRefreshing && !queryRefreshFailed;
      if (!shouldPoll) {
        reset();
        return;
      }
      if (pollStartedAtRef.current == null) {
        pollStartedAtRef.current = Date.now();
      }
      if (
        isAggregationPollBudgetExhausted({
          attempt: pollAttemptRef.current,
          startedAt: pollStartedAtRef.current,
        })
      ) {
        markTimedOut();
        return;
      }
      pollingRef.current = true;
    },
    [markTimedOut, reset],
  );

  const refetchInterval = useCallback(
    (query) => {
      const data = query.state.data;
      if (
        pollingTimedOut ||
        !data?.queryRefreshing ||
        data?.queryRefreshFailed
      ) {
        if (!pollingTimedOut) {
          pollAttemptRef.current = 0;
          pollingRef.current = false;
          pollStartedAtRef.current = null;
        }
        return false;
      }
      if (
        isAggregationPollBudgetExhausted({
          attempt: pollAttemptRef.current,
          startedAt: pollStartedAtRef.current,
        })
      ) {
        markTimedOut();
        return false;
      }
      return getAggregationPollDelay(pollAttemptRef.current);
    },
    [markTimedOut, pollingTimedOut],
  );

  const watchPending = useCallback(
    (active) => {
      if (!active || pollingTimedOut) return undefined;
      const startedAt = pollStartedAtRef.current ?? Date.now();
      pollStartedAtRef.current = startedAt;
      const remaining = Math.max(
        AGGREGATION_POLL_TIMEOUT_MS - (Date.now() - startedAt),
        0,
      );
      if (remaining === 0) {
        markTimedOut();
        return undefined;
      }
      const timer = globalThis.setTimeout(markTimedOut, remaining);
      return () => globalThis.clearTimeout(timer);
    },
    [markTimedOut, pollingTimedOut],
  );

  return {
    beforeRequest,
    pollingTimedOut,
    record,
    refetchInterval,
    reset,
    watchPending,
  };
}

function useAggregationWallDeadline(watchPending, active, identity) {
  useEffect(() => watchPending(active), [active, identity, watchPending]);
}

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
 * Fetch chart + stats for a period. Does NOT depend on page/pageSize.
 */
export function useEvalUsageChart(
  templateId,
  period = "30d",
  dateOption,
  dateFilter,
) {
  const dateParams = useMemo(
    () => getDateParams(dateOption, dateFilter),
    [dateOption, dateFilter],
  );
  const forceRefreshRef = useRef(false);
  const pollIdentity = useMemo(
    () => JSON.stringify([templateId, period, dateParams]),
    [dateParams, period, templateId],
  );
  const {
    beforeRequest,
    pollingTimedOut,
    record,
    refetchInterval,
    reset,
    watchPending,
  } = useAggregationPollBudget(pollIdentity);
  const query = useQuery({
    queryKey: ["evals", "usage-chart", templateId, period, dateParams],
    queryFn: async () => {
      beforeRequest();
      const refresh = forceRefreshRef.current;
      forceRefreshRef.current = false;
      const { data } = await axios.get(
        endpoints.develop.eval.getEvalUsage(templateId),
        {
          params: {
            page: 0,
            page_size: 1,
            period,
            ...dateParams,
            ...(refresh ? { refresh: true } : {}),
          },
        },
      );
      const aggregation = readAggregationResult(data);
      record(aggregation);
      const result = aggregation.result || {};
      return {
        stats: result.stats,
        chart: result.chart,
        queryPending: aggregation.queryPending,
        queryRefreshing: aggregation.queryRefreshing,
        queryRefreshFailed: aggregation.queryRefreshFailed,
        queryCompletedAt: aggregation.queryCompletedAt,
      };
    },
    enabled:
      !!templateId &&
      !(dateOption === "Custom" && !(dateFilter?.[0] && dateFilter?.[1])),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval,
    refetchIntervalInBackground: false,
    retry: false,
    meta: { errorHandled: true },
  });
  useAggregationWallDeadline(
    watchPending,
    query.data?.queryRefreshing === true &&
      query.data?.queryRefreshFailed !== true,
    pollIdentity,
  );
  const refetch = query.refetch;
  const refresh = useCallback(() => {
    reset();
    forceRefreshRef.current = true;
    return refetch({ cancelRefetch: true });
  }, [refetch, reset]);

  return {
    ...query,
    isError: query.isError || pollingTimedOut,
    pollingTimedOut,
    refresh,
  };
}

/**
 * Fetch paginated logs. Keeps previous data while loading next page.
 */
export function useEvalUsageLogs(
  templateId,
  { page = 0, pageSize = 25, period = "30d", dateOption, dateFilter } = {},
) {
  const dateParams = useMemo(
    () => getDateParams(dateOption, dateFilter),
    [dateOption, dateFilter],
  );
  const forceRefreshRef = useRef(false);
  const pollIdentity = useMemo(
    () => JSON.stringify([templateId, period, page, pageSize, dateParams]),
    [dateParams, page, pageSize, period, templateId],
  );
  const {
    beforeRequest,
    pollingTimedOut,
    record,
    refetchInterval,
    reset,
    watchPending,
  } = useAggregationPollBudget(pollIdentity);
  const query = useQuery({
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
      beforeRequest();
      const refresh = forceRefreshRef.current;
      forceRefreshRef.current = false;
      const { data } = await axios.get(
        endpoints.develop.eval.getEvalUsage(templateId),
        {
          params: {
            page,
            page_size: pageSize,
            period,
            ...dateParams,
            ...(refresh ? { refresh: true } : {}),
          },
        },
      );
      const aggregation = readAggregationResult(data);
      record(aggregation);
      const result = aggregation.result || {};
      return {
        table: result.table || [],
        pagination: result.logs || {},
        queryPending: aggregation.queryPending,
        queryRefreshing: aggregation.queryRefreshing,
        queryRefreshFailed: aggregation.queryRefreshFailed,
        queryCompletedAt: aggregation.queryCompletedAt,
      };
    },
    enabled:
      !!templateId &&
      !(dateOption === "Custom" && !(dateFilter?.[0] && dateFilter?.[1])),
    // TanStack Query v5 replaced the boolean v4 option with placeholderData.
    // Keep the exact previous page visible while the next exact page loads.
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval,
    refetchIntervalInBackground: false,
    retry: false,
    meta: { errorHandled: true },
  });
  useAggregationWallDeadline(
    watchPending,
    query.data?.queryRefreshing === true &&
      query.data?.queryRefreshFailed !== true,
    pollIdentity,
  );
  const refetch = query.refetch;
  const refresh = useCallback(() => {
    reset();
    forceRefreshRef.current = true;
    return refetch({ cancelRefetch: true });
  }, [refetch, reset]);

  return {
    ...query,
    isError: query.isError || pollingTimedOut,
    pollingTimedOut,
    refresh,
  };
}
