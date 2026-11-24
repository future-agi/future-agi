import EmptyState from "src/components/empty-state";
import React, { useState, useEffect, useMemo, useRef } from "react";
import { SegmentedTabs } from "src/components/tabs/tabs";
import ApexCharts from "apexcharts";
import { format } from "date-fns";
import {
  Box,
  Button,
  Chip,
  Collapse,
  Popover,
  Skeleton,
  Stack,
  Typography,
  alpha,
  useTheme,
  Tab,
} from "@mui/material";
import PropTypes from "prop-types";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import AgentGraph from "src/sections/projects/LLMTracing/GraphSection/AgentGraph";
import AgentPath from "src/sections/projects/LLMTracing/GraphSection/AgentPath";
import GraphSkeleton from "src/sections/projects/LLMTracing/GraphSection/GraphSkeleton";
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { statusColors } from "src/utils/runStatus";
import { seriesColor } from "src/theme/data-colors";
import { useErrorFeedOverview } from "src/api/errorFeed/error-feed";
import { useIssueTraceDetail } from "../useIssueTraceDetail";
import { useSharedIssue } from "../sharedIssueContext";
import EvalIOPanel from "./EvalIOPanel";
import VoiceEvalPanel from "./VoiceEvalPanel";
import EvidenceRichBody from "./EvidenceRichBody";
import { buildGraphDiff, comparisonGraphForMode } from "./buildGraphDiff";
import { useErrorFeedStore } from "../store";
import { TRACE_STATUS } from "../constants";

const formatDur = (ms) => {
  if (ms == null) return "—";
  if (ms >= 60_000) {
    const totalSec = Math.round(ms / 1000);
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
  }
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
};

