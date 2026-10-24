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
} from "@mui/material";
import Section from "src/components/section";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";
import { useAnalyticsCost } from "./hooks/useAnalyticsCost";
import { formatCost } from "../utils/formatters";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GROUP_BY_OPTIONS = [
  { value: "model", label: "Model" },
  { value: "provider", label: "Provider" },
];

const TOP_N = 10;

function toNumericCost(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue : 0;
}

// ---------------------------------------------------------------------------
// Chart color palette
// ---------------------------------------------------------------------------

function getChartColors(theme) {
  if (theme.palette.mode === "dark") {
    return [
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#60A5FA",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#F87171",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#34D399",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#FBBF24",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#A78BFA",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#F472B6",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#22D3EE",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#FB7185",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#2DD4BF",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      "#F59E0B",
    ];
  }

  return [
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#2563EB",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#DC2626",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#059669",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#D97706",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#7857FC",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#DB2777",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#0891B2",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#EA580C",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#0F766E",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#7857FC",
  ];
}

// ---------------------------------------------------------------------------
// Chart option builders
// ---------------------------------------------------------------------------

function buildBarChartOptions(theme, categories) {
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
        barHeight: "60%",
        distributed: true,
      },
    },
    dataLabels: {
      enabled: true,
      formatter: (val) => formatCost(val),
      style: { fontSize: "12px", fontWeight: 600 },
      offsetX: 5,
    },
    xaxis: {
      categories,
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "12px" },
        formatter: (val) => formatCost(val),
      },
    },
    yaxis: {
      labels: {
        style: { colors: theme.palette.text.primary, fontSize: "12px" },
        maxWidth: 180,
      },
    },
    tooltip: {
      theme: theme.palette.mode,
      y: {
        formatter: (val) => formatCost(val),
      },
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
        options: { chart: { height: 300 } },
      },
    ],
  };
}

