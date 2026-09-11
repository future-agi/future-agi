import React, {
  useState,
  useEffect,
  useId,
  useRef,
  useMemo,
  useCallback,
} from "react";
import { Box, Typography, useTheme } from "@mui/material";
import ReactApexChart from "react-apexcharts";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import EmptyGraph from "src/assets/illustrations/empty-graph";
import _ from "lodash";
import { getRandomId, getUniqueColorPalette } from "src/utils/utils";
import { add, format, sub } from "date-fns";
import {
  isDateRangeLessThan90Days,
  isDateRangeMoreThan7Days,
} from "src/utils/dateTimeUtils";

import RightControl from "./RightControl";
import LeftControl from "./LeftControl";
import Legend from "./Legend";
import GraphSkeleton from "./GraphSkeleton";
import { formatYAxisValue, getYAxisUnit, getLineSeriesName } from "./common";
import SVGColor from "src/components/svg-color";
import { useLLMTracingStoreShallow } from "../states";
import { logger } from "src/utils/logger";
import { FILTER_FOR_HAS_EVAL, toBackendFilters } from "../common";
import { buildDefaultDateEntry } from "./graphFilterUtils";
import {
  AGGREGATION_POLLING_PAUSED_MESSAGE,
  AGGREGATION_REQUEST_TIMEOUT_MS,
  GRAPH_LOADING_MESSAGE,
  QUERY_FAILED_RETRY_MESSAGE,
  createAggregationPollController,
  getAggregationRefreshState,
  getExactAggregationReadState,
  getExactGraphData,
  getQueryCompletedAt,
  awaitAggregationRequestWithDeadline,
} from "src/utils/queryReadState";
import { parseTraceGraphResponse } from "src/api/project/observe-contracts";

const deltaObject = {
  hour: { hours: 1 },
  day: { days: 1 },
  week: { weeks: 1 },
  month: { months: 1 },
};

const GRAPH_PROPERTY_KIND = {
  EVAL: "eval_config",
  ANNOTATION: "annotation",
};

const withGraphPropertyIdentity = (config, selectedTab = "trace") => {
  if (!config) return null;
  const { propertyId, ...requestConfig } = config;
  const prefix =
    requestConfig.type === "SYSTEM_METRIC"
      ? `system_attribute:${selectedTab === "spans" ? "spans" : "traces"}`
      : GRAPH_PROPERTY_KIND[requestConfig.type];
  const property_id =
    requestConfig.type === "SYSTEM_METRIC" && requestConfig.id
      ? `${prefix}:${requestConfig.id}`
      : requestConfig.property_id || propertyId;
  const canonicalPropertyId =
    property_id ||
    (prefix && requestConfig.id ? `${prefix}:${requestConfig.id}` : "");
  if (!canonicalPropertyId) return requestConfig;
  return {
    ...requestConfig,
    property_id: canonicalPropertyId,
    source: "traces",
  };
};

