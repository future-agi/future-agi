import React, { useState, useEffect, useMemo, useRef } from "react";
import ApexCharts from "apexcharts";
import { format } from "date-fns";
import {
  Box,
  Button,
  Chip,
  IconButton,
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
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import {
  useErrorFeedDeepAnalysis,
  useErrorFeedOverview,
} from "src/api/errorFeed/error-feed";
import { useErrorFeedStore } from "../store";

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

// ── Trace navigation header ──────────────────────────────────────────────────
function TraceHeader({ trace, traceIndex, total, onPrev, onNext }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const isFail = trace.status === "fail";
  const statusColor = isFail ? "#DB2F2D" : "#5ACE6D";

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: isDark ? alpha("#fff", 0.1) : alpha("#000", 0.1),
        borderRadius: "8px",
        bgcolor: isDark ? alpha("#fff", 0.025) : "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1.25}
        sx={{
          px: 1.75,
          py: 1.1,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.018),
        }}
      >
        {/* Trace ID */}
        <Iconify
          icon="mdi:sitemap-outline"
          width={13}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
        <Typography
          fontSize="12px"
          fontWeight={700}
          color="text.primary"
          sx={{ letterSpacing: "0.02em" }}
        >
          {trace.id}
        </Typography>

        <Box sx={{ flex: 1 }} />

        {/* Latency · Cost · Tokens */}
        <Stack direction="row" alignItems="center" gap={1.5} flexShrink={0}>
          <Stack direction="row" alignItems="center" gap={0.4}>
            <Iconify
              icon="mdi:timer-outline"
              width={12}
              sx={{ color: "text.disabled" }}
            />
            <Typography fontSize="12px" color="text.secondary">
              {trace.summary.latencyMs}ms
            </Typography>
          </Stack>
          <Stack direction="row" alignItems="center" gap={0.4}>
            <Iconify
              icon="mdi:currency-usd"
              width={12}
              sx={{ color: "text.disabled" }}
            />
            <Typography fontSize="12px" color="text.secondary">
              $
              {(
                trace.summary.cost ??
                (trace.summary.inputTokens ?? 0) * 0.000003 +
                  (trace.summary.outputTokens ?? 0) * 0.000015
              ).toFixed(4)}
            </Typography>
          </Stack>
          <Stack direction="row" alignItems="center" gap={0.4}>
            <Iconify
              icon="mdi:text-box-outline"
              width={12}
              sx={{ color: "text.disabled" }}
            />
            <Typography fontSize="12px" color="text.secondary">
              {(
                (trace.summary.inputTokens ?? 0) +
                (trace.summary.outputTokens ?? 0)
              ).toLocaleString()}{" "}
              tok
            </Typography>
          </Stack>
        </Stack>
      </Stack>
    </Box>
  );
}
TraceHeader.propTypes = {
  trace: PropTypes.object.isRequired,
  traceIndex: PropTypes.number.isRequired,
  total: PropTypes.number.isRequired,
  onPrev: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
};

// ── (SparkRow removed — replaced by unified EventsUsersChart below) ──────────
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
            <Typography
              fontSize="13px"
              fontWeight={700}
              color="text.primary"
              sx={{ fontFeatureSettings: "'tnum'", lineHeight: 1 }}
            >
              {item.total.toLocaleString()}
            </Typography>
          </Stack>
        ))}
      </Stack>

      {/* Chart — full width */}
      <Box sx={{ flex: 1, minWidth: 0, px: 0.5 }}>
        <div ref={chartRef} style={{ width: "100%" }} />
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
};

