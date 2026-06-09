import React, { useEffect, useRef } from "react";
import ApexCharts from "apexcharts";
import { format } from "date-fns";
import {
  Box,
  CircularProgress,
  Stack,
  Tooltip,
  Typography,
  alpha,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useErrorFeedTrends } from "src/api/errorFeed/error-feed";

// ── Events over time — dual-axis area + bars ────────────────────────────────
function EventsLineChart({ eventsData }) {
  const chartRef = useRef(null);
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const axisLabelColor = isDark ? "#71717a" : "#938FA3";
  const gridColor = isDark ? "#27272a" : "#E1DFEC";
  const barColor = isDark ? "rgba(147,197,253,0.20)" : "rgba(147,197,253,0.50)";
  const areaColor = isDark ? "#4F8EF7" : "#2563EB";

  useEffect(() => {
    if (!eventsData?.length || !chartRef.current) return;

    const areaData = eventsData.map((d) => ({
      x: new Date(d.date).getTime(),
      y: d.errors,
    }));
    // "Traffic" = total attempted = errors + passing (scanner-wide context).
    const trafficData = eventsData.map((d) => ({
      x: new Date(d.date).getTime(),
      y: (d.errors ?? 0) + (d.passing ?? 0),
    }));

    const options = {
      chart: {
        type: "line",
        height: 180,
        toolbar: { show: false },
        background: "transparent",
        animations: { enabled: false },
        zoom: { enabled: false },
      },
      series: [
        { name: "Errors", type: "area", data: areaData },
        { name: "Traffic", type: "bar", data: trafficData },
      ],
      stroke: { width: [2, 0], curve: "smooth" },
      colors: [areaColor, barColor],
      fill: {
        type: ["gradient", "solid"],
        gradient: {
          shade: "dark",
          type: "vertical",
          shadeIntensity: 0,
          gradientToColors: [areaColor],
          inverseColors: false,
          opacityFrom: isDark ? 0.28 : 0.22,
          opacityTo: 0.02,
          stops: [0, 100],
        },
        opacity: [1, 1],
      },
      plotOptions: { bar: { columnWidth: "90%", borderRadius: 0 } },
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
        },
        crosshairs: {
          show: true,
          stroke: { color: gridColor, width: 1, dashArray: 3 },
        },
      },
      yaxis: [
        {
          seriesName: "Errors",
          title: {
            text: "Errors",
            style: {
              fontSize: "10px",
              color: axisLabelColor,
              fontWeight: 400,
              cssClass: "",
            },
          },
          labels: {
            style: { fontSize: "10px", colors: axisLabelColor },
            formatter: (v) =>
              v >= 1000 ? `${(v / 1000).toFixed(1)}k` : Math.round(v),
          },
        },
        {
          seriesName: "Traffic",
          opposite: true,
          title: {
            text: "Traffic",
            style: {
              fontSize: "10px",
              color: axisLabelColor,
              fontWeight: 400,
              cssClass: "",
            },
          },
          labels: {
            style: { fontSize: "10px", colors: axisLabelColor },
            formatter: (v) =>
              v >= 1000 ? `${(v / 1000).toFixed(1)}k` : Math.round(v),
          },
        },
      ],
      grid: {
        borderColor: gridColor,
        strokeDashArray: 3,
        padding: { top: 0, right: 12, bottom: 0, left: 4 },
        xaxis: { lines: { show: false } },
        yaxis: { lines: { show: true } },
      },
      legend: {
        show: true,
        position: "top",
        horizontalAlign: "right",
        fontSize: "11px",
        labels: { colors: isDark ? "#a1a1aa" : "#605C70" },
        markers: { width: 8, height: 8, radius: 2 },
        itemMargin: { horizontal: 8 },
      },
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

    const chart = new ApexCharts(chartRef.current, options);
    chart.render();
    return () => {
      try {
        chart.destroy();
      } catch {
        /* ignore */
      }
    };
  }, [eventsData, isDark, axisLabelColor, gridColor, barColor, areaColor]);

  return (
    <Box sx={{ px: 0.5, pb: 0.5 }}>
      <div ref={chartRef} />
    </Box>
  );
}
EventsLineChart.propTypes = {
  eventsData: PropTypes.array.isRequired,
};

