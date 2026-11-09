import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";
import { useAnalyticsUsage } from "./hooks/useAnalyticsUsage";
import { REQUEST_DIMENSION_OPTIONS } from "../constants/requestTags";
import ChartPanel from "./ChartPanel";
import { countAxisLabel } from "./axisLabels";
import GroupByToggle from "./GroupByToggle";
import {
  UTC_DAYS_NOTE,
  bucketTime,
  localTimeAxis,
  localTimeTooltip,
} from "../utils/timeZone";
import { groupedColors, groupedSeries, topGroups } from "./seriesColors";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const GROUP_BY_OPTIONS = [
  { value: "", label: "None" },
  ...REQUEST_DIMENSION_OPTIONS,
];

// ---------------------------------------------------------------------------
// Chart option builders
// ---------------------------------------------------------------------------

function buildRequestChartOptions(theme, time, colors) {
  return {
    chart: {
      type: "area",
      toolbar: { show: false },
      zoom: { enabled: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors,
    dataLabels: { enabled: false },
    stroke: { curve: "smooth", width: 2 },
    fill: {
      type: "gradient",
      gradient: {
        shadeIntensity: 1,
        opacityFrom: 0.35,
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
        formatter: (val) => countAxisLabel(val),
      },
    },
    tooltip: {
      theme: theme.palette.mode,
      x: localTimeTooltip(time),
      y: { formatter: (val) => (val != null ? val.toLocaleString() : "0") },
    },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 3,
    },
    legend: {
      position: "top",
      horizontalAlign: "left",
      labels: { colors: theme.palette.text.secondary },
    },
    responsive: [
      {
        breakpoint: 600,
        options: { chart: { height: 260 }, legend: { position: "bottom" } },
      },
    ],
  };
}

function buildTokenChartOptions(theme, time, groupColors) {
  return {
    chart: {
      type: "area",
      stacked: true,
      toolbar: { show: false },
      zoom: { enabled: false },
      fontFamily: theme.typography.fontFamily,
    },
    // Grouped: the series colours. Else input and output tokens, never
    // warning.main (under 3:1 on white, too faint for a series).
    colors: groupColors || [
      theme.palette.info.main,
      theme.palette.primary.main,
    ],
    dataLabels: { enabled: false },
    stroke: { curve: "smooth", width: 2 },
    fill: {
      type: "gradient",
      gradient: {
        shadeIntensity: 1,
        opacityFrom: 0.35,
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
        formatter: (val) => countAxisLabel(val),
      },
    },
    tooltip: {
      theme: theme.palette.mode,
      x: localTimeTooltip(time),
      y: { formatter: (val) => (val != null ? val.toLocaleString() : "0") },
    },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 3,
    },
    legend: {
      position: "top",
      horizontalAlign: "left",
      labels: { colors: theme.palette.text.secondary },
    },
    responsive: [
      {
        breakpoint: 600,
        options: { chart: { height: 260 }, legend: { position: "bottom" } },
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Series builders
// ---------------------------------------------------------------------------

function buildUngroupedRequestSeries(series) {
  if (!series?.length) return [];
  return [
    {
      name: "Requests",
      data: series.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: point.request_count ?? 0,
      })),
    },
  ];
}

function buildUngroupedTokenSeries(series) {
  if (!series?.length) return [];
  return [
    {
      name: "Input tokens",
      data: series.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: point.input_tokens ?? point.total_tokens ?? 0,
      })),
    },
    {
      name: "Output tokens",
      data: series.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: point.output_tokens ?? 0,
      })),
    },
  ];
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const UsageCharts = ({ start, end, granularity, gatewayId }) => {
  const theme = useTheme();
  const [groupBy, setGroupBy] = useState("");

  const { data, isLoading } = useAnalyticsUsage({
    start,
    end,
    granularity,
    groupBy: groupBy || undefined,
    gatewayId,
  });

  const isGrouped = Boolean(groupBy) && Boolean(data?.groups);

  // Both charts draw the same groups (the top 8 by requests, then Other), so
  // a group keeps one colour across them.
  const groups = useMemo(
    () =>
      isGrouped ? topGroups(data.groups, (point) => point.request_count) : null,
    [data, isGrouped],
  );
  const groupColors = useMemo(
    () => (groups ? groupedColors(theme, groups) : null),
    [theme, groups],
  );

  const requestSeries = useMemo(() => {
    if (!data) return [];
    return groups
      ? groupedSeries(data.groups, groups, (point) => point.request_count)
      : buildUngroupedRequestSeries(data.series);
  }, [data, groups]);

  const tokenSeries = useMemo(() => {
    if (!data) return [];
    // A group's total tokens; ungrouped, input and output.
    return groups
      ? groupedSeries(data.groups, groups, (point) => point.total_tokens)
      : buildUngroupedTokenSeries(data.series);
  }, [data, groups]);

  // Day buckets read in the zone the API cut them in.
  const { day, utc } = bucketTime(granularity, data);
  const time = useMemo(() => ({ day, utc }), [day, utc]);
  const caption = utc ? UTC_DAYS_NOTE : undefined;

  const requestChartOptions = useMemo(
    () =>
      buildRequestChartOptions(
        theme,
        time,
        groupColors || [theme.palette.primary.main],
      ),
    [theme, time, groupColors],
  );

  const tokenChartOptions = useMemo(
    () => buildTokenChartOptions(theme, time, groupColors),
    [theme, time, groupColors],
  );

  const handleGroupByChange = (_event, newValue) => {
    if (newValue !== null) {
      setGroupBy(newValue);
    }
  };

  return (
    <Stack spacing={1.5}>
      <GroupByToggle
        value={groupBy}
        options={GROUP_BY_OPTIONS}
        onChange={(next) => handleGroupByChange(null, next)}
      />
      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
        }}
      >
        <ChartPanel
          title="Requests over time"
          caption={caption}
          loading={isLoading}
          empty={requestSeries.length === 0}
        >
          <Chart
            type="area"
            height={280}
            options={requestChartOptions}
            series={requestSeries}
          />
        </ChartPanel>
        <ChartPanel
          title="Tokens over time"
          caption={caption}
          loading={isLoading}
          empty={tokenSeries.length === 0}
        >
          <Chart
            type="area"
            height={280}
            options={tokenChartOptions}
            series={tokenSeries}
          />
        </ChartPanel>
      </Box>
    </Stack>
  );
};

UsageCharts.propTypes = {
  granularity: PropTypes.oneOf(["minute", "hour", "day"]),
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default UsageCharts;
