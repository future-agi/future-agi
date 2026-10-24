/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material";

export default function ColumnChart({ size }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  // Notice that each data point is an object with x (label) and y (value) properties.
  // The fillColor property is used to set a unique color for each bar.

  const chartHeight = size === "small" ? 50 : 250;

  const series = [
    {
      data: [
        { x: "Scammeds", y: 15, fillColor: "#FF4560" },
        { x: "Haley Group", y: 20, fillColor: "#00E396" },
        { x: "Reilly LLC", y: 25, fillColor: "#FEB019" },
        { x: "Champlin and...", y: 30, fillColor: "#775DD0" },
        { x: "Kirlin and S...", y: 35, fillColor: "#008FFB" },
        { x: "Leannon Ward", y: 40, fillColor: "#FF4560" },
        { x: "Schiller Ltd", y: 45, fillColor: "#00E396" },
      ],
    },
  ];

  let options = {
    chart: {
      type: "bar",
      background: "transparent",
      foreColor: theme.palette.text.secondary,
      toolbar: {
        show: false,
      },
    },
    theme: {
      mode: isDark ? "dark" : "light",
    },
    plotOptions: {
      bar: {
        borderRadius: 4,
        distributed: true, // This ensures that each bar is treated as a separate entity
        horizontal: false,
      },
    },
    dataLabels: {
      enabled: false,
    },
    xaxis: {
      // The categories can be omitted as we are now specifying the x value in the series data
    },
    yaxis: {
      title: {
        text: "% Volume",
      },
    },
    grid: {
      borderColor: theme.palette.divider,
    },
    tooltip: {
      theme: isDark ? "dark" : "light",
      enabled: true,
      custom: function ({ seriesIndex, dataPointIndex, w }) {
        // Retrieve the data for the current data point
        const data = w.globals.initialSeries[seriesIndex].data[dataPointIndex];

        // Define the HTML structure of the tooltip
        const tooltipHtml = `
          <div class="apexcharts-tooltip-title" style="font-size: 16px; font-weight: 600; margin-bottom: 4px;">
            ${data.x}
          </div>
          <div style="margin: 5px;">
            <div style="font-size: 12px; margin-bottom: 2px;">
              <span>False Negative Rate: </span>
              <span style="font-weight: 600;">${data.falseNegativeRate}</span>
            </div>
            <div style="font-size: 12px; margin-bottom: 2px;">
              <span>% Volume: </span>
              <span style="font-weight: 600;">${data.volumePercent}</span>
            </div>
            <div style="font-size: 12px;">
              <span>Impact Score: </span>
              <span style="font-weight: 600;">${data.impactScore}</span>
            </div>
          </div>
        `;

        return tooltipHtml;
      },
      style: {
        fontSize: "12px",
        fontFamily: undefined,
      },
      x: {
        show: false,
      },
      y: {
        title: {
          formatter: (seriesName) => seriesName,
        },
      },
      marker: {
        show: false,
      },
    },
  };

  if (size === "small") {
    const smallOptions = {
      chart: {
        parentHeightOffset: 0,
        toolbar: {
          show: false,
        },
        sparkline: {
          enabled: true, // Enable sparkline to remove axes and padding
        },
      },
      plotOptions: {
        bar: {
          columnWidth: "90%",
          borderRadius: 0,
          distributed: false, // This ensures that each bar is treated as a separate entity
          horizontal: false,
        },
      },
      dataLabels: {
        enabled: false,
      },
      title: {
        text: undefined,
      },
      xaxis: {
        show: false, // This hides the entire Y-axis, including the labels and title
      },
      yaxis: {
        show: false, // This hides the entire Y-axis, including the labels and title
      },
      tooltip: {
        enabled: false, // This will disable the tooltip
      },
    };

    options = {
      ...options,
      ...smallOptions,
    };
  }

  return (
    <ReactApexChart
      options={options}
      series={series}
      type="bar"
      height={chartHeight}
    />
  );
}

ColumnChart.propTypes = {
  size: PropTypes.string,
};
