import { Box, Radio, Typography, useTheme } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactApexChart from "react-apexcharts";
import SvgColor from "src/components/svg-color/svg-color";
import PropTypes from "prop-types";
import { generateAllColors } from "src/sections/projects/ChartsView/common";
import { palette } from "src/theme/palette";
import CompareDatasetSummaryIcon from "../CompareDatasetSummaryIcon";
import TotalRowCount from "./TotalRowCount";

const getDefaultOptions = (isDark) => ({
  chart: {
    height: 350,
    type: "area",
    background: "transparent",
    zoom: { type: "x", enabled: true, autoScaleYaxis: true },
    selection: {
      enabled: true,
      type: "x",
    },
    toolbar: {
      show: false,
    },
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    foreColor: isDark ? "#a1a1aa" : undefined,
  },
  theme: { mode: isDark ? "dark" : "light" },
  tooltip: { theme: isDark ? "dark" : "light" },
  grid: {
    show: true,
    strokeDashArray: 10,
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    borderColor: isDark ? "#27272a" : undefined,
  },
  dataLabels: {
    enabled: false,
  },
  stroke: {
    curve: "smooth",
    width: 1,
  },
  yaxis: {
    show: true,
    autoScale: true,
  },
  xaxis: {
    categories: [],
  },
  legend: {
    show: false,
  },
  colors: generateAllColors(palette),
});

const AreaChart = ({ data, graphLabels, datasetIndex, height = 250 }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const defaultOptions = useMemo(() => getDefaultOptions(isDark), [isDark]);
  const chartRef = useRef(null);
  const containerRef = useRef(null);
  const [visibleSeries, setVisibleSeries] = useState([]);
  const [containerHeight, setContainerHeight] = useState(0);

  useEffect(() => {
    if (visibleSeries.length > 1 && containerRef.current) {
      setContainerHeight(containerRef.current.offsetHeight + 20);
    }
  }, [visibleSeries.length]);

  useEffect(() => {
    const newData = data?.map((item, ind) => ({
      ...item,
      itemId: ind,
      active: true,
      value: { data: item.value, name: item.name },
    }));
    setVisibleSeries(newData || []);
  }, [data]);

  const clickAllLegend = (active) => {
    setVisibleSeries((pre) =>
      pre.map((item) => ({
        ...item,
        active: !active,
      })),
    );
  };

  const handleLegendClick = (allActive, index) => {
    const current = [
      ...visibleSeries.map(
        (temp, ind) =>
          ind === index
            ? { ...temp, active: true }
            : { ...temp, active: false },
        // ind === index
        //   ? { ...temp, active: allActive ? true : !temp.active }
        //   : { ...temp, active: allActive ? false : temp.active },
      ),
    ];
    setVisibleSeries(current);
  };

  const CustomLegend = () => {
    const notAllSelected =
      visibleSeries.length === 1
        ? true
        : visibleSeries.some((item) => !item.active);
    if (visibleSeries?.length === 0) return <></>;
    return (
      <Box
        display="flex"
        flexWrap={"wrap"}
        sx={{ gap: "8px 16px" }}
        ref={containerRef}
      >
        {visibleSeries.length > 1 && (
          <Box
            display={"flex"}
            alignItems="center"
            gap={0.5}
            color={!notAllSelected ? "text.primary" : "text.secondary"}
            sx={{ cursor: "pointer" }}
            onClick={() => clickAllLegend(!notAllSelected)}
          >
            <Radio checked={!notAllSelected} />
            <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
              All
            </Typography>
          </Box>
        )}
        {visibleSeries.map((item, index) => {
          const currentIndex =
            datasetIndex || datasetIndex === 0
              ? datasetIndex
              : item.datasetIndex;
          if (visibleSeries.length === 1) return <></>;
          return (
            <Box
              key={index}
              display={"flex"}
              alignItems="center"
              gap={0.5}
              color={"text.primary"}
              sx={{ cursor: "pointer" }}
              onClick={() => handleLegendClick(!notAllSelected, index)}
            >
              <Radio checked={notAllSelected && item.active} />
              <CompareDatasetSummaryIcon index={currentIndex} />
              <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
                {item.name}
              </Typography>
              <TotalRowCount
                value={item.totalCells}
                sx={{ marginTop: "4px" }}
              />
              <SvgColor
                // @ts-ignore
                src="/assets/icons/summary/bar-graph.svg"
                color={generateAllColors(palette)[index]}
              />
            </Box>
          );
        })}
      </Box>
    );
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        flex: 1,
      }}
    >
      <CustomLegend />
      <ReactApexChart
        ref={chartRef}
        type="area"
        // @ts-ignore
        options={{
          ...defaultOptions,
          xaxis: { categories: graphLabels },
        }}
        series={visibleSeries
          .filter((item) => item.active)
          .map((item) => ({
            ...item.value,
            color: generateAllColors(palette)[item.itemId],
          }))}
        // series={visibleSeries.map((item) =>
        //   item.active
        //     ? item.value
        //     : {
        //         ...item?.value,
        //         data: item?.value?.data?.map((_temp) => 0) || [0],
        //       },
        // )}
        height={height - containerHeight}
      />
    </Box>
  );
};

export default AreaChart;

AreaChart.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
  datasetIndex: PropTypes.number,
  height: PropTypes.number,
};
