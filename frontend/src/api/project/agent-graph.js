import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  getAggregationPollDelay,
  getAggregationRefreshState,
  getExactAggregationReadState,
} from "src/utils/queryReadState";

export const AGENT_GRAPH_PENDING_TIMEOUT_MS = 60_000;

export const getAgentGraphPresentationState = (
  query,
  { pendingTimedOut = false } = {},
) => {
  const readState = query.data
    ? getExactAggregationReadState(query.data)
    : null;
  const { refreshFailed } = getAggregationRefreshState(query.data);
  const failedPendingRefresh =
    readState === "pending" && (refreshFailed || query.isError);
  const timedOutPendingRefresh = readState === "pending" && pendingTimedOut;
  const hasUnreadablePayload =
    Boolean(query.data) && readState !== "complete" && readState !== "pending";

  return {
    data: readState === "complete" ? query.data : undefined,
    isLoading:
      !query.isError &&
      !timedOutPendingRefresh &&
      (query.isLoading || (readState === "pending" && !failedPendingRefresh)),
    isError:
      query.isError ||
      hasUnreadablePayload ||
      failedPendingRefresh ||
      timedOutPendingRefresh,
    queryReadState: readState,
  };
};

/**
 * Fetch an exact aggregate Agent Graph/Path snapshot.
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
  const pollAttemptRef = useRef(0);
  const [pendingTimedOut, setPendingTimedOut] = useState(false);
  const [pollEpoch, setPollEpoch] = useState(0);

  const query = useQuery({
    queryKey: ["agent-graph", projectId, filters],
    queryFn: async () => {
      const refresh = forceRefreshRef.current;
      forceRefreshRef.current = false;
      const response = await axios.get(endpoints.project.getAgentGraph(), {
        params: {
          project_id: projectId,
          filters: JSON.stringify(filters || []),
          ...(refresh ? { refresh: true } : {}),
        },
      });
      return response.data?.result;
    },
    enabled: !!projectId && enabled,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (activeQuery) => {
      if (pendingTimedOut) return false;
      const payload = activeQuery.state.data;
      const { isRefreshing, refreshFailed } =
        getAggregationRefreshState(payload);
      const readState = getExactAggregationReadState(payload);
      if (
        !isRefreshing ||
        refreshFailed ||
        (readState !== "pending" && readState !== "complete")
      ) {
        pollAttemptRef.current = 0;
        return false;
      }
      const delay = getAggregationPollDelay(pollAttemptRef.current);
      pollAttemptRef.current += 1;
      return delay;
    },
    refetchIntervalInBackground: false,
    retry: false,
    meta: { errorHandled: true },
  });
  const { refetch } = query;

  useEffect(() => {
    const handleRefresh = (event) => {
      if (!enabled || !projectId) return;
      if (
        event?.detail?.observeId &&
        String(event.detail.observeId) !== String(projectId)
      ) {
        return;
      }
      forceRefreshRef.current = true;
      pollAttemptRef.current = 0;
      setPendingTimedOut(false);
      setPollEpoch((value) => value + 1);
      refetch({ cancelRefetch: true });
    };
    window.addEventListener("observe-refresh", handleRefresh);
    return () => window.removeEventListener("observe-refresh", handleRefresh);
  }, [enabled, projectId, refetch]);

  const rawPresentationState = getAgentGraphPresentationState(query);
  const isLivePending =
    rawPresentationState.queryReadState === "pending" &&
    !rawPresentationState.isError;
  const pendingIdentity = JSON.stringify([projectId, filters || []]);

  useEffect(() => {
    pollAttemptRef.current = 0;
    setPendingTimedOut(false);
    if (!enabled || !projectId || !isLivePending) return undefined;

    const timeout = setTimeout(
      () => setPendingTimedOut(true),
      AGENT_GRAPH_PENDING_TIMEOUT_MS,
    );
    return () => clearTimeout(timeout);
  }, [enabled, projectId, pendingIdentity, isLivePending, pollEpoch]);

  const presentationState = getAgentGraphPresentationState(query, {
    pendingTimedOut,
  });

  return {
    ...query,
    ...presentationState,
  };
};
