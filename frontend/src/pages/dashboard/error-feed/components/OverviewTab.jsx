import React, { useState, useEffect, useMemo, useRef } from "react";
import { SegmentedTabs } from "src/components/tabs/tabs";
import ApexCharts from "apexcharts";
import { format } from "date-fns";
import {
  Box,
  Button,
  Chip,
  Skeleton,
  Stack,
  Typography,
  alpha,
  useTheme,
  Tab,
  Tooltip,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import AgentGraph from "src/sections/projects/LLMTracing/GraphSection/AgentGraph";
import AgentPath from "src/sections/projects/LLMTracing/GraphSection/AgentPath";
import GraphSkeleton from "src/sections/projects/LLMTracing/GraphSection/GraphSkeleton";
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";
import { statusColors } from "src/utils/runStatus";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useErrorFeedOverview } from "src/api/errorFeed/mockAdapter";
import EvalIOPanel from "./EvalIOPanel";
import VoiceEvalPanel from "./VoiceEvalPanel";
import { buildGraphDiff, comparisonGraphForMode } from "./buildGraphDiff";
import { useErrorFeedStore } from "../store";
import { TOKEN_PRICE_USD, TRACE_STATUS } from "../constants";
import {} from "src/sections/simulate-v2/components/textTone";
import {
  clickable,
  focusRing,
} from "src/sections/simulate-v2/components/clickable";

const estimateTraceCost = (inputTokens = 0, outputTokens = 0) =>
  inputTokens * TOKEN_PRICE_USD.INPUT + outputTokens * TOKEN_PRICE_USD.OUTPUT;

// ── Shared section card (collapsible) ────────────────────────────────────────
function SectionCard({
  title,
  icon,
  children,
  noPad,
  collapsible,
  defaultOpen = true,
  badge,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const isOpen = collapsible ? open : true;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        {...clickable(collapsible ? () => setOpen((v) => !v) : undefined, {
          expanded: open,
        })}
        sx={{
          ...focusRing,
          px: 1.75,
          py: 1.1,
          borderBottom: isOpen ? "1px solid" : "none",
          borderColor: "divider",
          bgcolor: "action.hover",
          cursor: collapsible ? "pointer" : "default",
          userSelect: "none",
          "&:hover": collapsible ? { bgcolor: "action.hover" } : {},
        }}
      >
        {icon && (
          <Iconify icon={icon} width={14} sx={{ color: "text.disabled" }} />
        )}
        <Typography
          fontSize="12px"
          fontWeight={600}
          color="text.secondary"
          sx={{ textTransform: "none", letterSpacing: 0 }}
        >
          {title}
        </Typography>
        {badge && badge}
        <Box sx={{ flex: 1 }} />
        {collapsible && (
          <Iconify
            icon={
              isOpen
                ? "solar:alt-arrow-up-linear"
                : "solar:alt-arrow-down-linear"
            }
            width={16}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
        )}
      </Stack>
      {isOpen && <Box sx={noPad ? {} : { p: 1.75 }}>{children}</Box>}
    </Box>
  );
}
SectionCard.propTypes = {
  title: PropTypes.string,
  badge: PropTypes.node,
  icon: PropTypes.string,
  children: PropTypes.node,
  noPad: PropTypes.bool,
  collapsible: PropTypes.bool,
  defaultOpen: PropTypes.bool,
};

