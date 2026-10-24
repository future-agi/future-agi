/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";

const COLORS = ["#7B56DB", "#1ABCFE", "#FF6B6B", "#2ECB71", "#F7B731"];

export default function WidgetRadarChart({ config }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const series = config.series || [];
  const categories = config.categories || [];

  const options = {
    chart: {
      type: "radar",
      background: "transparent",
      foreColor: theme.palette.text.secondary,
      toolbar: { show: false },
    },
    theme: { mode: isDark ? "dark" : "light" },
    colors: config.colors || COLORS,
    xaxis: { categories },
    yaxis: { show: false },
    stroke: { width: 2 },
    fill: { opacity: 0.15 },
    markers: { size: 3 },
    tooltip: { theme: isDark ? "dark" : "light" },
    legend: {
      show: series.length > 1,
      position: "bottom",
      fontSize: "12px",
    },
  };

  return (
    <ReactApexChart
      options={options}
      series={series}
      type="radar"
      height="100%"
    />
  );
}

WidgetRadarChart.propTypes = { config: PropTypes.object.isRequired };
