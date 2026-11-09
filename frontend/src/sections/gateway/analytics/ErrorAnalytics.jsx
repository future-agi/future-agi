/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography, Stack } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";
import { useAnalyticsErrors } from "./hooks/useAnalyticsErrors";
import { REQUEST_DIMENSION_OPTIONS } from "../constants/requestTags";
import { GatewayGrid } from "../components/frame";
import { formatLogTime } from "../logs/logDates";
import ChartPanel from "./ChartPanel";
import GroupByToggle from "./GroupByToggle";
import { EMPTY } from "../utils/formatters";
import {
  UTC_DAYS_NOTE,
  bucketTime,
  localTimeAxis,
  localTimeTooltip,
} from "../utils/timeZone";
import { TOP_SERIES, colorsForNames } from "./seriesColors";
import { barValueOptions } from "./barLabels";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GROUP_BY_OPTIONS = [
  { value: "status_code", label: "Status Code" },
  ...REQUEST_DIMENSION_OPTIONS,
];

const TOP_N = TOP_SERIES;

const formatRate = (value) =>
  value == null ? "0%" : `${Number(value).toFixed(1)}%`;

function Clipped({ value }) {
  return (
    <Box
      component="span"
      title={value || undefined}
      sx={{
        display: "block",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {value || EMPTY}
    </Box>
  );
}

// Each minimum holds its header: the label, 32 px of padding, the 24 px menu
// button and, sortable, the 24 px sort arrow (analytics/__tests__).
const TOP_ERROR_COLUMNS = [
  {
    field: "name",
    headerName: "Group",
    minWidth: 124,
    flex: 0.8,
    cellRenderer: ({ value }) => <Clipped value={value || "Unknown"} />,
  },
  {
    field: "error_count",
    headerName: "Errors",
    minWidth: 122,
    flex: 0.5,
    sort: "desc",
    valueFormatter: ({ value }) => Number(value ?? 0).toLocaleString(),
  },
  {
    field: "percentage_of_errors",
    headerName: "Share",
    minWidth: 120,
    flex: 0.5,
    valueFormatter: ({ value }) => formatRate(value),
  },
  {
    field: "error_rate",
    headerName: "Error rate",
    minWidth: 144,
    flex: 0.5,
    valueFormatter: ({ value }) => formatRate(value),
  },
  {
    field: "last_seen",
    headerName: "Last seen",
    minWidth: 170,
    flex: 0.8,
    valueFormatter: ({ value }) => (value ? formatLogTime(value) : EMPTY),
  },
  {
    field: "sample_error_message",
    headerName: "Sample message",
    minWidth: 240,
    flex: 2,
    sortable: false,
    cellRenderer: ({ value }) => <Clipped value={value} />,
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Chart option builders
// ---------------------------------------------------------------------------

function buildErrorTrendOptions(theme, time) {
  return {
    chart: {
      type: "area",
      toolbar: { show: false },
      zoom: { enabled: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors: [theme.palette.error.main],
    dataLabels: { enabled: false },
    stroke: { curve: "smooth", width: 2 },
    fill: {
      type: "gradient",
      gradient: {
        shadeIntensity: 1,
        opacityFrom: 0.4,
        opacityTo: 0.05,
        stops: [0, 100],
      },
    },
    xaxis: localTimeAxis({
      color: theme.palette.text.secondary,
      utc: time.utc,
    }),
    yaxis: {
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "11px" },
        formatter: (val) => `${Number(val)?.toFixed(1) ?? 0}%`,
      },
      title: {
        text: "Error Rate (%)",
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
      y: { formatter: (val) => `${Number(val)?.toFixed(2) ?? 0}%` },
    },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 3,
    },
    legend: { show: false },
    responsive: [
      {
        breakpoint: 600,
        options: { chart: { height: 260 } },
      },
    ],
  };
}

function buildBreakdownBarOptions(theme, categories) {
  // Each count past its bar's end (barLabels.js).
  const values = barValueOptions(
    theme,
    (val) => val?.toLocaleString("en-US") ?? "0",
  );
  // One gateway series hue per group (seriesColors.js), highest first.
  return {
    chart: {
      type: "bar",
      stacked: values.stacked,
      toolbar: { show: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors: colorsForNames(theme, categories),
    plotOptions: {
      bar: {
        horizontal: true,
        borderRadius: 4,
        barHeight: "55%",
        distributed: true,
        dataLabels: { total: values.total },
      },
    },
    dataLabels: values.dataLabels,
    xaxis: {
      categories,
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "11px" },
        formatter: (val) => {
          if (val >= 1000) return `${(val / 1000).toFixed(1)}K`;
          return val?.toString() ?? "0";
        },
      },
    },
    yaxis: {
      labels: {
        style: { colors: theme.palette.text.primary, fontSize: "12px" },
        maxWidth: 160,
      },
    },
    tooltip: {
      theme: theme.palette.mode,
      y: { formatter: (val) => (val != null ? val.toLocaleString() : "0") },
    },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 3,
      xaxis: { lines: { show: true } },
      yaxis: { lines: { show: false } },
      padding: values.gridPadding,
    },
    legend: { show: false },
    responsive: [
      {
        breakpoint: 600,
        options: { chart: { height: 280 } },
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Series builders
// ---------------------------------------------------------------------------

function buildErrorTrendSeries(errorTimeseries) {
  if (!errorTimeseries?.length) return [];
  return [
    {
      name: "Error Rate",
      data: errorTimeseries.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: point.error_rate ?? 0,
      })),
    },
  ];
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const ErrorAnalytics = ({ start, end, granularity, gatewayId }) => {
  const theme = useTheme();
  const [groupBy, setGroupBy] = useState("status_code");

  const { data, isLoading } = useAnalyticsErrors({
    start,
    end,
    granularity,
    groupBy,
    topN: TOP_N,
    gatewayId,
  });

  const breakdown = useMemo(() => data?.breakdown || [], [data?.breakdown]);
  const errorTimeseries = useMemo(
    () => data?.error_timeseries || [],
    [data?.error_timeseries],
  );

  // Error trend series
  const trendSeries = useMemo(
    () => buildErrorTrendSeries(errorTimeseries),
    [errorTimeseries],
  );
  // Day buckets read in the zone the API cut them in.
  const { day, utc } = bucketTime(granularity, data);
  const trendOptions = useMemo(
    () => buildErrorTrendOptions(theme, { day, utc }),
    [theme, day, utc],
  );

  // Breakdown bar series
  const barCategories = useMemo(
    () => breakdown.map((item) => item.name || "Unknown"),
    [breakdown],
  );
  const barSeries = useMemo(
    () => [
      { name: "Errors", data: breakdown.map((item) => item.error_count ?? 0) },
    ],
    [breakdown],
  );
  const barOptions = useMemo(
    () => buildBreakdownBarOptions(theme, barCategories),
    [theme, barCategories],
  );

  const handleGroupByChange = (_event, newValue) => {
    if (newValue !== null) {
      setGroupBy(newValue);
    }
  };

  return (
    <Stack spacing={1.5}>
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 2,
        }}
      >
        <Typography typography="s1" sx={{ fontVariantNumeric: "tabular-nums" }}>
          {`${(data?.total_errors ?? 0).toLocaleString()} errors`}
          <Box component="span" sx={{ color: "text.secondary" }}>
            {` in ${(data?.total_requests ?? 0).toLocaleString()} requests (${formatRate(data?.overall_error_rate)})`}
          </Box>
        </Typography>
        <Box sx={{ ml: { sm: "auto" } }}>
          <GroupByToggle
            value={groupBy}
            options={GROUP_BY_OPTIONS}
            onChange={(next) => handleGroupByChange(null, next)}
          />
        </Box>
      </Box>

      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: { xs: "1fr", md: "7fr 5fr" },
        }}
      >
        <ChartPanel
          title="Error rate over time"
          caption={utc ? UTC_DAYS_NOTE : undefined}
          loading={isLoading}
          empty={!(trendSeries.length > 0 && trendSeries[0].data.length > 0)}
          emptyText="No errors in this range."
        >
          <Chart
            type="area"
            height={280}
            options={trendOptions}
            series={trendSeries}
          />
        </ChartPanel>
        <ChartPanel
          title="Errors by group"
          loading={isLoading}
          empty={breakdown.length === 0}
          emptyText="No errors in this range."
        >
          <Chart
            type="bar"
            height={Math.max(280, breakdown.length * 34)}
            options={barOptions}
            series={barSeries}
          />
        </ChartPanel>
      </Box>

      <ChartPanel title="Top errors" loading={isLoading} height={200}>
        <GatewayGrid
          rowData={breakdown}
          columnDefs={TOP_ERROR_COLUMNS}
          stateKey="gateway-analytics-errors"
          getRowId={(params) => String(params.data.name)}
          domLayout="autoHeight"
          height="auto"
          rowHeight={36}
          headerHeight={36}
          emptyState={{
            icon: "inbox",
            title: "No errors in this range",
            description: "Requests that failed or were blocked show up here.",
          }}
        />
      </ChartPanel>
    </Stack>
  );
};

ErrorAnalytics.propTypes = {
  granularity: PropTypes.oneOf(["minute", "hour", "day"]),
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default ErrorAnalytics;