// ── SparkRow ─────────────────────────────────────────────────────────────────
function SparkRow({
  label,
  total,
  seriesData,
  color,
  borderBottom,
  showXAxis,
}) {
  const chartRef = useRef(null);
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const gridColor = theme.palette.divider;
  const axisColor = theme.palette.text.disabled;
  const labelColor = theme.palette.text.tertiary;
  const tooltipBg = theme.palette.background.paper;
  const tooltipBdr = theme.palette.border.default;
  const tooltipTxt = theme.palette.text.primary;
  const peakVal = seriesData.length
    ? Math.max(...seriesData.map((d) => d.y))
    : 0;
  const fmtN = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n));

  const CHART_H = showXAxis ? 52 : 44;

  useEffect(() => {
    if (!seriesData?.length || !chartRef.current) return;
    const opts = {
      chart: {
        type: "bar",
        height: CHART_H,
        sparkline: { enabled: false },
        toolbar: { show: false },
        background: "transparent",
        animations: { enabled: false },
        zoom: { enabled: false },
        offsetX: 0,
        offsetY: 0,
      },
      series: [{ name: label, data: seriesData }],
      plotOptions: {
        bar: {
          columnWidth: "50%",
          borderRadius: 2,
          borderRadiusApplication: "end",
        },
      },
      colors: [color],
      fill: { opacity: isDark ? 0.72 : 0.88 },
      dataLabels: { enabled: false },
      xaxis: {
        type: "datetime",
        axisBorder: { show: false },
        axisTicks: { show: false },
        labels: showXAxis
          ? {
              show: true,
              style: { fontSize: "12px", colors: axisColor },
              formatter: (val) => format(new Date(val), "MMM d"),
              datetimeUTC: false,
              offsetY: -2,
            }
          : { show: false },
        crosshairs: {
          show: true,
          stroke: { color: gridColor, width: 1, dashArray: 3 },
        },
        tooltip: { enabled: false },
      },
      yaxis: {
        show: false,
        min: 0,
        max: peakVal * 1.15 || 1,
      },
      grid: {
        show: true,
        borderColor: gridColor,
        strokeDashArray: 3,
        xaxis: { lines: { show: false } },
        yaxis: { lines: { show: true } },
        padding: { top: 4, bottom: showXAxis ? 0 : 2, left: 0, right: 0 },
      },
      states: {
        hover: { filter: { type: "lighten", value: 0.08 } },
        active: { filter: { type: "none" } },
      },
      tooltip: {
        enabled: true,
        shared: false,
        followCursor: true,
        custom: ({ series, seriesIndex, dataPointIndex, w }) => {
          const val = series[seriesIndex][dataPointIndex];
          const raw = w.globals.seriesX[seriesIndex][dataPointIndex];
          const dateStr = raw ? format(new Date(raw), "MMM d, yyyy") : "";
          return `<div style="background:${tooltipBg};border:1px solid ${tooltipBdr};border-radius:8px;padding:8px 12px;font-family:Inter,sans-serif;min-width:140px;box-shadow:0 4px 12px rgba(0,0,0,0.25);">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
              <span style="width:8px;height:8px;border-radius:50%;background:${color};display:inline-block;"></span>
              <span style="font-size:12px;color:${tooltipTxt};font-weight:500;">${label}</span>
              <span style="font-size:12px;color:${tooltipTxt};font-weight:600;margin-left:auto;">${val?.toLocaleString() ?? "—"}</span>
            </div>
            <div style="font-size:12px;color:${labelColor};">${dateStr}</div>
          </div>`;
        },
      },
    };
    const chart = new ApexCharts(chartRef.current, opts);
    chart.render();
    return () => {
      try {
        chart.destroy();
      } catch {
        /* */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, peakVal]);

  return (
    <Stack
      direction="row"
      alignItems="center"
      sx={{
        borderBottom: borderBottom ? "1px solid" : "none",
        borderColor: "divider",
      }}
    >
      {/* Left: label + total */}
      <Box
        sx={{
          width: 90,
          flexShrink: 0,
          px: 1.75,
          py: 1,
          borderRight: "1px solid",
          borderColor: "divider",
          alignSelf: "stretch",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
        }}
      >
        <Typography
          fontSize="12px"
          color="text.disabled"
          sx={{ textTransform: "none", letterSpacing: 0, mb: 0.25 }}
        >
          {label}
        </Typography>
        <Typography
          fontSize="16px"
          fontWeight={600}
          color="text.primary"
          sx={{ fontFeatureSettings: "'tnum'", lineHeight: 1 }}
        >
          {total.toLocaleString()}
        </Typography>
      </Box>

      {/* Center: chart */}
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <div ref={chartRef} style={{ width: "100%", height: CHART_H }} />
      </Box>

      {/* Right: peak label */}
      <Typography
        fontSize="12px"
        fontWeight={600}
        color="text.disabled"
        sx={{
          pr: 1.5,
          flexShrink: 0,
          fontFeatureSettings: "'tnum'",
          minWidth: 36,
          textAlign: "right",
        }}
      >
        {fmtN(peakVal)}
      </Typography>
    </Stack>
  );
}
SparkRow.propTypes = {
  label: PropTypes.string.isRequired,
  total: PropTypes.number.isRequired,
  seriesData: PropTypes.array.isRequired,
  color: PropTypes.string.isRequired,
  showXAxis: PropTypes.bool,
  borderBottom: PropTypes.bool,
};

// ── Events & Users over time chart ────────────────────────────────────────────
function EventsUsersChart({
  flat = false,
  data = null,
  deployMarkers: deployMarkersProp = null,
  loading = false,
}) {
  const chartRef = useRef(null);
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const eventsData = data && data.length > 0 ? data : [];
  const deployMarkers = deployMarkersProp ?? [];

  const evtSeries = eventsData.map((d) => ({
    x: new Date(d.date).getTime(),
    y: d.errors,
  }));
  const usersSeries = eventsData.map((d) => ({
    x: new Date(d.date).getTime(),
    y: d.users ?? Math.round(d.errors * 0.37),
  }));
  const totalEvents = eventsData.reduce((s, d) => s + d.errors, 0);
  const totalUsers = eventsData.reduce(
    (s, d) => s + (d.users ?? Math.round(d.errors * 0.37)),
    0,
  );

  const axisLabelColor = theme.palette.text.tertiary;
  const gridColor = theme.palette.divider;
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  const evtColor = isDark ? "#4F8EF7" : "#2563EB";
  const usrColor = isDark ? "rgba(99,155,245,0.18)" : "rgba(147,197,253,0.55)";

  useEffect(() => {
    if (!eventsData?.length || !chartRef.current) return;

    const annotBg = theme.palette.background.neutral;

    const opts = {
      chart: {
        type: "line",
        height: 160,
        toolbar: { show: false },
        background: "transparent",
        animations: { enabled: false },
        zoom: { enabled: false },
      },
      series: [
        { name: "Events", type: "line", data: evtSeries },
        { name: "Users", type: "bar", data: usersSeries },
      ],
      stroke: {
        width: [2.5, 0],
        curve: "smooth",
      },
      colors: [evtColor, usrColor],
      fill: {
        type: ["solid", "solid"],
        opacity: [1, 1],
      },
      plotOptions: {
        bar: { columnWidth: "88%", borderRadius: 0 },
      },
      dataLabels: { enabled: false },
      markers: { size: 0 },
      xaxis: {
        type: "datetime",
        axisBorder: { show: false },
        axisTicks: { show: false },
        labels: {
          style: { fontSize: "12px", colors: axisLabelColor },
          formatter: (val) => format(new Date(val), "MMM d"),
          datetimeUTC: false,
          rotate: 0,
        },
        crosshairs: {
          show: true,
          stroke: { color: gridColor, width: 1, dashArray: 3 },
        },
        tooltip: { enabled: false },
      },
      yaxis: [
        {
          seriesName: "Events",
          tickAmount: 3,
          labels: {
            style: { fontSize: "12px", colors: axisLabelColor },
            formatter: (v) =>
              v >= 1000 ? `${(v / 1000).toFixed(1)}k` : Math.round(v),
            offsetX: -2,
          },
          axisBorder: { show: false },
          axisTicks: { show: false },
        },
        {
          seriesName: "Users",
          opposite: true,
          show: false,
        },
      ],
      grid: {
        borderColor: gridColor,
        strokeDashArray: 3,
        padding: { top: 2, right: 10, bottom: 0, left: 2 },
        xaxis: { lines: { show: false } },
        yaxis: { lines: { show: true } },
      },
      annotations: {
        xaxis: deployMarkers.map((dm) => ({
          x: new Date(dm.date).getTime(),
          borderColor: theme.palette.status.warning.solid,
          borderWidth: 1.5,
          strokeDashArray: 4,
          label: {
            text: dm.version,
            offsetY: 6,
            orientation: "vertical",
            style: {
              color: theme.palette.text.secondary,
              background: annotBg,
              cssClass: "",
              fontSize: "12px",
              fontWeight: 600,
              padding: { top: 2, bottom: 2, left: 4, right: 4 },
            },
          },
        })),
      },
      legend: { show: false },
      tooltip: {
        shared: true,
        intersect: false,
        theme: isDark ? "dark" : "light",
        x: { formatter: (val) => format(new Date(val), "MMM d, yyyy") },
        y: { formatter: (v) => v?.toLocaleString() },
      },
      states: {
        hover: { filter: { type: "none" } },
        active: { filter: { type: "none" } },
      },
    };

    const chart = new ApexCharts(chartRef.current, opts);
    chart.render();
    return () => {
      try {
        chart.destroy();
      } catch {
        /* */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, eventsData.length]);

  const inner = (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        flex: 1,
      }}
    >
      {/* Top: inline stats row */}
      <Stack
        direction="row"
        alignItems="center"
        gap={2.5}
        sx={{ px: 2, pt: 1.25, pb: 0.5, flexShrink: 0 }}
      >
        {[
          { label: "Events", total: totalEvents, color: evtColor },
          { label: "Users", total: totalUsers, color: usrColor },
        ].map((item) => (
          <Stack
            key={item.label}
            direction="row"
            alignItems="baseline"
            gap={0.75}
          >
            <Stack direction="row" alignItems="center" gap={0.5}>
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "2px",
                  bgcolor: item.color,
                  flexShrink: 0,
                }}
              />
              <Typography
                fontSize="12px"
                color="text.disabled"
                sx={{ textTransform: "none", letterSpacing: 0 }}
              >
                {item.label}
              </Typography>
            </Stack>
            {loading ? (
              <Skeleton width={28} height={14} sx={{ borderRadius: "4px" }} />
            ) : (
              <Typography
                fontSize="13px"
                fontWeight={600}
                color="text.primary"
                sx={{ fontFeatureSettings: "'tnum'", lineHeight: 1 }}
              >
                {item.total.toLocaleString()}
              </Typography>
            )}
          </Stack>
        ))}
      </Stack>

      {/* Chart — full width */}
      <Box sx={{ flex: 1, minWidth: 0, px: 0.5 }}>
        {loading ? (
          <Skeleton
            variant="rectangular"
            height={64}
            sx={{ mx: 0.5, my: 1, borderRadius: "4px" }}
          />
        ) : (
          <div ref={chartRef} style={{ width: "100%" }} />
        )}
      </Box>
    </Box>
  );

  if (flat) return inner;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
        display: "flex",
      }}
    >
      {inner}
    </Box>
  );
}
EventsUsersChart.propTypes = {
  flat: PropTypes.bool,
  data: PropTypes.array,
  deployMarkers: PropTypes.array,
  loading: PropTypes.bool,
};

// ── Trace list (left panel) ───────────────────────────────────────────────────
function TraceList({ traces, selectedIndex, onSelect, loading = false }) {
  if (loading) {
    return (
      <Stack gap={0}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Box
            key={i}
            sx={{
              px: 1.5,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Stack direction="row" alignItems="center" gap={0.75} mb={0.4}>
              <Skeleton width="55%" height={11} sx={{ borderRadius: "4px" }} />
              <Box sx={{ flex: 1 }} />
              <Skeleton width={36} height={10} sx={{ borderRadius: "4px" }} />
            </Stack>
            <Stack direction="row" gap={1.5}>
              <Skeleton width={50} height={10} sx={{ borderRadius: "4px" }} />
              <Skeleton width={42} height={10} sx={{ borderRadius: "4px" }} />
              <Skeleton width={34} height={10} sx={{ borderRadius: "4px" }} />
            </Stack>
          </Box>
        ))}
      </Stack>
    );
  }

  return (
    <Stack gap={0}>
      {traces.map((t, i) => {
        const isSelected = i === selectedIndex;
        const time = new Date(t.timestamp).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        });
        /* The trace's own cost when it has one — the number the Traces tab
           and the evidence header show — else an estimate from its tokens. */
        const cost = (
          t.cost ??
          t.summary?.cost ??
          estimateTraceCost(
            t.summary.input_tokens ?? 0,
            t.summary.output_tokens ?? 0,
          )
        ).toFixed(4);

        return (
          <Box
            key={t.id}
            {...clickable(() => onSelect(i), { pressed: isSelected })}
            sx={{
              ...focusRing,
              px: 1.5,
              py: 1,
              cursor: "pointer",
              borderBottom: "1px solid",
              borderColor: "divider",
              bgcolor: isSelected ? "action.selected" : "transparent",
              transition: "background 0.12s",
              "&:hover": {
                bgcolor: isSelected ? "action.focus" : "action.hover",
              },
            }}
          >
            {/* Top row: trace ID + time */}
            <Stack direction="row" alignItems="center" gap={0.75} mb={0.4}>
              <Typography
                fontSize="13px"
                fontWeight={500}
                color="text.primary"
                sx={{ flex: 1, minWidth: 0 }}
                noWrap
              >
                {t.id}
              </Typography>
              <Typography fontSize="12px" color="text.disabled" flexShrink={0}>
                {time}
              </Typography>
            </Stack>

            {/* Input text */}
            <Typography
              fontSize="12px"
              color="text.secondary"
              noWrap
              sx={{ mb: 0.5 }}
            >
              {t.evidence?.input ?? "—"}
            </Typography>

            {/* Bottom row: outcome (and whether it recovered) · latency · cost; tokens are on the evidence header */}
            <Stack direction="row" alignItems="center" gap={1}>
              {t.outcome && (
                <Typography fontSize="12px" noWrap sx={{ flexShrink: 0 }}>
                  <Box
                    component="span"
                    sx={{
                      fontWeight: 600,
                      color: (OUTCOME[t.outcome] || OUTCOME.unclear).color,
                    }}
                  >
                    {(OUTCOME[t.outcome] || OUTCOME.unclear).label}
                  </Box>
                  {t.recovered === false && (
                    <Box component="span" sx={{ color: "text.tertiary" }}>
                      {" · not recovered"}
                    </Box>
                  )}
                  {t.recovered === true && (
                    <Box component="span" sx={{ color: "text.tertiary" }}>
                      {" · recovered"}
                    </Box>
                  )}
                </Typography>
              )}
              <Stack direction="row" alignItems="center" gap={0.3}>
                <Iconify
                  icon="solar:clock-circle-linear"
                  width={12}
                  sx={{ color: "text.disabled" }}
                />
                <Typography fontSize="12px" color="text.disabled">
                  {t.summary.latency_ms}ms
                </Typography>
              </Stack>
              <Stack direction="row" alignItems="center" gap={0.3}>
                <Iconify
                  icon="solar:dollar-minimalistic-linear"
                  width={12}
                  sx={{ color: "text.disabled" }}
                />
                <Typography fontSize="12px" color="text.disabled">
                  ${cost}
                </Typography>
              </Stack>
            </Stack>
          </Box>
        );
      })}
    </Stack>
  );
}
TraceList.propTypes = {
  loading: PropTypes.bool,
  traces: PropTypes.array.isRequired,
  selectedIndex: PropTypes.number.isRequired,
  onSelect: PropTypes.func.isRequired,
};