// ── Trace list (left panel) ───────────────────────────────────────────────────
function TraceList({ traces, selectedIndex, onSelect }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  return (
    <Stack gap={0}>
      {traces.map((t, i) => {
        const isFail = t.status === "fail";
        const isSelected = i === selectedIndex;
        const statusColor = isFail ? "#DB2F2D" : "#5ACE6D";
        const time = new Date(t.timestamp).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        });
        const tokens =
          (t.summary.inputTokens ?? 0) + (t.summary.outputTokens ?? 0);
        const cost = (
          (t.summary.inputTokens ?? 0) * 0.000003 +
          (t.summary.outputTokens ?? 0) * 0.000015
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
                  {t.summary.latencyMs}ms
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
  traces: PropTypes.array.isRequired,
  selectedIndex: PropTypes.number.isRequired,
  onSelect: PropTypes.func.isRequired,
};

function PatternSummary({ summary }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const insights = summary?.insights ?? [];

  if (!insights.length) {
    return (
      <Typography
        fontSize="11px"
        color="text.disabled"
        sx={{ py: 2, textAlign: "center" }}
      >
        Not enough data yet — waiting for more scanner results.
      </Typography>
    );
  }

  // Always render a 4-column grid so cards stay aligned across clusters;
  // fill empty slots with a muted placeholder when we have fewer insights.
  const slots = [...insights];
  while (slots.length < 4) slots.push(null);

  return (
    <Box
      sx={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 1 }}
    >
      {slots.map((insight, i) => (
        <Box
          key={i}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            px: 1.75,
            py: 1.5,
            bgcolor: isDark ? alpha("#fff", 0.03) : alpha("#000", 0.025),
            opacity: insight ? 1 : 0.35,
            minHeight: 56,
          }}
        >
          <Typography
            fontSize="15px"
            fontWeight={700}
            color="text.primary"
            sx={{
              lineHeight: 1,
              mb: 0.6,
              fontFeatureSettings: "'tnum'",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {insight?.value ?? "—"}
          </Typography>
          <Typography
            fontSize="11px"
            color="text.disabled"
            sx={{ lineHeight: 1.4 }}
          >
            {insight?.caption ?? ""}
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

function TraceAgentFlow({ traceId }) {
  const { data, isLoading } = useGetTraceDetail(traceId);
  const spanTree = data?.observation_spans || data?.observationSpans;

  const graphData = useMemo(() => {
    if (!spanTree?.length) return null;
    return buildTraceGraph(spanTree);
  }, [spanTree]);

  if (isLoading) {
    return (
      <Typography
        fontSize="12px"
        color="text.disabled"
        sx={{ py: 2, textAlign: "center" }}
      >
        Loading agent flow…
      </Typography>
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
    <Box sx={{ height: 300 }}>
      <AgentGraph data={graphData} isLoading={false} direction="TB" />
    </Box>
  );
}
TraceAgentFlow.propTypes = { traceId: PropTypes.string };

// ── Trace evidence reel (fail / pass tabs) ───────────────────────────────────

// Renders a text value that may be a plain string or a rich [{t, hl}] array
function RichText({ text, isFailReel: _isFailReel }) {
  const errorColor = "#DB2F2D";
  const okColor = "#5ACE6D";

  if (!text) return null;
  if (!Array.isArray(text)) {
    return <>{String(text)}</>;
  }
  return (
    <>
      {text.map((seg, i) => {
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

function ReelStep({ step, isFailReel }) {
  return (
    <Stack
      direction="row"
      gap={1.25}
      sx={{
        px: 1.5,
        py: 1.25,
        borderBottom: "1px solid",
        borderColor: "divider",
        "&:last-child": { borderBottom: "none" },
      }}
    >
      {/* Content */}
      <Stack gap={0.25} flex={1} minWidth={0}>
        <Typography
          fontSize="10px"
          fontWeight={600}
          color="text.disabled"
          sx={{ textTransform: "uppercase", letterSpacing: "0.05em" }}
        >
          {step.label}
        </Typography>
        <Typography
          fontSize="12px"
          color="text.primary"
          sx={{ lineHeight: 1.6 }}
        >
          <RichText text={step.text} isFailReel={isFailReel} />
        </Typography>
        {step.meta && (
          <Typography
            fontSize="10px"
            color="text.disabled"
            sx={{ lineHeight: 1.4 }}
          >
            {step.meta}
          </Typography>
        )}
      </Stack>
    </Stack>
  );
}
ReelStep.propTypes = {
  step: PropTypes.object.isRequired,
  isFailReel: PropTypes.bool,
};

function TraceEvidence({ evidence }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [activeReel, setActiveReel] = useState("fail");

  const steps =
    activeReel === "fail" ? evidence.failReel || [] : evidence.passReel || [];
  const isFailActive = activeReel === "fail";
  const failColor = "#DB2F2D";
  const passColor = "#5ACE6D";

  return (
    <Stack gap={1.25}>
      {/* Segmented tab control */}
      <Box
        sx={{
          display: "inline-flex",
          alignSelf: "flex-start",
          p: "3px",
          borderRadius: "8px",
          bgcolor: isDark ? alpha("#fff", 0.06) : alpha("#000", 0.06),
        }}
      >
        {[
          { value: "fail", label: "Failing Trace" },
          { value: "pass", label: "Working Trace" },
        ].map(({ value, label }) => {
          const isActive = activeReel === value;
          return (
            <Box
              key={value}
              onClick={() => setActiveReel(value)}
              sx={{
                px: 1.5,
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
                "&:hover": {
                  bgcolor: isActive
                    ? isDark
                      ? alpha("#fff", 0.1)
                      : "#fff"
                    : isDark
                      ? alpha("#fff", 0.05)
                      : alpha("#000", 0.04),
                },
              }}
            >
              <Typography
                fontSize="11px"
                fontWeight={isActive ? 600 : 400}
                sx={{
                  color: isActive ? "text.primary" : "text.disabled",
                  whiteSpace: "nowrap",
                }}
              >
                {label}
              </Typography>
            </Box>
          );
        })}
      </Box>

      {/* Reel */}
      <Box
        sx={{
          border: "1px solid",
          borderColor: isFailActive
            ? alpha(failColor, 0.18)
            : alpha(passColor, 0.18),
          borderRadius: "8px",
          overflow: "hidden",
          bgcolor: isDark ? alpha("#fff", 0.01) : "transparent",
        }}
      >
        {steps.length > 0 ? (
          steps.map((step, i) => (
            <ReelStep key={i} step={step} isFailReel={isFailActive} />
          ))
        ) : (
          <Box sx={{ p: 2, textAlign: "center" }}>
            <Typography fontSize="12px" color="text.disabled">
              No steps available
            </Typography>
          </Box>
        )}
      </Box>
    </Stack>
  );
}
TraceEvidence.propTypes = { evidence: PropTypes.object.isRequired };

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
              label={`${Math.round(issue.coOccurrence * 100)}% co-occurrence`}
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
        // Backend splits title vs description at first period/comma. When
        // the agent produces a short single-clause string with no internal
        // punctuation, both fields end up identical — skip the description
        // line in that case so we're not rendering the same text twice.
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
  const linkedCause = rootCauses?.find((c) => c.rank === rec.rootCauseLink);

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
                  {rec.immediateFix}
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
  const clusterId = currentError?.clusterId;
  const { data: overview, isLoading: isOverviewLoading } =
    useErrorFeedOverview(clusterId);
  const traces = useMemo(
    () => overview?.representativeTraces ?? [],
    [overview],
  );

  // Selected trace is kept in the Zustand store (per-cluster) so the
  // sidebar's AI Metadata / Evaluations / Deep Analysis sections stay in
  // sync. Local ``traceIndex`` is derived from the store's trace id.
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

  // Backfill the store on first overview load so the sidebar has a trace
  // id to work with even before the user clicks anything. Without this,
  // the sidebar would fall back to the cluster's "latest" trace which
  // might not be what the Overview tab is showing at index 0.
  useEffect(() => {
    if (!clusterId) return;
    if (!traces.length) return;
    if (selectedTraceId) return;
    setSelectedTraceId(clusterId, traces[0].id);
  }, [clusterId, traces, selectedTraceId, setSelectedTraceId]);

  const eventsOverTime = overview?.eventsOverTime ?? null;
  const patternSummary = overview?.patternSummary ?? null;

  // Deep analysis is driven by the backend root-cause query for the
  // currently-selected trace. When status flips to ``done`` we smooth-
  // scroll to the results panel.
  const { data: deepAnalysis } = useErrorFeedDeepAnalysis(clusterId, trace?.id);
  const deepAnalysisState = deepAnalysis?.status ?? "idle";
  const deepAnalysisRef = useRef(null);

  useEffect(() => {
    if (deepAnalysisState === "done" && deepAnalysisRef.current) {
      deepAnalysisRef.current.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }, [deepAnalysisState]);

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
    <Box
      ref={containerRef}
      sx={{
        display: "flex",
        gap: 0,
        height: "calc(100vh - 182px)",
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
          <EventsUsersChart flat data={eventsOverTime} deployMarkers={[]} />
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
            {traces.length.toLocaleString()}
          </Typography>
        </Stack>

        {/* Scrollable trace list */}
        <Box sx={{ flex: 1, overflow: "auto" }}>
          <TraceList
            traces={traces}
            selectedIndex={traceIndex}
            onSelect={selectTrace}
          />
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
            {/* Trace header bar */}
            <TraceHeader
              trace={trace}
              traceIndex={traceIndex}
              total={traces.length}
              onPrev={() => selectTrace(Math.max(0, traceIndex - 1))}
              onNext={() =>
                selectTrace(Math.min(traces.length - 1, traceIndex + 1))
              }
            />

            {/* Pattern Summary */}
            <SectionCard
              title="Pattern Summary"
              icon="mdi:information-outline"
              collapsible
            >
              <PatternSummary summary={patternSummary} />
            </SectionCard>

            {/* Agent Flow */}
            <SectionCard
              title="Agent Flow"
              icon="mdi:graph-outline"
              collapsible
            >
              <TraceAgentFlow traceId={trace.id} />
            </SectionCard>

            {/* Trace Evidence — scanner clusters only (evals don't have scanner steps) */}
            {currentError?.source !== "eval" && (
              <SectionCard
                title="Trace Evidence"
                icon="mdi:file-search-outline"
                collapsible
              >
                <TraceEvidence evidence={trace.evidence ?? {}} />
              </SectionCard>
            )}

            {/* Deep analysis results — shown only when done */}
            {deepAnalysisState === "done" && (
              <Box ref={deepAnalysisRef}>
                <Stack direction="row" alignItems="center" gap={1} mb={1.75}>
                  <Box sx={{ flex: 1, height: "1px", bgcolor: "divider" }} />
                  <Stack direction="row" alignItems="center" gap={0.6}>
                    <Iconify
                      icon="mdi:check-circle"
                      width={14}
                      sx={{ color: "#5ACE6D" }}
                    />
                    <Typography
                      fontSize="11px"
                      fontWeight={600}
                      color="text.secondary"
                      sx={{
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                      }}
                    >
                      Deep Analysis Results
                    </Typography>
                  </Stack>
                  <Box sx={{ flex: 1, height: "1px", bgcolor: "divider" }} />
                </Stack>
                <DeepAnalysisResults
                  rootCauses={deepAnalysis?.rootCauses}
                  recommendations={deepAnalysis?.recommendations}
                />
              </Box>
            )}
          </Stack>
        )}
      </Box>
    </Box>
  );
}

OverviewTab.propTypes = {
  _error: PropTypes.shape({
    clusterId: PropTypes.string,
    source: PropTypes.string,
  }),
};
