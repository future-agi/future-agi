import React from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";

// Sequential ramp — one hue, magnitude read as increasing distance from the
// surface. On light that means light -> dark; on dark it has to run the other
// way, otherwise the "Very High" cells are the ones that vanish into the page.
const LIGHT_RANGES = [
  { from: 0, to: 25, color: "#e0e7ff", name: "Low" },
  { from: 26, to: 50, color: "#818cf8", name: "Medium" },
  { from: 51, to: 75, color: "#6366f1", name: "High" },
  { from: 76, to: 100, color: "#4338ca", name: "Very High" },
];

const DARK_RANGES = [
  { from: 0, to: 25, color: "#312e81", name: "Low" },
  { from: 26, to: 50, color: "#4f46e5", name: "Medium" },
  { from: 51, to: 75, color: "#818cf8", name: "High" },
  { from: 76, to: 100, color: "#c7d2fe", name: "Very High" },
];

export default function WidgetHeatmap({ config }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const series = config.series || [];

  const options = {
    chart: {
      type: "heatmap",
      background: "transparent",
      foreColor: isDark ? "#a1a1aa" : "#666",
      toolbar: { show: false },
    },
    theme: { mode: isDark ? "dark" : "light" },
    dataLabels: { enabled: config.showValues ?? false },
    colors: [config.color || "#7B56DB"],
    plotOptions: {
      heatmap: {
        radius: 2,
        colorScale: {
          ranges: config.ranges || (isDark ? DARK_RANGES : LIGHT_RANGES),
        },
      },
    },
    xaxis: { categories: config.categories || [] },
    grid: { borderColor: isDark ? "#27272a" : "#f0f0f0" },
    tooltip: { theme: isDark ? "dark" : "light" },
  };

  return (
    <ReactApexChart
      options={options}
      series={series}
      type="heatmap"
      height="100%"
    />
  );
}

WidgetHeatmap.propTypes = { config: PropTypes.object.isRequired };