function buildDonutChartOptions(theme, labels) {
  return {
    chart: {
      type: "donut",
      background: "transparent",
      toolbar: { show: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors: getChartColors(theme),
    labels,
    dataLabels: {
      enabled: false,
    },
    plotOptions: {
      pie: {
        customScale: 0.9,
        expandOnClick: false,
        donut: {
          size: "72%",
          labels: {
            show: true,
            name: { show: false },
            value: {
              show: true,
              fontSize: "13px",
              fontWeight: 600,
              formatter: (val) => formatCost(Number(val)),
            },
            total: {
              show: true,
              showAlways: true,
              label: "Total",
              fontSize: "13px",
              formatter: (w) => {
                const total = w.globals.seriesTotals.reduce((a, b) => a + b, 0);
                return formatCost(total);
              },
            },
          },
        },
      },
    },
    stroke: { width: 2, colors: [theme.palette.background.paper] },
    legend: {
      position: "bottom",
      horizontalAlign: "center",
      itemMargin: { horizontal: 10, vertical: 6 },
      labels: { colors: theme.palette.text.secondary },
      formatter: (seriesName) =>
        seriesName.length > 20 ? `${seriesName.slice(0, 20)}…` : seriesName,
    },
    tooltip: {
      theme: theme.palette.mode,
      y: {
        formatter: (val) => formatCost(val),
      },
    },
    responsive: [
      {
        breakpoint: 960,
        options: {
          chart: { height: 340 },
          plotOptions: {
            pie: {
              customScale: 0.82,
              donut: {
                size: "70%",
                labels: {
                  value: { fontSize: "13px" },
                  total: { fontSize: "12px" },
                },
              },
            },
          },
        },
      },
      {
        breakpoint: 600,
        options: {
          chart: { height: 300 },
          legend: { position: "bottom", fontSize: "12px" },
          plotOptions: {
            pie: {
              customScale: 0.78,
              donut: {
                labels: {
                  value: { fontSize: "12px" },
                  total: { fontSize: "12px" },
                },
              },
            },
          },
        },
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const CostAnalytics = ({ start, end, gatewayId }) => {
  const theme = useTheme();
  const [groupBy, setGroupBy] = useState("model");

  const { data, isLoading } = useAnalyticsCost({
    start,
    end,
    groupBy,
    topN: TOP_N,
    gatewayId,
  });

  const breakdown = useMemo(() => data?.breakdown || [], [data?.breakdown]);

  const barCategories = useMemo(
    () => breakdown.map((item) => item.name || "Unknown"),
    [breakdown],
  );

  const barSeries = useMemo(
    () => [
      {
        name: "Cost",
        data: breakdown.map((item) => item.total_cost ?? 0),
      },
    ],
    [breakdown],
  );

  // Build labels and series from a single filtered list so the two arrays
  // stay aligned. Zero-cost slices are dropped — ApexCharts renders them as
  // invisible 0-degree wedges that still take up a legend slot.
  const donutSlices = useMemo(
    () =>
      breakdown
        .map((item) => ({
          name: item.name || "Unknown",
          cost: toNumericCost(item.total_cost),
        }))
        .filter((item) => item.cost > 0),
    [breakdown],
  );

  const donutLabels = useMemo(
    () => donutSlices.map((item) => item.name),
    [donutSlices],
  );

  const donutSeries = useMemo(
    () => donutSlices.map((item) => item.cost),
    [donutSlices],
  );

  const barChartOptions = useMemo(
    () => buildBarChartOptions(theme, barCategories),
    [theme, barCategories],
  );

  const donutChartOptions = useMemo(
    () => buildDonutChartOptions(theme, donutLabels),
    [theme, donutLabels],
  );

  const handleGroupByChange = (_event, newValue) => {
    if (newValue !== null) {
      setGroupBy(newValue);
    }
  };

  return (
    <Box>
      {/* Header with total cost and group-by toggle */}
      <Stack
        direction={{ xs: "column", md: "row" }}
        alignItems={{ xs: "flex-start", md: "center" }}
        justifyContent="space-between"
        spacing={2}
        mb={2}
      >
        <Stack direction="row" alignItems="baseline" spacing={1}>
          <Typography variant="subtitle1" fontWeight={600}>
            Total Cost:
          </Typography>
          <Typography variant="h5" fontWeight={600} color="primary.main">
            {formatCost(data?.total_cost)}
          </Typography>
        </Stack>

        <Stack
          direction={{ xs: "column", sm: "row" }}
          alignItems={{ xs: "flex-start", sm: "center" }}
          spacing={1.5}
          width={{ xs: "100%", md: "auto" }}
        >
          <Typography variant="body2" color="text.secondary" fontWeight={500}>
            Group by:
          </Typography>
          <ToggleButtonGroup
            value={groupBy}
            exclusive
            onChange={handleGroupByChange}
            size="small"
            sx={{ flexWrap: "wrap" }}
          >
            {GROUP_BY_OPTIONS.map((opt) => (
              <ToggleButton key={opt.value} value={opt.value}>
                {opt.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Stack>
      </Stack>

      <Grid container spacing={3}>
        {/* Cost breakdown bar chart */}
        <Grid item xs={12} md={7}>
          <Section variant="panel" sx={{ p: 3 }}>
            <Typography variant="subtitle1" fontWeight={600} mb={2}>
              Cost by {groupBy === "model" ? "Model" : "Provider"}
            </Typography>
            {isLoading ? (
              <Skeleton
                variant="rectangular"
                height={360}
                sx={{ borderRadius: 1 }}
              />
            ) : breakdown.length > 0 ? (
              <Chart
                type="bar"
                height={Math.max(360, breakdown.length * 40)}
                options={barChartOptions}
                series={barSeries}
              />
            ) : (
              <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                height={360}
              >
                <Typography variant="body2" color="text.secondary">
                  No cost data available for this time range.
                </Typography>
              </Box>
            )}
          </Section>
        </Grid>

        {/* Cost distribution donut chart */}
        <Grid item xs={12} md={5}>
          <Section variant="panel" sx={{ p: 3 }}>
            <Typography variant="subtitle1" fontWeight={600} mb={2}>
              Cost Distribution
            </Typography>
            {isLoading ? (
              <Skeleton
                variant="circular"
                width={280}
                height={280}
                sx={{ mx: "auto" }}
              />
            ) : donutSeries.length > 0 ? (
              <Box sx={{ display: "flex", justifyContent: "center" }}>
                <Chart
                  type="donut"
                  height={380}
                  options={donutChartOptions}
                  series={donutSeries}
                />
              </Box>
            ) : (
              <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                height={360}
              >
                <Typography variant="body2" color="text.secondary">
                  No cost data available.
                </Typography>
              </Box>
            )}
          </Section>
        </Grid>
      </Grid>
    </Box>
  );
};

CostAnalytics.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default CostAnalytics;