function renderRichCaption(text) {
  if (!text) return null;
  // Split on **bold** markers; alternate plain / bold spans.
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <Box
          key={i}
          component="span"
          sx={{ fontWeight: 600, color: "text.primary" }}
        >
          {part.slice(2, -2)}
        </Box>
      );
    }
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

function PatternSummary({ summary }) {
  // No stub fallback — un-analyzed clusters show the empty state, never fabricated cards.
  const insights = (summary?.insights ?? []).filter((i) => i && i.title);

  if (!insights.length) return null;

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: `repeat(${Math.min(insights.length, 4)}, 1fr)`,
        gap: 1,
      }}
    >
      {insights.map((insight, i) => (
        <Box
          key={i}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            px: 1.75,
            py: 1.5,
            bgcolor: "action.hover",
            minHeight: 92,
            display: "flex",
            flexDirection: "column",
            gap: 0.6,
          }}
        >
          {insight?.title && (
            <Typography
              fontSize="12px"
              fontWeight={600}
              color="text.disabled"
              sx={{
                textTransform: "none",
                letterSpacing: 0,
                lineHeight: 1.2,
              }}
            >
              {insight.title}
            </Typography>
          )}
          <Typography
            fontSize="16px"
            fontWeight={600}
            color="text.primary"
            sx={{
              lineHeight: 1.1,
              fontFeatureSettings: "'tnum'",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {insight?.value ?? "—"}
          </Typography>
          <Typography
            fontSize="12px"
            color="text.secondary"
            sx={{ lineHeight: 1.45 }}
          >
            {renderRichCaption(insight?.caption ?? "")}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
PatternSummary.propTypes = {
  summary: PropTypes.shape({
    insights: PropTypes.arrayOf(
      PropTypes.shape({
        value: PropTypes.string,
        caption: PropTypes.string,
      }),
    ),
  }),
};

// ── Agent flow from real span tree ────────────────────────────────────────────
function TraceGraphView({ traceId, mode }) {
  const { data, isLoading } = useGetTraceDetail(traceId);
  const spanTree = data?.observation_spans || data?.observationSpans;

  const graphData = useMemo(() => {
    if (!spanTree?.length) return null;
    return buildTraceGraph(spanTree);
  }, [spanTree]);

  if (isLoading) {
    return (
      <Box sx={{ height: 340 }}>
        <GraphSkeleton />
      </Box>
    );
  }

  if (!graphData) {
    return (
      <Typography
        fontSize="12px"
        color="text.disabled"
        sx={{ py: 2, textAlign: "center" }}
      >
        No span data available for this trace
      </Typography>
    );
  }

  return (
    <Box
      sx={{
        height: 340,
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {mode === "path" ? (
        <AgentPath data={graphData} isLoading={false} />
      ) : (
        <AgentGraph data={graphData} isLoading={false} direction="TB" />
      )}
    </Box>
  );
}
TraceGraphView.propTypes = {
  traceId: PropTypes.string,
  mode: PropTypes.oneOf(["graph", "path"]),
};

// ── Split-with-working graph compare ─────────────────────────────────────────
function CompareLegend({ summary }) {
  const items = [
    summary.failed > 0 && {
      color: "status.danger.solid",
      label: `${summary.failed} failed here`,
      badge: "✕",
    },
    summary.missing > 0 && {
      color: "status.success.solid",
      label: `−${summary.missing} skipped path`,
      badge: "−",
    },
    summary.added > 0 && {
      color: "status.danger.solid",
      label: `+${summary.added} extra step${summary.added > 1 ? "s" : ""}`,
      badge: "+",
    },
    summary.regressed > 0 && {
      color: "status.warning.solid",
      label: `${summary.regressed} regressed`,
      badge: "Δ",
    },
    summary.shared > 0 && {
      color: "status.neutral.solid",
      label: `${summary.shared} shared`,
      badge: null,
    },
  ].filter(Boolean);

  if (!items.length) {
    return (
      <Typography fontSize="12px" color="text.disabled">
        No structural differences between the failing and working traces.
      </Typography>
    );
  }

  return (
    <Stack direction="row" alignItems="center" gap={1.25} flexWrap="wrap">
      {items.map((item) => (
        <Stack
          key={item.label}
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ flexShrink: 0 }}
        >
          {item.badge ? (
            <Box
              sx={{
                minWidth: 14,
                height: 14,
                px: 0.4,
                borderRadius: "6px",
                bgcolor: item.color,
                color: "common.white",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                fontWeight: 600,
                fontFamily: "ui-monospace, SFMono-Regular, monospace",
                lineHeight: 1,
              }}
            >
              {item.badge}
            </Box>
          ) : (
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                bgcolor: item.color,
              }}
            />
          )}
          <Typography
            fontSize="12px"
            fontWeight={600}
            color="text.secondary"
            sx={{ whiteSpace: "nowrap" }}
          >
            {item.label}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}
CompareLegend.propTypes = { summary: PropTypes.object.isRequired };

function CompareColumn({ title, role, traceShortId, children }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: `status.${role}.border`,
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: "action.hover",
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{
          px: 1.25,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: `status.${role}.bg`,
        }}
      >
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            bgcolor: `status.${role}.solid`,
          }}
        />
        <Typography
          fontSize="12px"
          fontWeight={600}
          sx={{
            color: `status.${role}.fg`,
            textTransform: "none",
            letterSpacing: 0,
          }}
        >
          {title}
        </Typography>
        {traceShortId && (
          <Typography
            fontSize="12px"
            color="text.disabled"
            sx={{
              ml: 0.25,
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
            }}
          >
            {traceShortId}
          </Typography>
        )}
      </Stack>
      {/* Near-black bg in dark mode so diff halos pop */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          bgcolor: "background.paper",
        }}
      >
        {children}
      </Box>
    </Box>
  );
}
CompareColumn.propTypes = {
  title: PropTypes.string.isRequired,
  role: PropTypes.oneOf(["danger", "success"]).isRequired,
  traceShortId: PropTypes.string,
  children: PropTypes.node,
};

