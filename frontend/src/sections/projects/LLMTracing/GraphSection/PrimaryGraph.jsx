/* eslint-disable react/prop-types */
/**
 * PrimaryGraph
 *
 * Dual-axis chart:
 *   - Left Y: selected metric (line) — foreground, solid blue
 *   - Right Y: Traffic/Volume (bars) — background, light blue
 *   - X: Time (dates)
 *
 * Metric dropdown shows ALL metrics from the dashboard metrics API:
 * system metrics, evals, annotations — same as what the dashboard module uses.
 */
import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Badge,
  Box,
  Button,
  ButtonBase,
  InputAdornment,
  MenuItem,
  Popover,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import BoundedCursorPaginationControl from "src/components/BoundedCursorPaginationControl";
import ReactApexChart from "react-apexcharts";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import {
  isPropertyCatalogNotReadyError,
  PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
  usePropertyCatalog,
} from "src/hooks/useDashboards";
import {
  format,
  startOfToday,
  startOfTomorrow,
  startOfYesterday,
  sub,
} from "date-fns";
import _ from "lodash";
import GraphSkeleton from "./GraphSkeleton";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";
import { formatDate } from "src/utils/report-utils";
import { toBackendFilters } from "../common";
import { combineGraphFilters } from "./graphFilterUtils";
import {
  AGGREGATION_POLLING_PAUSED_MESSAGE,
  AGGREGATION_REQUEST_TIMEOUT_MS,
  GRAPH_LOADING_MESSAGE,
  QUERY_FAILED_RETRY_MESSAGE,
  createAggregationPollController,
  getAggregationRefreshState,
  getExactAggregationReadState,
  getQueryCompletedAt,
  getRenderableGraphData,
  awaitAggregationRequestWithDeadline,
} from "src/utils/queryReadState";
import { parseTraceGraphResponse } from "src/api/project/observe-contracts";
import {
  PROPERTY_CATALOG_LEGACY_PAGE_SIZE,
  PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
  PROPERTY_CATALOG_STALE_TIME_MS,
} from "src/config/runtime_limits";

// ---------------------------------------------------------------------------
// Map dashboard category → graph API type
// ---------------------------------------------------------------------------
const CATEGORY_TO_TYPE = {
  system_metric: "SYSTEM_METRIC",
  systemMetric: "SYSTEM_METRIC",
  eval_metric: "EVAL",
  evalMetric: "EVAL",
  annotation_metric: "ANNOTATION",
  annotationMetric: "ANNOTATION",
};

// Display labels for grouped headers
const CATEGORY_LABELS = {
  system_metric: "System Metrics",
  eval_metric: "Evals",
  annotation_metric: "Annotations",
};

const GRAPH_METRIC_CATEGORIES = Object.freeze([
  "system_metric",
  "eval_metric",
  "annotation_metric",
]);

// Unit hints for known system metrics
const METRIC_UNITS = {
  latency: "ms",
  cost: "$",
  tokens: "tok",
  error_rate: "%",
  input_tokens: "tok",
  output_tokens: "tok",
};

// Metrics that aren't graphable (string-only filters, counters, etc.)
const EXCLUDED = new Set([
  "project",
  "session_count",
  "user_count",
  "trace_count",
  "span_count",
  "dataset",
  "eval_source",
  "row_count",
  "cell_error_rate",
]);

const CHART_HEIGHT = 140;

const graphMetricIdentity = (metric) =>
  metric?.propertyId || metric?.property_id || metric?.id || "";

const COMPARE_DATE_OPTIONS = [
  { key: "Today", label: "Today" },
  { key: "Yesterday", label: "Yesterday" },
  { key: "7D", label: "Past 7D" },
  { key: "30D", label: "Past 30D" },
  { key: "3M", label: "Past 3M" },
  { key: "6M", label: "Past 6M" },
  { key: "12M", label: "Past 12M" },
  { key: "Custom", label: "Custom range" },
];

// ---------------------------------------------------------------------------
// Hook: fetch metrics from dashboard API (system + eval + annotation)
// ---------------------------------------------------------------------------
function useLegacyGraphMetrics(projectId, transportSource, enabled = true) {
  const query = useInfiniteQuery({
    queryKey: ["graph-metrics", projectId],
    queryFn: async ({ pageParam = 1, signal }) => {
      const { data } = await axios.get(endpoints.dashboard.metrics, {
        params: {
          exclude_custom_attributes: true,
          page: pageParam,
          page_size: PROPERTY_CATALOG_LEGACY_PAGE_SIZE,
          project_ids: projectId,
          per_eval_config: true,
        },
        signal,
        timeout: PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
      });
      return data?.result || { metrics: [], has_more: false, page: pageParam };
    },
    getNextPageParam: (lastPage, _pages, lastPageParam) => {
      if (!lastPage?.has_more) return undefined;
      const currentPage = Number(lastPage.page ?? lastPageParam);
      return Number.isSafeInteger(currentPage) && currentPage >= 1
        ? currentPage + 1
        : undefined;
    },
    initialPageParam: 1,
    enabled: enabled && Boolean(projectId),
    staleTime: PROPERTY_CATALOG_STALE_TIME_MS,
    meta: { errorHandled: true },
  });

  const metrics =
    query.data?.pages.flatMap((page) => page?.metrics || []) || [];
  const currentPage = Number(
    query.data?.pages?.at(-1)?.page ??
      query.data?.pageParams?.at(-1) ??
      query.data?.pages?.length ??
      1,
  );
  return {
    ...query,
    data: buildGraphMetricGroups(metrics, transportSource),
    continuationKey:
      query.hasNextPage && Number.isSafeInteger(currentPage) && currentPage >= 1
        ? `legacy-page:${currentPage + 1}`
        : null,
  };
}

