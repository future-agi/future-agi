/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import { Box, Typography, useTheme } from "@mui/material";
import ApexCharts from "apexcharts";
import PropTypes from "prop-types";
import { useEffect, useMemo, useRef } from "react";

const SparklineChart = ({ data = [], color = "#E2A6F1", height = 28 }) => {
  const muiTheme = useTheme();
  const chartRef = useRef(null);
  const chartInstanceRef = useRef(null);

  const totalCount = useMemo(
    () => data?.reduce((sum, item) => sum + (item?.value || 0), 0) || 0,
    [data],
  );

  const hasNonZeroData = useMemo(
    () => data?.some((item) => (item?.value || 0) > 0),
    [data],
  );

  useEffect(() => {
    if (!chartRef.current) return;

    // Always render a chart — flat line for zero data, actual line for real data
    const values = hasNonZeroData
      ? data.map((item) => item?.value || 0)
      : Array(Math.max(data?.length || 7, 7)).fill(0);

    const chartOptions = {
      chart: {
        type: "area",
        height: "100%",
        width: "100%",
        sparkline: { enabled: true },
        animations: { enabled: false },
      },
      colors: [hasNonZeroData ? color : muiTheme.palette.divider],
      stroke: {
        curve: "smooth",
        width: hasNonZeroData ? 1.5 : 1,
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "light",
          type: "vertical",
          shadeIntensity: 0.4,
          gradientToColors: [
            hasNonZeroData ? color : "transparent",
            muiTheme.palette.background.default,
          ],
          opacityFrom: hasNonZeroData ? 0.5 : 0.05,
          opacityTo: 0.02,
          stops: [0, 100],
        },
      },
      markers: { size: 0 },
      series: [{ data: values }],
      xaxis: { labels: { show: false } },
      yaxis: { show: false, min: 0 },
      tooltip: { enabled: false },
    };

    if (chartInstanceRef.current) {
      chartInstanceRef.current.destroy();
    }

    const chart = new ApexCharts(chartRef.current, chartOptions);
    chart.render();
    chartInstanceRef.current = chart;

    return () => {
      if (chartInstanceRef.current) {
        chartInstanceRef.current.destroy();
        chartInstanceRef.current = null;
      }
    };
  }, [
    data,
    color,
    hasNonZeroData,
    muiTheme.palette.background.default,
    muiTheme.palette.mode,
    muiTheme.palette.divider,
  ]);

  return (
    <Box
      sx={{ display: "flex", alignItems: "center", gap: 0.5, width: "100%" }}
    >
      <Box
        ref={chartRef}
        sx={{ flex: 1, height: `${height}px`, minWidth: 60 }}
      />
      <Typography
        variant="caption"
        color={hasNonZeroData ? "text.primary" : "text.disabled"}
        sx={{ minWidth: 20, textAlign: "right", fontSize: "12px" }}
      >
        {totalCount}
      </Typography>
    </Box>
  );
};

SparklineChart.propTypes = {
  data: PropTypes.arrayOf(
    PropTypes.shape({
      timestamp: PropTypes.string,
      value: PropTypes.number,
    }),
  ),
  color: PropTypes.string,
  height: PropTypes.number,
};

export default SparklineChart;
