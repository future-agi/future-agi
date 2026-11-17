import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useMemo, useRef, useState } from "react";
import ChartsGenerator from "./ChartsGenerator";
import axios, { endpoints } from "src/utils/axios";
import { transformEvaluationPayload } from "./common";
import { Box, Button, Skeleton, Typography } from "@mui/material";
import { useChartsViewContext } from "./ChartsViewProvider/ChartsViewContext";
import { getStorage } from "src/hooks/use-local-storage";
import { normalizeTimestamp } from "./ChartsViewProvider/common";
import {
  AGGREGATION_POLLING_PAUSED_MESSAGE,
  AGGREGATION_PREPARING_MESSAGE,
  awaitAggregationRequestWithDeadline,
  createAggregationPollController,
  getAggregationRefreshState,
  getExactAggregationReadState,
  QUERY_FAILED_RETRY_MESSAGE,
} from "src/utils/queryReadState";
import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { evalQualitySummary } from "src/utils/evalQualityMetrics";

const EVAL_CHART_REQUEST_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

export default function ChartWithFetch({ evaluation, observeId, inView }) {
  const autoRefresh = getStorage("autoRefresh") ?? false;
  const { selectedInterval, filters, handleZoomChange } =
    useChartsViewContext();

  const queryKey = [
    "chart-data",
    evaluation?.id,
    evaluation?.name,
    observeId,
    selectedInterval.toLowerCase(),
    JSON.stringify(filters),
  ];

  const pollIdentity = JSON.stringify(queryKey);
  const pollingRef = useRef(null);
  if (pollingRef.current?.identity !== pollIdentity) {
    pollingRef.current = {
      identity: pollIdentity,
      controller: createAggregationPollController(),
      forceRefresh: false,
    };
  }
  const polling = pollingRef.current;
  const [pausedController, setPausedController] = useState(null);
  const pollingPaused = pausedController === polling.controller;

  const { data, isLoading, isFetching, isError, refetch } = useQuery({
    queryKey,
    queryFn: ({ signal }) => {
      // Intervals cannot restart an exhausted controller. A toolbar
      // invalidation or explicit Retry may open a fresh observation window.
      if (polling.controller.isExhausted()) polling.controller.reset();
      setPausedController(null);
      polling.controller.recordAttempt();
      const refresh = polling.forceRefresh;
      polling.forceRefresh = false;
      const timeoutMs = polling.controller.isActive()
        ? polling.controller.remainingMs(EVAL_CHART_REQUEST_TIMEOUT_MS)
        : EVAL_CHART_REQUEST_TIMEOUT_MS;
      const payload = {
        project_id: observeId,
        property: "average",
        interval: selectedInterval?.toLowerCase(),
        filters: JSON.stringify(filters),
        ...transformEvaluationPayload(evaluation),
        ...(refresh ? { refresh: true } : {}),
      };

      return awaitAggregationRequestWithDeadline(
        (requestSignal) =>
          axios.get(endpoints.project.getEvalGraph, {
            params: { ...payload },
            signal: requestSignal,
            timeout: timeoutMs,
          }),
        { timeoutMs, signal },
      );
    },
    refetchInterval: (activeQuery) => {
      if (!inView || activeQuery.state.fetchStatus === "fetching") return false;
      const payload = activeQuery.state.data;
      const { isRefreshing, refreshFailed } =
        getAggregationRefreshState(payload);
      if (activeQuery.state.status === "error" || refreshFailed) {
        polling.controller.terminate("query_failed");
        return false;
      }
      const readState = getExactAggregationReadState(payload);
      if (
        isRefreshing &&
        (readState === "pending" || readState === "complete")
      ) {
        polling.controller.start();
        const delay = polling.controller.nextDelay();
        if (
          delay === false &&
          polling.controller.getTerminationReason() === "poll_budget"
        ) {
          setPausedController(polling.controller);
        }
        return delay;
      }
      polling.controller.stop();
      return autoRefresh && readState === "complete" ? 10000 : false;
    },
    staleTime: Infinity,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
    enabled: inView,
  });

  const retry = () => {
    polling.controller.reset();
    polling.forceRefresh = true;
    setPausedController(null);
    refetch({ cancelRefetch: true });
  };

  const result = data?.data?.result;
  const retainedReadState = getExactAggregationReadState(result, {
    isError: false,
  });
  const queryReadState =
    retainedReadState === "complete"
      ? "complete"
      : getExactAggregationReadState(result, { isError });
  const { refreshFailed } = getAggregationRefreshState(data);
  const queryFailed = isError || refreshFailed;
  const queryReadMessage = queryFailed
    ? QUERY_FAILED_RETRY_MESSAGE
    : pollingPaused
      ? AGGREGATION_POLLING_PAUSED_MESSAGE
      : queryReadState === "complete"
        ? null
        : AGGREGATION_PREPARING_MESSAGE;

  const evalsChartData = useMemo(() => {
    const baseChart = {
      id: `chart-${evaluation?.id}`,
      label: evaluation?.name,
      unit: "%",
      yAxisLabel: `${evaluation?.name} in (%)`,
      isEvaluationChart: true,
    };

    if (!Array.isArray(result) || queryReadState !== "complete") {
      return { ...baseChart, series: [] };
    }

    return {
      ...baseChart,
      series: result.map((seriesObj) => ({
        name: seriesObj?.name,
        data: (seriesObj?.data ?? []).map((item) => ({
          ...item,
          x: normalizeTimestamp(item.timestamp),
          y: item?.value,
        })),
      })),
    };
  }, [evaluation?.id, evaluation?.name, queryReadState, result]);

  if (isLoading) {
    return <Skeleton variant="rectangular" width="100%" height={250} />;
  }

  return (
    <Box>
      {queryReadMessage && (
        <Box
          role={queryFailed ? "alert" : "status"}
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 1,
          }}
        >
          <Typography
            variant="caption"
            color={queryFailed ? "warning.main" : "text.secondary"}
          >
            {queryReadMessage}
          </Typography>
          {(queryFailed || pollingPaused) && (
            <Button size="small" onClick={retry} disabled={isFetching}>
              Retry
            </Button>
          )}
        </Box>
      )}
      <ChartsGenerator {...evalsChartData} onZoom={handleZoomChange} />
      {queryReadState === "complete" && (
        <Typography variant="caption" color="text.secondary" role="status">
          {evalQualitySummary(result?.[0]?.data ?? null, true)}
        </Typography>
      )}
    </Box>
  );
}

ChartWithFetch.propTypes = {
  evaluation: PropTypes.object,
  observeId: PropTypes.string,
  inView: PropTypes.bool,
};