function buildGraphMetricGroups(metrics, transportSource) {
  // Group by category, filter to graphable numeric types.
  const groups = {};

  for (const m of metrics) {
    const cat = m.category;
    const apiType = CATEGORY_TO_TYPE[cat];
    if (!apiType) continue; // skip custom_column, datasets, etc.
    if (EXCLUDED.has(m.name)) continue;

    const metricSources = Array.isArray(m.sources) ? m.sources : [];
    const compatibleSystemSources =
      transportSource === "traces"
        ? ["traces", "spans", "all", "both"]
        : [transportSource, "traces", "spans", "all"];
    const supportsGraphSource =
      !m.source ||
      compatibleSystemSources.includes(m.source) ||
      metricSources.some((source) => compatibleSystemSources.includes(source));
    if (apiType === "SYSTEM_METRIC" && !supportsGraphSource) continue;

    // For system metrics, only include numeric ones (not string filters).
    if (apiType === "SYSTEM_METRIC" && m.type === "string") continue;

    const groupKey = cat
      .replace(/([A-Z])/g, "_$1")
      .toLowerCase()
      .replace(/^_/, "");
    const normalizedKey =
      groupKey === "system_metric"
        ? "system_metric"
        : groupKey === "eval_metric"
          ? "eval_metric"
          : groupKey === "annotation_metric"
            ? "annotation_metric"
            : groupKey;

    if (!groups[normalizedKey]) groups[normalizedKey] = [];
    groups[normalizedKey].push({
      id: m.name,
      propertyId: m.propertyId || m.property_id,
      source: m.source,
      label: m.displayName || m.display_name || _.startCase(m.name),
      unit: METRIC_UNITS[m.name] || "",
      apiType,
      outputType: m.outputType || m.output_type || "",
      dataType: m.type || "number",
    });
  }

  return groups;
}

