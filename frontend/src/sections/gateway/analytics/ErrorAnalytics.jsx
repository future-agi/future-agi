/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Grid,
  Typography,
  Skeleton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import Section from "src/components/section";
import MetricStrip from "src/components/metric-strip";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";
import { useAnalyticsErrors } from "./hooks/useAnalyticsErrors";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GROUP_BY_OPTIONS = [
  { value: "status_code", label: "Status Code" },
  { value: "model", label: "Model" },
  { value: "provider", label: "Provider" },
];

const TOP_N = 10;

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

function getChartColors(theme) {
  return [
    theme.palette.error.main,
    theme.palette.warning.main,
    theme.palette.primary.main,
    theme.palette.secondary.main,
    theme.palette.info.main,
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#7857FC",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#FF6D00",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#00BFA5",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#D500F9",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#64DD17",
  ];
}

// ---------------------------------------------------------------------------
// Chart option builders
// ---------------------------------------------------------------------------

function buildErrorTrendOptions(theme) {
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
    xaxis: {
      type: "datetime",
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "12px" },
      },
    },
    yaxis: {
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "12px" },
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
      x: { format: "MMM dd, HH:mm" },
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
  return {
    chart: {
      type: "bar",
      toolbar: { show: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors: getChartColors(theme),
    plotOptions: {
      bar: {
        horizontal: true,
        borderRadius: 4,
        barHeight: "55%",
        distributed: true,
      },
    },
    dataLabels: {
      enabled: true,
      formatter: (val) => val?.toLocaleString() ?? "0",
      style: { fontSize: "12px", fontWeight: 600 },
      offsetX: 5,
    },
    xaxis: {
      categories,
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "12px" },
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

const ErrorAnalytics = ({ start, end, gatewayId }) => {
  const theme = useTheme();
  const [groupBy, setGroupBy] = useState("status_code");

  const granularity = useMemo(
    () => computeGranularity(start, end),
    [start, end],
  );

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
  const trendOptions = useMemo(() => buildErrorTrendOptions(theme), [theme]);

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
    <Box>
      <MetricStrip
        sx={{ mb: 2 }}
        items={[
          {
            label: "Total errors",
            value: data?.total_errors?.toLocaleString() ?? "--",
          },
          {
            label: "Error rate",
            value:
              data?.overall_error_rate != null
                ? `${Number(data.overall_error_rate).toFixed(2)}%`
                : "--",
          },
        ]}
      />

      <Stack direction="row" spacing={3} alignItems="center" mb={2}>
        {/* Group-by toggle */}
        <Box sx={{ ml: "auto !important" }}>
          <Stack direction="row" alignItems="center" spacing={2}>
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
                <ToggleButton key={opt.value} value={opt.value}>
                  {opt.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Stack>
        </Box>
      </Stack>

      <Grid container spacing={3}>
        {/* Error rate trend */}
        <Grid item xs={12} md={7}>
          <Section variant="panel" sx={{ p: 3 }}>
            <Typography variant="subtitle1" fontWeight={600} mb={2}>
              Error Rate Over Time
            </Typography>
            {isLoading ? (
              <Skeleton
                variant="rectangular"
                height={320}
                sx={{ borderRadius: 1 }}
              />
            ) : trendSeries.length > 0 && trendSeries[0].data.length > 0 ? (
              <Chart
                type="area"
                height={320}
                options={trendOptions}
                series={trendSeries}
              />
            ) : (
              <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                height={320}
              >
                <Typography variant="body2" color="text.secondary">
                  No error data available for this time range.
                </Typography>
              </Box>
            )}
          </Section>
        </Grid>

        {/* Error breakdown bar */}
        <Grid item xs={12} md={5}>
          <Section variant="panel" sx={{ p: 3 }}>
            <Typography variant="subtitle1" fontWeight={600} mb={2}>
              Error Breakdown
            </Typography>
            {isLoading ? (
              <Skeleton
                variant="rectangular"
                height={320}
                sx={{ borderRadius: 1 }}
              />
            ) : breakdown.length > 0 ? (
              <Chart
                type="bar"
                height={Math.max(320, breakdown.length * 38)}
                options={barOptions}
                series={barSeries}
              />
            ) : (
              <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                height={320}
              >
                <Typography variant="body2" color="text.secondary">
                  No error breakdown available.
                </Typography>
              </Box>
            )}
          </Section>
        </Grid>

        {/* Top errors table */}
        <Grid item xs={12}>
          <Section variant="panel" sx={{ p: 3 }}>
            <Typography variant="subtitle1" fontWeight={600} mb={2}>
              Top Errors
            </Typography>
            {isLoading ? (
              <Stack spacing={1}>
                {[...Array(5)].map((_, i) => (
                  <Skeleton
                    key={i}
                    variant="rectangular"
                    height={40}
                    sx={{ borderRadius: 1 }}
                  />
                ))}
              </Stack>
            ) : breakdown.length > 0 ? (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ fontWeight: 600 }}>Name</TableCell>
                      <TableCell
                        align="right"
                        sx={{
                          fontVariantNumeric: "tabular-nums",
                          fontWeight: 600,
                        }}
                      >
                        Error Count
                      </TableCell>
                      <TableCell
                        align="right"
                        sx={{
                          fontVariantNumeric: "tabular-nums",
                          fontWeight: 600,
                        }}
                      >
                        Error Rate
                      </TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {breakdown.map((row, index) => (
                      <TableRow key={index} hover>
                        <TableCell>
                          <Typography variant="body2" color="text.primary">
                            {row.name || "Unknown"}
                          </Typography>
                        </TableCell>
                        <TableCell
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                          align="right"
                        >
                          {row.error_count?.toLocaleString() ?? 0}
                        </TableCell>
                        <TableCell
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                          align="right"
                        >
                          <Typography
                            variant="body2"
                            color={
                              (row.error_rate ?? 0) > 10
                                ? "error.main"
                                : (row.error_rate ?? 0) > 5
                                  ? "warning.main"
                                  : "text.primary"
                            }
                            fontWeight={500}
                          >
                            {row.error_rate != null
                              ? `${Number(row.error_rate).toFixed(2)}%`
                              : "0%"}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            ) : (
              <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                py={4}
              >
                <Typography variant="body2" color="text.secondary">
                  No errors recorded for this time range.
                </Typography>
              </Box>
            )}
          </Section>
        </Grid>
      </Grid>
    </Box>
  );
};

ErrorAnalytics.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default ErrorAnalytics;
