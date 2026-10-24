/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React from "react";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material";

export default function MultiLineChart() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const series = [
    {
      name: "Series A",
      data: [
        { x: new Date("2023-11-30").getTime(), y: 0.4 },
        { x: new Date("2023-12-02").getTime(), y: 0.5 },
        // Add more data points in { x, y } format
      ],
    },
    {
      name: "Series B",
      data: [
        { x: new Date("2023-11-30").getTime(), y: 0.3 },
        { x: new Date("2023-12-02").getTime(), y: 0.4 },
        // Add more data points in { x, y } format
      ],
    },
    // Add more series as per your data
  ];

  const options = {
    chart: {
      // height: 350,
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
    colors: ["#FF4560", "#00E396", "#008FFB", "#775DD0"], // Add more colors if you have more series
    dataLabels: {
      enabled: false,
    },
    stroke: {
      width: [3, 3], // Line thickness
      curve: "smooth",
    },
    markers: {
      size: 5,
    },
    xaxis: {
      type: "datetime",
      tooltip: {
        enabled: false, // This will disable the x-axis tooltip
      },
      // The categories are not needed anymore since x values are provided in the series data
    },
    tooltip: {
      theme: isDark ? "dark" : "light",
      x: {
        format: "dd MMM",
      },
    },
    grid: {
      borderColor: theme.palette.divider,
    },
    legend: {
      position: "bottom",
      horizontalAlign: "center",
    },
  };

  return (
    <ReactApexChart
      options={options}
      series={series}
      type="line"
      // height={250}
      width="100%"
      height="100%"
    />
  );
}

MultiLineChart.propTypes = {};
