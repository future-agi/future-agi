import React from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { darken, lighten, rgbToHex, useTheme } from "@mui/material/styles";
import { seriesColor } from "src/theme/data-colors";

// Sequential ramp from the first data colour: magnitude reads as increasing
// distance from the surface. On light that means light -> dark; on dark it has
// to run the other way, otherwise the "Very High" cells vanish into the page.
// ApexCharts shades range colours itself and only reads hex, hence rgbToHex.
const heatmapRanges = (theme) => {
  const base = seriesColor(theme, 0);
  const isDark = theme.palette.mode === "dark";
  const toSurface = isDark ? darken : lighten;
  const fromSurface = isDark ? lighten : darken;
  return [
    { from: 0, to: 25, color: toSurface(base, 0.75), name: "Low" },
    { from: 26, to: 50, color: toSurface(base, 0.35), name: "Medium" },
    { from: 51, to: 75, color: base, name: "High" },
    { from: 76, to: 100, color: fromSurface(base, 0.35), name: "Very High" },
  ].map((range) => ({ ...range, color: rgbToHex(range.color) }));
};

export default function WidgetHeatmap({ config }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const series = config.series || [];

  const options = {
    chart: {
      type: "heatmap",
      background: "transparent",
      foreColor: theme.palette.text.secondary,
      toolbar: { show: false },
    },
    theme: { mode: isDark ? "dark" : "light" },
    dataLabels: { enabled: config.showValues ?? false },
    colors: [config.color || seriesColor(theme, 0)],
    plotOptions: {
      heatmap: {
        radius: 2,
        colorScale: {
          ranges: config.ranges || heatmapRanges(theme),
        },
      },
    },
    xaxis: { categories: config.categories || [] },
    grid: { borderColor: theme.palette.divider },
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
