/**
 * Usage time-series chart — daily usage for a selected dimension.
 * Uses ApexCharts area chart with free tier threshold line.
 */

import { useMemo } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "@mui/material/styles";
import { Box, Typography, Skeleton, Stack } from "@mui/material";
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
        params: {
          dimension,
          period,
          ...(periodEnd ? { period_end: periodEnd } : {}),
        },
      }),
    select: (res) => res.data?.result?.series || [],
    enabled: !!dimension,
  });

  const maxUsage = useMemo(
    () =>
      Math.max(
        0,
        ...(seriesData || []).map(
          (/** @type {{ usage: number }} */ d) => d.usage,
        ),
      ),
    [seriesData],
  );

  const yAxisFormatter = useMemo(() => {
    let divisor = 1;
    let suffix = "";
    if (maxUsage >= 1e6) {
      divisor = 1e6;
      suffix = "M";
    } else if (maxUsage >= 1e3) {
      divisor = 1e3;
      suffix = "K";
    }
    return (val) => {
      if (val == null) return "";
      if (val === 0) return "0";
      if (suffix) return `${(val / divisor).toFixed(1)}${suffix}`;
      const abs = Math.abs(val);
      if (abs < 0.001) return val.toFixed(4);
      if (abs < 0.01) return val.toFixed(3);
      if (abs < 0.1) return val.toFixed(2);
      return val.toFixed(1);
    };
  }, [maxUsage]);

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
          formatter: yAxisFormatter,
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
                },
              ],
            }
          : {},
    }),
    [theme, freeAllowance, displayUnit, yAxisFormatter],
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

  /** @type {any} */
  const apexOptions = chartOptions;

  return (
    <Box>
      <ApexChart
        key={`${dimension}-${displayUnit}`}
        type="area"
        series={chartSeries}
        options={apexOptions}
        height={250}
      />
      <Stack
        direction="row"
        spacing={2.5}
        alignItems="center"
        sx={{ mt: 1, pl: 1 }}
      >
        <Stack direction="row" spacing={0.75} alignItems="center">
          <Box
            sx={{
              width: 18,
              height: 2,
              borderRadius: 1,
              bgcolor: theme.palette.primary.main,
            }}
          />
          <Typography variant="caption" color="text.secondary">
            Daily usage
          </Typography>
        </Stack>
        {freeAllowance > 0 && (
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Box
              sx={{
                width: 18,
                height: 0,
                borderTop: `2px dashed ${theme.palette.warning.main}`,
              }}
            />
            <Typography variant="caption" color="text.secondary">
              Free tier ({freeAllowance.toLocaleString()} {displayUnit})
            </Typography>
          </Stack>
        )}
      </Stack>
    </Box>
  );
}