const GraphSection = ({
  selectedTab,
  filters,
  showCompare,
  selectedGraphProperty,
  selectedGraphEvals,
  setSelectedGraphEvals,
  setSelectedGraphProperty,
  hasEvalFilter,
  selectedGraphAttributes,
  setSelectedGraphAttributes,
  compareType,
  dateFilter,
  setDateFilter,
  index,
  selectedInterval,
  setSelectedInterval,
  lineColor,
  trafficColor,
}) => {
  const [_height, setHeight] = useState(320);
  const [isDragging, setIsDragging] = useState(false);
  const [selectedGraphConfig, setSelectedGraphConfig] = useState(null);
  const boxRef = useRef(null);
  const llmTracingStore = useLLMTracingStoreShallow((s) => ({
    [`${compareType}Collapsed`]: s[`${compareType}Collapsed`],
    [`set${_.capitalize(compareType)}Collapsed`]:
      s[`set${_.capitalize(compareType)}Collapsed`],
  }));

  const setCollapsed = useCallback(
    (collapsed) => {
      llmTracingStore[`set${_.capitalize(compareType)}Collapsed`](collapsed);
    },
    [llmTracingStore, compareType],
  );

  const isCollapsed = llmTracingStore[`${compareType}Collapsed`];

  const isMoreThan7Days = isDateRangeMoreThan7Days(dateFilter.dateFilter);
  const isLessThan90Days = isDateRangeLessThan90Days(dateFilter.dateFilter);

  const chartId = useMemo(() => getRandomId(), []);
  const chartRef = useRef(null);

  const { observeId } = useParams();
  const aggregationSourceId = useId();
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const handleMouseMove = useCallback(
    (e) => {
      if (isDragging) {
        const rect = boxRef.current.getBoundingClientRect();
        let newHeight = e.clientY - rect.y;
        newHeight = Math.max(0, newHeight);
        newHeight = Math.round(newHeight);
        setHeight(newHeight);
      }
    },
    [isDragging],
  );

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  useEffect(() => {
    if (isDragging) {
      window.addEventListener("mousemove", handleMouseMove);
      window.addEventListener("mouseup", handleMouseUp);
    } else {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    }

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [handleMouseMove, isDragging]);

  const combinedFilters = useMemo(
    () => [
      ...(filters || []),
      ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
      ...buildDefaultDateEntry(filters, dateFilter),
    ],
    [filters, dateFilter, hasEvalFilter],
  );

  const handleGraphConfigChange = (config) => {
    setSelectedGraphConfig(withGraphPropertyIdentity(config, selectedTab));
  };
  const graphRequestConfig = useMemo(
    () => withGraphPropertyIdentity(selectedGraphConfig, selectedTab),
    [selectedGraphConfig, selectedTab],
  );
  const forceRefreshRef = useRef(false);
  const pollingRef = useRef(false);
  const pollingControllerRef = useRef(null);
  if (pollingControllerRef.current === null) {
    pollingControllerRef.current = createAggregationPollController();
  }
  const [aggregationTransportFailed, setAggregationTransportFailed] =
    useState(false);
  const [aggregationPollingPaused, setAggregationPollingPaused] =
    useState(false);
  const requestScopeRef = useRef(null);
  const requestGenerationRef = useRef(0);

  const resetAggregationBudget = useCallback(() => {
    requestGenerationRef.current += 1;
    requestScopeRef.current = null;
    pollingControllerRef.current.reset();
    pollingRef.current = false;
    setAggregationTransportFailed(false);
    setAggregationPollingPaused(false);
  }, []);

  const runAggregationRequest = useCallback(
    async (scopeKey, signal, request) => {
      if (requestScopeRef.current !== scopeKey) {
        requestGenerationRef.current += 1;
        requestScopeRef.current = scopeKey;
        pollingControllerRef.current.reset();
        pollingRef.current = false;
        setAggregationTransportFailed(false);
        setAggregationPollingPaused(false);
      }

      // Start the action clock before the first HTTP read. Pending-response
      // polling consumes this same sub-ten-second budget; an explicit Refresh
      // resets it through resetAggregationBudget().
      pollingControllerRef.current.start();
      pollingControllerRef.current.recordAttempt();
      const generation = requestGenerationRef.current;
      return awaitAggregationRequestWithDeadline(request, {
        timeoutMs: pollingControllerRef.current.remainingMs(
          AGGREGATION_REQUEST_TIMEOUT_MS,
        ),
        signal,
        isCurrent: () => generation === requestGenerationRef.current,
        onTimeout: () =>
          pollingControllerRef.current.terminate("action_deadline"),
      });
    },
    [],
  );

  const recordAggregationResponse = useCallback((response) => {
    pollingControllerRef.current.recordSuccess();
    setAggregationTransportFailed(false);
    setAggregationPollingPaused(false);
    const { isRefreshing, refreshFailed } =
      getAggregationRefreshState(response);
    const readState = getExactAggregationReadState(response);
    const shouldPoll =
      isRefreshing &&
      !refreshFailed &&
      (readState === "complete" || readState === "pending");
    if (!shouldPoll) {
      pollingControllerRef.current.stop();
      pollingRef.current = false;
      return;
    }
    pollingRef.current = pollingControllerRef.current.start();
  }, []);
  const recordAggregationFailure = useCallback(() => {
    if (!pollingRef.current) return;
    if (!pollingControllerRef.current.recordFailure()) {
      pollingRef.current = false;
      setAggregationTransportFailed(true);
      setAggregationPollingPaused(false);
    }
  }, []);
  const recordAggregationTerminalFailure = useCallback(() => {
    pollingControllerRef.current.terminate();
    pollingRef.current = false;
    setAggregationTransportFailed(true);
    setAggregationPollingPaused(false);
  }, []);

  // Graph APIs

  // Trace Graph Data
  const {
    data: traceGraphData,
    isFetching: traceGraphLoading,
    isPending: traceGraphPending,
    isError: rawTraceGraphError,
    refetch: refetchTraceGraph,
  } = useQuery({
    queryKey: [
      "llm-tracing-graph",
      "trace",
      observeId,
      selectedInterval,
      selectedGraphEvals,
      combinedFilters,
      graphRequestConfig,
    ],
    queryFn: async ({ queryKey, signal }) => {
      const refresh = forceRefreshRef.current;
      forceRefreshRef.current = false;
      let response;
      try {
        response = await runAggregationRequest(
          JSON.stringify(queryKey),
          signal,
          (requestSignal) =>
            axios.post(
              endpoints.project.getTraceGraphData(),
              {
                interval: selectedInterval,
                filters: toBackendFilters(combinedFilters),
                property: "average",
                req_data_config: graphRequestConfig,
                project_id: observeId,
              },
              {
                params: refresh ? { refresh: true } : undefined,
                signal: requestSignal,
              },
            ),
        );
      } catch (error) {
        if (!signal.aborted) recordAggregationFailure();
        throw error;
      }
      let result;
      try {
        result = parseTraceGraphResponse(response.data);
      } catch (error) {
        recordAggregationTerminalFailure();
        throw error;
      }
      recordAggregationResponse(result);
      return result;
    },
    enabled: selectedTab === "trace" && Boolean(selectedGraphConfig?.id),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (query) => {
      // The inactive span query shares this component's polling refs. It must
      // not reset the active trace query's failure counter from its own empty
      // state, otherwise a failed trace poll can run forever.
      if (selectedTab !== "trace") return false;
      const { isRefreshing, refreshFailed } = getAggregationRefreshState(
        query.state.data,
      );
      const readState = getExactAggregationReadState(query.state.data);
      if (
        !isRefreshing ||
        refreshFailed ||
        (readState !== "complete" && readState !== "pending")
      ) {
        pollingControllerRef.current.stop();
        pollingRef.current = false;
        return false;
      }
      // React Query recalculates intervals when a poll starts. Do not spend the
      // next delay budget until the in-flight response records its outcome.
      if (query.state.fetchStatus === "fetching") return false;
      pollingControllerRef.current.start();
      const delay = pollingControllerRef.current.nextDelay();
      if (delay === false) {
        pollingRef.current = false;
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

  // Span Graph Data
  const {
    data: spanGraphData,
    isFetching: spanGraphLoading,
    isPending: spanGraphPending,
    isError: rawSpanGraphError,
    refetch: refetchSpanGraph,
  } = useQuery({
    queryKey: [
      "llm-tracing-graph",
      "span",
      observeId,
      selectedGraphProperty,
      selectedInterval,
      selectedGraphEvals,
      combinedFilters,
      graphRequestConfig,
    ],
    queryFn: async ({ queryKey, signal }) => {
      const refresh = forceRefreshRef.current;
      forceRefreshRef.current = false;
      let response;
      try {
        response = await runAggregationRequest(
          JSON.stringify(queryKey),
          signal,
          (requestSignal) =>
            axios.post(
              endpoints.project.getSpanGraphData(),
              {
                interval: selectedInterval,
                filters: toBackendFilters(combinedFilters),
                property: "average",
                req_data_config: graphRequestConfig,
                project_id: observeId,
              },
              {
                params: refresh ? { refresh: true } : undefined,
                signal: requestSignal,
              },
            ),
        );
      } catch (error) {
        if (!signal.aborted) recordAggregationFailure();
        throw error;
      }
      let result;
      try {
        result = parseTraceGraphResponse(response.data);
      } catch (error) {
        recordAggregationTerminalFailure();
        throw error;
      }
      recordAggregationResponse(result);
      return result;
    },
    enabled: selectedTab === "spans" && Boolean(selectedGraphConfig?.id),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (query) => {
      // Symmetric guard for trace mode; only the selected graph owns the
      // shared retry budget.
      if (selectedTab !== "spans") return false;
      const { isRefreshing, refreshFailed } = getAggregationRefreshState(
        query.state.data,
      );
      const readState = getExactAggregationReadState(query.state.data);
      if (
        !isRefreshing ||
        refreshFailed ||
        (readState !== "complete" && readState !== "pending")
      ) {
        pollingControllerRef.current.stop();
        pollingRef.current = false;
        return false;
      }
      // React Query recalculates intervals when a poll starts. Do not spend the
      // next delay budget until the in-flight response records its outcome.
      if (query.state.fetchStatus === "fetching") return false;
      pollingControllerRef.current.start();
      const delay = pollingControllerRef.current.nextDelay();
      if (delay === false) {
        pollingRef.current = false;
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

  const apiGraphData = selectedTab === "trace" ? traceGraphData : spanGraphData;
  const apiGraphLoading =
    selectedTab === "trace"
      ? traceGraphLoading && traceGraphPending
      : spanGraphLoading && spanGraphPending;
  const rawApiGraphError =
    selectedTab === "trace" ? rawTraceGraphError : rawSpanGraphError;
  const apiGraphError =
    aggregationTransportFailed || (rawApiGraphError && !pollingRef.current);
  const apiGraphReadState = getExactAggregationReadState(apiGraphData, {
    isError: apiGraphError,
  });
  const graphSnapshotKey = useMemo(
    () =>
      JSON.stringify([
        selectedTab,
        observeId,
        selectedInterval,
        combinedFilters,
        selectedGraphConfig,
      ]),
    [
      combinedFilters,
      observeId,
      selectedGraphConfig,
      selectedInterval,
      selectedTab,
    ],
  );
  const [lastExactSnapshot, setLastExactSnapshot] = useState(null);
  const [refreshUnavailable, setRefreshUnavailable] = useState(false);
  const notifyAggregationRefresh = useCallback(
    (refreshing) => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-refresh-state", {
          detail: {
            observeId,
            sourceId: aggregationSourceId,
            refreshing,
          },
        }),
      );
    },
    [aggregationSourceId, observeId],
  );

  useEffect(() => {
    const handleRefresh = (event) => {
      if (
        event?.detail?.observeId &&
        String(event.detail.observeId) !== String(observeId)
      ) {
        return;
      }
      forceRefreshRef.current = true;
      resetAggregationBudget();
      setRefreshUnavailable(false);
      notifyAggregationRefresh(true);
      if (selectedTab === "trace") {
        refetchTraceGraph({ cancelRefetch: true });
      } else {
        refetchSpanGraph({ cancelRefetch: true });
      }
    };
    window.addEventListener("observe-refresh", handleRefresh);
    return () => window.removeEventListener("observe-refresh", handleRefresh);
  }, [
    notifyAggregationRefresh,
    observeId,
    refetchSpanGraph,
    refetchTraceGraph,
    resetAggregationBudget,
    selectedTab,
  ]);

  useEffect(() => {
    return () => notifyAggregationRefresh(false);
  }, [graphSnapshotKey, notifyAggregationRefresh]);

  useEffect(
    () => () => {
      requestGenerationRef.current += 1;
    },
    [],
  );

  useEffect(() => {
    // A terminal client-side transport failure overrides retained
    // query_refreshing metadata. Unlock the shared Reload control so the user
    // can start a fresh exact request.
    if (apiGraphError) {
      setRefreshUnavailable(true);
      notifyAggregationRefresh(false);
      return;
    }
    if (aggregationPollingPaused) {
      setRefreshUnavailable(true);
      notifyAggregationRefresh(false);
      return;
    }
    if (!apiGraphData) return;
    const { isRefreshing, refreshFailed } =
      getAggregationRefreshState(apiGraphData);
    const refreshReadState = getExactAggregationReadState(apiGraphData);
    const completedAt = getQueryCompletedAt(apiGraphData);
    if (apiGraphReadState === "complete") {
      setLastExactSnapshot({
        key: graphSnapshotKey,
        data: apiGraphData,
        updatedAt: completedAt,
      });
    }
    if (
      isRefreshing &&
      !refreshFailed &&
      (refreshReadState === "complete" || refreshReadState === "pending")
    ) {
      setRefreshUnavailable(apiGraphReadState !== "complete");
      notifyAggregationRefresh(true);
      return;
    }
    notifyAggregationRefresh(false);
    if (refreshFailed) {
      setRefreshUnavailable(apiGraphReadState !== "complete");
      return;
    }
    if (apiGraphReadState !== "complete") {
      setRefreshUnavailable(true);
      return;
    }
    setRefreshUnavailable(false);
    if (completedAt) {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-completed", {
          detail: {
            observeId,
            queryCompletedAt: completedAt.toISOString(),
          },
        }),
      );
    }
  }, [
    apiGraphData,
    apiGraphError,
    apiGraphReadState,
    aggregationPollingPaused,
    graphSnapshotKey,
    notifyAggregationRefresh,
    observeId,
  ]);

  const currentExactSnapshot =
    apiGraphData && apiGraphReadState === "complete"
      ? {
          key: graphSnapshotKey,
          data: apiGraphData,
          updatedAt: getQueryCompletedAt(apiGraphData),
        }
      : null;
  const exactSnapshot =
    currentExactSnapshot ||
    (lastExactSnapshot?.key === graphSnapshotKey ? lastExactSnapshot : null);
  const exactGraphData = exactSnapshot?.data;
  const apiGraphRefreshState = getAggregationRefreshState(apiGraphData);
  const apiGraphReadFailed =
    apiGraphError ||
    apiGraphRefreshState.refreshFailed ||
    (Boolean(apiGraphData) &&
      apiGraphReadState !== "complete" &&
      apiGraphReadState !== "pending");
  const graphRequestEnabled = Boolean(selectedGraphConfig?.id);
  const apiGraphReadMessage = !graphRequestEnabled
    ? null
    : apiGraphReadFailed
      ? QUERY_FAILED_RETRY_MESSAGE
      : aggregationPollingPaused
        ? AGGREGATION_POLLING_PAUSED_MESSAGE
        : !exactSnapshot &&
            (apiGraphLoading ||
              refreshUnavailable ||
              apiGraphReadState === "pending" ||
              !apiGraphData)
          ? GRAPH_LOADING_MESSAGE
          : null;
  // Keep one stable cold-load treatment while an exact aggregation moves
  // from the initial request into server-side preparation. A retained exact
  // snapshot still wins during refresh, and terminal/paused states replace
  // the skeleton with their actionable status copy.
  const isColdGraphLoading =
    !exactSnapshot && apiGraphReadMessage === GRAPH_LOADING_MESSAGE;

  const chartData = useMemo(() => {
    const primaryData = [];
    const trafficData = [];

    const evalData = getExactGraphData(exactGraphData);

    for (const item of evalData) {
      if (item.timestamp != null) {
        // Remove timezone suffix to normalize format
        const normalizedTimestamp = item.timestamp.replace(/\+00:00$/, "");

        primaryData.push({
          x: normalizedTimestamp,
          y: item.value == null ? null : Number(item.value),
        });
        trafficData.push({
          x: normalizedTimestamp,
          y: item.primary_traffic == null ? null : Number(item.primary_traffic),
        });
      }
    }

    const baseLineSeriesName = getLineSeriesName(selectedGraphProperty);
    const lineSeriesName = baseLineSeriesName;
    const trafficSeriesName = "Traffic";
    const isEval = selectedGraphConfig?.type === "EVAL";

    const series = [
      {
        name: lineSeriesName,
        type: "line",
        data: primaryData,
        color: lineColor || theme.palette.blue[600],
        group: "apexcharts-axis-0",
      },
    ];

    const yAxis = [
      {
        seriesName: lineSeriesName,
        title: {
          text:
            getYAxisUnit(_.toLower(selectedGraphProperty)) ||
            getYAxisUnit("default"),
          style: isCollapsed
            ? { fontSize: "6px", fontWeight: 400 }
            : { fontSize: "11px", fontWeight: 400 },
        },
        labels: {
          formatter: (val) => formatYAxisValue(val, selectedGraphProperty),
          style: isCollapsed
            ? { fontSize: "6px", fontWeight: 400 }
            : { fontSize: "11px", fontWeight: 400 },
        },
        opposite: false,
        forceNiceScale: true,
      },
    ];

    if (!isEval) {
      series.push({
        name: trafficSeriesName,
        type: "column",
        data: trafficData,
        color: trafficColor,
        group: "apexcharts-axis-1",
      });
      yAxis.push({
        seriesName: trafficSeriesName,
        title: {
          text: "Traffic",
          style: isCollapsed
            ? { fontSize: "6px", fontWeight: 400 }
            : { fontSize: "11px", fontWeight: 400 },
        },
        labels: {
          formatter: (val) => formatYAxisValue(val),
          style: isCollapsed
            ? { fontSize: "6px", fontWeight: 400 }
            : { fontSize: "11px", fontWeight: 400 },
        },
        opposite: true,
      });
    }

    const xAxis = {
      type: "datetime",
      convertedCatToNumeric: false, // include this explicitly
      labels: {
        datetimeUTC: false,
        style: isCollapsed ? { fontSize: "6px" } : { fontSize: "11px" },
        offsetY: -4,
      },
    };

    return {
      series,
      options: {
        chart: {
          id: chartId,
          height: 200,
          type: "line",
          stacked: false,
          background: "transparent",
          foreColor: isDark ? "#a1a1aa" : undefined,
          toolbar: {
            show: false,
          },
          events: {
            zoomed: (_, { xaxis }) => {
              const startDate = format(
                new Date(xaxis.min),
                "yyyy-MM-dd HH:mm:ss",
              );
              const endDate = format(
                new Date(xaxis.max),
                "yyyy-MM-dd HH:mm:ss",
              );
              setDateFilter({
                dateFilter: [startDate, endDate],
                dateOption: "Custom",
              });
            },
          },
        },
        theme: {
          mode: isDark ? "dark" : "light",
        },
        grid: {
          borderColor: isDark ? "#27272a" : theme.palette.divider,
          strokeDashArray: 6,
        },
        stroke: {
          width: 3,
          curve: "smooth",
        },
        plotOptions: {
          bar: {
            columnWidth: "50%",
          },
        },
        dataLabels: {
          enabledOnSeries: [1],
        },
        states: {
          hover: {
            filter: {
              type: "none",
            },
          },
        },
        xaxis: xAxis,
        yaxis: yAxis,
        tooltip: {
          theme: isDark ? "dark" : "light",
          shared: true,
          intersect: false,
        },
        legend: {
          show: false,
          // position: "top",
          // horizontalAlign: "left",
        },
      },
    };
  }, [
    exactGraphData,
    chartId,
    lineColor,
    selectedGraphProperty,
    selectedGraphConfig,
    isCollapsed,
    isDark,
  ]);
  const hasExactGraphPoints = chartData.series.some((series) =>
    series.data.some((point) => point.y != null),
  );

  const handleZoomIn = () => {
    const chart = chartRef.current?.chart;
    if (chart) {
      const xaxis = chart.w.globals.minX;
      const maxX = chart.w.globals.maxX;
      const range = maxX - xaxis;
      chart.zoomX(xaxis + range * 0.1, maxX - range * 0.1);
    }
  };

  const handleZoomOut = () => {
    const chart = chartRef.current?.chart;
    if (chart) {
      const xaxis = chart.w.globals.minX;
      const maxX = chart.w.globals.maxX;
      const range = maxX - xaxis;
      chart.zoomX(xaxis - range * 0.1, maxX + range * 0.1);
    }
  };

  const handleMoveAhead = () => {
    setDateFilter((e) => ({
      dateFilter: [
        add(new Date(e?.dateFilter?.[0]), deltaObject[selectedInterval]),
        add(new Date(e?.dateFilter?.[1]), deltaObject[selectedInterval]),
      ],
      dateOption: "Custom",
    }));
  };

  const handleMoveBack = () => {
    setDateFilter((e) => ({
      dateFilter: [
        sub(new Date(e.dateFilter?.[0]), deltaObject[selectedInterval]),
        sub(new Date(e.dateFilter?.[1]), deltaObject[selectedInterval]),
      ],
      dateOption: "Custom",
    }));
  };

  logger.debug({
    selectedGraphProperty,
    selectedGraphConfig,
    selectedGraphEvals,
    selectedGraphAttributes,
  });

  return (
    <Box
      sx={{
        // height: `${height}px`,
        position: "relative",
        transition: isDragging ? "none" : "height 400ms ease-in-out",
      }}
      // onMouseMove={handleMouseMove}
      // ref={boxRef}
    >
      <Box
        sx={{
          height: "100%",
          overflow: "hidden",
          paddingTop: theme.spacing(2),
          gap: theme.spacing(1),
          flexDirection: "column",
          display: "flex",
          border: "1px solid",
          borderColor: "divider",
          backgroundColor: "background.paper",

          borderRadius: 1,
          paddingX: 1,
          paddingY: "20px",
        }}
      >
        <Box
          sx={{
            padding: "12px",
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            borderRadius: 0.5,
            gap: theme.spacing(1),
            flexDirection: isCollapsed ? "row" : "column",
            display: "flex",
            position: "relative",
          }}
        >
          <Box sx={{ position: "absolute", top: 12, right: 12 }}>
            <SVGColor
              src="/assets/icons/custom/down-chevron.svg"
              sx={{
                width: 24,
                height: 24,
                rotate: isCollapsed ? "0deg" : "180deg",
                cursor: "pointer",
              }}
              onClick={() => setCollapsed(!isCollapsed)}
            />
          </Box>
          <Box
            sx={{
              gap: theme.spacing(2),
              flexDirection: "column",
              display: "flex",
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                pb: 0.5,
                gap: 1,
              }}
            >
              <ShowComponent condition={showCompare}>
                <Box
                  sx={() => {
                    const { tagBackground: bg, tagForeground: text } =
                      getUniqueColorPalette(compareType === "primary" ? 1 : 3);
                    return {
                      width: theme.spacing(3),
                      height: theme.spacing(3.125),
                      borderRadius: theme.spacing(0.5),
                      backgroundColor: bg,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 12,
                      fontWeight: 600,
                      color: text,
                    };
                  }}
                >
                  {compareType === "primary" ? "A" : "B"}
                </Box>
              </ShowComponent>
              <Typography typography="m3" fontWeight="fontWeightMedium">
                {compareType === "primary" ? "Primary" : "Compare"} Graph
              </Typography>
            </Box>

            <ShowComponent
              condition={
                setSelectedGraphEvals !== undefined &&
                setSelectedGraphProperty !== undefined
              }
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <LeftControl
                  onGraphConfigChange={handleGraphConfigChange}
                  selectedGraphEvals={selectedGraphEvals}
                  selectedGraphProperty={selectedGraphProperty}
                  setSelectedGraphEvals={setSelectedGraphEvals}
                  setSelectedGraphProperty={setSelectedGraphProperty}
                  selectedGraphAttributes={selectedGraphAttributes}
                  setSelectedGraphAttributes={setSelectedGraphAttributes}
                />
              </Box>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <Legend series={chartData.series} />
                <ShowComponent
                  condition={selectedGraphProperty && !isCollapsed}
                >
                  <RightControl
                    selectedInterval={selectedInterval}
                    setSelectedInterval={setSelectedInterval}
                    isMoreThan7Days={isMoreThan7Days}
                    isLessThan90Days={isLessThan90Days}
                    disabled={!selectedGraphProperty}
                    // Start of Selection
                    index={index}
                    onZoomIn={handleZoomIn}
                    onZoomOut={handleZoomOut}
                    onMoveAhead={handleMoveAhead}
                    onMoveBack={handleMoveBack}
                  />
                </ShowComponent>
              </Box>
            </ShowComponent>
          </Box>

          <Box
            sx={{
              gap: theme.spacing(1),
              flexDirection: "column",
              display: "flex",
              flex: 1,
              marginRight: isCollapsed ? "30px" : "0px",
              overflow: "hidden",
            }}
          >
            <ShowComponent
              condition={
                !selectedGraphProperty ||
                (!selectedGraphConfig &&
                  !selectedGraphEvals?.length &&
                  !Object.keys(selectedGraphAttributes || {}).length)
              }
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "center",
                  height: "100%",
                  alignItems: "center",
                  flexDirection: "column",
                }}
              >
                <EmptyGraph />
                <Typography
                  fontSize="14px"
                  fontWeight={700}
                  color="text.secondary"
                >
                  View Graph
                </Typography>
                <Typography fontSize="12px" fontWeight={400} color="text.muted">
                  Choose from the filter above to view your graph
                </Typography>
              </Box>
            </ShowComponent>

            <ShowComponent
              condition={
                selectedGraphProperty &&
                (selectedGraphConfig ||
                  selectedGraphEvals?.length > 0 ||
                  Object.keys(selectedGraphAttributes || {}).length > 0) &&
                !apiGraphLoading &&
                Boolean(exactSnapshot) &&
                hasExactGraphPoints
              }
            >
              <ShowComponent condition={Boolean(apiGraphReadMessage)}>
                <Typography
                  role="status"
                  fontSize="11px"
                  color="text.secondary"
                  sx={{ px: 1 }}
                >
                  {apiGraphReadMessage}
                </Typography>
              </ShowComponent>
              <ReactApexChart
                ref={chartRef}
                options={chartData.options}
                series={chartData.series}
                type="line"
                height={isCollapsed ? 124 : 248}
              />
            </ShowComponent>

            <ShowComponent
              condition={
                !apiGraphLoading &&
                Boolean(exactSnapshot) &&
                !hasExactGraphPoints
              }
            >
              <Box
                role="status"
                sx={{
                  minHeight: isCollapsed ? 124 : 248,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "text.secondary",
                }}
              >
                <Typography fontSize="12px">
                  {apiGraphReadMessage ||
                    "No data available for this time range"}
                </Typography>
              </Box>
            </ShowComponent>

            <ShowComponent condition={isColdGraphLoading}>
              <Box
                role="status"
                aria-label={GRAPH_LOADING_MESSAGE}
                sx={{ height: isCollapsed ? 124 : 248 }}
              >
                <GraphSkeleton />
              </Box>
            </ShowComponent>

            <ShowComponent
              condition={
                !isColdGraphLoading && apiGraphReadMessage && !exactSnapshot
              }
            >
              <Box
                role="status"
                sx={{
                  minHeight: isCollapsed ? 124 : 248,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "text.secondary",
                }}
              >
                <Typography fontSize="12px">{apiGraphReadMessage}</Typography>
              </Box>
            </ShowComponent>
          </Box>
        </Box>
        {/* <Box
          sx={{
            position: "absolute",
            bottom: "-12px",
            left: "30px",
            borderRadius: "50%",
            zIndex: 10,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            border: "1px solid",
            borderColor: "divider",
            padding: 0.3,
            cursor: "pointer",
            backgroundColor: "background.paper",
          }}
          onClick={toggleHeight}
        >
          <Iconify
            icon="bi:arrows-collapse"
            width={16}
            sx={{ color: "text.disabled" }}
          />
        </Box>
        <Box
          sx={{
            position: "absolute",
            bottom: "-12px",
            left: "70px",
            borderRadius: "50%",
            zIndex: 10,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            border: "1px solid",
            borderColor: "divider",
            padding: 0.3,
            backgroundColor: isDragging ? "background.neutral" : "background.paper",
            cursor: isDragging ? "grabbing" : "grab",
          }}
          onMouseDown={handleMouseDown}
        >
          <Iconify
            icon="charm:grab-horizontal"
            width={16}
            sx={{ color: "text.disabled", padding: theme.spacing(0.25) }}
          />
        </Box> */}
      </Box>
    </Box>
  );
};

GraphSection.propTypes = {
  selectedTab: PropTypes.string,
  filters: PropTypes.array,
  showCompare: PropTypes.bool,
  selectedGraphProperty: PropTypes.string,
  selectedGraphEvals: PropTypes.array,
  compareType: PropTypes.string,
  setSelectedGraphEvals: PropTypes.func,
  setSelectedGraphProperty: PropTypes.func,
  dateFilter: PropTypes.object,
  selectedGraphAttributes: PropTypes.object,
  setSelectedGraphAttributes: PropTypes.func,
  setDateFilter: PropTypes.func,
  index: PropTypes.number,
  selectedInterval: PropTypes.string,
  setSelectedInterval: PropTypes.func,
  lineColor: PropTypes.string,
  trafficColor: PropTypes.string,
  hasEvalFilter: PropTypes.bool,
};

export default GraphSection;
