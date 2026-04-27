import { Box, useTheme } from "@mui/material";
import React, { useEffect, useRef, useState } from "react";

import ResizeObserver from "resize-observer-polyfill";
import PropType from "prop-types";
import ReactApexChart from "react-apexcharts";
import { getColorFromSeed } from "src/utils/utils";

const PerformanceGraph = ({ data }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [chartWidth, setChartWidth] = useState(0);
  const containerRef = useRef(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.target === container) {
          setChartWidth(entry.contentRect.width);
        }
      }
    });

    setChartWidth(container.offsetWidth);

    resizeObserver.observe(container);

    return () => {
      resizeObserver.unobserve(container);
    };
  }, []);

  const series = Object.entries(data).map(([name, graphData]) => ({
    type: "line",
    data: graphData,
    color: getColorFromSeed(name),
    name: name,
  }));

  const chartOptions = {
    xaxis: {
      type: "datetime",
      labels: {
        datetimeUTC: false,
      },
    },
    yaxis: {
      min: 0,
      max: 100,
      tickAmount: 5,
      labels: {
        formatter: (value) => Math.round(value),
      },
    },
    legend: {
      show: true,
      position: "top",
      horizontalAlign: "left",
      offsetX: 18,
      offsetY: 10,
    },
    stroke: {
      width: 3,
      curve: "smooth", // This will make the line smooth
    },
    chart: {
      background: "transparent",
      foreColor: isDark ? "#a1a1aa" : undefined,
      toolbar: {
        show: true,
        tools: {
          download: false,
          selection: false,
          zoom: false,
          zoomin: false,
          zoomout: false,
          pan: false,
          reset: true,
        },
      },
    },
    theme: {
      mode: isDark ? "dark" : "light",
    },
    tooltip: {
      theme: isDark ? "dark" : "light",
    },
    grid: {
      borderColor: isDark ? "#27272a" : undefined,
    },
  };

  return (
    <Box ref={containerRef} sx={{ width: "100%" }}>
      <ReactApexChart
        options={chartOptions}
        series={series}
        type="line"
        width={chartWidth}
        height={240}
      />
    </Box>
  );
};

PerformanceGraph.propTypes = {
  data: PropType.object,
};

export default PerformanceGraph;