// ── Score trends ─────────────────────────────────────────────────────────────
// Known eval labels get distinctive icons; everything else falls back to the
// generic chart-line icon. `invertDelta` flips the red/green logic for evals
// where a HIGHER score is BAD (hallucination, toxicity, etc).
const SCORE_META = {
  Faithfulness: { icon: "mdi:flag-checkered", invertDelta: false },
  Relevancy: { icon: "mdi:target", invertDelta: false },
  Precision: { icon: "mdi:check-decagram-outline", invertDelta: false },
  Hallucination: { icon: "mdi:brain", invertDelta: true },
};

function ScoreTrends({ scoreTrends }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  if (!scoreTrends?.length) {
    return (
      <Typography fontSize="11px" color="text.disabled" sx={{ py: 1 }}>
        No eval scores recorded for this cluster yet.
      </Typography>
    );
  }

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: `repeat(${Math.min(scoreTrends.length, 4)}, 1fr)`,
        gap: 1.25,
      }}
    >
      {scoreTrends.map((s) => {
        const meta = SCORE_META[s.label] || {
          icon: "mdi:chart-line",
          invertDelta: false,
        };
        const delta = s.current - s.prev;
        const isPos = delta > 0;
        const isBad = meta.invertDelta ? isPos : !isPos;
        const deltaColor =
          delta === 0
            ? isDark
              ? "#71717a"
              : "#938FA3"
            : isBad
              ? "#DB2F2D"
              : "#5ACE6D";
        const trendIcon = isPos ? "mdi:trending-up" : "mdi:trending-down";

        return (
          <Box
            key={s.label}
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "10px",
              p: 1.75,
              bgcolor: "transparent",
              position: "relative",
            }}
          >
            <Stack
              direction="row"
              alignItems="flex-start"
              justifyContent="space-between"
              mb={1.5}
            >
              <Typography
                fontSize="12px"
                color="text.disabled"
                fontWeight={400}
                sx={{ lineHeight: 1.3 }}
                noWrap
                title={s.label}
              >
                {s.label}
              </Typography>
              <Box
                sx={{
                  width: 28,
                  height: 28,
                  borderRadius: "6px",
                  bgcolor: isDark ? alpha("#fff", 0.04) : alpha("#000", 0.03),
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                <Iconify
                  icon={meta.icon}
                  width={15}
                  sx={{ color: "text.disabled" }}
                />
              </Box>
            </Stack>

            <Typography
              fontSize="26px"
              fontWeight={700}
              sx={{
                color: "#fff",
                lineHeight: 1,
                mb: 1.25,
                fontFeatureSettings: "'tnum'",
              }}
            >
              {s.current.toFixed(2)}
            </Typography>

            <Stack direction="row" alignItems="center" gap={0.4}>
              <Iconify icon={trendIcon} width={12} sx={{ color: deltaColor }} />
              <Typography
                fontSize="11px"
                fontWeight={600}
                sx={{ color: deltaColor }}
              >
                {isPos ? "+" : ""}
                {Math.abs(delta).toFixed(2)}
              </Typography>
              <Typography
                fontSize="11px"
                color="text.disabled"
                sx={{ ml: 0.25 }}
              >
                vs prev
              </Typography>
            </Stack>
          </Box>
        );
      })}
    </Box>
  );
}
ScoreTrends.propTypes = { scoreTrends: PropTypes.array.isRequired };

