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
import React, { useCallback, useMemo, useRef, useState } from "react";
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
import ReactApexChart from "react-apexcharts";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
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
import { FILTER_FOR_HAS_EVAL } from "../common";

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
// Hook: fetch all metrics from dashboard API (system + eval + annotation)
// ---------------------------------------------------------------------------
function useGraphMetrics() {
  return useQuery({
    queryKey: ["graph-metrics-all"],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.dashboard.metrics);
      return data?.result?.metrics || [];
    },
    select: (metrics) => {
      // Group by category, filter to graphable numeric types
      const groups = {};

      for (const m of metrics) {
        const cat = m.category;
        const apiType = CATEGORY_TO_TYPE[cat];
        if (!apiType) continue; // skip custom_column, datasets, etc.
        if (EXCLUDED.has(m.name)) continue;

        // For system metrics, only include numeric ones (not string filters)
        if (apiType === "SYSTEM_METRIC" && m.type === "string") continue;

        const groupKey = cat
          .replace(/([A-Z])/g, "_$1")
          .toLowerCase()
          .replace(/^_/, "");
        // Normalize to snake_case key
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
          label: m.displayName || m.display_name || _.startCase(m.name),
          unit: METRIC_UNITS[m.name] || "",
          apiType,
          outputType: m.outputType || m.output_type || "",
          dataType: m.type || "number",
        });
      }

      return groups;
    },
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
const PrimaryGraph = ({
  filters = [],
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
  const theme = useTheme();
  const [selectedMetric, setSelectedMetric] = useState(
    defaultMetric || "latency",
  );
  const [pickerAnchor, setPickerAnchor] = useState(null);
  const [pickerSearch, setPickerSearch] = useState("");
  const [dateAnchor, setDateAnchor] = useState(null);
  const [customDateOpen, setCustomDateOpen] = useState(false);
  const dateButtonRef = useRef(null);

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

  // Fetch all available metrics (system + eval + annotation)
  const { data: dynamicMetricGroups } = useGraphMetrics();
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
      allMetrics.find((m) => m.id === selectedMetric) ||
      allMetrics[0] || {
        id: "latency",
        label: "Latency",
        unit: "ms",
        apiType: "SYSTEM_METRIC",
      },
    [allMetrics, selectedMetric],
  );

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

  // Combine filters with date filter + eval filter
  const combinedFilters = useMemo(() => {
    const base = filters || [];
    const hasDateFilter = base.some((f) => f?.column_id === "created_at");
    const startDate = dateFilter?.dateFilter?.[0];
    const endDate = dateFilter?.dateFilter?.[1];

    const dateEntry =
      !hasDateFilter && startDate && endDate
        ? [
            {
              column_id: "created_at",
              filter_config: {
                filter_type: "datetime",
                filter_op: "between",
                filter_value: [
                  new Date(startDate).toISOString(),
                  new Date(endDate).toISOString(),
                ],
              },
            },
          ]
        : [];

    return [
      ...base,
      ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
      ...dateEntry,
    ];
  }, [filters, dateFilter, hasEvalFilter]);

  // Fetch graph data
  const apiEndpoint = graphEndpoint || endpoints.project.getTraceGraphData();
  const { data: graphData, isLoading } = useQuery({
    queryKey: [
      "primary-graph",
      observeId,
      selectedMetric,
      selectedInterval,
      combinedFilters,
      apiEndpoint,
    ],
    queryFn: () =>
      axios.post(apiEndpoint, {
        interval: selectedInterval,
        filters: combinedFilters,
        property: "average",
        req_data_config: {
          id: metricDef.id,
          type: metricDef.apiType || "SYSTEM_METRIC",
          ...(metricDef.outputType && { output_type: metricDef.outputType }),
        },
        project_id: observeId,
      }),
    select: (d) => d.data?.result,
    enabled: !!observeId && !!metricDef.id,
    staleTime: 30_000,
  });

  // Parse API data → [{timestamp, value, primary_traffic}, ...]
  const { metricData, trafficData } = useMemo(() => {
    if (!graphData) return { metricData: [], trafficData: [] };

    const items = Array.isArray(graphData.data) ? graphData.data : [];
    const mData = [];
    const tData = [];

    for (const item of items) {
      if (item.timestamp == null) continue;
      const ts = item.timestamp.replace(/\+00:00$/, "");
      mData.push({ x: new Date(ts).getTime(), y: item.value ?? 0 });
      tData.push({ x: new Date(ts).getTime(), y: item.primary_traffic ?? 0 });
    }

    return { metricData: mData, trafficData: tData };
  }, [graphData]);

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

  const lineSeriesName = metricDef.unit
    ? `${metricDef.label} (${metricDef.unit})`
    : metricDef.label;

  // Series: metric line FIRST (left axis), traffic bars SECOND (right axis)
  const series = useMemo(
    () => [
      { name: lineSeriesName, type: "line", data: metricData },
      { name: "Traffic", type: "column", data: trafficData },
    ],
    [lineSeriesName, metricData, trafficData],
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
          seriesName: "Traffic",
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
    ],
  );

  if (isLoading) {
    return (
      <Box sx={{ px: 2, py: 1, height: CHART_HEIGHT + 40 }}>
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
            <Box sx={{ overflow: "auto", flex: 1 }}>
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
                        key={m.id}
                        onClick={() => {
                          setSelectedMetric(m.id);
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
            No data available for this time range
          </Typography>
        </Box>
      )}
    </Box>
  );
};

PrimaryGraph.propTypes = {
  filters: PropTypes.array,
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
