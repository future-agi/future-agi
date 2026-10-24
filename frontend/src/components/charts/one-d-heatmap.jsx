/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material";

export default function OneDHeatmap() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  // The data series representing the heatmap values
  // You'll need to provide the actual data values here.
  const series = [
    {
      // name: 'Heatmap',
      data: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
    },
  ];

  // The options for the ApexChart
  const options = {
    chart: {
      // height: 80,
      type: "heatmap",
      background: "transparent",
      foreColor: theme.palette.text.secondary,
      parentHeightOffset: 0,
      toolbar: {
        show: false,
      },
      sparkline: {
        enabled: true, // Enable sparkline to remove axes and padding
      },
    },
    theme: {
      mode: isDark ? "dark" : "light",
    },
    plotOptions: {
      heatmap: {
        shadeIntensity: 0.5,
        radius: 0,
        useFillColorAsStroke: true,
        colorScale: {
          min: 0,
          max: 1,
          color: ["#ffcccc", "#ff3333"], // Gradient from very light red to dark red
        },
      },
    },
    dataLabels: {
      enabled: false,
    },
    title: {
      text: undefined,
    },
    xaxis: {
      type: "category",
      // Assuming you want to display the same values as ticks that are in your data series
      categories: series[0].data.map((value) => value.toFixed(4)), // Format numbers to 4 decimal places
      tickPlacement: "on",
    },
    yaxis: {
      show: false, // This hides the entire Y-axis, including the labels and title
    },
    tooltip: {
      theme: isDark ? "dark" : "light",
      enabled: false, // This will disable the tooltip
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
        type="heatmap"
        height={20}
      />
    </div>
  );
}

OneDHeatmap.propTypes = {
  model: PropTypes.object,
};