// ── Activity heatmap ────────────────────────────────────────────────────────
function ActivityHeatmap({ data }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const allValues = data.flat().map((d) => d.value);
  const maxVal = Math.max(...allValues) || 1;

  const getColor = (val) => {
    const norm = val / maxVal;
    if (norm === 0)
      return isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)";
    if (norm < 0.25)
      return isDark ? alpha("#DB2F2D", 0.2) : alpha("#DB2F2D", 0.15);
    if (norm < 0.5)
      return isDark ? alpha("#DB2F2D", 0.4) : alpha("#DB2F2D", 0.3);
    if (norm < 0.75)
      return isDark ? alpha("#DB2F2D", 0.65) : alpha("#DB2F2D", 0.55);
    return isDark ? alpha("#DB2F2D", 0.85) : alpha("#DB2F2D", 0.75);
  };

  const hourLabels = Array.from({ length: 24 }, (_, i) =>
    i % 3 === 0 ? `${String(i).padStart(2, "0")}:00` : "",
  );

  return (
    <Box sx={{ overflowX: "auto" }}>
      <Box sx={{ minWidth: 600 }}>
        <Stack direction="row" sx={{ ml: "36px", mb: 0.25 }}>
          {hourLabels.map((label, i) => (
            <Box key={i} sx={{ flex: 1, minWidth: 0 }}>
              {label && (
                <Typography fontSize="9px" color="text.disabled" noWrap>
                  {label}
                </Typography>
              )}
            </Box>
          ))}
        </Stack>

        {data.map((row, dayIdx) => (
          <Stack
            key={dayIdx}
            direction="row"
            alignItems="center"
            gap={0}
            sx={{ mb: 0.25 }}
          >
            <Typography
              fontSize="10px"
              color="text.disabled"
              sx={{ width: 32, flexShrink: 0, textAlign: "right", pr: 0.75 }}
            >
              {days[dayIdx]}
            </Typography>
            {row.map((cell) => (
              <Tooltip
                key={cell.hour}
                title={`${days[cell.day]} ${String(cell.hour).padStart(2, "0")}:00 — ${cell.value} errors`}
                arrow
              >
                <Box
                  sx={{
                    flex: 1,
                    height: 14,
                    bgcolor: getColor(cell.value),
                    borderRadius: "2px",
                    mx: "1px",
                    cursor: "default",
                    transition: "opacity 0.1s",
                    "&:hover": { opacity: 0.75 },
                  }}
                />
              </Tooltip>
            ))}
          </Stack>
        ))}

        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          mt={1}
          sx={{ ml: "36px" }}
        >
          <Typography fontSize="10px" color="text.disabled">
            Low
          </Typography>
          {[0.1, 0.25, 0.5, 0.75, 1.0].map((n) => (
            <Box
              key={n}
              sx={{
                width: 12,
                height: 12,
                borderRadius: "2px",
                bgcolor: getColor(n * maxVal),
              }}
            />
          ))}
          <Typography fontSize="10px" color="text.disabled">
            High
          </Typography>
        </Stack>
      </Box>
    </Box>
  );
}
ActivityHeatmap.propTypes = { data: PropTypes.array.isRequired };

// ── Section card ────────────────────────────────────────────────────────────
function SectionCard({ title, icon, children }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        bgcolor: isDark ? alpha("#fff", 0.02) : "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{
          px: 1.75,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: isDark ? alpha("#fff", 0.025) : alpha("#000", 0.02),
        }}
      >
        {icon && (
          <Iconify icon={icon} width={15} sx={{ color: "text.disabled" }} />
        )}
        <Typography
          fontSize="12px"
          fontWeight={600}
          color="text.secondary"
          sx={{ textTransform: "uppercase", letterSpacing: "0.05em" }}
        >
          {title}
        </Typography>
      </Stack>
      <Box sx={{ p: 1.75 }}>{children}</Box>
    </Box>
  );
}
SectionCard.propTypes = {
  title: PropTypes.string,
  icon: PropTypes.string,
  children: PropTypes.node,
};

