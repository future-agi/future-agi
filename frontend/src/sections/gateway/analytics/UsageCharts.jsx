import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Card,
  Grid,
  Typography,
  Skeleton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";
import { useAnalyticsUsage } from "./hooks/useAnalyticsUsage";
import { computeGranularity } from "../utils/analyticsHelpers";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const GROUP_BY_OPTIONS = [
  { value: "", label: "None" },
  { value: "model", label: "Model" },
  { value: "provider", label: "Provider" },
];

// ---------------------------------------------------------------------------
// Chart option builders
// ---------------------------------------------------------------------------

function buildRequestChartOptions(theme, _series) {
  return {
    chart: {
      type: "area",
      toolbar: { show: false },
      zoom: { enabled: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors: [
      theme.palette.primary.main,
      theme.palette.secondary.main,
      theme.palette.warning.main,
      theme.palette.info.main,
      theme.palette.success.main,
      theme.palette.error.main,
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
    xaxis: {
      type: "datetime",
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "11px" },
      },
    },
    yaxis: {
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "11px" },
        formatter: (val) => {
          if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`;
          if (val >= 1_000) return `${(val / 1_000).toFixed(1)}K`;
          return val != null ? Number(val).toFixed(0) : "0";
        },
      },
    },
    tooltip: {
      theme: theme.palette.mode,
      x: { format: "MMM dd, HH:mm" },
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

function buildTokenChartOptions(theme) {
  return {
    chart: {
      type: "area",
      stacked: true,
      toolbar: { show: false },
      zoom: { enabled: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors: [theme.palette.info.main, theme.palette.warning.main],
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
    xaxis: {
      type: "datetime",
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "11px" },
      },
    },
    yaxis: {
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "11px" },
        formatter: (val) => {
          if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`;
          if (val >= 1_000) return `${(val / 1_000).toFixed(1)}K`;
          return val != null ? Number(val).toFixed(0) : "0";
        },
      },
    },
    tooltip: {
      theme: theme.palette.mode,
      x: { format: "MMM dd, HH:mm" },
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

function buildGroupedRequestSeries(groups) {
  if (!groups) return [];
  return Object.entries(groups).map(([groupName, points]) => ({
    name: groupName,
    data: (points || []).map((point) => ({
      x: new Date(point.bucket).getTime(),
      y: point.request_count ?? 0,
    })),
  }));
}

function buildUngroupedTokenSeries(series) {
  if (!series?.length) return [];
  return [
    {
      name: "Input Tokens",
      data: series.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: point.input_tokens ?? point.total_tokens ?? 0,
      })),
    },
    {
      name: "Output Tokens",
      data: series.map((point) => ({
        x: new Date(point.bucket).getTime(),
        y: point.output_tokens ?? 0,
      })),
    },
  ];
}

function buildGroupedTokenSeries(groups) {
  if (!groups) return [];
  // For grouped mode, show total tokens per group
  return Object.entries(groups).map(([groupName, points]) => ({
    name: groupName,
    data: (points || []).map((point) => ({
      x: new Date(point.bucket).getTime(),
      y: point.total_tokens ?? 0,
    })),
  }));
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const UsageCharts = ({ start, end, gatewayId }) => {
  const theme = useTheme();
  const [groupBy, setGroupBy] = useState("");

  const granularity = useMemo(
    () => computeGranularity(start, end),
    [start, end],
  );

  const { data, isLoading } = useAnalyticsUsage({
    start,
    end,
    granularity,
    groupBy: groupBy || undefined,
    gatewayId,
  });

  const isGrouped = Boolean(groupBy) && data?.groups;

  const requestSeries = useMemo(() => {
    if (!data) return [];
    return isGrouped
      ? buildGroupedRequestSeries(data.groups)
      : buildUngroupedRequestSeries(data.series);
  }, [data, isGrouped]);

  const tokenSeries = useMemo(() => {
    if (!data) return [];
    return isGrouped
      ? buildGroupedTokenSeries(data.groups)
      : buildUngroupedTokenSeries(data.series);
  }, [data, isGrouped]);

  const requestChartOptions = useMemo(
    () => buildRequestChartOptions(theme, requestSeries),
    [theme, requestSeries],
  );

  const tokenChartOptions = useMemo(
    () => buildTokenChartOptions(theme),
    [theme],
  );

  const handleGroupByChange = (_event, newValue) => {
    if (newValue !== null) {
      setGroupBy(newValue);
    }
  };

  return (
    <Box>
      {/* Group-by toggle */}
      <Stack direction="row" alignItems="center" spacing={2} mb={2}>
        <Typography variant="body2" color="text.secondary" fontWeight={500}>
          Group by:
        </Typography>
        <ToggleButtonGroup
          value={groupBy}
          exclusive
          onChange={handleGroupByChange}
          size="small"
        >
          {GROUP_BY_OPTIONS.map((opt) => (
            <ToggleButton
              key={opt.value}
              value={opt.value}
              sx={{
                px: 1.5,
                py: 0.25,
                textTransform: "none",
                fontSize: "0.8125rem",
              }}
            >
              {opt.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>

      <Grid container spacing={3}>
        {/* Requests over time */}
        <Grid item xs={12} md={6}>
          <Card sx={{ p: 3 }}>
            <Typography variant="subtitle1" fontWeight={600} mb={2}>
              Requests Over Time
            </Typography>
            {isLoading ? (
              <Skeleton
                variant="rectangular"
                height={320}
                sx={{ borderRadius: 1 }}
              />
            ) : requestSeries.length > 0 ? (
              <Chart
                type="area"
                height={320}
                options={requestChartOptions}
                series={requestSeries}
              />
            ) : (
              <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                height={320}
              >
                <Typography variant="body2" color="text.secondary">
                  No usage data available for this time range.
                </Typography>
              </Box>
            )}
          </Card>
        </Grid>

        {/* Tokens over time */}
        <Grid item xs={12} md={6}>
          <Card sx={{ p: 3 }}>
            <Typography variant="subtitle1" fontWeight={600} mb={2}>
              Tokens Over Time
            </Typography>
            {isLoading ? (
              <Skeleton
                variant="rectangular"
                height={320}
                sx={{ borderRadius: 1 }}
              />
            ) : tokenSeries.length > 0 ? (
              <Chart
                type="area"
                height={320}
                options={tokenChartOptions}
                series={tokenSeries}
              />
            ) : (
              <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                height={320}
              >
                <Typography variant="body2" color="text.secondary">
                  No token data available for this time range.
                </Typography>
              </Box>
            )}
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
};

UsageCharts.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default UsageCharts;