function useGraphMetrics(projectId, transportSource, enabled = true) {
  const fallbackScopeKey = JSON.stringify([
    "graph-property-catalog",
    projectId || "",
  ]);
  const systemCatalog = usePropertyCatalog({
    category: GRAPH_METRIC_CATEGORIES[0],
    projectIds: projectId ? [projectId] : [],
    source: transportSource,
    perEvalConfig: true,
    role: "metric",
    pageSize: PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
    enabled: enabled && Boolean(projectId),
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: `${fallbackScopeKey}:system_metric`,
  });
  const evalCatalog = usePropertyCatalog({
    category: GRAPH_METRIC_CATEGORIES[1],
    projectIds: projectId ? [projectId] : [],
    source: transportSource,
    perEvalConfig: true,
    role: "metric",
    pageSize: PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
    enabled: enabled && Boolean(projectId),
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: `${fallbackScopeKey}:eval_metric`,
  });
  const annotationCatalog = usePropertyCatalog({
    category: GRAPH_METRIC_CATEGORIES[2],
    projectIds: projectId ? [projectId] : [],
    source: transportSource,
    perEvalConfig: true,
    role: "metric",
    pageSize: PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
    enabled: enabled && Boolean(projectId),
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: `${fallbackScopeKey}:annotation_metric`,
  });
  const catalogs = [systemCatalog, evalCatalog, annotationCatalog];
  const legacyFallbackRequired = catalogs.some(
    (catalog) => catalog.legacyFallbackRequired,
  );
  const legacy = useLegacyGraphMetrics(
    projectId,
    transportSource,
    enabled && legacyFallbackRequired,
  );

  if (legacyFallbackRequired) return legacy;

  const catalogNotReady = catalogs.some((catalog) =>
    isPropertyCatalogNotReadyError(catalog.error),
  );
  const failedCatalog = catalogs.find(
    (catalog) =>
      catalog.isError && !isPropertyCatalogNotReadyError(catalog.error),
  );
  // Advance one category at a time. This keeps each user gesture bounded to
  // one request and prevents an error in one category from advancing another
  // category's cursor behind the user's back.
  const nextCatalogIndex = catalogs.findIndex((catalog) => catalog.hasNextPage);
  const nextCatalog = nextCatalogIndex >= 0 ? catalogs[nextCatalogIndex] : null;
  const nextCategory =
    nextCatalogIndex >= 0 ? GRAPH_METRIC_CATEGORIES[nextCatalogIndex] : null;
  const cursorChainStopped = catalogs.some(
    (catalog) => catalog.cursorChainStopped,
  );
  return {
    data: buildGraphMetricGroups(
      catalogs.flatMap((catalog) => catalog.metrics || []),
      transportSource,
    ),
    fetchNextPage: nextCatalog?.fetchNextPage || (() => Promise.resolve()),
    continuationKey:
      nextCatalog && nextCategory && nextCatalog.continuationKey
        ? `${nextCategory}:${nextCatalog.continuationKey}`
        : null,
    isLoading: catalogs.some((catalog) => catalog.isLoading) || catalogNotReady,
    isError: Boolean(failedCatalog),
    error: failedCatalog?.error || null,
    hasNextPage: Boolean(nextCatalog) && !cursorChainStopped,
    isFetchingNextPage: Boolean(nextCatalog?.isFetchingNextPage),
    isFetchNextPageError: Boolean(
      nextCatalog?.isFetchNextPageError || cursorChainStopped,
    ),
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
const PrimaryGraph = ({
  filters = [],
  extraFilters,
  metricFilters = [],
  dateFilter,
  setDateFilter,
  selectedInterval = "day",
  hasEvalFilter = false,
  lineColorOverride,
  barColorOverride,
  graphLabel = "Primary Graph",
  showDateFilter = false,
  observeIdOverride,
  hasActiveFilter = false,
  onFilterToggle,
  // Optional: override the graph API endpoint (for sessions/users graphs)
  graphEndpoint,
  // Optional: override the default metric (e.g. "session_count" for sessions)
  defaultMetric,
  // Optional: static metric options (instead of fetching from dashboard API)
  staticMetrics,
  // Label used for the traffic (bar) series in the tooltip, e.g. "traces",
  // "spans", "sessions", or "users". Defaults to "traces".
  trafficLabel = "traces",
}) => {
  const { observeId } = useParams();
  const effectiveObserveId = observeIdOverride || observeId;
  // Keep the logical registry namespace (users/spans) distinct from the
  // physical transport adapter (sessions/traces).
  const graphPropertyNamespace = [
    "traces",
    "spans",
    "sessions",
    "users",
  ].includes(trafficLabel)
    ? trafficLabel
    : "traces";
  const graphTransportSource = ["sessions", "users"].includes(trafficLabel)
    ? "sessions"
    : "traces";
  const theme = useTheme();
  const aggregationSourceId = useId();
  const [selectedMetric, setSelectedMetric] = useState(
    defaultMetric || "latency",
  );
  const [pickerAnchor, setPickerAnchor] = useState(null);
  const [pickerSearch, setPickerSearch] = useState("");
  const [dateAnchor, setDateAnchor] = useState(null);
  const [customDateOpen, setCustomDateOpen] = useState(false);
  const dateButtonRef = useRef(null);
  const metricPickerScrollRef = useRef(null);

  const handleDateOptionChange = useCallback(
    (option) => {
      setDateAnchor(null);
      if (!setDateFilter) return;
      if (option === "Custom") {
        setCustomDateOpen(true);
        return;
      }
      let filter = null;
      switch (option) {
        case "Today":
          filter = [formatDate(startOfToday()), formatDate(startOfTomorrow())];
          break;
        case "Yesterday":
          filter = [formatDate(startOfYesterday()), formatDate(startOfToday())];
          break;
        case "7D":
          filter = [
            formatDate(sub(new Date(), { days: 7 })),
            formatDate(startOfTomorrow()),
          ];
          break;
        case "30D":
          filter = [
            formatDate(sub(new Date(), { days: 30 })),
            formatDate(startOfTomorrow()),
          ];
          break;
        case "3M":
          filter = [
            formatDate(sub(new Date(), { months: 3 })),
            formatDate(startOfTomorrow()),
          ];
          break;
        case "6M":
          filter = [
            formatDate(sub(new Date(), { months: 6 })),
            formatDate(startOfTomorrow()),
          ];
          break;
        case "12M":
          filter = [
            formatDate(sub(new Date(), { months: 12 })),
            formatDate(startOfTomorrow()),
          ];
          break;
        default:
          break;
      }
      if (filter)
        setDateFilter((prev) => ({
          ...prev,
          dateFilter: filter,
          dateOption: option,
        }));
    },
    [setDateFilter],
  );

  const pillSx = {
    textTransform: "none",
    fontWeight: 500,
    fontSize: 13,
    fontFamily: "'IBM Plex Sans', sans-serif",
    height: 26,
    border: "1px solid",
    borderColor: "divider",
    borderRadius: "4px",
    color: "text.primary",
    bgcolor: "background.paper",
    px: 1,
    "&:hover": { bgcolor: "background.neutral", borderColor: "text.disabled" },
  };

  // Fetch a bounded catalog page at a time. The picker end sentinel advances
  // each distinct continuation once while it remains visible.
  const {
    data: dynamicMetricGroups,
    fetchNextPage: fetchNextMetricPage,
    hasNextPage: hasNextMetricPage,
    continuationKey: metricContinuationKey,
    isFetchingNextPage: isFetchingNextMetricPage,
    isFetchNextPageError: isNextMetricPageError,
  } = useGraphMetrics(
    effectiveObserveId,
    graphTransportSource,
    !staticMetrics && Boolean(pickerAnchor),
  );
  // Use staticMetrics if provided (for sessions/users), otherwise dynamic
  const metricGroups = staticMetrics || dynamicMetricGroups;

  // Flatten groups into a single options list for lookup
  const allMetrics = useMemo(() => {
    if (!metricGroups) return [];
    return Object.values(metricGroups).flat();
  }, [metricGroups]);

  // Current selected metric definition
  const metricDef = useMemo(
    () =>
      allMetrics.find(
        (m) =>
          graphMetricIdentity(m) === selectedMetric || m.id === selectedMetric,
      ) ||
      allMetrics[0] || {
        id: "latency",
        propertyId: `system_attribute:${graphPropertyNamespace}:latency`,
        source: graphTransportSource,
        label: "Latency",
        unit: "ms",
        apiType: "SYSTEM_METRIC",
      },
    [allMetrics, graphPropertyNamespace, graphTransportSource, selectedMetric],
  );

  const graphPropertyId = useMemo(() => {
    if ((metricDef.apiType || "SYSTEM_METRIC") === "SYSTEM_METRIC") {
      return `system_attribute:${graphPropertyNamespace}:${metricDef.id}`;
    }
    return metricDef.propertyId || metricDef.property_id || "";
  }, [graphPropertyNamespace, metricDef]);

  // A metric selected in one project may not exist in the next one's catalog.
  // Drop it once loaded so the trigger label and picker highlight agree. The
  // catalog-backed picker stores canonical property ids, while legacy entries
  // may still be selected by metric id, so both identities must be accepted.
  useEffect(() => {
    if (!metricGroups || !allMetrics.length) return;
    if (
      !allMetrics.some(
        (m) =>
          graphMetricIdentity(m) === selectedMetric || m.id === selectedMetric,
      )
    ) {
      setSelectedMetric(graphMetricIdentity(metricDef));
    }
  }, [metricGroups, allMetrics, selectedMetric, metricDef]);

  // Filter metrics by search term for the picker
  const filteredGroups = useMemo(() => {
    if (!metricGroups) return {};
    if (!pickerSearch.trim()) return metricGroups;
    const q = pickerSearch.toLowerCase();
    const result = {};
    for (const [key, items] of Object.entries(metricGroups)) {
      const filtered = items.filter(
        (m) =>
          m.label.toLowerCase().includes(q) || m.id.toLowerCase().includes(q),
      );
      if (filtered.length > 0) result[key] = filtered;
    }
    return result;
  }, [metricGroups, pickerSearch]);

  const combinedFilters = useMemo(
    () =>
      combineGraphFilters({
        filters,
        extraFilters,
        metricFilters,
        dateFilter,
        hasEvalFilter,
      }),
    [filters, extraFilters, metricFilters, dateFilter, hasEvalFilter],
  );

  // Fetch graph data
  const apiEndpoint = graphEndpoint || endpoints.project.getTraceGraphData();
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

      // The first HTTP read and any pending-response polls share one visible
      // action wall. The user-facing Refresh path resets this controller.
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
    pollingControllerRef.current.start();
    pollingRef.current = true;
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
  const {
    data: graphData,
    isLoading,
    isError: rawGraphError,
    refetch,
  } = useQuery({
    queryKey: [
      "primary-graph",
      effectiveObserveId,
      selectedMetric,
      // metricDef resolves asynchronously: while the project's scoped catalog
      // loads it is the hardcoded latency fallback, so keying on selectedMetric
      // alone pinned that first response for the real metric — the chart then
      // showed latency data under the eval's name and unit (TH-6787).
      metricDef.id,
      metricDef.apiType,
      selectedInterval,
      combinedFilters,
      apiEndpoint,
      graphPropertyId,
      graphTransportSource,
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
              apiEndpoint,
              {
                interval: selectedInterval,
                filters: toBackendFilters(combinedFilters),
                property: "average",
                req_data_config: {
                  id: metricDef.id,
                  type: metricDef.apiType || "SYSTEM_METRIC",
                  ...(metricDef.outputType && {
                    output_type: metricDef.outputType,
                  }),
                  ...(graphPropertyId && {
                    property_id: graphPropertyId,
                    source: graphTransportSource,
                  }),
                },
                project_id: effectiveObserveId,
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
    enabled: !!effectiveObserveId && !!metricDef.id,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (query) => {
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
  const graphError =
    aggregationTransportFailed || (rawGraphError && !pollingRef.current);
  const graphReadState = graphError
    ? "error"
    : graphData?.queryReadState || getExactAggregationReadState(graphData);
  const snapshotKey = useMemo(
    () =>
      JSON.stringify([
        effectiveObserveId,
        selectedMetric,
        selectedInterval,
        combinedFilters,
        apiEndpoint,
      ]),
    [
      apiEndpoint,
      combinedFilters,
      effectiveObserveId,
      selectedInterval,
      selectedMetric,
    ],
  );
  const [lastExactSnapshot, setLastExactSnapshot] = useState(null);
  const [refreshUnavailable, setRefreshUnavailable] = useState(false);
  const notifyAggregationRefresh = useCallback(
    (refreshing) => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-refresh-state", {
          detail: {
            observeId: effectiveObserveId,
            sourceId: aggregationSourceId,
            refreshing,
          },
        }),
      );
    },
    [aggregationSourceId, effectiveObserveId],
  );

  useEffect(() => {
    const handleRefresh = (event) => {
      if (
        event?.detail?.observeId &&
        String(event.detail.observeId) !== String(effectiveObserveId)
      ) {
        return;
      }
      forceRefreshRef.current = true;
      resetAggregationBudget();
      setRefreshUnavailable(false);
      notifyAggregationRefresh(true);
      refetch({ cancelRefetch: true });
    };
    window.addEventListener("observe-refresh", handleRefresh);
    return () => window.removeEventListener("observe-refresh", handleRefresh);
  }, [
    effectiveObserveId,
    notifyAggregationRefresh,
    refetch,
    resetAggregationBudget,
  ]);

  useEffect(() => {
    return () => notifyAggregationRefresh(false);
  }, [notifyAggregationRefresh, snapshotKey]);

  useEffect(
    () => () => {
      requestGenerationRef.current += 1;
    },
    [],
  );

  useEffect(() => {
    // Client-side retry exhaustion is terminal for this request scope even if
    // the retained server snapshot still says query_refreshing=true. Publish
    // false so ObserveHeader releases Reload for an explicit retry.
    if (graphError) {
      setRefreshUnavailable(true);
      notifyAggregationRefresh(false);
      return;
    }
    if (aggregationPollingPaused) {
      setRefreshUnavailable(true);
      notifyAggregationRefresh(false);
      return;
    }
    if (!graphData) return;
    const { isRefreshing, refreshFailed } =
      getAggregationRefreshState(graphData);
    const refreshReadState = getExactAggregationReadState(graphData);
    const completedAt = getQueryCompletedAt(graphData);
    if (graphReadState === "complete") {
      setLastExactSnapshot({
        key: snapshotKey,
        data: graphData,
        updatedAt: completedAt,
      });
    }
    if (
      isRefreshing &&
      !refreshFailed &&
      (refreshReadState === "complete" || refreshReadState === "pending")
    ) {
      setRefreshUnavailable(graphReadState !== "complete");
      notifyAggregationRefresh(true);
      return;
    }
    notifyAggregationRefresh(false);
    if (refreshFailed) {
      setRefreshUnavailable(graphReadState !== "complete");
      return;
    }
    if (graphReadState !== "complete") {
      setRefreshUnavailable(true);
      return;
    }
    setRefreshUnavailable(false);
    if (completedAt) {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-completed", {
          detail: {
            observeId: effectiveObserveId,
            queryCompletedAt: completedAt.toISOString(),
          },
        }),
      );
    }
  }, [
    effectiveObserveId,
    graphData,
    graphError,
    graphReadState,
    aggregationPollingPaused,
    notifyAggregationRefresh,
    snapshotKey,
  ]);

  // A completed response is already safe to render in this render pass. Do
  // not wait for the persistence effect below: that one-frame gap used to
  // advertise "No data" even when the exact response contained points.
  const currentExactSnapshot =
    graphData && graphReadState === "complete"
      ? {
          key: snapshotKey,
          data: graphData,
          updatedAt: getQueryCompletedAt(graphData),
        }
      : null;
  const retainedExactSnapshot =
    currentExactSnapshot ||
    (lastExactSnapshot?.key === snapshotKey ? lastExactSnapshot : null);
  const displaySnapshot = retainedExactSnapshot;
  const displayGraphData = displaySnapshot?.data;
  const graphRefreshState = getAggregationRefreshState(graphData);
  const graphReadFailed =
    graphError ||
    graphRefreshState.refreshFailed ||
    (Boolean(graphData) &&
      graphReadState !== "complete" &&
      graphReadState !== "pending");
  const graphStatusMessage = graphReadFailed
    ? QUERY_FAILED_RETRY_MESSAGE
    : aggregationPollingPaused
      ? AGGREGATION_POLLING_PAUSED_MESSAGE
      : !displaySnapshot &&
          (isLoading ||
            refreshUnavailable ||
            graphReadState === "pending" ||
            !graphData)
        ? GRAPH_LOADING_MESSAGE
        : null;
  // A cold exact aggregation can move from the transport request into a
  // server-side pending state. Keep the same skeleton throughout that phase
  // so the loader does not visibly change mid-request. Retained snapshots are
  // never covered while refreshing, and terminal/paused states render copy.
  const isColdGraphLoading =
    !displaySnapshot && graphStatusMessage === GRAPH_LOADING_MESSAGE;

  // Parse API data → [{timestamp, value, primary_traffic}, ...]
  const { metricData, trafficData } = useMemo(() => {
    if (!displayGraphData) return { metricData: [], trafficData: [] };

    const items = getRenderableGraphData(displayGraphData);
    const mData = [];
    const tData = [];

    for (const item of items) {
      if (item.timestamp == null) continue;
      const ts = item.timestamp.replace(/\+00:00$/, "");
      mData.push({
        x: new Date(ts).getTime(),
        y: item.value == null ? null : Number(item.value),
      });
      tData.push({
        x: new Date(ts).getTime(),
        y: item.primary_traffic == null ? null : Number(item.primary_traffic),
      });
    }

    return { metricData: mData, trafficData: tData };
  }, [displayGraphData]);

  // Colors — soft blue line over light blue bars (overridable via props)
  const lineColor =
    lineColorOverride ||
    (theme.palette.mode === "dark"
      ? "rgba(147, 160, 245, 0.85)"
      : "rgba(100, 130, 230, 0.70)");
  const barColor =
    barColorOverride ||
    (theme.palette.mode === "dark"
      ? "rgba(147, 130, 220, 0.30)"
      : "rgba(147, 160, 230, 0.25)");

  const metricSeriesName = metricDef.unit
    ? `${metricDef.label} (${metricDef.unit})`
    : metricDef.label;
  const lineSeriesName = metricSeriesName;
  const trafficSeriesName = "Traffic";

  // Series: metric line FIRST (left axis), traffic bars SECOND (right axis)
  const series = useMemo(
    () => [
      { name: lineSeriesName, type: "line", data: metricData },
      {
        name: trafficSeriesName,
        type: "column",
        data: trafficData,
      },
    ],
    [lineSeriesName, metricData, trafficData, trafficSeriesName],
  );

  // Drag-to-zoom → apply as date filter
  const handleZoomed = useCallback(
    (_, { xaxis }) => {
      if (!setDateFilter || !xaxis?.min || !xaxis?.max) return;
      setDateFilter({
        dateFilter: [
          format(new Date(xaxis.min), "yyyy-MM-dd HH:mm:ss"),
          format(new Date(xaxis.max), "yyyy-MM-dd HH:mm:ss"),
        ],
        dateOption: "Custom",
      });
    },
    [setDateFilter],
  );

  // Chart options
  const chartOptions = useMemo(
    () => ({
      chart: {
        type: "line",
        height: CHART_HEIGHT,
        toolbar: { show: false },
        zoom: { enabled: true, type: "x", autoScaleYaxis: true },
        selection: {
          enabled: true,
          type: "x",
          fill: { color: theme.palette.primary.main, opacity: 0.08 },
          stroke: {
            width: 1,
            color: theme.palette.primary.main,
            opacity: 0.3,
            dashArray: 3,
          },
        },
        events: { zoomed: handleZoomed },
        animations: { enabled: false },
        background: "transparent",
        fontFamily: "'IBM Plex Sans', sans-serif",
        parentHeightOffset: 0,
      },
      colors: [lineColor, barColor],
      stroke: {
        width: [1.8, 0],
        curve: "smooth",
      },
      plotOptions: {
        bar: {
          columnWidth: "50%",
          borderRadius: 2,
          borderRadiusApplication: "end",
        },
      },
      fill: {
        type: ["solid", "solid"],
        opacity: [1, 1],
      },
      xaxis: {
        type: "datetime",
        labels: {
          datetimeUTC: false,
          style: { fontSize: "10px", colors: theme.palette.text.disabled },
          datetimeFormatter: {
            year: "yyyy",
            month: "MMM 'yy",
            day: "dMMM",
            hour: "HH:mm",
          },
          rotateAlways: false,
          hideOverlappingLabels: true,
          offsetY: -2,
        },
        axisBorder: { show: false },
        axisTicks: { show: false },
        tooltip: { enabled: false },
      },
      yaxis: [
        {
          seriesName: lineSeriesName,
          opposite: false,
          title: { text: undefined },
          labels: {
            style: { fontSize: "10px", colors: theme.palette.text.disabled },
            formatter: (v) => {
              if (v == null) return "";
              if (v >= 1000000) return `${(v / 1000000).toFixed(1)}M`;
              if (v >= 1000) return `${(v / 1000).toFixed(1)}K`;
              return v % 1 === 0 ? String(v) : v.toFixed(1);
            },
            offsetX: -4,
          },
          min: 0,
          forceNiceScale: true,
          tickAmount: 4,
        },
        {
          seriesName: trafficSeriesName,
          opposite: true,
          title: { text: undefined },
          labels: {
            style: { fontSize: "10px", colors: theme.palette.text.disabled },
            formatter: (v) => (v != null ? Math.round(v).toLocaleString() : ""),
            offsetX: 4,
          },
          min: 0,
          forceNiceScale: true,
          tickAmount: 4,
        },
      ],
      grid: {
        borderColor: theme.palette.divider,
        strokeDashArray: 3,
        xaxis: { lines: { show: false } },
        yaxis: { lines: { show: true } },
        padding: { left: 0, right: 0, top: -8, bottom: 2 },
      },
      legend: { show: false },
      tooltip: {
        shared: true,
        intersect: false,
        theme: theme.palette.mode,
        x: { format: "dd MMM yyyy" },
        y: {
          formatter: (v, { seriesIndex }) => {
            if (v == null) return "-";
            if (seriesIndex === 0) {
              return metricDef.unit
                ? `${v.toFixed(2)} ${metricDef.unit}`
                : v.toFixed(2);
            }
            return `${Math.round(v)} ${trafficLabel}`;
          },
        },
      },
      dataLabels: { enabled: false },
    }),
    [
      metricDef,
      lineSeriesName,
      lineColor,
      barColor,
      theme,
      handleZoomed,
      trafficLabel,
      trafficSeriesName,
    ],
  );

  if (isColdGraphLoading) {
    return (
      <Box
        role="status"
        aria-label={GRAPH_LOADING_MESSAGE}
        sx={{ px: 2, py: 1, height: CHART_HEIGHT + 40 }}
      >
        <GraphSkeleton />
      </Box>
    );
  }

  const hasData = metricData.length > 0;

  // Order of category groups in the dropdown
  const groupOrder = ["system_metric", "eval_metric", "annotation_metric"];

  return (
    <Box sx={{ px: 1.5, pt: 0.5, pb: 0 }}>
      {/* Header row */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          mb: 0,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Typography
            sx={{ fontSize: 13, fontWeight: 600, color: "text.primary" }}
          >
            {graphLabel}
          </Typography>

          {/* Metric picker trigger */}
          <ButtonBase
            data-testid="graph-metric-picker-trigger"
            onClick={(e) => setPickerAnchor(e.currentTarget)}
            sx={{
              height: 26,
              px: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              fontSize: 12,
              gap: 0.5,
              maxWidth: 160,
              "&:hover": { borderColor: "text.disabled" },
            }}
          >
            <Typography noWrap sx={{ fontSize: 12, maxWidth: 120 }}>
              {metricDef.label}
            </Typography>
            <Iconify
              icon="mdi:chevron-down"
              width={14}
              sx={{ flexShrink: 0, color: "text.secondary" }}
            />
          </ButtonBase>

          {/* Metric picker popover */}
          <Popover
            open={Boolean(pickerAnchor)}
            anchorEl={pickerAnchor}
            onClose={() => {
              setPickerAnchor(null);
              setPickerSearch("");
            }}
            anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
            transformOrigin={{ vertical: "top", horizontal: "left" }}
            slotProps={{
              paper: {
                sx: {
                  width: 260,
                  maxHeight: 360,
                  display: "flex",
                  flexDirection: "column",
                  mt: 0.5,
                  borderRadius: "8px",
                },
              },
            }}
          >
            {/* Search */}
            <Box
              sx={{ p: 1, borderBottom: "1px solid", borderColor: "divider" }}
            >
              <TextField
                size="small"
                placeholder="Search metrics..."
                value={pickerSearch}
                onChange={(e) => setPickerSearch(e.target.value)}
                autoFocus
                fullWidth
                slotProps={{
                  input: {
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify
                          icon="mdi:magnify"
                          width={16}
                          sx={{ color: "text.disabled" }}
                        />
                      </InputAdornment>
                    ),
                    sx: { fontSize: 12, height: 32 },
                  },
                }}
              />
            </Box>

            {/* Scrollable grouped list */}
            <Box ref={metricPickerScrollRef} sx={{ overflow: "auto", flex: 1 }}>
              {groupOrder.map((groupKey) => {
                const items = filteredGroups[groupKey];
                if (!items?.length) return null;
                return (
                  <Box key={groupKey}>
                    <Typography
                      sx={{
                        fontSize: 10,
                        fontWeight: 700,
                        color: "text.disabled",
                        textTransform: "uppercase",
                        letterSpacing: 0.5,
                        px: 1.5,
                        pt: 1,
                        pb: 0.25,
                      }}
                    >
                      {CATEGORY_LABELS[groupKey]}
                    </Typography>
                    {items.map((m) => (
                      <ButtonBase
                        key={graphMetricIdentity(m)}
                        onClick={() => {
                          setSelectedMetric(graphMetricIdentity(m));
                          setPickerAnchor(null);
                          setPickerSearch("");
                        }}
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          width: "100%",
                          textAlign: "left",
                          px: 1.5,
                          py: 0.5,
                          gap: 0.5,
                          bgcolor:
                            graphMetricIdentity(m) === selectedMetric ||
                            m.id === selectedMetric
                              ? "action.selected"
                              : "transparent",
                          "&:hover": { bgcolor: "action.hover" },
                        }}
                      >
                        <Typography
                          noWrap
                          sx={{ fontSize: 12, flex: 1, maxWidth: 180 }}
                        >
                          {m.label}
                        </Typography>
                        {m.unit && (
                          <Typography
                            sx={{
                              fontSize: 10,
                              color: "text.disabled",
                              flexShrink: 0,
                            }}
                          >
                            {m.unit}
                          </Typography>
                        )}
                      </ButtonBase>
                    ))}
                  </Box>
                );
              })}
              {Object.keys(filteredGroups).length === 0 && (
                <Typography
                  sx={{
                    fontSize: 12,
                    color: "text.disabled",
                    textAlign: "center",
                    py: 2,
                  }}
                >
                  No metrics found
                </Typography>
              )}
              {!staticMetrics && (
                <BoundedCursorPaginationControl
                  resetKey={JSON.stringify([
                    "primary-graph-metrics",
                    effectiveObserveId || "",
                    graphPropertyNamespace,
                    graphTransportSource,
                  ])}
                  channels={[
                    {
                      channelKey: "primary-graph-metrics",
                      hasNextPage: Boolean(hasNextMetricPage),
                      continuationKey: metricContinuationKey,
                      isFetching: isFetchingNextMetricPage,
                      error: isNextMetricPageError,
                      loadNextPage: fetchNextMetricPage,
                    },
                  ]}
                  rootRef={metricPickerScrollRef}
                  requireUserAdvanceGesture
                  loadingLabel="Loading more metrics…"
                  retryLabel="Retry loading more metrics"
                  errorMessage={
                    "The next metric page failed. Loaded metrics remain available."
                  }
                  testId="primary-graph-metric-pagination-sentinel"
                />
              )}
            </Box>
          </Popover>

          {/* Date + Filter pills — inline on same row in compare mode */}
          {showDateFilter && (
            <>
              <Button
                ref={dateButtonRef}
                variant="outlined"
                size="small"
                startIcon={<Iconify icon="mdi:calendar-outline" width={16} />}
                endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
                onClick={(e) => setDateAnchor(e.currentTarget)}
                sx={pillSx}
              >
                {dateFilter?.dateOption || "Past 7D"}
              </Button>
              <Popover
                open={Boolean(dateAnchor)}
                anchorEl={dateAnchor}
                onClose={() => setDateAnchor(null)}
                anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
                transformOrigin={{ vertical: "top", horizontal: "left" }}
                slotProps={{
                  paper: {
                    sx: { mt: 0.5, borderRadius: "8px", minWidth: 140 },
                  },
                }}
              >
                {COMPARE_DATE_OPTIONS.map((opt) => (
                  <MenuItem
                    key={opt.key}
                    selected={dateFilter?.dateOption === opt.key}
                    onClick={() => handleDateOptionChange(opt.key)}
                    sx={{ fontSize: 13, py: 0.75 }}
                  >
                    {opt.label}
                  </MenuItem>
                ))}
              </Popover>
              <CustomDateRangePicker
                open={customDateOpen}
                onClose={() => setCustomDateOpen(false)}
                anchorEl={dateButtonRef.current}
                setDateFilter={(range) => {
                  setDateFilter?.((prev) => ({
                    ...prev,
                    dateFilter: range,
                    dateOption: "Custom",
                  }));
                  setCustomDateOpen(false);
                }}
                setDateOption={() => {}}
              />
              {onFilterToggle && (
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={
                    hasActiveFilter ? (
                      <Badge variant="dot" color="error" overlap="circular">
                        <Iconify icon="mdi:filter-outline" width={16} />
                      </Badge>
                    ) : (
                      <Iconify icon="mdi:filter-outline" width={16} />
                    )
                  }
                  onClick={(e) => onFilterToggle(e)}
                  sx={pillSx}
                >
                  Filter
                </Button>
              )}
            </>
          )}
        </Box>

        {/* Legend */}
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Box
              sx={{
                width: 12,
                height: 2,
                borderRadius: "1px",
                bgcolor: lineColor,
              }}
            />
            <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
              {lineSeriesName}
            </Typography>
          </Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: "2px",
                bgcolor: barColor,
              }}
            />
            <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
              Traffic
            </Typography>
          </Box>
        </Box>
      </Box>

      {/* Chart */}
      {graphStatusMessage && displaySnapshot ? (
        <Typography
          role="status"
          sx={{ px: 1, fontSize: 11, color: "text.secondary" }}
        >
          {graphStatusMessage}
        </Typography>
      ) : null}
      {hasData ? (
        <Box sx={{ mx: -0.5 }}>
          <ReactApexChart
            options={chartOptions}
            series={series}
            type="line"
            height={CHART_HEIGHT}
          />
        </Box>
      ) : (
        <Box
          sx={{
            height: CHART_HEIGHT,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
            {graphStatusMessage || "No data available for this time range"}
          </Typography>
        </Box>
      )}
    </Box>
  );
};

PrimaryGraph.propTypes = {
  filters: PropTypes.array,
  extraFilters: PropTypes.array,
  metricFilters: PropTypes.array,
  dateFilter: PropTypes.object,
  setDateFilter: PropTypes.func,
  selectedInterval: PropTypes.string,
  hasEvalFilter: PropTypes.bool,
  lineColorOverride: PropTypes.string,
  barColorOverride: PropTypes.string,
  graphLabel: PropTypes.string,
  showDateFilter: PropTypes.bool,
  observeIdOverride: PropTypes.string,
  hasActiveFilter: PropTypes.bool,
  onFilterToggle: PropTypes.func,
};

export default React.memo(PrimaryGraph);
