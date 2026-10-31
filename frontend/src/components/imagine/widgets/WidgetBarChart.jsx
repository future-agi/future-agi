/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";

const COLORS = [
  "#7B56DB",
  "#1ABCFE",
  "#FF6B6B",
  "#2ECB71",
  "#F7B731",
  "#E84393",
  "#0984E3",
  "#FD7E14",
  "#00CEC9",
  "#A29BFE",
];

export default function WidgetBarChart({ config }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const series = config.series || [];
  const categories = config.categories || [];
  const horizontal = config.horizontal ?? false;
  const stacked = config.stacked ?? false;

  const options = {
    chart: {
      type: "bar",
      stacked,
      background: "transparent",
      foreColor: theme.palette.text.secondary,
      toolbar: { show: false },
    },
    theme: { mode: isDark ? "dark" : "light" },
    colors: config.colors || COLORS,
    plotOptions: {
      bar: {
        borderRadius: 3,
        horizontal,
        columnWidth: "60%",
        barHeight: "60%",
      },
    },
    dataLabels: { enabled: false },
    xaxis: { categories },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 3,
    },
    tooltip: { theme: isDark ? "dark" : "light" },
    legend: {
      show: series.length > 1,
      position: "top",
      horizontalAlign: "left",
      fontSize: "12px",
    },
  };

  return (
    <ReactApexChart
      options={options}
      series={series}
      type="bar"
      height="100%"
    />
  );
}

WidgetBarChart.propTypes = { config: PropTypes.object.isRequired };
