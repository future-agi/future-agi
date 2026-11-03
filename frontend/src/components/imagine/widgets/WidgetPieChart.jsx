import React from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";
import { seriesColors } from "src/theme/data-colors";

export default function WidgetPieChart({ config }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const series = config.series || [];
  const labels = config.labels || [];

  const options = {
    chart: {
      type: "pie",
      background: "transparent",
      foreColor: theme.palette.text.secondary,
    },
    theme: { mode: isDark ? "dark" : "light" },
    colors: config.colors || seriesColors(theme),
    labels,
    dataLabels: { enabled: true, style: { fontSize: "12px" } },
    legend: {
      position: "bottom",
      fontSize: "12px",
    },
    tooltip: { theme: isDark ? "dark" : "light" },
    stroke: { width: isDark ? 1 : 2, colors: [theme.palette.background.paper] },
  };

  return (
    <ReactApexChart
      options={options}
      series={series}
      type="pie"
      height="100%"
    />
  );
}

WidgetPieChart.propTypes = { config: PropTypes.object.isRequired };
