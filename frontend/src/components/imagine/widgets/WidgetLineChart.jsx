import React from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";
import { seriesColors } from "src/theme/data-colors";

export default function WidgetLineChart({ config }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const series = config.series || [];
  const categories = config.categories || [];

  const options = {
    chart: {
      type: "line",
      background: "transparent",
      foreColor: theme.palette.text.secondary,
      toolbar: { show: false },
      zoom: { enabled: false },
    },
    theme: { mode: isDark ? "dark" : "light" },
    colors: config.colors || seriesColors(theme),
    stroke: { width: 2, curve: config.curve || "smooth" },
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
    markers: { size: config.showMarkers ? 4 : 0 },
  };

  return (
    <ReactApexChart
      options={options}
      series={series}
      type="line"
      height="100%"
    />
  );
}

WidgetLineChart.propTypes = { config: PropTypes.object.isRequired };
