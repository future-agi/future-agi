import { Box, useTheme } from "@mui/material";
import { chartAxisColors } from "src/theme/data-colors";
import React, { useMemo } from "react";
import ReactApexChart from "react-apexcharts";
import PropTypes from "prop-types";
import { green } from "src/theme/palette";

const getDefaultOptions = (isDark, axis) => ({
  chart: {
    height: 350,
    type: "heatmap",
    background: "transparent",
    zoom: { type: "x", enabled: true, autoScaleYaxis: true },
    selection: {
      enabled: false,
      type: "x",
    },
    toolbar: {
      show: false,
    },
    foreColor: axis.foreColor,
  },
  theme: { mode: isDark ? "dark" : "light" },
  tooltip: { theme: isDark ? "dark" : "light" },
  grid: {
    show: true,
    strokeDashArray: 12,
    borderColor: axis.gridColor,
  },
  dataLabels: {
    enabled: false,
  },
  plotOptions: {
    heatmap: {
      shadeIntensity: 0.5,
      radius: 16,
    },
  },
  colors: [green[500]],
  xaxis: {
    position: "top",
    categories: [],
    axisBorder: {
      show: false, // removes x-axis border
    },
    axisTicks: {
      show: false, // removes x-axis ticks
    },
  },
  yaxis: {
    axisBorder: {
      show: false, // removes y-axis border
    },
    axisTicks: {
      show: false, // removes y-axis ticks
    },
  },
  stroke: {
    width: 8, // thickness of the "gap"
    colors: [axis.surface], // gap colour matches the chart background
  },
});

const HeatChart = ({ data, graphLabels, height = 250 }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const options = useMemo(
    () => getDefaultOptions(isDark, chartAxisColors(theme)),
    [isDark, theme],
  );
  // const graphData = useMemo(() => {
  //   const newData = data?.map((item) => ({
  //     ...item,
  //     active: true,
  //     value: { data: item.value, name: item.name },
  //   }));
  //   return newData || [];
  // }, [data]);

  const graphData = useMemo(() => {
    return (
      data?.map((item) => ({
        name: item.name,
        data: item.value.map((val, idx) => ({
          x: graphLabels[idx], // pick label for this position
          y: val, // value for this position
        })),
      })) || []
    );
  }, [data, graphLabels]);

  return (
    <Box>
      <ReactApexChart
        options={{
          ...options,
          xaxis: { ...options.xaxis, categories: graphLabels },
          stroke: {
            ...options.stroke,
            colors: [theme.palette.background.paper],
          },
        }}
        series={graphData}
        type="heatmap"
        height={height}
      />
    </Box>
  );
};

export default HeatChart;

HeatChart.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
  height: PropTypes.number,
};
