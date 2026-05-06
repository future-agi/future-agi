import { Box, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import ReactApexChart from "react-apexcharts";
import { getTabLabel } from "src/utils/utils";

const TopTagsGraph = ({ data, type }) => {
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

  const series = [
    {
      type: "bar",
      data: data.map((d) => ({ x: getTabLabel(d[0]), y: d[1] })),
      color:
        type === "good"
          ? theme.palette.success.light
          : theme.palette.error.light,
      name: "Tag Count",
    },
  ];

  const chartOptions = {
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
    xaxis: {
      labels: {
        trim: true,
        style: {
          //   fontSize: "10px",
        },
      },
    },
  };

  return (
    <Box ref={containerRef} sx={{ width: "100%" }}>
      <ReactApexChart
        options={chartOptions}
        series={series}
        type="bar"
        width={chartWidth}
        height={400}
      />
    </Box>
  );
};

TopTagsGraph.propTypes = {
  data: PropTypes.object,
  type: PropTypes.string,
};

export default TopTagsGraph;
