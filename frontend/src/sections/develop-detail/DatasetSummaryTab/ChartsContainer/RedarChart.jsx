import { Box, useTheme } from "@mui/material";
import { chartAxisColors } from "src/theme/data-colors";
import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactApexChart from "react-apexcharts";
import PropTypes from "prop-types";
import { generateAllColors } from "src/sections/projects/ChartsView/common";
import { palette } from "src/theme/palette";

const getDefaultOptions = (isDark, axis) => ({
  chart: {
    type: "radar",
    background: "transparent",
    zoom: { type: "x", enabled: true, autoScaleYaxis: true },
    selection: {
      enabled: true,
      type: "x",
    },
    toolbar: {
      show: false,
    },
    dropShadow: {
      enabled: true,
      blur: 1,
      left: 1,
      top: 1,
    },
    foreColor: axis.foreColor,
  },
  theme: {
    mode: isDark ? "dark" : "light",
  },
  plotOptions: {
    radar: {
      size: 150,
      polygons: {
        strokeColors: axis.gridColor,
        connectorColors: axis.gridColor,
      },
    },
  },
  legend: {
    show: false,
  },
  stroke: {
    width: 2,
  },
  fill: {
    opacity: 0.1,
  },
  markers: {
    size: 0,
  },
  yaxis: {
    show: false,
  },
  xaxis: {
    categories: [],
  },
  tooltip: {
    theme: isDark ? "dark" : "light",
  },
  colors: generateAllColors(palette),
});

const RedarChart = ({ data, graphLabels }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const chartRef = useRef(null);
  const [visibleSeries, setVisibleSeries] = useState([]);

  const defaultOptions = useMemo(
    () => getDefaultOptions(isDark, chartAxisColors(theme)),
    [isDark, theme],
  );

  useEffect(() => {
    const newData = data?.map((item) => ({
      ...item,
      active: true,
      value: { data: item.value },
    }));
    setVisibleSeries(newData || []);
  }, [data]);

  return (
    <Box sx={{ display: "flex" }}>
      <ReactApexChart
        ref={chartRef}
        // @ts-ignore
        options={{ ...defaultOptions, xaxis: { categories: graphLabels } }}
        series={visibleSeries
          .filter((temp) => temp.active)
          .map((item) => item.value)}
        type="radar"
        width={500}
        height={400}
      />
    </Box>
  );
};

export default RedarChart;

RedarChart.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
};