const formatCompactTokens = (tok) => {
  if (tok == null) return "";
  if (tok >= 1_000_000) {
    return `${(tok / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
  }
  if (tok >= 1_000) {
    return `${(tok / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  }
  return tok.toLocaleString();
};

const formatShortTime = (iso) => {
  if (!iso) return "";
  try {
    return format(new Date(iso), "MMM d, HH:mm");
  } catch {
    return "";
  }
};

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
        onClick={collapsible ? () => setOpen((v) => !v) : undefined}
        sx={{
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
  const evtColor = seriesColor(theme, 1);
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
              color: theme.palette.status.warning.solid,
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
                color="text.secondary"
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

function TraceList({ traces, selectedIndex, onSelect, filterQuery = "", loading = false }) {
  if (loading) {
    return (
      <Stack gap={0.5} sx={{ px: 1, pb: 1 }}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Box
            key={i}
            sx={{
              px: 1.25,
              py: 1,
              borderRadius: "6px",
            }}
          >
            <Stack direction="row" alignItems="center" gap={0.75} mb={0.4}>
              <Skeleton width="65%" height={12} sx={{ borderRadius: "4px" }} />
              <Box sx={{ flex: 1 }} />
              <Skeleton width={44} height={10} sx={{ borderRadius: "4px" }} />
            </Stack>
            <Stack direction="row" gap={1.5}>
              <Skeleton width={56} height={10} sx={{ borderRadius: "4px" }} />
              <Skeleton width={42} height={10} sx={{ borderRadius: "4px" }} />
              <Skeleton width={38} height={10} sx={{ borderRadius: "4px" }} />
            </Stack>
          </Box>
        ))}
      </Stack>
    );
  }

  const q = filterQuery.trim().toLowerCase();
  const visible = traces
    .map((t, i) => ({ t, i }))
    .filter(({ t }) => {
      if (!q) return true;
      const hay = `${t.id} ${t.evidence?.input || ""} ${t.short_request || ""} ${t.omega_deep?.origin_alias || ""} ${t.omega_deep?.symptom_alias || ""}`.toLowerCase();
      return hay.includes(q);
    });

  if (!visible.length) {
    return (
      <Typography typography="s1" color="text.disabled" sx={{ px: 1.5, py: 3, textAlign: "center" }}>
        No matching traces
      </Typography>
    );
  }

  return (
    <Stack gap={0.25} sx={{ px: 1, pb: 1.25 }}>
      {visible.map(({ t, i }) => {
        const isSelected = i === selectedIndex;
        const deep = t.omega_deep;
        const turns = t.summary?.turns ?? deep?.telemetry?.turn_count ?? 1;
        const spanCount = deep?.telemetry?.span_count ?? null;
        const pathLabel = deep
          ? `${deep.origin_alias}${deep.symptom_alias && deep.symptom_alias !== deep.origin_alias ? ` → ${deep.symptom_alias}` : ""}`
          : String(t.id || "");
        const promptPreview = t.evidence?.input || t.short_request || t.id;

        return (
          <Box
            key={t.id}
            component="button"
            type="button"
            onClick={() => onSelect(i)}
            sx={{
              width: "100%",
              display: "block",
              textAlign: "left",
              border: 0,
              px: 1.25,
              py: 1,
              cursor: "pointer",
              borderRadius: "6px",
              bgcolor: isSelected ? "action.selected" : "transparent",
              fontFamily: "inherit",
              "&:hover": {
                bgcolor: isSelected ? "action.selected" : "action.hover",
              },
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "primary.main",
                outlineOffset: -2,
              },
            }}
          >
            <Typography
              typography="s1"
              fontWeight={isSelected ? 500 : 400}
              color="text.primary"
              sx={{
                lineHeight: 1.4,
                mb: 0.5,
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {promptPreview}
            </Typography>

            <Stack direction="row" alignItems="center" justifyContent="space-between" gap={0.75}>
              <Stack direction="row" alignItems="center" gap={0.65} sx={{ minWidth: 0 }}>
                <Typography
                  typography="s3"
                  color={isSelected ? "text.primary" : "text.secondary"}
                  noWrap
                  sx={{
                    fontFamily: "ui-monospace, SFMono-Regular, monospace",
                    maxWidth: deep ? "none" : 78,
                    flexShrink: deep ? 0 : 1,
                  }}
                >
                  {pathLabel}
                </Typography>
                <Typography typography="s3" color="text.tertiary" noWrap>
                  {turns > 1
                    ? `${turns} turns`
                    : spanCount != null
                      ? `${spanCount} ${spanCount === 1 ? "span" : "spans"}`
                      : ""}
                  {t.summary?.latency_ms != null ? ` · ${formatDur(t.summary.latency_ms)}` : ""}
                </Typography>
              </Stack>
              <Typography
                typography="s3"
                color="text.tertiary"
                sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
              >
                {formatShortTime(t.timestamp)}
              </Typography>
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
  filterQuery: PropTypes.string,
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
export function TraceGraphView({ traceId, mode, projectId }) {
  const { data, isLoading, isError } = useIssueTraceDetail(traceId, {
    projectId,
  });
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

  if (isError) {
    return (
      <Typography
        fontSize="12px"
        color="error.main"
        sx={{ py: 2, textAlign: "center" }}
      >
        Could not load trace spans. Please retry.
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
  projectId: PropTypes.string,
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

function TraceGraphCompare({
  failingTraceId,
  workingTraceId,
  mode,
  projectId,
}) {
  const failQ = useIssueTraceDetail(failingTraceId, { projectId });
  const passQ = useIssueTraceDetail(workingTraceId, { projectId });

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
        No matching working trace yet for this cluster — backend KNN matching
        will populate it shortly.
      </Typography>
    </Box>
  );

  const renderSide = (graph, loading, failed, label) => {
    if (loading) {
      return (
        <Box sx={{ height: 360 }}>
          <GraphSkeleton />
        </Box>
      );
    }
    if (failed) {
      return (
        <Stack
          alignItems="center"
          justifyContent="center"
          sx={{ height: 360, p: 2 }}
        >
          <Typography fontSize="12px" color="error.main" textAlign="center">
            Could not load {label} spans. Please retry.
          </Typography>
        </Stack>
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
          {renderSide(
            failRenderGraph,
            failLoading,
            failQ.isError,
            "failing trace",
          )}
        </CompareColumn>
        <CompareColumn
          title="Expected path"
          role="success"
          traceShortId={
            workingTraceId
              ? workingTraceId.startsWith("working_")
                ? "reference baseline"
                : workingTraceId.slice(0, 8)
              : null
          }
        >
          {renderSide(
            passRenderGraph,
            passLoading,
            passQ.isError,
            "working trace",
          )}
        </CompareColumn>
      </Box>
    </Stack>
  );
}
TraceGraphCompare.propTypes = {
  failingTraceId: PropTypes.string,
  workingTraceId: PropTypes.string,
  mode: PropTypes.oneOf(["graph", "path"]),
  projectId: PropTypes.string,
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

function ReelStep({
  step,
  isFailReel,
  isLast,
  compact = false,
  defaultExpanded = true,
  onInspectSpan,
  onClaimClick,
}) {
  const [showRaw, setShowRaw] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(defaultExpanded);

  const status = step.status;
  const isFailure = step.isFailure || status === "fail";
  const isOrigin = step.role === "origin" || status === "warning";
  const dotColor = isFailure
    ? FAIL_COLOR
    : isOrigin
      ? "status.warning.solid"
      : !isFailReel && (status === "pass" || status === "ok")
        ? PASS_COLOR
        : "text.secondary";
  const raw = step.rawJson || step.raw;
  const hasRichEvidence = Boolean(step.evidence_text || step.evidence_error);
  const isCollapsible =
    hasRichEvidence &&
    step.evidence_kind !== "propagated" &&
    step.evidence_kind !== "empty_abort";
  const rColor = isFailure
    ? "status.danger.fg"
    : isOrigin
      ? "status.warning.fg"
      : status === "pass"
        ? "status.success.fg"
        : "text.tertiary";

  const header = (
    <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1} flexWrap="wrap">
      <Stack direction="row" alignItems="baseline" gap={0.75} sx={{ flex: 1, minWidth: 0 }}>
        <Box
          component="span"
          sx={{
            flexShrink: 0,
            fontSize: "12px",
            fontWeight: 600,
            color: rColor,
          }}
        >
          {step.label}
        </Box>
        {step.span_name && (
          <Typography
            component="span"
            fontSize="11px"
            color="text.secondary"
            noWrap
            sx={{ fontFamily: "ui-monospace, SFMono-Regular, monospace" }}
          >
            {step.span_name}
            {step.relation_note ? ` (${step.relation_note})` : ""}
          </Typography>
        )}
      </Stack>

      <Stack direction="row" alignItems="center" gap={0.75} sx={{ flexShrink: 0 }}>
        {step.meta && (
          <Box
            component="span"
            sx={{
              fontSize: "11px",
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              color: "text.secondary",
              whiteSpace: "nowrap",
            }}
          >
            {step.meta}
          </Box>
        )}
        {isCollapsible && !compact && (
          <Button
            size="small"
            variant="text"
            onClick={() => setEvidenceOpen((v) => !v)}
            sx={{ minWidth: 0, px: 0.5, py: 0, color: "text.secondary" }}
          >
            {evidenceOpen ? "Hide" : "Evidence"}
          </Button>
        )}
        {step.span_id && onInspectSpan && (
          <Button
            size="small"
            variant="text"
            onClick={() => onInspectSpan(step.span_id)}
            sx={{ minWidth: 0, px: 0.5, py: 0, color: "text.secondary" }}
          >
            Inspect →
          </Button>
        )}
      </Stack>
    </Stack>
  );

  const stepBorderColor = isFailure
    ? "status.danger.border"
    : isOrigin
      ? "status.warning.border"
      : "divider";

  return (
    <Box sx={{ position: "relative", mb: isLast ? 0 : 1.25 }}>
      {/* Timeline dot */}
      {isFailure ? (
        <Box
          sx={{
            position: "absolute",
            left: "-16px",
            top: "12px",
            width: 13,
            height: 13,
            borderRadius: "50%",
            bgcolor: "background.paper",
            border: "2px solid",
            borderColor: FAIL_COLOR,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "11px",
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
            left: "-15px",
            top: "13px",
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

      {/* Flat DS step panel with 1px hairline border */}
      <Box
        sx={{
          border: "1px solid",
          borderColor: stepBorderColor,
          borderRadius: "8px",
          bgcolor: "background.paper",
          px: 1.5,
          py: 1.1,
        }}
      >
        {header}
        <Typography
          fontSize="12px"
          fontWeight={500}
          color="text.primary"
          sx={{ lineHeight: 1.45, mt: 0.4 }}
        >
          <RichText text={step.text} isFailReel={isFailReel} />
        </Typography>

        {step.evidence_kind === "propagated" && step.evidence_text && (
          <Typography fontSize="12px" color="text.secondary" sx={{ mt: 0.5 }}>
            {step.evidence_text}
          </Typography>
        )}

        {step.evidence_kind === "empty_abort" && step.evidence_text && (
          <Typography fontSize="12px" color="status.danger.fg" sx={{ mt: 0.5 }}>
            {step.evidence_text}
          </Typography>
        )}

        {isCollapsible && (
          <Collapse in={evidenceOpen}>
            <Box sx={{ mt: 1 }}>
              {step.evidence_kind === "error" && step.evidence_error ? (
                <Box
                  sx={{
                    p: 1.15,
                    border: "1px solid",
                    borderColor: "status.danger.border",
                    borderRadius: "6px",
                    bgcolor: "status.danger.bg",
                  }}
                >
                  <Typography fontSize="11px" fontWeight={600} color="status.danger.fg" sx={{ mb: 0.25 }}>
                    {step.evidence_error.title}
                  </Typography>
                  <Typography
                    fontSize="12px"
                    color="text.primary"
                    sx={{ fontFamily: "ui-monospace, SFMono-Regular, monospace", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                  >
                    {step.evidence_error.message}
                  </Typography>
                </Box>
              ) : (
                <EvidenceRichBody
                  text={step.evidence_text}
                  terms={step.evidence_highlights || []}
                  role={isOrigin ? "warning" : "danger"}
                  maxRows={5}
                  truncation={step.truncation}
                  onClaimClick={
                    step.claim_provenance && onClaimClick
                      ? (anchor, phrase) => onClaimClick(anchor, phrase, step.claim_provenance)
                      : null
                  }
                />
              )}
            </Box>
          </Collapse>
        )}
      </Box>

      {/* Explicit causal bridge connecting this step -> next step */}
      {!isLast && step.causal_link_to_next && (
        <Stack direction="row" alignItems="center" gap={0.5} sx={{ mt: 0.5, pl: 0.5 }}>
          <Iconify icon="solar:arrow-down-linear" width={12} sx={{ color: "text.disabled", flexShrink: 0 }} />
          <Typography fontSize="11px" color="text.secondary">
            {step.causal_link_to_next}
          </Typography>
        </Stack>
      )}

      {raw && (
        <Box
          component="button"
          type="button"
          aria-expanded={showRaw}
          onClick={() => setShowRaw((v) => !v)}
          sx={{
            border: 0,
            p: 0,
            bgcolor: "transparent",
            fontSize: "12px",
            color: "text.disabled",
            mt: 0.4,
            cursor: "pointer",
            "&:hover": { color: "text.secondary" },
          }}
        >
          {showRaw ? "Hide cited excerpt" : "Show cited excerpt"}
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
          {step.evidence_id && `Evidence ${step.evidence_id}\n`}
          {typeof raw === "string" ? raw : JSON.stringify(raw, null, 2)}
        </Box>
      )}
    </Box>
  );
}
ReelStep.propTypes = {
  step: PropTypes.object.isRequired,
  isFailReel: PropTypes.bool,
  isLast: PropTypes.bool,
  compact: PropTypes.bool,
  defaultExpanded: PropTypes.bool,
  onInspectSpan: PropTypes.func,
  onClaimClick: PropTypes.func,
};

function BreadcrumbList({
  steps,
  isFailReel,
  compact = false,
  userRequest = null,
  onInspectSpan,
  onClaimClick,
}) {
  const theme = useTheme();
  const [reqExpanded, setReqExpanded] = useState(false);
  const accent = statusColors(theme, isFailReel ? "danger" : "success").solid;
  const isLongRequest = Boolean(userRequest && userRequest.length > 180);
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
    <Stack gap={1.25}>
      {userRequest && (
        <Box
          sx={{
            px: 1.5,
            py: 1,
            borderRadius: "8px",
            bgcolor: "background.neutral",
          }}
        >
          <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1} sx={{ mb: 0.35 }}>
            <Stack direction="row" alignItems="center" gap={0.6}>
              <Iconify
                icon="solar:chat-round-line-linear"
                width={13}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography typography="s3" fontWeight={600} color="text.secondary">
                User request
              </Typography>
            </Stack>
            {isLongRequest && (
              <Button
                size="small"
                variant="text"
                onClick={() => setReqExpanded((v) => !v)}
                sx={{ minWidth: 0, px: 0.4, py: 0, color: "text.secondary" }}
              >
                {reqExpanded ? "Show less" : "Show more"}
              </Button>
            )}
          </Stack>
          <Typography
            typography="s1"
            color="text.primary"
            sx={{
              lineHeight: 1.5,
              ...(!reqExpanded && isLongRequest
                ? {
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }
                : {}),
            }}
          >
            {userRequest}
          </Typography>
        </Box>
      )}
      <Box sx={{ position: "relative", pl: 2, pt: 0.25 }}>
        <Box
          sx={{
            position: "absolute",
            left: "5px",
            top: 12,
            bottom: 12,
            width: "2px",
            borderRadius: "1px",
            background: `linear-gradient(${alpha(accent, 0.28)}, ${alpha(accent, 0.05)})`,
          }}
        />
        {steps.map((step, i) => {
          const defaultExpanded =
            steps.length <= 3 ||
            step.role === "origin" ||
            step.role === "symptom" ||
            i === steps.length - 1;
          return (
            <ReelStep
              key={step.span_id || `${step.step}-${i}`}
              step={step}
              isFailReel={isFailReel}
              isLast={i === steps.length - 1}
              compact={compact}
              defaultExpanded={defaultExpanded}
              onInspectSpan={onInspectSpan}
              onClaimClick={onClaimClick}
            />
          );
        })}
      </Box>
    </Stack>
  );
}
BreadcrumbList.propTypes = {
  steps: PropTypes.array.isRequired,
  isFailReel: PropTypes.bool,
  compact: PropTypes.bool,
  userRequest: PropTypes.string,
  onInspectSpan: PropTypes.func,
  onClaimClick: PropTypes.func,
};

function ReelColumn({
  title,
  headerMeta,
  role,
  steps,
  isFailReel,
  emptyMessage,
  userRequest,
  onInspectSpan,
  onClaimClick,
}) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: "background.paper",
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
          py: 0.9,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
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
          <BreadcrumbList
            steps={steps}
            isFailReel={isFailReel}
            compact
            userRequest={userRequest}
            onInspectSpan={onInspectSpan}
            onClaimClick={onClaimClick}
          />
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
  userRequest: PropTypes.string,
  onInspectSpan: PropTypes.func,
  onClaimClick: PropTypes.func,
};

function ViewModeToggle({ value, onChange }) {
  const options = [
    { v: "breadcrumb", label: "Steps", icon: "solar:list-linear" },
    { v: "agentgraph", label: "Graph", icon: "solar:graph-linear" },
    { v: "agentpath", label: "Path", icon: "solar:routing-2-linear" },
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

function ReelTabLabel({ label, dot, active }) {
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.625 }}
    >
      <Box
        component="span"
        sx={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          bgcolor: dot,
          opacity: active ? 1 : 0.5,
          flexShrink: 0,
        }}
      />
      {label}
    </Box>
  );
}
ReelTabLabel.propTypes = {
  label: PropTypes.string.isRequired,
  dot: PropTypes.string.isRequired,
  active: PropTypes.bool.isRequired,
};

function ReelTabs({ value, onChange }) {
  const opts = [
    { v: "fail", label: "Failing", dot: FAIL_COLOR },
    { v: "pass", label: "Expected", dot: PASS_COLOR },
  ];
  return (
    <SegmentedTabs value={value} onChange={(_, v) => onChange(v)}>
      {opts.map(({ v, label, dot }) => (
        <Tab
          key={v}
          value={v}
          label={<ReelTabLabel label={label} dot={dot} active={value === v} />}
        />
      ))}
    </SegmentedTabs>
  );
}
ReelTabs.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

function TraceEvidence({
  evidence,
  trace,
  traceId,
  workingTraceId,
  projectId,
  onInspectSpan,
  onClaimClick,
}) {
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
  const deep = trace?.omega_deep ?? null;
  const tel = deep?.telemetry ?? {};
  const truthLanes = deep?.evidence_pairs?.[0]?.truncation
    ? deep.evidence_pairs[0].truth_loss_lanes || []
    : [];
  const tokens =
    (summary.input_tokens ?? 0) + (summary.output_tokens ?? 0) || null;
  const shortId = traceId ? traceId.slice(0, 8) : null;
  const isTraceFail = trace?.status === TRACE_STATUS.FAIL;
  const turnCount = summary.turns ?? tel.turn_count ?? null;

  const pathValue = deep?.origin_alias
    ? deep.symptom_alias && deep.symptom_alias !== deep.origin_alias
      ? `${deep.origin_alias} → ${deep.symptom_alias}`
      : deep.origin_alias
    : null;

  const telemetryStats = [
    shortId && {
      label: "Trace",
      value: shortId,
      mono: true,
    },
    pathValue && {
      label: "Path",
      value: pathValue,
      mono: true,
    },
    tel.span_count != null && {
      label: "Spans",
      value: tel.span_count.toLocaleString(),
    },
    turnCount != null && turnCount > 1 && {
      label: "Turns",
      value: turnCount.toLocaleString(),
    },
    summary.latency_ms != null && {
      label: "Duration",
      value: formatDur(summary.latency_ms),
    },
    tokens != null && {
      label: "Tokens",
      value: formatCompactTokens(tokens),
    },
  ].filter(Boolean);

  return (
    <Box sx={{ bgcolor: "background.paper" }}>
      {/* ── Row 1: Single-line calm toolbar (no wrapping, no enclosing box borders) ── */}
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        flexWrap="nowrap"
        sx={{
          px: 2,
          pt: 1.25,
          pb: 1,
          overflowX: "auto",
        }}
      >
        <ViewModeToggle value={viewMode} onChange={setViewMode} />

        <Box sx={{ flex: 1, minWidth: 8 }} />

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
                width={13}
              />
            }
            onClick={() => setSplitView((v) => !v)}
            sx={{
              whiteSpace: "nowrap",
              flexShrink: 0,
              borderColor: "divider",
              color: splitView ? "text.primary" : "text.secondary",
              bgcolor: splitView ? "action.selected" : "transparent",
            }}
          >
            {splitView ? "Single" : "Split"}
          </Button>
        )}

        {onInspectSpan && traceId && (
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="solar:layers-linear" width={13} />}
            onClick={() => onInspectSpan(trace?.origin_span_id || null)}
            sx={{
              whiteSpace: "nowrap",
              flexShrink: 0,
              borderColor: "divider",
              color: "text.primary",
            }}
          >
            Inspect trace
          </Button>
        )}
      </Stack>

      {/* Trace telemetry strip — flat hairline-bordered DS strip */}
      {telemetryStats.length > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          gap={{ xs: 1.5, sm: 2.25 }}
          flexWrap="wrap"
          sx={{
            mx: 2,
            mb: 1.5,
            px: 1.5,
            py: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
          }}
        >
          <StatusBadge
            role={isTraceFail ? "danger" : "success"}
            label={isTraceFail ? "Failed" : "Passed"}
            size="sm"
          />

          {telemetryStats.map((stat) => (
            <Box
              key={stat.label}
              component={stat.onClick ? "button" : "div"}
              type={stat.onClick ? "button" : undefined}
              onClick={stat.onClick || undefined}
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-start",
                gap: 0.15,
                p: 0,
                border: "none",
                bgcolor: "transparent",
                fontFamily: "inherit",
                textAlign: "left",
                flexShrink: 0,
                cursor: stat.onClick ? "pointer" : "default",
                ...(stat.onClick
                  ? {
                      "&:hover .telemetry-val": { textDecoration: "underline" },
                    }
                  : {}),
              }}
            >
              <Typography
                fontSize="11px"
                fontWeight={500}
                color="text.tertiary"
                sx={{ lineHeight: 1.15 }}
              >
                {stat.label}
              </Typography>
              <Typography
                className="telemetry-val"
                fontSize="12px"
                fontWeight={stat.mono ? 400 : 500}
                color="text.primary"
                sx={{
                  lineHeight: 1.25,
                  fontFamily: stat.mono
                    ? "ui-monospace, SFMono-Regular, monospace"
                    : "inherit",
                  fontVariantNumeric: "tabular-nums",
                  whiteSpace: "nowrap",
                }}
              >
                {stat.value}
              </Typography>
            </Box>
          ))}
        </Stack>
      )}

      {/* ── Body ── */}
      <Box sx={{ px: 2, pb: 2, pt: 0.25 }}>
        {/* Agent Graph / Agent Path — single trace, or split with a working trace. */}
        {isGraphMode &&
          (traceId ? (
            splitView ? (
              <TraceGraphCompare
                failingTraceId={traceId}
                workingTraceId={workingTraceId}
                mode={viewMode === "agentpath" ? "path" : "graph"}
                projectId={projectId}
              />
            ) : (
              <TraceGraphView
                traceId={traceId}
                mode={viewMode === "agentpath" ? "path" : "graph"}
                projectId={projectId}
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

        {/* Breadcrumb mode — single reel or side-by-side Failing vs Working */}
        {isBreadcrumb &&
          (!splitView ? (
            <Stack gap={1.5}>
              {isFailActive && truthLanes.length >= 2 && (
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: {
                      xs: "1fr",
                      sm: `repeat(${truthLanes.length}, 1fr)`,
                    },
                    gap: 1.25,
                  }}
                >
                  {truthLanes.map((lane, idx) => (
                    <Stack
                      key={idx}
                      gap={0.25}
                      sx={{
                        px: 1.4,
                        py: 1,
                        border: "1px solid",
                        borderColor: lane.is_failure
                          ? "status.danger.border"
                          : lane.is_origin
                            ? "status.warning.border"
                            : "divider",
                        borderRadius: "8px",
                        bgcolor: "background.paper",
                      }}
                    >
                      <Stack direction="row" justifyContent="space-between" gap={0.5}>
                        <Typography
                          fontSize="11px"
                          fontWeight={600}
                          color={
                            lane.is_failure
                              ? "status.danger.fg"
                              : lane.is_origin
                                ? "status.warning.fg"
                                : "text.secondary"
                          }
                        >
                          {idx + 1}. {lane.stage}
                        </Typography>
                        <Typography
                          fontSize="11px"
                          color="text.tertiary"
                          sx={{ fontFamily: "ui-monospace, SFMono-Regular, monospace" }}
                        >
                          {lane.span_alias}
                        </Typography>
                      </Stack>
                      <Typography fontSize="13px" fontWeight={600} color="text.primary" noWrap>
                        {lane.value}
                      </Typography>
                      <Typography fontSize="11px" color="text.secondary" noWrap>
                        {lane.detail}
                      </Typography>
                    </Stack>
                  ))}
                </Box>
              )}

              <BreadcrumbList
                steps={steps}
                isFailReel={isFailActive}
                userRequest={isFailActive ? evidence.input : null}
                onInspectSpan={isFailActive ? onInspectSpan : undefined}
                onClaimClick={isFailActive ? onClaimClick : undefined}
              />
            </Stack>
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
                headerMeta={shortId || undefined}
                role="danger"
                steps={failReel}
                isFailReel
                userRequest={evidence.input}
                onInspectSpan={onInspectSpan}
                onClaimClick={onClaimClick}
                emptyMessage="No failing-trace evidence available."
              />
              <ReelColumn
                title="Expected path"
                headerMeta={hasPassing ? "reference baseline" : undefined}
                role="success"
                steps={passReel}
                isFailReel={false}
                emptyMessage="No reference expected path available for this issue yet."
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
  projectId: PropTypes.string,
  onInspectSpan: PropTypes.func,
  onClaimClick: PropTypes.func,
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
          px: 1.5,
          py: 1,
          cursor: "pointer",
          "&:hover": {
            bgcolor: "action.hover",
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

            {/* Immediate Fix */}
            <Box>
              <RecSectionLabel
                icon="solar:toolbox-linear"
                label="Immediate fix"
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
        <SectionCard
          title="Recommendations & Fixes"
          icon="solar:lightbulb-linear"
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
export default function OverviewTab({
  _error: currentError,
  onOpenFix,
  onOpenTraces,
}) {
  const [leftWidth, setLeftWidth] = useState(276);
  const [traceFilter, setTraceFilter] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerInitialSpanId, setDrawerInitialSpanId] = useState(null);
  const [claimPopover, setClaimPopover] = useState({ anchorEl: null, phrase: "", prov: null });
  const containerRef = useRef(null);
  const isDraggingRef = useRef(false);
  const clusterId = currentError?.cluster_id;
  // A cluster's modality (text vs voice) decides the per-trace surface —
  // the BE derives it from the project's voice agent definition.
  const isVoice = currentError?.modality === "voice";
  const shared = useSharedIssue();
  const { data: fetchedOverview, isLoading: isFetchingOverview } =
    useErrorFeedOverview(shared ? null : clusterId);
  const overview = shared ? shared.overview : fetchedOverview;
  const isOverviewLoading = shared ? false : isFetchingOverview;
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
  const totalEventsInWindow = useMemo(
    () => (eventsOverTime || []).reduce((acc, d) => acc + Number(d.errors || 0), 0),
    [eventsOverTime],
  );
  const managerSummary = trace?.omega_deep?.manager_summary || null;
  const impactText = managerSummary?.user_impact || currentError?.rca?.user_impact || "";
  const rootCauseText =
    managerSummary?.why_it_happened || currentError?.rca?.synthesis || currentError?.description || "";
  const suggestedFixText = managerSummary?.recommended_fix || currentError?.rca?.fix || "";

  const selectTrace = (i) => {
    const next = traces[i];
    if (next && clusterId) setSelectedTraceId(clusterId, next.id);
  };

  const openNativeTraceDrawer = (spanId = null) => {
    setClaimPopover({ anchorEl: null, phrase: "", prov: null });
    setDrawerInitialSpanId(spanId || trace?.origin_span_id || null);
    setDrawerOpen(true);
  };

  // Draggable divider handlers
  useEffect(() => {
    const onMove = (e) => {
      if (!isDraggingRef.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const newW = Math.min(Math.max(e.clientX - rect.left, 230), 540);
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
    <Stack gap={2} sx={{ minHeight: 0 }}>
      {(impactText || rootCauseText || suggestedFixText) && (
        <Stack
          gap={0.85}
          sx={{
            px: 2,
            py: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
          }}
        >
          {impactText && (
            <Stack direction={{ xs: "column", sm: "row" }} alignItems="baseline" gap={{ xs: 0.25, sm: 2 }}>
              <Typography typography="s3" color="text.tertiary" sx={{ width: 88, flexShrink: 0 }}>
                Impact
              </Typography>
              <Typography typography="s1" fontWeight={500} color="text.primary" sx={{ lineHeight: 1.5 }}>
                {impactText}
              </Typography>
            </Stack>
          )}
          {rootCauseText && (
            <Stack direction={{ xs: "column", sm: "row" }} alignItems="baseline" gap={{ xs: 0.25, sm: 2 }}>
              <Typography typography="s3" color="text.tertiary" sx={{ width: 88, flexShrink: 0 }}>
                Root cause
              </Typography>
              <Typography typography="s1" color="text.secondary" sx={{ lineHeight: 1.5 }}>
                {rootCauseText}
              </Typography>
            </Stack>
          )}
          {suggestedFixText && (
            <Stack direction={{ xs: "column", sm: "row" }} alignItems="baseline" gap={{ xs: 0.25, sm: 2 }}>
              <Typography typography="s3" color="text.tertiary" sx={{ width: 88, flexShrink: 0 }}>
                Suggested fix
              </Typography>
              <Stack
                direction={{ xs: "column", md: "row" }}
                alignItems={{ xs: "flex-start", md: "baseline" }}
                justifyContent="space-between"
                gap={1.25}
                sx={{ flex: 1, minWidth: 0 }}
              >
                <Typography
                  typography="s1"
                  color="text.secondary"
                  sx={{
                    lineHeight: 1.5,
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {suggestedFixText}
                </Typography>
                {onOpenFix && (
                  <Button
                    size="small"
                    variant="text"
                    onClick={onOpenFix}
                    endIcon={<Iconify icon="solar:arrow-right-linear" width={13} />}
                    sx={{ px: 0, minWidth: 0, flexShrink: 0, whiteSpace: "nowrap" }}
                  >
                    Review & verify fix
                  </Button>
                )}
              </Stack>
            </Stack>
          )}
        </Stack>
      )}

      {/* Master-detail workspace — flat DS surfaces with 1px hairline borders */}
      <Box
        ref={containerRef}
        sx={{
          display: "flex",
          gap: 0,
          minHeight: 540,
          flex: 1,
          overflow: "hidden",
          bgcolor: "background.paper",
        }}
      >
        <Stack
          sx={{
            width: leftWidth,
            flexShrink: 0,
            overflow: "hidden",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
          }}
        >
          {/* Show chart only when enough volume; otherwise a clean 1-line 7d summary */}
          <Box sx={{ flexShrink: 0 }}>
            {totalEventsInWindow >= 5 ? (
              <EventsUsersChart
                flat
                data={eventsOverTime}
                deployMarkers={[]}
                loading={isOverviewLoading && !overview}
              />
            ) : (
              <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 1.5, pt: 1.25, pb: 0.5 }}>
                <Typography typography="s3" color="text.tertiary">
                  Last 7 days
                </Typography>
                <Typography typography="s3" fontWeight={500} color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
                  {repTotal.toLocaleString()} {repTotal === 1 ? "event" : "events"} ·{" "}
                  {(currentError?.users_affected ?? 1).toLocaleString()}{" "}
                  {(currentError?.users_affected ?? 1) === 1 ? "user" : "users"}
                </Typography>
              </Stack>
            )}
          </Box>

          {/* Traces / Conversations heading + inline filter */}
          <Stack
            gap={0.75}
            sx={{
              px: 1.5,
              py: 0.85,
              flexShrink: 0,
            }}
          >
            <Stack direction="row" alignItems="center" justifyContent="space-between" gap={0.75}>
              <Stack direction="row" alignItems="center" gap={0.65}>
                <Typography fontSize="12px" fontWeight={600} color="text.secondary">
                  Traces affected
                </Typography>
                {isOverviewLoading && !overview ? (
                  <Skeleton width={28} height={14} sx={{ borderRadius: "4px", bgcolor: "action.selected" }} />
                ) : (
                  <Typography
                    fontSize="11px"
                    fontWeight={600}
                    sx={{
                      color: "text.secondary",
                      bgcolor: "action.selected",
                      px: 0.7,
                      py: 0.1,
                      borderRadius: "4px",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {repTotal.toLocaleString()}
                  </Typography>
                )}
              </Stack>
              {onOpenTraces && (
                <Button
                  size="small"
                  variant="text"
                  onClick={onOpenTraces}
                  sx={{ minWidth: 0, px: 0.5, py: 0, color: "text.secondary" }}
                >
                  All traces →
                </Button>
              )}
            </Stack>

            {traces.length > 2 && (
              <FormSearchField
                searchQuery={traceFilter}
                onChange={(e) => setTraceFilter(e.target.value)}
                placeholder="Filter traces or conversations"
              />
            )}
          </Stack>

          {/* Scrollable trace / conversation list */}
          <Box sx={{ flex: 1, overflow: "auto" }}>
            <TraceList
              traces={traces}
              selectedIndex={traceIndex}
              onSelect={selectTrace}
              filterQuery={traceFilter}
              loading={isOverviewLoading && !overview}
            />
            {repTotal > traces.length && (
              <Stack alignItems="center" sx={{ px: 1.5, py: 1.25 }}>
                <Typography fontSize="12px" color="text.disabled" sx={{ textAlign: "center" }}>
                  Showing {traces.length} most recent of {repTotal.toLocaleString()}
                </Typography>
                {onOpenTraces && (
                  <Button size="small" variant="text" onClick={onOpenTraces} sx={{ mt: 0.25 }}>
                    Browse all in Traces tab →
                  </Button>
                )}
              </Stack>
            )}
          </Box>
        </Stack>

        {/* ── QUIET DRAG HANDLE (invisible until hovered — zero vertical border clutter) ── */}
        <Box
          onMouseDown={(e) => {
            e.preventDefault();
            isDraggingRef.current = true;
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
          }}
          sx={{
            width: 8,
            flexShrink: 0,
            cursor: "col-resize",
            bgcolor: "transparent",
            position: "relative",
            borderRadius: "4px",
            transition: "background 0.15s",
            "&:hover": {
              bgcolor: "action.hover",
            },
            "&:hover .drag-dots": { opacity: 1 },
          }}
        >
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
                  bgcolor: "text.disabled",
                }}
              />
            ))}
          </Stack>
        </Box>

        {/* ── RIGHT PANEL: Flush Trace Evidence Canvas (zero enclosing box!) ── */}
        <Box sx={{ flex: 1, minWidth: 0, overflow: "auto" }}>
          {isOverviewLoading && !trace ? (
            <Stack gap={1.5} sx={{ p: 2 }}>
              <Skeleton variant="rectangular" height={56} sx={{ borderRadius: "8px" }} />
              <Skeleton variant="rectangular" height={140} sx={{ borderRadius: "8px" }} />
              <Skeleton variant="rectangular" height={260} sx={{ borderRadius: "8px" }} />
            </Stack>
          ) : !trace ? (
            <EmptyState
              icon="solar:file-search-linear"
              title="No trace evidence yet"
              description="No trace evidence available for this cluster yet."
              sx={{ pt: 6, pb: 6 }}
            />
          ) : currentError?.source === "eval" ? (
            <Box sx={{ p: 1.5 }}>
              <SectionCard
                title={isVoice ? "Voice call" : "Input / Output"}
                icon={isVoice ? "solar:phone-linear" : "solar:code-linear"}
                collapsible
              >
                {isVoice && !shared ? (
                  <VoiceEvalPanel
                    trace={trace}
                    evalScore={trace?.eval_score}
                    successTraceId={currentError?.success_trace?.trace_id}
                    projectId={currentError?.project_id}
                  />
                ) : (
                  <EvalIOPanel trace={trace} evalScore={trace?.eval_score} />
                )}
              </SectionCard>
            </Box>
          ) : (
            <TraceEvidence
              key={trace.id}
              evidence={trace.evidence ?? {}}
              trace={trace}
              traceId={trace.id}
              workingTraceId={currentError?.success_trace?.trace_id}
              projectId={currentError?.project_id}
              onInspectSpan={openNativeTraceDrawer}
              onClaimClick={(anchorEl, phrase, prov) =>
                setClaimPopover({ anchorEl, phrase, prov })
              }
            />
          )}
        </Box>
      </Box>

      {/* ── Interactive Claim-to-Source Popover (jumps straight into native TraceDetailDrawerV2!) ── */}
      <Popover
        open={Boolean(claimPopover.anchorEl && claimPopover.prov)}
        anchorEl={claimPopover.anchorEl}
        onClose={() => setClaimPopover({ anchorEl: null, phrase: "", prov: null })}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        PaperProps={{
          sx: {
            width: 340,
            p: 1.75,
            mt: 0.75,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          },
        }}
      >
        {claimPopover.prov && (
          <Stack gap={1.25}>
            <Stack gap={0.25}>
              <Typography typography="s3" fontWeight={600} color="status.danger.fg">
                Contradicted claim{claimPopover.prov.entity_label ? ` · ${claimPopover.prov.entity_label}` : ""}
              </Typography>
              <Typography typography="s1" fontWeight={500} color="text.primary">
                “{claimPopover.phrase || claimPopover.prov.claimed_text}”
              </Typography>
            </Stack>

            <Stack gap={0.6} sx={{ pt: 0.75, borderTop: "1px solid", borderColor: "divider" }}>
              {claimPopover.prov.facts.map((fact) => (
                <Stack key={fact.label} direction="row" alignItems="baseline" justifyContent="space-between" gap={1}>
                  <Typography typography="s3" color="text.secondary">
                    {fact.label}
                  </Typography>
                  <Typography typography="s3" fontWeight={600} color="text.primary">
                    {fact.value}
                  </Typography>
                </Stack>
              ))}
            </Stack>

            <Stack direction="row" justifyContent="space-between" gap={1} sx={{ pt: 0.5 }}>
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
                  const st = (trace?.omega_deep?.story_steps || []).find(
                    (s) => s.span_alias === claimPopover.prov.tool_alias,
                  );
                  openNativeTraceDrawer(st?.span_id || trace?.origin_span_id || null);
                }}
                sx={{ borderColor: "divider", color: "text.primary" }}
              >
                Open tool span ({claimPopover.prov.tool_alias})
              </Button>
              <Button
                size="small"
                variant="text"
                onClick={() => {
                  const st = (trace?.omega_deep?.story_steps || []).find(
                    (s) => s.span_alias === claimPopover.prov.reply_alias,
                  );
                  openNativeTraceDrawer(st?.span_id || null);
                }}
                sx={{ color: "text.secondary" }}
              >
                Open reply ({claimPopover.prov.reply_alias})
              </Button>
            </Stack>
          </Stack>
        )}
      </Popover>

      {drawerOpen && Boolean(trace?.id) && (
        <TraceDetailDrawerV2
          traceId={trace.id}
          initialSpanId={drawerInitialSpanId}
          open
          onClose={() => setDrawerOpen(false)}
          projectId={currentError?.project_id}
          clusterId={clusterId}
          defaultCompressed
          readOnly={!!shared}
          sharedToken={shared?.token ?? null}
          hasPrev={traceIndex > 0}
          hasNext={traceIndex < traces.length - 1}
          onPrev={() => {
            if (traceIndex > 0) {
              const prevTrace = traces[traceIndex - 1];
              selectTrace(traceIndex - 1);
              setDrawerInitialSpanId(prevTrace?.origin_span_id || null);
            }
          }}
          onNext={() => {
            if (traceIndex < traces.length - 1) {
              const nextTrace = traces[traceIndex + 1];
              selectTrace(traceIndex + 1);
              setDrawerInitialSpanId(nextTrace?.origin_span_id || null);
            }
          }}
        />
      )}
    </Stack>
  );
}

OverviewTab.propTypes = {
  _error: PropTypes.shape({
    cluster_id: PropTypes.string,
    project_id: PropTypes.string,
    source: PropTypes.string,
    modality: PropTypes.string,
    description: PropTypes.string,
    users_affected: PropTypes.number,
    rca: PropTypes.shape({
      user_impact: PropTypes.string,
      synthesis: PropTypes.string,
      fix: PropTypes.string,
    }),
    success_trace: PropTypes.shape({
      trace_id: PropTypes.string,
    }),
  }),
  onOpenFix: PropTypes.func,
  onOpenTraces: PropTypes.func,
};
