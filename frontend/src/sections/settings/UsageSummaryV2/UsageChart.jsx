/**
 * Usage time-series chart — daily usage for a selected dimension.
 * Uses ApexCharts area chart with free tier threshold line.
 */

import { useMemo } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "@mui/material/styles";
import { Box, Typography, Skeleton } from "@mui/material";
import ApexChart from "react-apexcharts";

import axios, { endpoints } from "src/utils/axios";

UsageChart.propTypes = {
  dimension: PropTypes.string.isRequired,
  period: PropTypes.string,
  periodEnd: PropTypes.string,
  freeAllowance: PropTypes.number,
  displayUnit: PropTypes.string,
};

export default function UsageChart({
  dimension,
  period,
  periodEnd,
  freeAllowance,
  displayUnit,
}) {
  const theme = useTheme();

  const { data: seriesData, isLoading } = useQuery({
    queryKey: ["v2-usage-time-series", dimension, period, periodEnd],
    queryFn: () =>
      axios.get(endpoints.settings.v2.usageTimeSeries, {
        params: { dimension, period, ...(periodEnd ? { period_end: periodEnd } : {}) },
      }),
    select: (res) => res.data?.result?.series || [],
    enabled: !!dimension,
  });

  const chartOptions = useMemo(
    () => ({
      chart: {
        type: "area",
        toolbar: { show: false },
        zoom: { enabled: false },
        background: "transparent",
        fontFamily: theme.typography.fontFamily,
      },
      colors: [theme.palette.primary.main],
      fill: {
        type: "gradient",
        gradient: {
          shadeIntensity: 1,
          opacityFrom: 0.4,
          opacityTo: 0.05,
          stops: [0, 100],
        },
      },
      stroke: { curve: "smooth", width: 2.5 },
      dataLabels: { enabled: false },
      xaxis: {
        type: "datetime",
        labels: {
          style: { colors: theme.palette.text.secondary, fontSize: "11px" },
        },
        axisBorder: { show: false },
        axisTicks: { show: false },
      },
      yaxis: {
        labels: {
          style: { colors: theme.palette.text.secondary, fontSize: "11px" },
          formatter: (val) => {
            if (val >= 1e6) return `${(val / 1e6).toFixed(1)}M`;
            if (val >= 1e3) return `${(val / 1e3).toFixed(1)}K`;
            return val?.toFixed(1);
          },
        },
      },
      grid: {
        borderColor: theme.palette.divider,
        strokeDashArray: 3,
        xaxis: { lines: { show: false } },
      },
      tooltip: {
        theme: theme.palette.mode,
        x: { format: "MMM dd" },
        y: {
          formatter: (val) => `${val?.toLocaleString()} ${displayUnit}`,
        },
      },
      annotations:
        freeAllowance > 0
          ? {
              yaxis: [
                {
                  y: freeAllowance,
                  borderColor: theme.palette.warning.main,
                  strokeDashArray: 4,
                  label: {
                    text: `Free tier: ${freeAllowance.toLocaleString()} ${displayUnit}`,
                    position: "front",
                    style: {
                      color: theme.palette.warning.contrastText,
                      background: theme.palette.warning.main,
                      fontSize: "11px",
                      padding: { left: 6, right: 6, top: 2, bottom: 2 },
                    },
                  },
                },
              ],
            }
          : {},
    }),
    [theme, freeAllowance, displayUnit],
  );

  const chartSeries = useMemo(
    () => [
      {
        name: "Usage",
        data: (seriesData || []).map((d) => ({
          x: new Date(d.date).getTime(),
          y: d.usage,
        })),
      },
    ],
    [seriesData],
  );

  if (isLoading) {
    return <Skeleton variant="rounded" height={250} />;
  }

  if (!seriesData || seriesData.length === 0) {
    return (
      <Box
        sx={{
          height: 250,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: 2,
        }}
      >
        <Typography variant="body2" color="text.disabled">
          No usage data for this period
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      <ApexChart
        type="area"
        series={chartSeries}
        options={chartOptions}
        height={250}
      />
    </Box>
  );
}