function TraceGraphCompare({ failingTraceId, workingTraceId, mode }) {
  const failQ = useGetTraceDetail(failingTraceId);
  const passQ = useGetTraceDetail(workingTraceId);

  const failGraph = useMemo(() => {
    const tree = failQ.data?.observation_spans || failQ.data?.observationSpans;
    if (!tree?.length) return null;
    return buildTraceGraph(tree);
  }, [failQ.data]);

  const passGraph = useMemo(() => {
    const tree = passQ.data?.observation_spans || passQ.data?.observationSpans;
    if (!tree?.length) return null;
    return buildTraceGraph(tree);
  }, [passQ.data]);

  const { failAnnotated, passAnnotated, summary } = useMemo(() => {
    if (!failGraph || !passGraph) {
      return {
        failAnnotated: failGraph,
        passAnnotated: passGraph,
        summary: { added: 0, missing: 0, regressed: 0, shared: 0 },
      };
    }
    return buildGraphDiff(failGraph, passGraph);
  }, [failGraph, passGraph]);

  const failLoading = !!failingTraceId && failQ.isLoading && !failQ.data;
  const passLoading = !!workingTraceId && passQ.isLoading && !passQ.data;
  const failRenderGraph = comparisonGraphForMode(
    mode,
    failGraph,
    failAnnotated,
  );
  const passRenderGraph = comparisonGraphForMode(
    mode,
    passGraph,
    passAnnotated,
  );

  const NoWorkingNotice = !workingTraceId && (
    <Box
      sx={{
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: "8px",
        py: 3,
        px: 2,
        textAlign: "center",
      }}
    >
      <Typography fontSize="12px" color="text.disabled">
        No matching working trace yet for this issue — backend KNN matching will
        populate it shortly.
      </Typography>
    </Box>
  );

  const renderSide = (graph, loading, label) => {
    if (loading) {
      return (
        <Box sx={{ height: 360 }}>
          <GraphSkeleton />
        </Box>
      );
    }
    if (!graph) {
      return (
        <Stack
          alignItems="center"
          justifyContent="center"
          sx={{ height: 360, p: 2 }}
        >
          <Typography fontSize="12px" color="text.disabled" textAlign="center">
            No span data for {label}.
          </Typography>
        </Stack>
      );
    }
    return (
      <Box sx={{ height: 360 }}>
        {mode === "path" ? (
          <AgentPath data={graph} isLoading={false} />
        ) : (
          <AgentGraph data={graph} isLoading={false} direction="TB" />
        )}
      </Box>
    );
  };

  if (NoWorkingNotice) return NoWorkingNotice;

  return (
    <Stack gap={1.25}>
      {/* Diff summary strip — explains the colour cues on the graph nodes. */}
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          px: 1.25,
          py: 0.85,
        }}
      >
        <CompareLegend summary={summary} />
      </Box>

      {/* Side-by-side graphs */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          gap: 1.25,
          alignItems: "stretch",
        }}
      >
        <CompareColumn
          title="Failing trace"
          role="danger"
          traceShortId={failingTraceId ? failingTraceId.slice(0, 8) : null}
        >
          {renderSide(failRenderGraph, failLoading, "failing trace")}
        </CompareColumn>
        <CompareColumn
          title="Working trace"
          role="success"
          traceShortId={workingTraceId ? workingTraceId.slice(0, 8) : null}
        >
          {renderSide(passRenderGraph, passLoading, "working trace")}
        </CompareColumn>
      </Box>
    </Stack>
  );
}
TraceGraphCompare.propTypes = {
  failingTraceId: PropTypes.string,
  workingTraceId: PropTypes.string,
  mode: PropTypes.oneOf(["graph", "path"]),
};

// ── Trace evidence reel (fail / pass tabs) ───────────────────────────────────
function RichText({ text, isFailReel: _isFailReel }) {
  const theme = useTheme();
  const errorColor = statusColors(theme, "danger");
  const okColor = statusColors(theme, "success");
  const codeColor = statusColors(theme, "info").fg;
  const boldColor = errorColor.fg;

  if (!text) return null;
  if (!Array.isArray(text)) {
    return <>{String(text)}</>;
  }
  return (
    <>
      {text.map((seg, i) => {
        if (seg.code) {
          return (
            <Box
              key={i}
              component="code"
              sx={{
                fontFamily: "ui-monospace, SFMono-Regular, monospace",
                fontSize: "12px",
                color: codeColor,
              }}
            >
              {seg.t}
            </Box>
          );
        }
        if (seg.em) {
          return (
            <Box
              key={i}
              component="em"
              sx={{ fontStyle: "italic", color: "text.secondary" }}
            >
              {seg.t}
            </Box>
          );
        }
        if (seg.b) {
          return (
            <Box
              key={i}
              component="strong"
              sx={{ fontWeight: 600, color: boldColor }}
            >
              {seg.t}
            </Box>
          );
        }
        if (!seg.hl) return <React.Fragment key={i}>{seg.t}</React.Fragment>;
        const hl = seg.hl === "error" ? errorColor : okColor;
        return (
          <Box
            key={i}
            component="span"
            sx={{
              bgcolor: hl.bg,
              color: hl.fg,
              px: "4px",
              py: "1px",
              borderRadius: "4px",
              fontWeight: 500,
              display: "inline",
            }}
          >
            {seg.t}
          </Box>
        );
      })}
    </>
  );
}
RichText.propTypes = {
  text: PropTypes.oneOfType([PropTypes.string, PropTypes.array]).isRequired,
  isFailReel: PropTypes.bool,
};

// Module-scoped (used outside component bodies), so read the palette export
// rather than the theme hook.
const FAIL_COLOR = "status.danger.solid";
const PASS_COLOR = "status.success.solid";

function roleColor(isFailure) {
  return isFailure ? "status.danger.fg" : "text.tertiary";
}

function ReelStep({ step, isFailReel, isLast, index }) {
  const [showRaw, setShowRaw] = useState(false);

  const status = step.status;
  const isFailure = step.isFailure || status === "fail";
  const dotColor = isFailure
    ? FAIL_COLOR
    : status === "pass" || status === "ok"
      ? PASS_COLOR
      : "text.disabled";
  /* The step's own data: what the analyzer saved, else its input and output. */
  const raw =
    step.rawJson ||
    step.raw ||
    (step.wentIn || step.cameOut
      ? { input: step.wentIn ?? null, output: step.cameOut ?? null }
      : null);
  const note = step.note;
  const rColor = roleColor(isFailure);

  const header = (
    <Stack direction="row" alignItems="baseline" gap={0.85}>
      <Box
        component="span"
        sx={{
          flexShrink: 0,
          fontSize: "12px",
          fontWeight: 600,
          textTransform: "none",
          letterSpacing: 0,
          color: rColor,
        }}
      >
        {step.label}
      </Box>
      <Typography
        component="span"
        fontSize="13px"
        color="text.primary"
        sx={{ lineHeight: 1.5, flex: 1, minWidth: 0 }}
      >
        <RichText text={step.text} isFailReel={isFailReel} />
      </Typography>
      {step.meta && (
        <Box
          component="span"
          sx={{
            flexShrink: 0,
            fontSize: "12px",
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
            color: "text.disabled",
            whiteSpace: "nowrap",
          }}
        >
          {step.meta}
        </Box>
      )}
    </Stack>
  );

  return (
    <Box
      data-reel-step={index}
      sx={{ position: "relative", mb: isLast ? 0 : 1.25, scrollMarginTop: 80 }}
    >
      {/* Timeline dot — ! badge for the failure moment, else status dot. */}
      {isFailure ? (
        <Box
          sx={{
            position: "absolute",
            left: "-16px",
            top: "11px",
            width: 13,
            height: 13,
            borderRadius: "50%",
            bgcolor: "background.paper",
            border: "2px solid",
            borderColor: FAIL_COLOR,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "12px",
            fontWeight: 600,
            color: FAIL_COLOR,
            zIndex: 1,
          }}
        >
          !
        </Box>
      ) : (
        <Box
          sx={{
            position: "absolute",
            left: "-16px",
            top: "4px",
            width: 11,
            height: 11,
            borderRadius: "50%",
            bgcolor: "background.paper",
            border: "2px solid",
            borderColor: dotColor,
            zIndex: 1,
          }}
        />
      )}

      {isFailure ? (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "status.danger.border",
            borderRadius: "6px",
            bgcolor: "status.danger.bg",
            px: 1.25,
            py: 0.85,
          }}
        >
          {header}
          {note && (
            <Typography
              component="div"
              fontSize="12px"
              color="text.secondary"
              sx={{ lineHeight: 1.55, mt: 0.6 }}
            >
              <RichText text={note} isFailReel={isFailReel} />
            </Typography>
          )}
          <StepIO step={step} />
          {raw && (
            <StepOpen
              raw={raw}
              open={showRaw}
              onToggle={() => setShowRaw((v) => !v)}
            />
          )}
        </Box>
      ) : (
        <>
          {header}
          <StepIO step={step} />
          {raw && (
            <StepOpen
              raw={raw}
              open={showRaw}
              onToggle={() => setShowRaw((v) => !v)}
            />
          )}
        </>
      )}
    </Box>
  );
}
/* What went into the step and what came out — one line each, the full value on hover. */
function StepIO({ step }) {
  if (!step.wentIn && !step.cameOut) return null;
  const row = (label, value) =>
    value ? (
      <>
        <Typography
          fontSize="12px"
          color="text.disabled"
          sx={{ lineHeight: "20px" }}
        >
          {label}
        </Typography>
        <Tooltip title={value} arrow placement="top-start">
          <Box
            component="code"
            sx={{
              minWidth: 0,
              justifySelf: "start",
              maxWidth: "100%",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              px: 0.75,
              borderRadius: "4px",
              bgcolor: "action.hover",
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              fontSize: "12px",
              lineHeight: "20px",
              color: "text.primary",
            }}
          >
            {value}
          </Box>
        </Tooltip>
      </>
    ) : null;
  return (
    <Box
      sx={{
        mt: 0.6,
        display: "grid",
        gridTemplateColumns: "auto minmax(0, 1fr)",
        columnGap: 1,
        rowGap: 0.25,
        alignItems: "center",
      }}
    >
      {row("Went in", step.wentIn)}
      {row("Came out", step.cameOut)}
    </Box>
  );
}
StepIO.propTypes = { step: PropTypes.object.isRequired };

