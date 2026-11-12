import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";
import { useAnalyticsLatency } from "./hooks/useAnalyticsLatency";
import { formatMs } from "../logs/logStatus";
import ChartPanel, { AnalyticsQueryState } from "./ChartPanel";
import { isWholeTick } from "./axisLabels";
import { EMPTY } from "../utils/formatters";
import {
  UTC_DAYS_NOTE,
  bucketTime,
  localTimeAxis,
  localTimeTooltip,
} from "../utils/timeZone";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatLatency(value) {
  if (value == null) return EMPTY;
  const num = Number(value);
  if (Number.isNaN(num)) return EMPTY;
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

function buildLatencyChartOptions(theme, time) {
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
    xaxis: localTimeAxis({
      color: theme.palette.text.secondary,
      fontSize: "12px",
      utc: time.utc,
    }),
    yaxis: {
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "12px" },
        // Whole milliseconds only (axisLabels.js); the tooltip shows each
        // point's own value.
        formatter: (val) =>
          val != null && Number(val) < 1000 && !isWholeTick(val)
            ? ""
            : formatLatency(val),
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
      x: localTimeTooltip(time),
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
        y: Number(point.request_count) > 0 ? point.p50_ms ?? null : null,
      })),
    },
    {
      name: "P95",
      data: timeseries.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: Number(point.request_count) > 0 ? point.p95_ms ?? null : null,
      })),
    },
    {
      name: "P99",
      data: timeseries.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: Number(point.request_count) > 0 ? point.p99_ms ?? null : null,
      })),
    },
  ];
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const LatencyAnalytics = ({ start, end, granularity, gatewayId }) => {
  const theme = useTheme();

  const { data, isLoading, error, refetch, isFetching, isPlaceholderData } =
    useAnalyticsLatency({
      start,
      end,
      granularity,
      gatewayId,
    });

  const summary = data?.summary;
  const timeseries = data?.timeseries;

  const chartSeries = useMemo(() => buildSeries(timeseries), [timeseries]);
  // Day buckets read in the zone the API cut them in.
  const { day, utc } = bucketTime(granularity, data);
  const chartOptions = useMemo(
    () => buildLatencyChartOptions(theme, { day, utc }),
    [theme, day, utc],
  );

  return (
    <AnalyticsQueryState
      title="Latency"
      error={error}
      hasData={data != null && !isPlaceholderData}
      isFetching={isFetching}
      onRetry={() => refetch()}
    >
      <Box
        component="ul"
        aria-label="Latency summary"
        sx={{
          m: 0,
          p: 1.5,
          listStyle: "none",
          display: "grid",
          gridTemplateColumns: {
            xs: "repeat(2, minmax(0, 1fr))",
            sm: "repeat(4, minmax(0, 1fr))",
          },
          gap: 1.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        {SUMMARY_STATS.map((stat) => (
          <Box component="li" key={stat.key} sx={{ minWidth: 0 }}>
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ display: "block" }}
            >
              {stat.label}
            </Typography>
            {isLoading || isPlaceholderData ? (
              <Skeleton width="50%" height={22} />
            ) : (
              <Typography
                typography="m3"
                sx={{ fontVariantNumeric: "tabular-nums" }}
              >
                {/* No request, no latency: "—", never "0 ms". */}
                {formatMs(
                  Number(summary?.total_requests) === 0
                    ? null
                    : summary?.[stat.key],
                )}
              </Typography>
            )}
          </Box>
        ))}
      </Box>
      <ChartPanel
        title="Latency percentiles over time"
        caption={utc ? UTC_DAYS_NOTE : undefined}
        loading={isLoading || isPlaceholderData}
        height={320}
        empty={!timeseries?.some((point) => Number(point.request_count) > 0)}
      >
        <Chart
          type="line"
          height={320}
          options={chartOptions}
          series={chartSeries}
        />
      </ChartPanel>
    </AnalyticsQueryState>
  );
};

LatencyAnalytics.propTypes = {
  granularity: PropTypes.oneOf(["minute", "hour", "day"]),
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default LatencyAnalytics;