// ── Top metric cards ────────────────────────────────────────────────────────
function MetricCards({ metrics }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  return (
    <Box
      sx={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 1 }}
    >
      {metrics.map((m, i) => {
        // "Affected users" treats a drop as good, but for error-rate and
        // eval-score the direction is the same sign convention as for errors:
        // positive delta = red. Eval score is the exception — higher is
        // better. Keep it simple: let backend signs speak, frontend colors.
        const isErrorMetric = m.label !== "Avg eval score";
        const isNeg = isErrorMetric ? m.delta > 0 : m.delta < 0;
        const badgeColor = isNeg ? "#DB2F2D" : "#5ACE6D";
        const badgeIcon = m.delta > 0 ? "mdi:trending-up" : "mdi:trending-down";
        return (
          <Box
            key={m.label}
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              p: 1.5,
              bgcolor: "transparent",
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              mb={0.5}
            >
              <Typography fontSize="11px" color="text.disabled">
                {m.label}
              </Typography>
              <Box
                sx={{
                  width: 24,
                  height: 24,
                  borderRadius: "5px",
                  bgcolor: isDark ? alpha("#fff", 0.06) : alpha("#000", 0.05),
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <Iconify
                  icon={
                    i === 0
                      ? "mdi:alert-circle-outline"
                      : i === 1
                        ? "mdi:speedometer"
                        : "mdi:account-multiple-outline"
                  }
                  width={13}
                  sx={{ color: "text.secondary" }}
                />
              </Box>
            </Stack>
            <Typography
              fontSize="22px"
              fontWeight={700}
              sx={{ color: "#fff", lineHeight: 1.1 }}
            >
              {m.value}
            </Typography>
            <Stack direction="row" alignItems="center" gap={0.4} mt={0.5}>
              <Iconify icon={badgeIcon} width={12} sx={{ color: badgeColor }} />
              <Typography
                fontSize="11px"
                fontWeight={600}
                sx={{ color: badgeColor }}
              >
                {m.delta > 0 ? "+" : ""}
                {typeof m.delta === "number"
                  ? Math.abs(m.delta) < 1
                    ? m.delta.toFixed(2)
                    : m.delta
                  : m.delta}
                {m.unit}
              </Typography>
              <Typography fontSize="11px" color="text.disabled">
                vs prev period
              </Typography>
            </Stack>
          </Box>
        );
      })}
    </Box>
  );
}
MetricCards.propTypes = { metrics: PropTypes.array.isRequired };

// ── Main TrendsTab ──────────────────────────────────────────────────────────
export default function TrendsTab({ error }) {
  const clusterId = error?.clusterId;
  const { data, isLoading } = useErrorFeedTrends(clusterId);

  if (isLoading || !data) {
    return (
      <Stack alignItems="center" justifyContent="center" sx={{ py: 6 }}>
        <CircularProgress size={18} />
      </Stack>
    );
  }

  const hasHeatmapData = (data.activityHeatmap ?? []).some((row) =>
    row.some((cell) => cell.value > 0),
  );

  return (
    <Stack gap={2}>
      {/* Top metrics */}
      {data.metrics?.length > 0 && <MetricCards metrics={data.metrics} />}

      {/* Events over time */}
      {data.eventsOverTime?.length > 0 && (
        <SectionCard title="Events Over Time" icon="mdi:chart-bar">
          <EventsLineChart eventsData={data.eventsOverTime} />
        </SectionCard>
      )}

      {/* Score trends */}
      <SectionCard title="Score Trends" icon="mdi:chart-line">
        <ScoreTrends scoreTrends={data.scoreTrends ?? []} />
      </SectionCard>

      {/* Activity heatmap */}
      {hasHeatmapData && (
        <SectionCard title="Activity Heatmap (errors by hour)" icon="mdi:grid">
          <ActivityHeatmap data={data.activityHeatmap} />
        </SectionCard>
      )}
    </Stack>
  );
}

TrendsTab.propTypes = {
  error: PropTypes.object,
};