/* "Open this step": the step's full data, in place. */
function StepOpen({ raw, open, onToggle }) {
  return (
    <>
      <Box
        {...clickable(onToggle, { expanded: open })}
        sx={{
          ...focusRing,
          display: "inline-block",
          fontSize: "12px",
          fontWeight: 500,
          color: "text.secondary",
          mt: 0.5,
          cursor: "pointer",
          userSelect: "none",
          "&:hover": { color: "text.primary", textDecoration: "underline" },
        }}
      >
        {open ? "Hide step" : "Open this step"}
      </Box>
      {open && (
        <Box
          component="pre"
          sx={{
            m: 0,
            mt: 0.5,
            p: 1,
            borderRadius: "6px",
            bgcolor: "action.hover",
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
            fontSize: "12px",
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: "text.secondary",
            maxHeight: 200,
            overflow: "auto",
          }}
        >
          {typeof raw === "string" ? raw : JSON.stringify(raw, null, 2)}
        </Box>
      )}
    </>
  );
}
StepOpen.propTypes = {
  raw: PropTypes.oneOfType([PropTypes.string, PropTypes.object]).isRequired,
  open: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
};

ReelStep.propTypes = {
  index: PropTypes.number,
  step: PropTypes.object.isRequired,
  isFailReel: PropTypes.bool,
  isLast: PropTypes.bool,
};

function BreadcrumbList({ steps, isFailReel }) {
  const theme = useTheme();
  const accent = statusColors(theme, isFailReel ? "danger" : "success").solid;
  if (!steps.length) {
    return (
      <Box sx={{ p: 2, textAlign: "center" }}>
        <Typography fontSize="12px" color="text.disabled">
          No steps available
        </Typography>
      </Box>
    );
  }
  return (
    <Box>
      <Box sx={{ position: "relative", pl: 2, pt: 0.5 }}>
        {/* Vertical timeline line — gradient from the reel accent → fade. */}
        <Box
          sx={{
            position: "absolute",
            left: "5px",
            top: 10,
            bottom: 10,
            width: "2px",
            borderRadius: "1px",
            background: `linear-gradient(${alpha(accent, 0.45)}, ${alpha(accent, 0.08)})`,
          }}
        />
        {steps.map((step, i) => (
          <ReelStep
            key={i}
            index={i}
            step={step}
            isFailReel={isFailReel}
            isLast={i === steps.length - 1}
          />
        ))}
      </Box>
    </Box>
  );
}
BreadcrumbList.propTypes = {
  steps: PropTypes.array.isRequired,
  isFailReel: PropTypes.bool,
};

function ReelColumn({
  title,
  headerMeta,
  role,
  steps,
  isFailReel,
  emptyMessage,
}) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: `status.${role}.border`,
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: "transparent",
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 1.5,
          py: 0.85,
          borderBottom: "1px solid",
          borderColor: `status.${role}.border`,
          bgcolor: `status.${role}.bg`,
        }}
      >
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            bgcolor: `status.${role}.solid`,
            flexShrink: 0,
          }}
        />
        <Typography
          fontSize="12px"
          fontWeight={600}
          sx={{
            color: `status.${role}.fg`,
            textTransform: "none",
            letterSpacing: 0,
          }}
        >
          {title}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {headerMeta && (
          <Typography
            fontSize="12px"
            color="text.disabled"
            sx={{
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              whiteSpace: "nowrap",
            }}
          >
            {headerMeta}
          </Typography>
        )}
      </Stack>
      <Box sx={{ flex: 1, px: 1.25, py: 1 }}>
        {steps.length > 0 ? (
          <BreadcrumbList steps={steps} isFailReel={isFailReel} />
        ) : (
          <Stack
            alignItems="center"
            justifyContent="center"
            sx={{ p: 2.5, gap: 0.5 }}
          >
            <Iconify
              icon="solar:file-search-linear"
              width={16}
              sx={{ color: "text.disabled" }}
            />
            <Typography
              fontSize="12px"
              color="text.disabled"
              sx={{ textAlign: "center" }}
            >
              {emptyMessage}
            </Typography>
          </Stack>
        )}
      </Box>
    </Box>
  );
}
ReelColumn.propTypes = {
  title: PropTypes.string.isRequired,
  headerMeta: PropTypes.string,
  role: PropTypes.oneOf(["danger", "success"]).isRequired,
  steps: PropTypes.array.isRequired,
  isFailReel: PropTypes.bool,
  emptyMessage: PropTypes.string,
};

function ViewModeToggle({ value, onChange }) {
  const options = [
    { v: "breadcrumb", label: "Breadcrumb", icon: "solar:list-linear" },
    { v: "agentgraph", label: "Agent Graph", icon: "solar:graph-linear" },
    { v: "agentpath", label: "Agent Path", icon: "solar:graph-linear" },
  ];
  return (
    <SegmentedTabs value={value} onChange={(_, v) => onChange(v)}>
      {options.map((opt) => (
        <Tab
          key={opt.v}
          value={opt.v}
          label={opt.label}
          icon={<Iconify icon={opt.icon} width={12} />}
          iconPosition="start"
        />
      ))}
    </SegmentedTabs>
  );
}
ViewModeToggle.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

function TabDot({ color, active }) {
  return (
    <Box
      sx={{
        width: 6,
        height: 6,
        borderRadius: "50%",
        bgcolor: color,
        opacity: active ? 1 : 0.5,
      }}
    />
  );
}
TabDot.propTypes = {
  color: PropTypes.string,
  active: PropTypes.bool,
};

function ReelTabs({ value, onChange }) {
  const opts = [
    { v: "fail", label: "Failing", dot: FAIL_COLOR },
    { v: "pass", label: "Working", dot: PASS_COLOR },
  ];
  return (
    <SegmentedTabs value={value} onChange={(_, v) => onChange(v)}>
      {opts.map(({ v, label, dot }) => (
        <Tab
          key={v}
          value={v}
          label={label}
          icon={<TabDot color={dot} active={value === v} />}
          iconPosition="start"
        />
      ))}
    </SegmentedTabs>
  );
}
ReelTabs.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

