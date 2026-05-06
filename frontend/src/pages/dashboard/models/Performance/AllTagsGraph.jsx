import { Box, useTheme } from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import ReactApexChart from "react-apexcharts";

const AllTagsGraph = ({ data }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [chartWidth, setChartWidth] = useState(0);
  const containerRef = useRef(null);

  useEffect(() => {
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.target === containerRef.current) {
          setChartWidth(entry.contentRect.width);
        }
      }
    });

    setChartWidth(containerRef.current.offsetWidth);

    if (containerRef.current) {
      resizeObserver.observe(containerRef.current);
    }

    return () => {
      if (containerRef.current) {
        // eslint-disable-next-line react-hooks/exhaustive-deps
        resizeObserver.unobserve(containerRef.current);
      }
    };
  }, []);

  const series = Object.entries(data).map(([name, graphData]) => ({
    type: "line",
    data: graphData,
    color:
      name === "good" ? theme.palette.success.light : theme.palette.error.light,
    name: _.capitalize(name),
  }));

  const chartOptions = {
    xaxis: {
      type: "datetime",
    },
    yaxis: {
      min: 0,
      labels: {
        formatter: (value) => Math.round(value),
      },
    },
    legend: {
      show: true,
      position: "top",
      horizontalAlign: "right",
      offsetY: 20,
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

AllTagsGraph.propTypes = {
  data: PropTypes.object,
};

export default AllTagsGraph;
