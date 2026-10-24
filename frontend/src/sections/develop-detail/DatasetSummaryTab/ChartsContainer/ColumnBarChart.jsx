import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Radio, Typography, useTheme } from "@mui/material";
import ReactApexChart from "react-apexcharts";
import SvgColor from "src/components/svg-color/svg-color";
import { generateAllColors } from "src/sections/projects/ChartsView/common";
import { palette } from "src/theme/palette";
import CompareDatasetSummaryIcon from "../CompareDatasetSummaryIcon";
import TotalRowCount from "./TotalRowCount";

const getDefaultOptions = (isDark) => ({
  chart: {
    height: 350,
    type: "bar",
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
  theme: {
    mode: isDark ? "dark" : "light",
  },
  grid: {
    show: true,
    strokeDashArray: 10,
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    borderColor: isDark ? "#27272a" : undefined,
  },
  plotOptions: {
    bar: {
      horizontal: false,
      borderRadius: 0,
      columnWidth: "16px",
      borderRadiusApplication: "end",
    },
  },
  dataLabels: {
    enabled: false,
  },
  stroke: {
    show: true,
    width: 2,
    colors: ["transparent"],
  },
  yaxis: {
    show: true,
    autoScale: true,
  },
  xaxis: {
    categories: [],
  },
  fill: {
    opacity: 1,
  },
  legend: {
    show: false,
  },
  tooltip: {
    theme: isDark ? "dark" : "light",
  },
  colors: generateAllColors(palette),
});

const ColumnBarChart = ({
  height = 250,
  data,
  graphLabels,
  showCustomLegend = true,
  title = null,
  legend = {},
  colors = null,
  datasetIndex,
  type = "",
  plotOptions = null,
}) => {
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
                color={generateAllColors(palette, colors || undefined)[index]}
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
        height: "100%",
      }}
    >
      {type !== "annotation" && showCustomLegend && <CustomLegend />}
      <ReactApexChart
        ref={chartRef}
        key={visibleSeries
          .map((d, ind) => `${ind}-${d.datasetIndex}`)
          .join("-")}
        // @ts-ignore
        options={{
          ...defaultOptions,
          ...(title ? { title } : {}),
          legend: { ...defaultOptions.legend, ...legend },
          xaxis: { categories: graphLabels },
          ...(colors && { colors: generateAllColors(palette, colors) }),
          ...(plotOptions ? { plotOptions } : {}),
        }}
        series={visibleSeries
          .filter((item) => item.active)
          .map((item) => ({
            ...item.value,
            color: generateAllColors(palette, colors || undefined)[
              item?.itemId
            ],
          }))}
        // series={visibleSeries.map((item) =>
        //   item.active
        //     ? item.value
        //     : { ...item.value, data: item.value.data.map((_temp) => 0) },
        // )}
        type="bar"
        height={height - (containerHeight || 0)}
      />
    </Box>
  );
};

export default ColumnBarChart;

ColumnBarChart.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
  showCustomLegend: PropTypes.bool,
  title: PropTypes.object,
  legend: PropTypes.object,
  colors: PropTypes.array,
  datasetIndex: PropTypes.number,
  type: PropTypes.string,
  plotOptions: PropTypes.object,
  height: PropTypes.number,
};
