import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { startOfDay, endOfDay, startOfMinute, subDays } from "date-fns";
import axios, { endpoints } from "src/utils/axios";
import {
  AGGREGATION_REQUEST_TIMEOUT_MS,
  awaitAggregationRequestWithDeadline,
  createAggregationPollController,
  getAggregationRefreshState,
  getExactAggregationReadState,
  getQueryCompletedAt,
} from "src/utils/queryReadState";

const readAggregationResult = (data) => {
  const queryReadState = getExactAggregationReadState(data);
  const { isRefreshing, refreshFailed } = getAggregationRefreshState(data);
  const result = data?.result ?? data;
  const readMetadata = {
    query_complete: result?.query_complete,
    query_status: result?.query_status,
    query_sampled: result?.query_sampled,
    query_error_code: result?.query_error_code,
    query_cached: result?.query_cached,
    query_refreshing: result?.query_refreshing,
    query_refresh_failed: result?.query_refresh_failed,
    data_stale: result?.data_stale,
  };
  if (queryReadState === "pending") {
    return {
      result: null,
      readMetadata,
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
    result,
    readMetadata,
    queryPending: false,
    queryRefreshing: isRefreshing,
    queryRefreshFailed: refreshFailed,
    queryCompletedAt: getQueryCompletedAt(data)?.toISOString() || null,
  };
};

function useAggregationPolling(identity) {
  const pollingControllerRef = useRef(null);
  if (pollingControllerRef.current === null) {
    pollingControllerRef.current = createAggregationPollController();
  }
  const [aggregationTransportFailed, setAggregationTransportFailed] =
    useState(false);
  const [aggregationPollingPaused, setAggregationPollingPaused] =
    useState(false);

  const reset = useCallback(() => {
    pollingControllerRef.current.reset();
    setAggregationTransportFailed(false);
    setAggregationPollingPaused(false);
  }, []);

  useEffect(() => reset(), [identity, reset]);

  const record = useCallback(({ queryRefreshing, queryRefreshFailed }) => {
    pollingControllerRef.current.recordSuccess();
    setAggregationTransportFailed(false);
    setAggregationPollingPaused(false);
    const shouldPoll = queryRefreshing && !queryRefreshFailed;
    if (!shouldPoll) {
      pollingControllerRef.current.stop();
      return;
    }
    pollingControllerRef.current.start();
  }, []);

  const recordFailure = useCallback(() => {
    if (
      pollingControllerRef.current.isActive() &&
      !pollingControllerRef.current.recordFailure()
    ) {
      setAggregationTransportFailed(true);
      setAggregationPollingPaused(false);
    }
  }, []);

  const recordAttempt = useCallback(() => {
    pollingControllerRef.current.recordAttempt();
  }, []);

  // A 2xx body that violates the exact-aggregation contract is deterministic,
  // not a transient transport outage. Stop polling immediately so retained
  // pending metadata cannot hide the failure or disable an explicit retry.
  const recordTerminalFailure = useCallback(() => {
    pollingControllerRef.current.terminate();
    setAggregationTransportFailed(true);
    setAggregationPollingPaused(false);
  }, []);

  const refetchInterval = useCallback((query) => {
    const data = query.state.data;
    if (!data?.queryRefreshing || data?.queryRefreshFailed) {
      pollingControllerRef.current.stop();
      return false;
    }
    // React Query recalculates intervals when a poll starts. Do not spend the
    // next delay budget until the in-flight response records its outcome.
    if (query.state.fetchStatus === "fetching") return false;
    pollingControllerRef.current.start();
    const delay = pollingControllerRef.current.nextDelay();
    if (delay === false) {
      if (
        pollingControllerRef.current.getTerminationReason() === "poll_budget"
      ) {
        setAggregationPollingPaused(true);
      }
    }
    return delay;
  }, []);

  const isFailureTerminal = useCallback(
    () => !pollingControllerRef.current.isActive(),
    [],
  );

  return {
    record,
    recordAttempt,
    recordFailure,
    recordTerminalFailure,
    refetchInterval,
    reset,
    isFailureTerminal,
    aggregationTransportFailed,
    aggregationPollingPaused,
  };
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
  const lastExactSnapshotRef = useRef(null);
  const pollIdentity = useMemo(
    () => JSON.stringify([templateId, period, dateParams]),
    [dateParams, period, templateId],
  );
  const {
    record,
    recordAttempt,
    recordFailure,
    recordTerminalFailure,
    refetchInterval,
    reset,
    isFailureTerminal,
    aggregationTransportFailed,
    aggregationPollingPaused,
  } = useAggregationPolling(pollIdentity);
  const query = useQuery({
    queryKey: ["evals", "usage-chart", templateId, period, dateParams],
    queryFn: async ({ signal }) => {
      recordAttempt();
      const refresh = forceRefreshRef.current;
      forceRefreshRef.current = false;
      let data;
      try {
        ({ data } = await awaitAggregationRequestWithDeadline(
          (requestSignal) =>
            axios.get(endpoints.develop.eval.getEvalUsage(templateId), {
              params: {
                page: 0,
                page_size: 1,
                period,
                ...dateParams,
                ...(refresh ? { refresh: true } : {}),
              },
              signal: requestSignal,
            }),
          { timeoutMs: AGGREGATION_REQUEST_TIMEOUT_MS, signal },
        ));
      } catch (error) {
        if (!signal.aborted) recordFailure();
        throw error;
      }
      let aggregation;
      try {
        aggregation = readAggregationResult(data);
      } catch (error) {
        recordTerminalFailure();
        throw error;
      }
      record(aggregation);
      const result = aggregation.result || {};
      const nextData = {
        ...aggregation.readMetadata,
        stats: result.stats,
        chart: result.chart,
        queryPending: aggregation.queryPending,
        queryRefreshing: aggregation.queryRefreshing,
        queryRefreshFailed: aggregation.queryRefreshFailed,
        queryCompletedAt: aggregation.queryCompletedAt,
      };
      if (!aggregation.queryPending) {
        lastExactSnapshotRef.current = {
          identity: pollIdentity,
          data: nextData,
        };
        return nextData;
      }
      const previous = lastExactSnapshotRef.current;
      return previous?.identity === pollIdentity
        ? {
            ...previous.data,
            queryPending: true,
            queryRefreshing: aggregation.queryRefreshing,
            queryRefreshFailed: aggregation.queryRefreshFailed,
            query_refreshing: aggregation.queryRefreshing,
            query_refresh_failed: aggregation.queryRefreshFailed,
          }
        : nextData;
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
  const refetch = query.refetch;
  const refresh = useCallback(() => {
    reset();
    forceRefreshRef.current = true;
    return refetch({ cancelRefetch: true });
  }, [refetch, reset]);

  const terminalError =
    !query.isFetching &&
    (aggregationTransportFailed ||
      (query.isError && isFailureTerminal()) ||
      query.data?.queryRefreshFailed === true);
  const data =
    (terminalError || aggregationPollingPaused) && query.data?.queryRefreshing
      ? { ...query.data, queryRefreshing: false, query_refreshing: false }
      : query.data;

  return {
    ...query,
    data,
    isError: terminalError,
    isPollingPaused: aggregationPollingPaused,
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
  const lastExactSnapshotRef = useRef(null);
  const pollIdentity = useMemo(
    () => JSON.stringify([templateId, period, page, pageSize, dateParams]),
    [dateParams, page, pageSize, period, templateId],
  );
  const {
    record,
    recordAttempt,
    recordFailure,
    recordTerminalFailure,
    refetchInterval,
    reset,
    isFailureTerminal,
    aggregationTransportFailed,
    aggregationPollingPaused,
  } = useAggregationPolling(pollIdentity);
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
    queryFn: async ({ signal }) => {
      recordAttempt();
      const refresh = forceRefreshRef.current;
      forceRefreshRef.current = false;
      let data;
      try {
        ({ data } = await awaitAggregationRequestWithDeadline(
          (requestSignal) =>
            axios.get(endpoints.develop.eval.getEvalUsage(templateId), {
              params: {
                page,
                page_size: pageSize,
                period,
                ...dateParams,
                ...(refresh ? { refresh: true } : {}),
              },
              signal: requestSignal,
            }),
          { timeoutMs: AGGREGATION_REQUEST_TIMEOUT_MS, signal },
        ));
      } catch (error) {
        if (!signal.aborted) recordFailure();
        throw error;
      }
      let aggregation;
      try {
        aggregation = readAggregationResult(data);
      } catch (error) {
        recordTerminalFailure();
        throw error;
      }
      record(aggregation);
      const result = aggregation.result || {};
      const nextData = {
        ...aggregation.readMetadata,
        table: result.table || [],
        pagination: result.logs || {},
        queryPending: aggregation.queryPending,
        queryRefreshing: aggregation.queryRefreshing,
        queryRefreshFailed: aggregation.queryRefreshFailed,
        queryCompletedAt: aggregation.queryCompletedAt,
      };
      if (!aggregation.queryPending) {
        lastExactSnapshotRef.current = {
          identity: pollIdentity,
          data: nextData,
        };
        return nextData;
      }
      const previous = lastExactSnapshotRef.current;
      return previous?.identity === pollIdentity
        ? {
            ...previous.data,
            queryPending: true,
            queryRefreshing: aggregation.queryRefreshing,
            queryRefreshFailed: aggregation.queryRefreshFailed,
            query_refreshing: aggregation.queryRefreshing,
            query_refresh_failed: aggregation.queryRefreshFailed,
          }
        : nextData;
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
  const refetch = query.refetch;
  const refresh = useCallback(() => {
    reset();
    forceRefreshRef.current = true;
    return refetch({ cancelRefetch: true });
  }, [refetch, reset]);

  const terminalError =
    !query.isFetching &&
    (aggregationTransportFailed ||
      (query.isError && isFailureTerminal()) ||
      query.data?.queryRefreshFailed === true);
  const data =
    (terminalError || aggregationPollingPaused) && query.data?.queryRefreshing
      ? { ...query.data, queryRefreshing: false, query_refreshing: false }
      : query.data;

  return {
    ...query,
    data,
    isError: terminalError,
    isPollingPaused: aggregationPollingPaused,
    refresh,
  };
}
