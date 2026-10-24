import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography, Skeleton } from "@mui/material";
import Section from "src/components/section";
import MetricStrip from "src/components/metric-strip";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";
import { useAnalyticsLatency } from "./hooks/useAnalyticsLatency";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function computeGranularity(start, end) {
  if (!start || !end) return "1h";
  const diffMs = new Date(end).getTime() - new Date(start).getTime();
  const diffHours = diffMs / (1000 * 60 * 60);
  if (diffHours <= 6) return "5m";
  if (diffHours <= 24) return "15m";
  if (diffHours <= 168) return "1h";
  if (diffHours <= 720) return "6h";
  return "1d";
}

function formatLatency(value) {
  if (value == null) return "--";
  const num = Number(value);
  if (Number.isNaN(num)) return "--";
  if (num >= 1000) return `${(num / 1000).toFixed(2)}s`;
  return `${Math.round(num)}ms`;
}

// ---------------------------------------------------------------------------
// Summary stat cards
// ---------------------------------------------------------------------------

const SUMMARY_STATS = [
  { key: "p50_ms", label: "P50" },
  { key: "p95_ms", label: "P95" },
  { key: "p99_ms", label: "P99" },
  { key: "avg_ms", label: "Average" },
];

// ---------------------------------------------------------------------------
// Chart builder
// ---------------------------------------------------------------------------

function buildLatencyChartOptions(theme) {
  return {
    chart: {
      type: "line",
      toolbar: { show: false },
      zoom: { enabled: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors: [
      theme.palette.success.main, // P50
      theme.palette.warning.main, // P95
      theme.palette.error.main, // P99
    ],
    dataLabels: { enabled: false },
    stroke: {
      curve: "smooth",
      width: [2, 2, 2],
      dashArray: [0, 5, 8], // P50 solid, P95 dashed, P99 dotted
    },
    xaxis: {
      type: "datetime",
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "12px" },
      },
    },
    yaxis: {
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "12px" },
        formatter: (val) => formatLatency(val),
      },
      title: {
        text: "Latency (ms)",
        style: {
          color: theme.palette.text.secondary,
          fontSize: "12px",
          fontWeight: 500,
        },
      },
    },
    tooltip: {
      theme: theme.palette.mode,
      x: { format: "MMM dd, HH:mm" },
      y: { formatter: (val) => formatLatency(val) },
    },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 3,
    },
    legend: {
      position: "top",
      horizontalAlign: "left",
      labels: { colors: theme.palette.text.secondary },
      markers: { width: 12, height: 3, radius: 0 },
    },
    responsive: [
      {
        breakpoint: 600,
        options: { chart: { height: 280 }, legend: { position: "bottom" } },
      },
    ],
  };
}

function buildSeries(timeseries) {
  if (!timeseries?.length) return [];
  return [
    {
      name: "P50",
      data: timeseries.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: point.p50_ms ?? 0,
      })),
    },
    {
      name: "P95",
      data: timeseries.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: point.p95_ms ?? 0,
      })),
    },
    {
      name: "P99",
      data: timeseries.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: point.p99_ms ?? 0,
      })),
    },
  ];
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const LatencyAnalytics = ({ start, end, gatewayId }) => {
  const theme = useTheme();

  const granularity = useMemo(
    () => computeGranularity(start, end),
    [start, end],
  );

  const { data, isLoading } = useAnalyticsLatency({
    start,
    end,
    granularity,
    gatewayId,
  });

  const summary = data?.summary;
  const timeseries = data?.timeseries;

  const chartSeries = useMemo(() => buildSeries(timeseries), [timeseries]);
  const chartOptions = useMemo(() => buildLatencyChartOptions(theme), [theme]);

  return (
    <Box>
      <MetricStrip
        sx={{ mb: 3 }}
        items={SUMMARY_STATS.map((stat) => ({
          key: stat.key,
          label: stat.label,
          value: isLoading ? (
            <Skeleton variant="text" width={64} />
          ) : (
            formatLatency(summary?.[stat.key])
          ),
        }))}
      />

      {/* Latency percentiles over time */}
      <Section variant="panel" sx={{ p: 3 }}>
        <Typography variant="subtitle1" fontWeight={600} mb={2}>
          Latency Percentiles Over Time
        </Typography>
        {isLoading ? (
          <Skeleton
            variant="rectangular"
            height={380}
            sx={{ borderRadius: 1 }}
          />
        ) : chartSeries.length > 0 && chartSeries[0].data.length > 0 ? (
          <Chart
            type="line"
            height={380}
            options={chartOptions}
            series={chartSeries}
          />
        ) : (
          <Box
            display="flex"
            alignItems="center"
            justifyContent="center"
            height={380}
          >
            <Typography variant="body2" color="text.secondary">
              No latency data available for this time range.
            </Typography>
          </Box>
        )}
      </Section>
    </Box>
  );
};

LatencyAnalytics.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default LatencyAnalytics;
