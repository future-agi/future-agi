/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React from "react";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material";

export default function BaselineChart() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const series = [
    {
      name: "Primary False Negative Rate",
      type: "line",
      data: [
        { x: new Date("2024-10-15").getTime(), y: 0.5 },
        { x: new Date("2024-10-16").getTime(), y: 0.6 },
        { x: new Date("2024-10-17").getTime(), y: 0.7 },
        // Add more data points
      ],
    },
    {
      name: "Baseline Average",
      type: "line",
      data: [
        { x: new Date("2024-10-15").getTime(), y: 0.4 },
        { x: new Date("2024-10-16").getTime(), y: 0.4 },
        { x: new Date("2024-10-17").getTime(), y: 0.4 },
        // Fixed y-value, varying x-values to create a straight line
      ],
    },
  ];

  const options = {
    chart: {
      type: "line",
      background: "transparent",
      foreColor: theme.palette.text.secondary,
      zoom: {
        enabled: false,
      },
      toolbar: {
        show: false,
      },
    },
    theme: {
      mode: isDark ? "dark" : "light",
    },
    colors: ["#77B6EA", "#545454"],
    dataLabels: {
      enabled: false,
    },
    stroke: {
      curve: "smooth",
      dashArray: [0, 4], // Solid line for the first series, dashed line for the second
    },
    markers: {
      size: 0,
    },
    xaxis: {
      type: "datetime",
      tickAmount: 6,
      tooltip: {
        enabled: false, // This will disable the x-axis tooltip
      },
    },
    yaxis: {
      title: {
        text: "Rate",
      },
      min: 0,
      max: 1,
    },
    legend: {
      position: "bottom",
      horizontalAlign: "center",
    },
    tooltip: {
      theme: isDark ? "dark" : "light",
      x: {
        format: "dd MMM yyyy",
      },
    },
    grid: {
      borderColor: theme.palette.divider,
    },
  };

  return (
    <div id="chart">
      <ReactApexChart
        options={options}
        series={series}
        type="line"
        height={180}
      />
    </div>
  );
}

BaselineChart.propTypes = {};
