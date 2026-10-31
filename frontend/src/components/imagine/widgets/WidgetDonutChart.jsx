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

export default function WidgetDonutChart({ config }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const series = config.series || [];
  const labels = config.labels || [];

  const options = {
    chart: {
      type: "donut",
      background: "transparent",
      foreColor: theme.palette.text.secondary,
    },
    theme: { mode: isDark ? "dark" : "light" },
    colors: config.colors || COLORS,
    labels,
    plotOptions: {
      pie: {
        donut: {
          size: "60%",
          labels: {
            show: !!config.centerLabel,
            total: config.centerLabel
              ? {
                  show: true,
                  label: config.centerLabel,
                  fontSize: "13px",
                  fontWeight: 600,
                }
              : { show: false },
          },
        },
      },
    },
    dataLabels: { enabled: false },
    legend: { position: "bottom", fontSize: "12px" },
    tooltip: { theme: isDark ? "dark" : "light" },
    stroke: { width: isDark ? 1 : 2, colors: [theme.palette.background.paper] },
  };

  return (
    <ReactApexChart
      options={options}
      series={series}
      type="donut"
      height="100%"
    />
  );
}

WidgetDonutChart.propTypes = { config: PropTypes.object.isRequired };
