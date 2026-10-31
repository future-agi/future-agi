import { Box, Button, Checkbox, Typography, useTheme } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactApexChart from "react-apexcharts";
import PropTypes from "prop-types";
import { generateAllColors } from "src/sections/projects/ChartsView/common";
import { palette } from "src/theme/palette";
import CompareDatasetSummaryIcon from "../CompareDatasetSummaryIcon";
import TotalRowCount from "./TotalRowCount";

const getDefaultOptions = (isDark) => ({
  chart: {
    height: 350,
    type: "bar",
    stacked: true,
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
  },
  yaxis: {
    show: true,
    autoScale: true,
  },
  xaxis: {
    categories: [],
  },
  legend: {
    show: true,
    onItemClick: {
      toggleDataSeries: false,
    },
  },
  plotOptions: {
    bar: {
      columnWidth: "16px",
    },
  },
  colors: generateAllColors(palette, ["green", "red"]),
});

const StackBarChart = ({
  data,
  graphLabels,
  headerData,
  datasetIndex,
  height = 250,
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
    const newData = data?.map((item) => ({
      ...item,
      active: true,
      value: { data: item.value, name: item.name },
    }));
    setVisibleSeries(newData || []);
  }, [data]);

  const series = useMemo(() => {
    const pass = [];
    const fail = [];
    visibleSeries.forEach((item) => {
      if (item.active) {
        pass.push(item.value.data[0]);
        fail.push(item.value.data[1]);
      } else {
        pass.push(0);
        fail.push(0);
      }
    });

    return [
      { name: "Pass", data: pass },
      { name: "Fail", data: fail },
    ];
  }, [visibleSeries]);

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
      ...visibleSeries.map((temp, ind) =>
        ind === index
          ? { ...temp, active: allActive ? true : !temp.active }
          : { ...temp, active: allActive ? false : temp.active },
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
          <Button
            variant={!notAllSelected ? "contained" : "outlined"}
            color={!notAllSelected ? "primary" : "secondary"}
            size="small"
            onClick={() => clickAllLegend(!notAllSelected)}
          >
            All
          </Button>
        )}
        {visibleSeries.map((item, index) => {
          const isActive = notAllSelected && item.active;
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
              border={"1px solid"}
              borderColor={"divider"}
              paddingX={"12px"}
              borderRadius={"4px"}
              onClick={() => handleLegendClick(!notAllSelected, index)}
            >
              <Checkbox sx={{ padding: 0 }} checked={isActive} />
              <CompareDatasetSummaryIcon index={currentIndex} />
              <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
                {item.name}
              </Typography>
              <TotalRowCount value={item.totalCells} />
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
        justifyContent: "space-between",
        height: "100%",
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Box display={"flex"} gap={0.5}>
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color="text.primary"
          >
            {headerData?.name} :
          </Typography>
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color="status.success.fg"
          >
            {headerData?.average} {headerData?.average && "%"}
          </Typography>
          {visibleSeries?.length === 1 && (
            <TotalRowCount value={visibleSeries?.[0]?.totalCells} />
          )}
        </Box>
        <CustomLegend />
      </Box>
      <ReactApexChart
        ref={chartRef}
        // @ts-ignore
        options={{
          ...defaultOptions,
          xaxis: {
            categories: graphLabels,
            labels: {
              trim: true,
              rotate: 0,
              rotateAlways: false,
              hideOverlappingLabels: false,
            },
          },
        }}
        series={series}
        type="bar"
        height={height - containerHeight}
      />
    </Box>
  );
};

export default StackBarChart;

StackBarChart.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
  headerData: PropTypes.object,
  datasetIndex: PropTypes.number,
  height: PropTypes.number,
};
