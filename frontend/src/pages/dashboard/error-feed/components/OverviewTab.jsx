import React, { useState, useEffect, useMemo, useRef } from "react";
import ApexCharts from "apexcharts";
import { format } from "date-fns";
import {
  Box,
  Button,
  Chip,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
  alpha,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import AgentGraph from "src/sections/projects/LLMTracing/GraphSection/AgentGraph";
import AgentPath from "src/sections/projects/LLMTracing/GraphSection/AgentPath";
import GraphSkeleton from "src/sections/projects/LLMTracing/GraphSection/GraphSkeleton";
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";
import { error as errorPalette, success } from "src/theme/palette";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useErrorFeedOverview } from "src/api/errorFeed/error-feed";
import EvalIOPanel from "./EvalIOPanel";
import VoiceEvalPanel from "./VoiceEvalPanel";
import { buildGraphDiff } from "./buildGraphDiff";
import { useErrorFeedStore } from "../store";
import { TOKEN_PRICE_USD, TRACE_STATUS } from "../constants";

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
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [open, setOpen] = useState(defaultOpen);
  const isOpen = collapsible ? open : true;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: isDark ? alpha("#fff", 0.02) : "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        onClick={collapsible ? () => setOpen((v) => !v) : undefined}
        sx={{
          px: 1.75,
          py: 1.1,
          borderBottom: isOpen ? "1px solid" : "none",
          borderColor: "divider",
          bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.018),
          cursor: collapsible ? "pointer" : "default",
          userSelect: "none",
          "&:hover": collapsible
            ? { bgcolor: isDark ? alpha("#fff", 0.04) : alpha("#000", 0.03) }
            : {},
        }}
      >
        {icon && (
          <Iconify icon={icon} width={14} sx={{ color: "text.disabled" }} />
        )}
        <Typography
          fontSize="11px"
          fontWeight={600}
          color="text.secondary"
          sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
        >
          {title}
        </Typography>
        {badge && badge}
        <Box sx={{ flex: 1 }} />
        {collapsible && (
          <Iconify
            icon={isOpen ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={15}
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

  const gridColor = isDark ? "#27272a" : "#E4E2EE";
  const axisColor = isDark ? "#52525b" : "#C4C0D4";
  const labelColor = isDark ? "#71717a" : "#938FA3";
  const tooltipBg = isDark ? "#1c1c1e" : "#ffffff";
  const tooltipBdr = isDark ? "#3f3f46" : "#e4e4e7";
  const tooltipTxt = isDark ? "#f4f4f5" : "#18181b";
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
              style: { fontSize: "9px", colors: axisColor },
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
              <span style="font-size:12px;color:${tooltipTxt};font-weight:700;margin-left:auto;">${val?.toLocaleString() ?? "—"}</span>
            </div>
            <div style="font-size:11px;color:${labelColor};">${dateStr}</div>
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
          fontSize="10px"
          color="text.disabled"
          sx={{ textTransform: "uppercase", letterSpacing: "0.05em", mb: 0.25 }}
        >
          {label}
        </Typography>
        <Typography
          fontSize="18px"
          fontWeight={700}
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
        fontSize="11px"
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

  const axisLabelColor = isDark ? "#71717a" : "#938FA3";
  const gridColor = isDark ? "#27272a" : "#E1DFEC";
  const evtColor = isDark ? "#4F8EF7" : "#2563EB";
  const usrColor = isDark ? "rgba(99,155,245,0.18)" : "rgba(147,197,253,0.55)";

  useEffect(() => {
    if (!eventsData?.length || !chartRef.current) return;

    const annotBg = isDark ? "#111111" : "#f4f4f5";

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
          style: { fontSize: "10px", colors: axisLabelColor },
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
            style: { fontSize: "10px", colors: axisLabelColor },
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
          borderColor: "#F5A623",
          borderWidth: 1.5,
          strokeDashArray: 4,
          label: {
            text: dm.version,
            offsetY: 6,
            orientation: "vertical",
            style: {
              color: "#F5A623",
              background: annotBg,
              cssClass: "",
              fontSize: "9px",
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
                fontSize="10px"
                color="text.disabled"
                sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
              >
                {item.label}
              </Typography>
            </Stack>
            {loading ? (
              <Skeleton width={28} height={14} sx={{ borderRadius: "3px" }} />
            ) : (
              <Typography
                fontSize="13px"
                fontWeight={700}
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
        bgcolor: isDark ? alpha("#fff", 0.02) : "background.paper",
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
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  if (loading) {
    return (
      <Stack gap={0}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Box
            key={i}
            sx={{
              px: 1.5,
              py: 1.1,
              borderBottom: "1px solid",
              borderColor: "divider",
              borderLeft: "3px solid transparent",
            }}
          >
            <Stack direction="row" alignItems="center" gap={0.75} mb={0.4}>
              <Skeleton width="55%" height={11} sx={{ borderRadius: "3px" }} />
              <Box sx={{ flex: 1 }} />
              <Skeleton width={36} height={10} sx={{ borderRadius: "3px" }} />
            </Stack>
            <Stack direction="row" gap={1.5}>
              <Skeleton width={50} height={10} sx={{ borderRadius: "3px" }} />
              <Skeleton width={42} height={10} sx={{ borderRadius: "3px" }} />
              <Skeleton width={34} height={10} sx={{ borderRadius: "3px" }} />
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
        const tokens =
          (t.summary.input_tokens ?? 0) + (t.summary.output_tokens ?? 0);
        const cost = estimateTraceCost(
          t.summary.input_tokens ?? 0,
          t.summary.output_tokens ?? 0,
        ).toFixed(4);

        return (
          <Box
            key={t.id}
            onClick={() => onSelect(i)}
            sx={{
              px: 1.5,
              py: 1.1,
              cursor: "pointer",
              borderBottom: "1px solid",
              borderColor: "divider",
              bgcolor: isSelected
                ? isDark
                  ? alpha("#7857FC", 0.12)
                  : alpha("#7857FC", 0.06)
                : "transparent",
              borderLeft: "3px solid",
              borderLeftColor: isSelected ? "#7857FC" : "transparent",
              transition: "background 0.12s",
              "&:hover": {
                bgcolor: isSelected
                  ? isDark
                    ? alpha("#7857FC", 0.15)
                    : alpha("#7857FC", 0.08)
                  : isDark
                    ? alpha("#fff", 0.04)
                    : alpha("#000", 0.03),
              },
            }}
          >
            {/* Top row: trace ID + time */}
            <Stack direction="row" alignItems="center" gap={0.75} mb={0.4}>
              <Typography
                fontSize="11px"
                fontWeight={600}
                color="text.primary"
                sx={{ flex: 1, minWidth: 0 }}
                noWrap
              >
                {t.id}
              </Typography>
              <Typography fontSize="10px" color="text.disabled" flexShrink={0}>
                {time}
              </Typography>
            </Stack>

            {/* Input text */}
            <Typography
              fontSize="11px"
              color="text.secondary"
              noWrap
              sx={{ mb: 0.5 }}
            >
              {t.evidence?.input ?? "—"}
            </Typography>

            {/* Bottom row: latency · cost · tokens */}
            <Stack direction="row" alignItems="center" gap={1}>
              <Stack direction="row" alignItems="center" gap={0.3}>
                <Iconify
                  icon="mdi:timer-outline"
                  width={11}
                  sx={{ color: "text.disabled" }}
                />
                <Typography fontSize="10px" color="text.disabled">
                  {t.summary.latency_ms}ms
                </Typography>
              </Stack>
              <Stack direction="row" alignItems="center" gap={0.3}>
                <Iconify
                  icon="mdi:currency-usd"
                  width={11}
                  sx={{ color: "text.disabled" }}
                />
                <Typography fontSize="10px" color="text.disabled">
                  ${cost}
                </Typography>
              </Stack>
              <Stack direction="row" alignItems="center" gap={0.3}>
                <Iconify
                  icon="mdi:text-box-outline"
                  width={11}
                  sx={{ color: "text.disabled" }}
                />
                <Typography fontSize="10px" color="text.disabled">
                  {tokens.toLocaleString()} tok
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
          sx={{ fontWeight: 700, color: "text.primary" }}
        >
          {part.slice(2, -2)}
        </Box>
      );
    }
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

function PatternSummary({ summary }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
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
            bgcolor: isDark ? alpha("#fff", 0.03) : alpha("#000", 0.025),
            minHeight: 92,
            display: "flex",
            flexDirection: "column",
            gap: 0.6,
          }}
        >
          {insight?.title && (
            <Typography
              fontSize="10px"
              fontWeight={600}
              color="text.disabled"
              sx={{
                textTransform: "uppercase",
                letterSpacing: "0.07em",
                lineHeight: 1.2,
              }}
            >
              {insight.title}
            </Typography>
          )}
          <Typography
            fontSize="18px"
            fontWeight={700}
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
            fontSize="11.5px"
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
        bgcolor: (theme) =>
          theme.palette.mode === "dark" ? "#111111" : "background.paper",
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
// Diff cues only land on graph mode — AgentPath nodes are too compressed for colored rings.
function CompareLegend({ summary }) {
  const items = [
    summary.failed > 0 && {
      color: "#DB2F2D",
      label: `${summary.failed} failed here`,
      badge: "✕",
    },
    summary.missing > 0 && {
      color: "#5ACE6D",
      label: `−${summary.missing} skipped path`,
      badge: "−",
    },
    summary.added > 0 && {
      color: "#DB2F2D",
      label: `+${summary.added} extra step${summary.added > 1 ? "s" : ""}`,
      badge: "+",
    },
    summary.regressed > 0 && {
      color: "#F5A623",
      label: `${summary.regressed} regressed`,
      badge: "Δ",
    },
    summary.shared > 0 && {
      color: "#9AA3AF",
      label: `${summary.shared} shared`,
      badge: null,
    },
  ].filter(Boolean);

  if (!items.length) {
    return (
      <Typography fontSize="11px" color="text.disabled">
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
                borderRadius: "7px",
                bgcolor: item.color,
                color: "#fff",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 9,
                fontWeight: 700,
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
            fontSize="11px"
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

function CompareColumn({ title, accentColor, traceShortId, children }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: alpha(accentColor, isDark ? 0.35 : 0.4),
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: isDark ? alpha("#fff", 0.015) : alpha("#000", 0.012),
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
          bgcolor: alpha(accentColor, isDark ? 0.1 : 0.06),
        }}
      >
        <Box
          sx={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            bgcolor: accentColor,
          }}
        />
        <Typography
          fontSize="10.5px"
          fontWeight={700}
          sx={{
            color: accentColor,
            textTransform: "uppercase",
            letterSpacing: "0.07em",
          }}
        >
          {title}
        </Typography>
        {traceShortId && (
          <Typography
            fontSize="10px"
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
          bgcolor: (theme) =>
            theme.palette.mode === "dark" ? "#111111" : "background.paper",
        }}
      >
        {children}
      </Box>
    </Box>
  );
}
CompareColumn.propTypes = {
  title: PropTypes.string.isRequired,
  accentColor: PropTypes.string.isRequired,
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
        No matching working trace yet for this cluster — backend KNN matching
        will populate it shortly.
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
          accentColor="#DB2F2D"
          traceShortId={failingTraceId ? failingTraceId.slice(0, 8) : null}
        >
          {renderSide(failAnnotated, failLoading, "failing trace")}
        </CompareColumn>
        <CompareColumn
          title="Working trace"
          accentColor="#5ACE6D"
          traceShortId={workingTraceId ? workingTraceId.slice(0, 8) : null}
        >
          {renderSide(passAnnotated, passLoading, "working trace")}
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
  const isDark = theme.palette.mode === "dark";
  const errorColor = "#DB2F2D";
  const okColor = "#5ACE6D";
  const codeColor = isDark ? "#7DB1FF" : "#2563EB";
  const boldColor = isDark ? "#ffb3b3" : "#c0322f";

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
                fontSize: "11.5px",
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
              sx={{ fontWeight: 700, color: boldColor }}
            >
              {seg.t}
            </Box>
          );
        }
        if (!seg.hl) return <React.Fragment key={i}>{seg.t}</React.Fragment>;
        const color = seg.hl === "error" ? errorColor : okColor;
        return (
          <Box
            key={i}
            component="span"
            sx={{
              bgcolor:
                seg.hl === "error"
                  ? alpha(errorColor, 0.14)
                  : alpha(okColor, 0.14),
              color,
              px: "4px",
              py: "1px",
              borderRadius: "3px",
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
const FAIL_COLOR = errorPalette.main;
const PASS_COLOR = success.main;

function SpanPointer({ pointer }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const blue = isDark ? "#7DB1FF" : "#2563EB";
  return (
    <Tooltip title="Open this span in the trace drawer" arrow>
      <Box
        component="span"
        sx={{
          ml: "auto",
          flexShrink: 0,
          fontSize: "10px",
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          color: blue,
          cursor: "pointer",
          whiteSpace: "nowrap",
          "&:hover": { textDecoration: "underline" },
        }}
      >
        ⌖ {pointer}
      </Box>
    </Tooltip>
  );
}
SpanPointer.propTypes = { pointer: PropTypes.string.isRequired };

function roleColor(isFailure, isDark) {
  if (isFailure) return isDark ? "#ff9a99" : "#c0322f";
  return isDark ? alpha("#fff", 0.42) : alpha("#000", 0.45);
}

function ReelStep({ step, isFailReel, isLast }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [showRaw, setShowRaw] = useState(false);

  const status = step.status;
  const isFailure = step.isFailure || status === "fail";
  const dotColor = isFailure
    ? FAIL_COLOR
    : status === "pass" || status === "ok"
      ? PASS_COLOR
      : isDark
        ? alpha("#fff", 0.28)
        : alpha("#000", 0.28);
  const pointer = step.spanPointer || step.span;
  const raw = step.rawJson || step.raw;
  const note = step.note;
  const rColor = roleColor(isFailure, isDark);

  const header = (
    <Stack direction="row" alignItems="baseline" gap={0.85}>
      <Box
        component="span"
        sx={{
          flexShrink: 0,
          fontSize: "9.5px",
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          color: rColor,
        }}
      >
        {step.label}
      </Box>
      <Typography
        component="span"
        fontSize="12.5px"
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
            fontSize: "10px",
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
            color: "text.disabled",
            whiteSpace: "nowrap",
          }}
        >
          {step.meta}
        </Box>
      )}
      {pointer && <SpanPointer pointer={pointer} />}
    </Stack>
  );

  return (
    <Box sx={{ position: "relative", mb: isLast ? 0 : 1.25 }}>
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
            bgcolor: isDark ? "#0e0e10" : "#fff",
            border: `2px solid ${FAIL_COLOR}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "9px",
            fontWeight: 700,
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
            bgcolor: isDark ? alpha("#fff", 0.06) : "background.paper",
            border: `2px solid ${dotColor}`,
            zIndex: 1,
          }}
        />
      )}

      {isFailure ? (
        <Box
          sx={{
            border: "1px solid",
            borderColor: alpha(FAIL_COLOR, 0.4),
            borderRadius: "6px",
            bgcolor: alpha(FAIL_COLOR, isDark ? 0.07 : 0.05),
            px: 1.25,
            py: 0.85,
          }}
        >
          {header}
          {note && (
            <Typography
              component="div"
              fontSize="11.5px"
              color="text.secondary"
              sx={{ lineHeight: 1.55, mt: 0.6 }}
            >
              <RichText text={note} isFailReel={isFailReel} />
            </Typography>
          )}
        </Box>
      ) : (
        <>
          {header}
          {raw && (
            <Box
              onClick={() => setShowRaw((v) => !v)}
              sx={{
                fontSize: "10.5px",
                color: "text.disabled",
                mt: 0.4,
                cursor: "pointer",
                userSelect: "none",
                "&:hover": { color: "text.secondary" },
              }}
            >
              {showRaw ? "− raw JSON" : "+ raw JSON ▾"}
            </Box>
          )}
          {raw && showRaw && (
            <Box
              component="pre"
              sx={{
                m: 0,
                mt: 0.5,
                p: 1,
                borderRadius: "6px",
                bgcolor: isDark ? alpha("#fff", 0.03) : alpha("#000", 0.03),
                fontFamily: "ui-monospace, SFMono-Regular, monospace",
                fontSize: "11px",
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
      )}
    </Box>
  );
}
ReelStep.propTypes = {
  step: PropTypes.object.isRequired,
  isFailReel: PropTypes.bool,
  isLast: PropTypes.bool,
};

function BreadcrumbList({ steps, isFailReel, showFooter = true }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const accent = isFailReel ? FAIL_COLOR : PASS_COLOR;
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
            background: `linear-gradient(${alpha(accent, isDark ? 0.5 : 0.4)}, ${alpha(
              accent,
              0.08,
            )})`,
          }}
        />
        {steps.map((step, i) => (
          <ReelStep
            key={i}
            step={step}
            isFailReel={isFailReel}
            isLast={i === steps.length - 1}
          />
        ))}
      </Box>
      {showFooter && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="flex-end"
          gap={0.4}
          sx={{ mt: 1.25 }}
        >
          <Iconify
            icon="mdi:cursor-default-click-outline"
            width={11}
            sx={{ color: "text.disabled" }}
          />
          <Typography fontSize="10.5px" color="text.disabled">
            click any pointer to open that span in the trace drawer
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
BreadcrumbList.propTypes = {
  steps: PropTypes.array.isRequired,
  isFailReel: PropTypes.bool,
  showFooter: PropTypes.bool,
};

function ReelColumn({
  title,
  headerMeta,
  accentColor,
  steps,
  isFailReel,
  emptyMessage,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: alpha(accentColor, 0.28),
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: isDark ? alpha("#fff", 0.01) : "transparent",
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
          borderColor: alpha(accentColor, 0.2),
          bgcolor: alpha(accentColor, isDark ? 0.1 : 0.06),
        }}
      >
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            bgcolor: accentColor,
            flexShrink: 0,
          }}
        />
        <Typography
          fontSize="11px"
          fontWeight={700}
          sx={{
            color: accentColor,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
          }}
        >
          {title}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {headerMeta && (
          <Typography
            fontSize="10.5px"
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
          <BreadcrumbList
            steps={steps}
            isFailReel={isFailReel}
            showFooter={false}
          />
        ) : (
          <Stack
            alignItems="center"
            justifyContent="center"
            sx={{ p: 2.5, gap: 0.5 }}
          >
            <Iconify
              icon="mdi:file-search-outline"
              width={20}
              sx={{ color: "text.disabled" }}
            />
            <Typography
              fontSize="11.5px"
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
  accentColor: PropTypes.string.isRequired,
  steps: PropTypes.array.isRequired,
  isFailReel: PropTypes.bool,
  emptyMessage: PropTypes.string,
};

function ViewModeToggle({ value, onChange }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const options = [
    { v: "breadcrumb", label: "Breadcrumb", icon: "mdi:format-list-bulleted" },
    { v: "agentgraph", label: "Agent Graph", icon: "mdi:graph-outline" },
    { v: "agentpath", label: "Agent Path", icon: "mdi:sitemap-outline" },
  ];
  return (
    <Box
      sx={{
        display: "inline-flex",
        p: "3px",
        borderRadius: "8px",
        bgcolor: isDark ? alpha("#fff", 0.06) : alpha("#000", 0.06),
      }}
    >
      {options.map((opt) => {
        const isActive = opt.v === value;
        return (
          <Stack
            key={opt.v}
            direction="row"
            alignItems="center"
            gap={0.4}
            onClick={() => onChange(opt.v)}
            sx={{
              px: 1.25,
              py: "5px",
              borderRadius: "6px",
              cursor: "pointer",
              bgcolor: isActive
                ? isDark
                  ? alpha("#fff", 0.1)
                  : "#fff"
                : "transparent",
              boxShadow: isActive
                ? isDark
                  ? "none"
                  : "0 1px 3px rgba(0,0,0,0.12)"
                : "none",
              transition: "all 0.15s",
            }}
          >
            <Iconify
              icon={opt.icon}
              width={12}
              sx={{ color: isActive ? "text.primary" : "text.disabled" }}
            />
            <Typography
              fontSize="11px"
              fontWeight={isActive ? 600 : 400}
              sx={{
                color: isActive ? "text.primary" : "text.disabled",
                whiteSpace: "nowrap",
              }}
            >
              {opt.label}
            </Typography>
          </Stack>
        );
      })}
    </Box>
  );
}
ViewModeToggle.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

function ReelTabs({ value, onChange }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const opts = [
    { v: "fail", label: "Failing", dot: FAIL_COLOR },
    { v: "pass", label: "Working", dot: PASS_COLOR },
  ];
  return (
    <Box
      sx={{
        display: "inline-flex",
        p: "3px",
        borderRadius: "8px",
        bgcolor: isDark ? alpha("#fff", 0.06) : alpha("#000", 0.06),
      }}
    >
      {opts.map(({ v, label, dot }) => {
        const isActive = value === v;
        return (
          <Stack
            key={v}
            direction="row"
            alignItems="center"
            gap={0.5}
            onClick={() => onChange(v)}
            sx={{
              px: 1.25,
              py: "5px",
              borderRadius: "6px",
              cursor: "pointer",
              bgcolor: isActive
                ? isDark
                  ? alpha("#fff", 0.1)
                  : "#fff"
                : "transparent",
              boxShadow:
                isActive && !isDark ? "0 1px 3px rgba(0,0,0,0.12)" : "none",
              transition: "all 0.15s",
            }}
          >
            <Box
              sx={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                bgcolor: dot,
                opacity: isActive ? 1 : 0.5,
              }}
            />
            <Typography
              fontSize="11px"
              fontWeight={isActive ? 600 : 500}
              sx={{
                color: isActive ? "text.primary" : "text.disabled",
                whiteSpace: "nowrap",
              }}
            >
              {label}
            </Typography>
          </Stack>
        );
      })}
    </Box>
  );
}
ReelTabs.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

function TraceEvidence({ evidence, trace, traceId, workingTraceId }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
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
    shortId && { icon: "mdi:sitemap-outline", text: shortId, mono: true },
    summary.latency_ms != null && {
      icon: "mdi:timer-outline",
      text: `${summary.latency_ms}ms`,
    },
    tokens != null && { icon: "mdi:text-box-outline", text: `${tokens} tok` },
    cost != null && { icon: "mdi:currency-usd", text: cost.toFixed(4) },
  ].filter(Boolean);

  return (
    <Box
      sx={{
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        overflow: "hidden",
        bgcolor: isDark ? alpha("#fff", 0.02) : "background.paper",
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
          bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.018),
        }}
      >
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Iconify
            icon="mdi:file-search-outline"
            width={14}
            sx={{ color: "text.disabled" }}
          />
          <Typography
            fontSize="11px"
            fontWeight={600}
            color="text.secondary"
            sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
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
                    ? "mdi:view-sequential-outline"
                    : "mdi:compare-horizontal"
                }
                width={13}
              />
            }
            onClick={() => setSplitView((v) => !v)}
            sx={{
              height: 28,
              fontSize: "11.5px",
              fontWeight: 600,
              borderRadius: "6px",
              textTransform: "none",
              // Dark theme: translucent lift avoids flat mismatch against header strip.
              color: "text.primary",
              borderColor: isDark ? alpha("#fff", 0.16) : "divider",
              bgcolor: isDark ? alpha("#fff", 0.05) : "background.paper",
              "&:hover": {
                borderColor: isDark ? alpha("#fff", 0.3) : "text.secondary",
                bgcolor: isDark ? alpha("#fff", 0.09) : alpha("#000", 0.04),
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
            bgcolor: isDark ? alpha("#fff", 0.015) : alpha("#000", 0.012),
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
              fontSize="10px"
              fontWeight={700}
              sx={{
                textTransform: "uppercase",
                letterSpacing: "0.05em",
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
                width={11}
                sx={{ color: "text.disabled" }}
              />
              <Typography
                fontSize="10.5px"
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
        {/* Agent Graph / Agent Path — single trace, OR split with working. */}
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
                accentColor={FAIL_COLOR}
                steps={failReel}
                isFailReel
                emptyMessage="No failing-trace evidence available."
              />
              <ReelColumn
                title="Working trace"
                headerMeta={hasPassing ? "nearest passing run" : undefined}
                accentColor={PASS_COLOR}
                steps={passReel}
                isFailReel={false}
                emptyMessage="No matching passing trace found for this cluster yet."
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
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const severityColors = {
    critical: "#DB2F2D",
    high: "#F5A623",
    medium: "#F5A623",
    low: "#5ACE6D",
  };

  return (
    <Stack gap={0.5}>
      {issues.map((issue) => {
        const sColor = severityColors[issue.severity] || "#888";
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
              bgcolor: isDark ? alpha("#fff", 0.02) : "transparent",
              cursor: "pointer",
              transition: "all 0.15s",
              "&:hover": {
                borderColor: alpha("#7857FC", 0.35),
                bgcolor: isDark
                  ? alpha("#7857FC", 0.06)
                  : alpha("#7857FC", 0.03),
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
              <Typography fontSize="10px" color="text.disabled">
                {issue.type}
              </Typography>
            </Stack>
            <Chip
              label={`${Math.round(issue.co_occurrence * 100)}% co-occurrence`}
              size="small"
              sx={{
                height: 16,
                fontSize: "10px",
                fontWeight: 600,
                borderRadius: "3px",
                bgcolor: alpha(sColor, 0.1),
                color: sColor,
                "& .MuiChip-label": { px: "6px" },
                flexShrink: 0,
              }}
            />
            <Iconify
              icon="mdi:chevron-right"
              width={13}
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
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

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
              bgcolor: isDark ? alpha("#fff", 0.02) : "transparent",
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
  critical: { color: "#DB2F2D", label: "Critical", icon: "mdi:alert-circle" },
  high: { color: "#F5A623", label: "High", icon: "mdi:alert-circle-outline" },
  medium: {
    color: "#2F7CF7",
    label: "Medium",
    icon: "mdi:information-outline",
  },
  low: { color: "#5ACE6D", label: "Low", icon: "mdi:check-circle-outline" },
};
const EFFORT_COLOR = { Low: "#5ACE6D", Medium: "#F5A623", High: "#DB2F2D" };

// Shared sub-heading style inside expanded card
function RecSectionLabel({ icon, label }) {
  return (
    <Stack direction="row" alignItems="center" gap={0.5} mb={0.65}>
      <Iconify icon={icon} width={12} sx={{ color: "text.secondary" }} />
      <Typography
        fontSize="9.5px"
        fontWeight={700}
        color="text.secondary"
        sx={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
      >
        {label}
      </Typography>
    </Stack>
  );
}
RecSectionLabel.propTypes = { icon: PropTypes.string, label: PropTypes.string };

function RecommendationCard({ rec, rootCauses, isDark }) {
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
        bgcolor: isDark ? alpha("#fff", 0.025) : "background.paper",
      }}
    >
      {/* Header row */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 1.5,
          py: 1,
          cursor: "pointer",
          "&:hover": {
            bgcolor: isDark ? alpha("#fff", 0.03) : alpha("#000", 0.02),
          },
        }}
        onClick={() => setExpanded((v) => !v)}
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
                bgcolor: isDark ? alpha(pm.color, 0.2) : alpha(pm.color, 0.14),
                border: "1px solid",
                borderColor: alpha(pm.color, 0.3),
              }}
            >
              <Typography
                fontSize="9px"
                fontWeight={700}
                sx={{
                  color: pm.color,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
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
            fontSize="11px"
            color="text.disabled"
            noWrap
            sx={{ mt: 0.2 }}
          >
            {rec.description}
          </Typography>
        </Stack>
        <Iconify
          icon={expanded ? "mdi:chevron-up" : "mdi:chevron-down"}
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
              <RecSectionLabel
                icon="mdi:text-box-outline"
                label="Description"
              />
              <Typography
                fontSize="11.5px"
                color="text.primary"
                sx={{ lineHeight: 1.65 }}
              >
                {rec.description}
              </Typography>
            </Box>

            {/* Immediate Fix */}
            <Box>
              <RecSectionLabel
                icon="mdi:wrench-outline"
                label="Immediate Fix"
              />
              <Box
                sx={{
                  px: 1.25,
                  py: 1,
                  borderRadius: "6px",
                  bgcolor: isDark ? alpha("#fff", 0.04) : alpha("#000", 0.03),
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  fontSize="11.5px"
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
                  icon="mdi:lightbulb-outline"
                  label="Insights"
                />
                <Typography
                  fontSize="11.5px"
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
                <RecSectionLabel icon="mdi:magnify" label="Evidence" />
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
                        fontSize="11px"
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
                <RecSectionLabel icon="mdi:magnify-scan" label="Root Cause" />
                <Stack
                  direction="row"
                  alignItems="flex-start"
                  gap={0.75}
                  sx={{
                    px: 1.25,
                    py: 0.9,
                    borderRadius: "6px",
                    bgcolor: isDark
                      ? alpha("#fff", 0.03)
                      : alpha("#000", 0.025),
                    border: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    fontSize="10px"
                    fontWeight={700}
                    color="text.disabled"
                    sx={{ flexShrink: 0, mt: "1px" }}
                  >
                    #{linkedCause.rank}
                  </Typography>
                  <Stack gap={0.2} minWidth={0}>
                    <Typography
                      fontSize="11.5px"
                      fontWeight={600}
                      color="text.primary"
                      noWrap
                    >
                      {linkedCause.title}
                    </Typography>
                    <Typography
                      fontSize="11px"
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
  isDark: PropTypes.bool,
};

function Recommendations({ recs, rootCauses }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  if (!recs?.length) return null;
  return (
    <Stack gap={0.75}>
      {recs.map((rec) => (
        <RecommendationCard
          key={rec.id}
          rec={rec}
          rootCauses={rootCauses}
          isDark={isDark}
        />
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
      <Typography fontSize="11px" color="text.disabled" sx={{ py: 1 }}>
        Deep analysis completed but found no issues worth surfacing.
      </Typography>
    );
  }
  return (
    <Stack gap={1.75}>
      {rootCauses?.length > 0 && (
        <SectionCard title="Probable Root Cause" icon="mdi:magnify-scan">
          <RootCauses causes={rootCauses} />
        </SectionCard>
      )}
      {recommendations?.length > 0 && (
        <SectionCard
          title="Recommendations & Fixes"
          icon="mdi:lightbulb-on-outline"
        >
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
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
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
      {/* ── Patterns across the cluster ── */}
      {(patternSummary?.insights ?? []).some((i) => i && i.title) && (
        <SectionCard
          title="Patterns across the cluster"
          icon="mdi:clipboard-text-outline"
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
          bgcolor: isDark ? alpha("#fff", 0.015) : "background.paper",
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
            <Typography fontSize="11px" fontWeight={600} color="text.secondary">
              Traces affected
            </Typography>
            {isOverviewLoading && !overview ? (
              <Skeleton
                width={28}
                height={14}
                sx={{
                  borderRadius: "4px",
                  bgcolor: isDark ? alpha("#fff", 0.06) : alpha("#000", 0.05),
                }}
              />
            ) : (
              <Typography
                fontSize="11px"
                fontWeight={600}
                sx={{
                  color: "text.disabled",
                  bgcolor: isDark ? alpha("#fff", 0.06) : alpha("#000", 0.05),
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
                fontSize="11px"
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
              bgcolor: isDark ? alpha("#7857FC", 0.18) : alpha("#7857FC", 0.1),
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
                  bgcolor: isDark ? alpha("#fff", 0.35) : alpha("#000", 0.25),
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
                icon="mdi:file-search-outline"
                width={40}
                sx={{ color: "text.disabled", mb: 1.5 }}
              />
              <Typography fontSize="13px" color="text.disabled">
                No trace evidence available for this cluster yet.
              </Typography>
            </Stack>
          ) : (
            <Stack gap={1.5} sx={{ p: 1.75 }}>
              {currentError?.source === "eval" ? (
                <SectionCard
                  title={isVoice ? "Voice call" : "Input / Output"}
                  icon={isVoice ? "mdi:phone-outline" : "mdi:code-tags"}
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
