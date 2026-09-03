import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  AGGREGATION_REQUEST_TIMEOUT_MS,
  awaitAggregationRequestWithDeadline,
  createAggregationPollController,
  getAggregationRefreshState,
  getExactAggregationReadState,
} from "src/utils/queryReadState";
import { parseAgentGraphResponse } from "./agent-graph-contract";

export const getAgentGraphPresentationState = (query) => {
  const readState = query.data
    ? getExactAggregationReadState(query.data)
    : null;
  const hasExactSnapshot = readState === "complete";
  const exactSnapshot = hasExactSnapshot ? query.data : query.previousExactData;
  const { refreshFailed } = getAggregationRefreshState(query.data);
  const failedPendingRefresh =
    readState === "pending" && (refreshFailed || query.isError);
  const hasUnreadablePayload =
    Boolean(query.data) && readState !== "complete" && readState !== "pending";
  const pollingPaused = query.pollingPaused === true;
  const terminalError =
    query.isError || hasUnreadablePayload || failedPendingRefresh;

  return {
    data: exactSnapshot,
    isLoading:
      !exactSnapshot &&
      !terminalError &&
      !pollingPaused &&
      (query.isLoading || (readState === "pending" && !failedPendingRefresh)),
    // A polling transport/refresh failure must never hide an exact snapshot
    // already returned by the server. Cold failures still render the generic,
    // retryable error state below the exactness gate.
    isError: !exactSnapshot && terminalError,
    queryReadState: readState,
    refreshUnavailable: query.refreshUnavailable === true,
    pollingPaused: pollingPaused && !terminalError,
  };
};

/**
 * Fetch an exact aggregate Agent Graph snapshot.
 *
 * Cold reads are background jobs: the hook polls their explicit pending
 * envelope and never exposes its empty arrays as a completed graph. A manual
 * Observe refresh asks the backend to recompute atomically; if a prior exact
 * snapshot exists it remains visible while that refresh runs.
 */
export const useAgentGraph = (
  projectId,
  filters = [],
  { enabled = true } = {},
) => {
  const forceRefreshRef = useRef(false);
  const pollingControllerRef = useRef(null);
  if (pollingControllerRef.current === null) {
    pollingControllerRef.current = createAggregationPollController();
  }
  const serverPendingRef = useRef(false);
  const requestScopeRef = useRef(null);
  const lastExactSnapshotRef = useRef(null);
  const [aggregationTransportFailed, setAggregationTransportFailed] =
    useState(false);
  const [aggregationPollingPaused, setAggregationPollingPaused] =
    useState(false);

  const query = useQuery({
    queryKey: ["agent-graph", projectId, filters],
    queryFn: async ({ queryKey, signal }) => {
      const requestScope = JSON.stringify(queryKey);
      if (requestScopeRef.current !== requestScope) {
        requestScopeRef.current = requestScope;
        pollingControllerRef.current.reset();
        serverPendingRef.current = false;
        lastExactSnapshotRef.current = null;
        setAggregationTransportFailed(false);
        setAggregationPollingPaused(false);
      }
      pollingControllerRef.current.recordAttempt();
      const refresh = forceRefreshRef.current;
      forceRefreshRef.current = false;
      let response;
      try {
        response = await awaitAggregationRequestWithDeadline(
          (requestSignal) =>
            axios.get(endpoints.project.getAgentGraph(), {
              params: {
                project_id: projectId,
                filters: JSON.stringify(filters || []),
                ...(refresh ? { refresh: true } : {}),
              },
              signal: requestSignal,
            }),
          { timeoutMs: AGGREGATION_REQUEST_TIMEOUT_MS, signal },
        );
      } catch (error) {
        if (!signal.aborted && serverPendingRef.current) {
          if (!pollingControllerRef.current.recordFailure()) {
            serverPendingRef.current = false;
            setAggregationTransportFailed(true);
            setAggregationPollingPaused(false);
          }
        }
        throw error;
      }

      let result;
      try {
        result = parseAgentGraphResponse(response.data);
      } catch (error) {
        // A 2xx body that violates the generated contract cannot become valid
        // by polling the same snapshot. Stop immediately and leave any prior
        // exact graph visible until an explicit refresh starts a new budget.
        pollingControllerRef.current.terminate();
        serverPendingRef.current = false;
        setAggregationTransportFailed(true);
        setAggregationPollingPaused(false);
        throw error;
      }

      const { isRefreshing, refreshFailed } =
        getAggregationRefreshState(result);
      const readState = getExactAggregationReadState(result);
      if (readState === "complete") lastExactSnapshotRef.current = result;
      serverPendingRef.current =
        isRefreshing &&
        !refreshFailed &&
        (readState === "pending" || readState === "complete");
      pollingControllerRef.current.recordSuccess();
      if (serverPendingRef.current) pollingControllerRef.current.start();
      else pollingControllerRef.current.stop();
      setAggregationTransportFailed(false);
      setAggregationPollingPaused(false);
      return result;
    },
    enabled: !!projectId && enabled,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (activeQuery) => {
      const payload = activeQuery.state.data;
      const { isRefreshing, refreshFailed } =
        getAggregationRefreshState(payload);
      const readState = getExactAggregationReadState(payload);
      if (
        !isRefreshing ||
        refreshFailed ||
        (readState !== "pending" && readState !== "complete")
      ) {
        pollingControllerRef.current.stop();
        serverPendingRef.current = false;
        return false;
      }
      // React Query recalculates intervals when a poll starts. Do not spend the
      // next delay budget until the in-flight response records its outcome.
      if (activeQuery.state.fetchStatus === "fetching") return false;
      pollingControllerRef.current.start();
      const delay = pollingControllerRef.current.nextDelay();
      if (delay === false) {
        serverPendingRef.current = false;
        if (
          pollingControllerRef.current.getTerminationReason() === "poll_budget"
        ) {
          setAggregationPollingPaused(true);
        }
      }
      return delay;
    },
    refetchIntervalInBackground: false,
    retry: false,
    meta: { errorHandled: true },
  });
  const { refetch } = query;

  const refresh = useCallback(() => {
    forceRefreshRef.current = true;
    pollingControllerRef.current.reset();
    serverPendingRef.current = false;
    setAggregationTransportFailed(false);
    setAggregationPollingPaused(false);
    return refetch({ cancelRefetch: true });
  }, [refetch]);

  useEffect(() => {
    const handleRefresh = (event) => {
      if (!enabled || !projectId) return;
      if (
        event?.detail?.observeId &&
        String(event.detail.observeId) !== String(projectId)
      ) {
        return;
      }
      refresh();
    };
    window.addEventListener("observe-refresh", handleRefresh);
    return () => window.removeEventListener("observe-refresh", handleRefresh);
  }, [enabled, projectId, refresh]);

  const pollingTransportError =
    query.isError &&
    serverPendingRef.current &&
    pollingControllerRef.current.isActive();
  const presentationState = getAgentGraphPresentationState({
    ...query,
    previousExactData: lastExactSnapshotRef.current,
    refreshUnavailable: aggregationTransportFailed,
    pollingPaused: aggregationPollingPaused,
    isError:
      aggregationTransportFailed || (query.isError && !pollingTransportError),
  });

  return {
    ...query,
    ...presentationState,
    refresh,
  };
};