/* ── This conversation: outcome, recovered, failure type, and its checklist ── */
const OUTCOME = {
  succeeded: { label: "Succeeded", color: "status.success.fg" },
  failed: { label: "Failed", color: "status.danger.fg" },
  unclear: { label: "Unclear", color: "text.secondary" },
};
const CHECK = {
  met: {
    icon: "solar:check-circle-linear",
    color: "status.success.fg",
    label: "Met",
  },
  broken: {
    icon: "solar:close-circle-linear",
    color: "status.danger.fg",
    label: "Broken",
  },
  unclear: {
    icon: "solar:question-circle-linear",
    color: "text.tertiary",
    label: "Unclear",
  },
};
const goToStep = (i) => {
  const el = document.querySelector(`[data-reel-step="${i}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.animate?.([{ opacity: 0.4 }, { opacity: 1 }], { duration: 600 });
};

function ConversationFindings({ trace }) {
  const [all, setAll] = useState(false);
  const list = trace?.checklist || [];
  if (!trace?.outcome && !list.length) return null;
  const n = (k) => list.filter((c) => c.status === k).length;
  const open = list.filter((c) => c.status !== "met");
  const shown = all ? list : open;
  const o = OUTCOME[trace.outcome] || OUTCOME.unclear;
  const labels = [
    trace.recovered == null
      ? null
      : trace.recovered
        ? "Recovered"
        : "Not recovered",
    trace.failure_type,
  ].filter(Boolean);
  return (
    <Box
      sx={{
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      {/* Outcome · recovered · failure type, then the checklist's tally. */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 1.75,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          minWidth: 0,
        }}
      >
        <Typography
          fontSize="12px"
          fontWeight={600}
          color="text.secondary"
          noWrap
        >
          This conversation
        </Typography>
        <Typography fontSize="12px" noWrap sx={{ minWidth: 0 }}>
          <Box component="span" sx={{ color: o.color, fontWeight: 600 }}>
            {o.label}
          </Box>
          {labels.length > 0 && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {` · ${labels.join(" · ")}`}
            </Box>
          )}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {list.length > 0 && (
          <Typography
            fontSize="12px"
            color="text.tertiary"
            noWrap
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {`${n("met")} of ${list.length} met`}
            {n("broken") ? ` · ${n("broken")} broken` : ""}
            {n("unclear") ? ` · ${n("unclear")} unclear` : ""}
          </Typography>
        )}
      </Stack>
      {/* What it was supposed to do — the open items first; the met ones fold away. */}
      {shown.length > 0 && (
        <Stack sx={{ px: 1.75, py: 0.75 }} gap={0.25}>
          {shown.map((c, i) => {
            const k = CHECK[c.status] || CHECK.unclear;
            return (
              <Stack
                key={i}
                direction="row"
                alignItems="center"
                gap={1}
                sx={{ minHeight: 26, minWidth: 0 }}
              >
                <Tooltip title={k.label} arrow>
                  <Box
                    component="span"
                    sx={{ display: "inline-flex", flexShrink: 0 }}
                  >
                    <Iconify icon={k.icon} width={16} sx={{ color: k.color }} />
                  </Box>
                </Tooltip>
                <Typography
                  fontSize="13px"
                  color="text.primary"
                  noWrap
                  sx={{ minWidth: 0 }}
                >
                  {c.text}
                  {c.note && (
                    <Box component="span" sx={{ color: "text.tertiary" }}>
                      {` (${c.note})`}
                    </Box>
                  )}
                </Typography>
                {c.status === "broken" && c.proofStep != null && (
                  <Box
                    {...clickable(() => goToStep(c.proofStep), {
                      role: "link",
                    })}
                    sx={{
                      ...focusRing,
                      flexShrink: 0,
                      fontSize: "12px",
                      fontWeight: 500,
                      color: "text.secondary",
                      cursor: "pointer",
                      "&:hover": {
                        color: "text.primary",
                        textDecoration: "underline",
                      },
                    }}
                  >
                    See proof
                  </Box>
                )}
              </Stack>
            );
          })}
        </Stack>
      )}
      {n("met") > 0 && (
        <Box
          {...clickable(() => setAll((v) => !v), { expanded: all })}
          sx={{
            ...focusRing,
            px: 1.75,
            pb: 1,
            pt: shown.length ? 0 : 1,
            fontSize: "12px",
            fontWeight: 500,
            color: "text.secondary",
            cursor: "pointer",
            "&:hover": { color: "text.primary" },
          }}
        >
          {all
            ? "Show only what's open"
            : `Show all ${list.length} requirements`}
        </Box>
      )}
    </Box>
  );
}
ConversationFindings.propTypes = { trace: PropTypes.object };

function TraceEvidence({ evidence, trace, traceId, workingTraceId }) {
  const [viewMode, setViewMode] = useState("breadcrumb");
  const [activeReel, setActiveReel] = useState("fail");
  const [splitView, setSplitView] = useState(false);

  const supportsSplit =
    viewMode === "breadcrumb" ||
    viewMode === "agentgraph" ||
    viewMode === "agentpath";
  const isGraphMode = viewMode === "agentgraph" || viewMode === "agentpath";

  const failReel = evidence.fail_reel || [];
  const passReel = evidence.pass_reel || [];
  const hasPassing = passReel.length > 0;

  const steps = activeReel === "fail" ? failReel : passReel;
  const isFailActive = activeReel === "fail";
  const isBreadcrumb = viewMode === "breadcrumb";

  const summary = trace?.summary ?? {};
  const tokens =
    (summary.input_tokens ?? 0) + (summary.output_tokens ?? 0) || null;
  const cost =
    summary.cost ??
    (estimateTraceCost(summary.input_tokens ?? 0, summary.output_tokens ?? 0) ||
      null);
  const shortId = traceId ? traceId.slice(0, 8) : null;
  // Fail explicitly — an undefined/loading status must not read as "Failing".
  const isTraceFail = trace?.status === TRACE_STATUS.FAIL;

  const metaItems = [
    shortId && { icon: "solar:graph-linear", text: shortId, mono: true },
    summary.latency_ms != null && {
      icon: "solar:clock-circle-linear",
      text: `${summary.latency_ms}ms`,
    },
    tokens != null && { icon: "solar:notes-linear", text: `${tokens} tok` },
    cost != null && {
      icon: "solar:dollar-minimalistic-linear",
      text: cost.toFixed(4),
    },
  ].filter(Boolean);

  return (
    <Box
      sx={{
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* ── Header — matches the other section headings (icon + uppercase) ── */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1.25}
        flexWrap="wrap"
        sx={{
          px: 1.75,
          py: 1.1,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "action.hover",
        }}
      >
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Iconify
            icon="solar:file-search-linear"
            width={14}
            sx={{ color: "text.disabled" }}
          />
          <Typography
            fontSize="12px"
            fontWeight={600}
            color="text.secondary"
            sx={{ textTransform: "none", letterSpacing: 0 }}
          >
            Trace Evidence
          </Typography>
        </Stack>

        <ViewModeToggle value={viewMode} onChange={setViewMode} />

        <Box sx={{ flex: 1 }} />

        {isBreadcrumb && !splitView && (
          <ReelTabs value={activeReel} onChange={setActiveReel} />
        )}

        {supportsSplit && (
          <Button
            size="small"
            variant="outlined"
            startIcon={
              <Iconify
                icon={
                  splitView
                    ? "solar:list-linear"
                    : "solar:transfer-horizontal-linear"
                }
                width={14}
              />
            }
            onClick={() => setSplitView((v) => !v)}
            sx={{
              // Dark theme: translucent lift avoids flat mismatch against header strip.
              color: "text.primary",
              borderColor: "divider",
              bgcolor: "background.paper",
              "&:hover": {
                borderColor: "text.secondary",
                bgcolor: "action.selected",
              },
            }}
          >
            {splitView ? "Single view" : "Split with working"}
          </Button>
        )}
      </Stack>

      {/* ── Dense trace meta strip (observability style) ── */}
      {metaItems.length > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          gap={1.5}
          flexWrap="wrap"
          sx={{
            px: 2,
            py: 0.85,
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: "action.hover",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={0.4}
            sx={{ flexShrink: 0 }}
          >
            <Box
              sx={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                bgcolor: isTraceFail ? FAIL_COLOR : PASS_COLOR,
              }}
            />
            <Typography
              fontSize="12px"
              fontWeight={600}
              sx={{
                textTransform: "none",
                letterSpacing: 0,
                color: isTraceFail ? FAIL_COLOR : PASS_COLOR,
              }}
            >
              {isTraceFail ? "Failing" : "Passing"}
            </Typography>
          </Stack>
          {metaItems.map((m, i) => (
            <Stack
              key={i}
              direction="row"
              alignItems="center"
              gap={0.4}
              sx={{ flexShrink: 0 }}
            >
              <Iconify
                icon={m.icon}
                width={12}
                sx={{ color: "text.disabled" }}
              />
              <Typography
                fontSize="12px"
                sx={{
                  color: "text.secondary",
                  fontFamily: "ui-monospace, SFMono-Regular, monospace",
                  whiteSpace: "nowrap",
                }}
              >
                {m.text}
              </Typography>
            </Stack>
          ))}
        </Stack>
      )}

      {/* ── Body ── */}
      <Box sx={{ p: 1.75 }}>
        {/* Agent Graph / Agent Path — single trace, or split with a working trace. */}
        {isGraphMode &&
          (traceId ? (
            splitView ? (
              <TraceGraphCompare
                failingTraceId={traceId}
                workingTraceId={workingTraceId}
                mode={viewMode === "agentpath" ? "path" : "graph"}
              />
            ) : (
              <TraceGraphView
                traceId={traceId}
                mode={viewMode === "agentpath" ? "path" : "graph"}
              />
            )
          ) : (
            <Typography
              fontSize="12px"
              color="text.disabled"
              sx={{ py: 2, textAlign: "center" }}
            >
              No trace selected.
            </Typography>
          ))}

        {/* Breadcrumb mode — single reel or side-by-side */}
        {isBreadcrumb &&
          (!splitView ? (
            <BreadcrumbList steps={steps} isFailReel={isFailActive} />
          ) : (
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
                gap: 1.25,
                alignItems: "stretch",
              }}
            >
              <ReelColumn
                title="Failing trace"
                role="danger"
                steps={failReel}
                isFailReel
                emptyMessage="No failing-trace evidence available."
              />
              <ReelColumn
                title="Working trace"
                headerMeta={hasPassing ? "nearest passing run" : undefined}
                role="success"
                steps={passReel}
                isFailReel={false}
                emptyMessage="No matching passing trace found for this issue yet."
              />
            </Box>
          ))}
      </Box>
    </Box>
  );
}
TraceEvidence.propTypes = {
  evidence: PropTypes.object.isRequired,
  trace: PropTypes.object,
  traceId: PropTypes.string,
  workingTraceId: PropTypes.string,
};

// ── Co-occurring issues ───────────────────────────────────────────────────────
function CoOccurringIssues({ issues }) {
  const severityRoles = {
    critical: "danger",
    high: "warning",
    medium: "warning",
    low: "success",
  };

  return (
    <Stack gap={0.5}>
      {issues.map((issue) => {
        const sRole = severityRoles[issue.severity] || "neutral";
        return (
          <Stack
            key={issue.id}
            direction="row"
            alignItems="center"
            gap={1}
            sx={{
              px: 1.25,
              py: 0.85,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              bgcolor: "transparent",
              cursor: "pointer",
              transition: "all 0.15s",
              "&:hover": {
                borderColor: "status.discovery.border",
                bgcolor: "status.discovery.bg",
              },
            }}
          >
            <Stack gap={0} flex={1} minWidth={0}>
              <Typography
                fontSize="12px"
                fontWeight={600}
                color="text.primary"
                noWrap
              >
                {issue.title}
              </Typography>
              <Typography fontSize="12px" color="text.disabled">
                {issue.type}
              </Typography>
            </Stack>
            <Chip
              label={`${Math.round(issue.co_occurrence * 100)}% co-occurrence`}
              size="small"
              sx={{
                bgcolor: `status.${sRole}.bg`,
                color: `status.${sRole}.fg`,
                flexShrink: 0,
              }}
            />
            <Iconify
              icon="solar:alt-arrow-right-linear"
              width={14}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
          </Stack>
        );
      })}
    </Stack>
  );
}
CoOccurringIssues.propTypes = { issues: PropTypes.array.isRequired };

// ── Probable root causes ──────────────────────────────────────────────────────
function RootCauses({ causes }) {
  return (
    <Stack gap={0.75}>
      {causes.map((c) => {
        // Skip description when identical to title (short single-clause strings).
        const hasDistinctDescription =
          c.description && c.description.trim() !== c.title?.trim();
        return (
          <Box
            key={c.rank}
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              p: 1.25,
              bgcolor: "transparent",
            }}
          >
            <Stack direction="row" alignItems="flex-start" gap={1}>
              <Stack gap={0.3} flex={1}>
                <Stack direction="row" alignItems="baseline" gap={0.5}>
                  <Typography
                    fontSize="12px"
                    fontWeight={600}
                    color="text.primary"
                    sx={{ flexShrink: 0 }}
                  >
                    Root cause {c.rank}:
                  </Typography>
                  <Typography
                    fontSize="12px"
                    fontWeight={600}
                    color="text.primary"
                  >
                    {c.title}
                  </Typography>
                </Stack>
                {hasDistinctDescription && (
                  <Typography
                    fontSize="12px"
                    color="text.secondary"
                    sx={{ lineHeight: 1.55 }}
                  >
                    {c.description}
                  </Typography>
                )}
              </Stack>
            </Stack>
          </Box>
        );
      })}
    </Stack>
  );
}
RootCauses.propTypes = { causes: PropTypes.array.isRequired };

// ── Recommendations ──────────────────────────────────────────────────────────
const PRIORITY_META = {
  critical: {
    role: "danger",
    label: "Critical",
    icon: "solar:danger-circle-linear",
  },
  high: {
    role: "warning",
    label: "High",
    icon: "solar:danger-circle-linear",
  },
  medium: {
    role: "info",
    label: "Medium",
    icon: "solar:info-circle-linear",
  },
  low: {
    role: "success",
    label: "Low",
    icon: "solar:check-circle-linear",
  },
};
// Shared sub-heading style inside expanded card
function RecSectionLabel({ icon, label }) {
  return (
    <Stack direction="row" alignItems="center" gap={0.5} mb={0.65}>
      <Iconify icon={icon} width={12} sx={{ color: "text.secondary" }} />
      <Typography
        fontSize="12px"
        fontWeight={600}
        color="text.secondary"
        sx={{ textTransform: "none", letterSpacing: 0 }}
      >
        {label}
      </Typography>
    </Stack>
  );
}
RecSectionLabel.propTypes = { icon: PropTypes.string, label: PropTypes.string };

function RecommendationCard({ rec, rootCauses }) {
  const [expanded, setExpanded] = useState(false);
  const pm = PRIORITY_META[rec.priority] || PRIORITY_META.medium;
  const linkedCause = rootCauses?.find((c) => c.rank === rec.root_cause_link);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* Header row */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          ...focusRing,
          px: 1.5,
          py: 1,
          cursor: "pointer",
          "&:hover": {
            bgcolor: "action.hover",
          },
        }}
        {...clickable(() => setExpanded((v) => !v), { expanded })}
      >
        <Stack flex={1} gap={0} minWidth={0}>
          <Stack direction="row" alignItems="center" gap={0.75}>
            {/* Priority pill */}
            <Box
              sx={{
                px: 0.7,
                py: 0.15,
                borderRadius: "4px",
                flexShrink: 0,
                bgcolor: `status.${pm.role}.bg`,
                border: "1px solid",
                borderColor: `status.${pm.role}.border`,
              }}
            >
              <Typography
                fontSize="12px"
                fontWeight={600}
                sx={{
                  color: `status.${pm.role}.fg`,
                  textTransform: "none",
                  letterSpacing: 0,
                }}
              >
                {pm.label}
              </Typography>
            </Box>
            <Typography
              fontSize="12px"
              fontWeight={600}
              color="text.primary"
              noWrap
            >
              {rec.title}
            </Typography>
          </Stack>
          <Typography
            fontSize="12px"
            color="text.disabled"
            noWrap
            sx={{ mt: 0.2 }}
          >
            {rec.description}
          </Typography>
        </Stack>
        <Iconify
          icon={
            expanded
              ? "solar:alt-arrow-up-linear"
              : "solar:alt-arrow-down-linear"
          }
          width={16}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
      </Stack>

      {/* Expanded body */}
      {expanded && (
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            px: 1.5,
            pt: 1.25,
            pb: 1.5,
          }}
        >
          <Stack gap={1.5}>
            {/* Description */}
            <Box>
              <RecSectionLabel icon="solar:notes-linear" label="Description" />
              <Typography
                fontSize="12px"
                color="text.primary"
                sx={{ lineHeight: 1.65 }}
              >
                {rec.description}
              </Typography>
            </Box>

            {/* Suggested fix */}
            <Box>
              <RecSectionLabel
                icon="solar:toolbox-linear"
                label="Suggested fix"
              />
              <Box
                sx={{
                  px: 1.25,
                  py: 1,
                  borderRadius: "6px",
                  bgcolor: "action.hover",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  fontSize="12px"
                  color="text.primary"
                  sx={{
                    lineHeight: 1.65,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {rec.immediate_fix}
                </Typography>
              </Box>
            </Box>

            {/* Insights */}
            {rec.insights && (
              <Box>
                <RecSectionLabel
                  icon="solar:lightbulb-linear"
                  label="Insights"
                />
                <Typography
                  fontSize="12px"
                  color="text.primary"
                  sx={{ lineHeight: 1.65 }}
                >
                  {rec.insights}
                </Typography>
              </Box>
            )}

            {/* Evidence */}
            {rec.evidence?.length > 0 && (
              <Box>
                <RecSectionLabel
                  icon="solar:magnifier-linear"
                  label="Evidence"
                />
                <Stack gap={0.55}>
                  {rec.evidence.map((e, i) => (
                    <Stack
                      key={i}
                      direction="row"
                      alignItems="flex-start"
                      gap={0.75}
                    >
                      <Box
                        sx={{
                          width: 4,
                          height: 4,
                          borderRadius: "50%",
                          bgcolor: "text.disabled",
                          mt: "6px",
                          flexShrink: 0,
                        }}
                      />
                      <Typography
                        fontSize="12px"
                        color="text.primary"
                        sx={{ lineHeight: 1.6 }}
                      >
                        {e}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              </Box>
            )}

            {/* Root Cause link */}
            {linkedCause && (
              <Box>
                <RecSectionLabel
                  icon="solar:magnifier-linear"
                  label="Root Cause"
                />
                <Stack
                  direction="row"
                  alignItems="flex-start"
                  gap={0.75}
                  sx={{
                    px: 1.25,
                    py: 0.9,
                    borderRadius: "6px",
                    bgcolor: "action.hover",
                    border: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    fontSize="12px"
                    fontWeight={600}
                    color="text.disabled"
                    sx={{ flexShrink: 0, mt: "1px" }}
                  >
                    #{linkedCause.rank}
                  </Typography>
                  <Stack gap={0.2} minWidth={0}>
                    <Typography
                      fontSize="12px"
                      fontWeight={600}
                      color="text.primary"
                      noWrap
                    >
                      {linkedCause.title}
                    </Typography>
                    <Typography
                      fontSize="12px"
                      color="text.secondary"
                      sx={{ lineHeight: 1.55 }}
                    >
                      {linkedCause.description}
                    </Typography>
                  </Stack>
                </Stack>
              </Box>
            )}
          </Stack>
        </Box>
      )}
    </Box>
  );
}
RecommendationCard.propTypes = {
  rec: PropTypes.object.isRequired,
  rootCauses: PropTypes.array,
};

function Recommendations({ recs, rootCauses }) {
  if (!recs?.length) return null;
  return (
    <Stack gap={0.75}>
      {recs.map((rec) => (
        <RecommendationCard key={rec.id} rec={rec} rootCauses={rootCauses} />
      ))}
    </Stack>
  );
}
Recommendations.propTypes = {
  recs: PropTypes.array,
  rootCauses: PropTypes.array,
};

// ── Deep analysis revealed section ───────────────────────────────────────────
function DeepAnalysisResults({ rootCauses, recommendations }) {
  if (!rootCauses?.length && !recommendations?.length) {
    return (
      <Typography fontSize="12px" color="text.disabled" sx={{ py: 1 }}>
        Deep analysis completed but found no issues worth surfacing.
      </Typography>
    );
  }
  return (
    <Stack gap={1.75}>
      {rootCauses?.length > 0 && (
        <SectionCard title="Probable Root Cause" icon="solar:magnifier-linear">
          <RootCauses causes={rootCauses} />
        </SectionCard>
      )}
      {recommendations?.length > 0 && (
        <SectionCard title="Recommendations" icon="solar:lightbulb-linear">
          <Recommendations recs={recommendations} rootCauses={rootCauses} />
        </SectionCard>
      )}
    </Stack>
  );
}
DeepAnalysisResults.propTypes = {
  rootCauses: PropTypes.array,
  recommendations: PropTypes.array,
};

// ── Main OverviewTab ──────────────────────────────────────────────────────────
export default function OverviewTab({ _error: currentError }) {
  const [leftWidth, setLeftWidth] = useState(347);
  const containerRef = useRef(null);
  const isDraggingRef = useRef(false);
  const clusterId = currentError?.cluster_id;
  // A cluster's modality (text vs voice) decides the per-trace surface —
  // the BE derives it from the project's voice agent definition.
  const isVoice = currentError?.modality === "voice";
  const { data: overview, isLoading: isOverviewLoading } =
    useErrorFeedOverview(clusterId);
  const traces = useMemo(
    () => overview?.representative_traces ?? [],
    [overview],
  );
  // BE caps the card list (rep_limit, default 20); full membership lives in
  // the Traces tab.
  const repTotal = overview?.representative_total ?? traces.length;

  // Per-cluster in Zustand so sidebar stays in sync.
  const selectedTraceId = useErrorFeedStore(
    (s) => s.selectedTraceIdByCluster[clusterId] ?? null,
  );
  const setSelectedTraceId = useErrorFeedStore((s) => s.setSelectedTraceId);

  const traceIndex = useMemo(() => {
    if (!traces.length) return 0;
    if (!selectedTraceId) return 0;
    const idx = traces.findIndex((t) => t.id === selectedTraceId);
    return idx >= 0 ? idx : 0;
  }, [traces, selectedTraceId]);
  const trace = traces[traceIndex];

  // Seed store so sidebar reads the same trace the Overview shows at index 0.
  useEffect(() => {
    if (!clusterId) return;
    if (!traces.length) return;
    if (selectedTraceId) return;
    setSelectedTraceId(clusterId, traces[0].id);
  }, [clusterId, traces, selectedTraceId, setSelectedTraceId]);

  const eventsOverTime = overview?.events_over_time ?? null;
  const patternSummary = overview?.pattern_summary ?? null;

  const selectTrace = (i) => {
    const next = traces[i];
    if (next && clusterId) setSelectedTraceId(clusterId, next.id);
  };

  // Draggable divider handlers
  useEffect(() => {
    const onMove = (e) => {
      if (!isDraggingRef.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const newW = Math.min(Math.max(e.clientX - rect.left, 220), 600);
      setLeftWidth(newW);
    };
    const onUp = () => {
      isDraggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, []);

  return (
    <Stack gap={1.5} sx={{ minHeight: 0 }}>
      {/* ── Patterns across the issue ── */}
      {(patternSummary?.insights ?? []).some((i) => i && i.title) && (
        <SectionCard
          title="Patterns across the issue"
          icon="solar:clipboard-linear"
        >
          <PatternSummary summary={patternSummary} />
        </SectionCard>
      )}

      <Box
        ref={containerRef}
        sx={{
          display: "flex",
          gap: 0,
          height: "calc(100vh - 360px)",
          minHeight: 420,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          overflow: "hidden",
          bgcolor: "background.paper",
        }}
      >
        {/* ── LEFT PANEL: chart + trace list ── */}
        <Stack
          sx={{
            width: leftWidth,
            flexShrink: 0,
            overflow: "hidden",
          }}
        >
          {/* Events/Users chart — flat (no own border) */}
          <Box
            sx={{
              flexShrink: 0,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <EventsUsersChart
              flat
              data={eventsOverTime}
              deployMarkers={[]}
              loading={isOverviewLoading && !overview}
            />
          </Box>

          {/* Traces heading */}
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{
              px: 1.5,
              py: 0.75,
              flexShrink: 0,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography fontSize="12px" fontWeight={600} color="text.secondary">
              Traces affected
            </Typography>
            {isOverviewLoading && !overview ? (
              <Skeleton
                width={28}
                height={14}
                sx={{
                  borderRadius: "4px",
                  bgcolor: "action.selected",
                }}
              />
            ) : (
              <Typography
                fontSize="12px"
                fontWeight={600}
                sx={{
                  color: "text.disabled",
                  bgcolor: "action.selected",
                  px: 0.75,
                  py: 0.1,
                  borderRadius: "4px",
                }}
              >
                {repTotal.toLocaleString()}
              </Typography>
            )}
          </Stack>

          {/* Scrollable trace list */}
          <Box sx={{ flex: 1, overflow: "auto" }}>
            <TraceList
              traces={traces}
              selectedIndex={traceIndex}
              onSelect={selectTrace}
              loading={isOverviewLoading && !overview}
            />
            {repTotal > traces.length && (
              <Typography
                fontSize="12px"
                color="text.disabled"
                sx={{ px: 1.5, py: 1, textAlign: "center" }}
              >
                Showing the {traces.length} most recent — all{" "}
                {repTotal.toLocaleString()} are in the Traces tab
              </Typography>
            )}
          </Box>
        </Stack>

        {/* ── DRAG HANDLE ── */}
        <Box
          onMouseDown={(e) => {
            e.preventDefault();
            isDraggingRef.current = true;
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
          }}
          sx={{
            width: 5,
            flexShrink: 0,
            cursor: "col-resize",
            bgcolor: "transparent",
            borderLeft: "1px solid",
            borderColor: "divider",
            position: "relative",
            transition: "background 0.15s",
            "&:hover": {
              bgcolor: "status.discovery.bg",
            },
            "&:hover .drag-dots": { opacity: 1 },
          }}
        >
          {/* Grip dots */}
          <Stack
            className="drag-dots"
            alignItems="center"
            justifyContent="center"
            gap={0.4}
            sx={{
              position: "absolute",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              opacity: 0,
              transition: "opacity 0.15s",
            }}
          >
            {[0, 1, 2, 3, 4].map((i) => (
              <Box
                key={i}
                sx={{
                  width: 3,
                  height: 3,
                  borderRadius: "50%",
                  bgcolor: "action.selected",
                }}
              />
            ))}
          </Stack>
        </Box>

        {/* ── RIGHT PANEL: trace detail ── */}
        <Box sx={{ flex: 1, minWidth: 0, overflow: "auto" }}>
          {isOverviewLoading && !trace ? (
            <Stack gap={1.5} sx={{ p: 1.75 }}>
              <Skeleton
                variant="rectangular"
                height={56}
                sx={{ borderRadius: "6px" }}
              />
              <Skeleton
                variant="rectangular"
                height={140}
                sx={{ borderRadius: "8px" }}
              />
              <Skeleton
                variant="rectangular"
                height={260}
                sx={{ borderRadius: "8px" }}
              />
              <Skeleton
                variant="rectangular"
                height={200}
                sx={{ borderRadius: "8px" }}
              />
            </Stack>
          ) : !trace ? (
            <Stack
              alignItems="center"
              justifyContent="center"
              sx={{ height: "100%", p: 4 }}
            >
              <Iconify
                icon="solar:file-search-linear"
                width={40}
                sx={{ color: "text.disabled", mb: 1.5 }}
              />
              <Typography fontSize="13px" color="text.disabled">
                No trace evidence available for this issue yet.
              </Typography>
            </Stack>
          ) : (
            <Stack gap={1.5} sx={{ p: 1.75 }}>
              <ConversationFindings trace={trace} />
              {currentError?.source === "eval" ? (
                <SectionCard
                  title={isVoice ? "Voice call" : "Input / Output"}
                  icon={isVoice ? "solar:phone-linear" : "solar:code-linear"}
                  collapsible
                >
                  {isVoice ? (
                    <VoiceEvalPanel
                      trace={trace}
                      evalScore={trace?.eval_score}
                      successTraceId={currentError?.success_trace?.trace_id}
                    />
                  ) : (
                    <EvalIOPanel trace={trace} evalScore={trace?.eval_score} />
                  )}
                </SectionCard>
              ) : (
                <TraceEvidence
                  evidence={trace.evidence ?? {}}
                  trace={trace}
                  traceId={trace.id}
                  workingTraceId={currentError?.success_trace?.trace_id}
                />
              )}
            </Stack>
          )}
        </Box>
      </Box>
    </Stack>
  );
}

OverviewTab.propTypes = {
  _error: PropTypes.shape({
    cluster_id: PropTypes.string,
    source: PropTypes.string,
    modality: PropTypes.string,
    success_trace: PropTypes.shape({
      trace_id: PropTypes.string,
    }),
  }),
};
