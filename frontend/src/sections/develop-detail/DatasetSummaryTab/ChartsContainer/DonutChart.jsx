import { Box, Button, Checkbox, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactApexChart from "react-apexcharts";
import { generateAllColors } from "src/sections/projects/ChartsView/common";
import { palette } from "src/theme/palette";
import CompareDatasetSummaryIcon from "../CompareDatasetSummaryIcon";
import TotalRowCount from "./TotalRowCount";
import { cleanChoiceLabel } from "src/sections/develop-detail/DataTab/common";

const getDefaultOptions = (isDark) => ({
  chart: {
    type: "donut",
    background: "transparent",
    toolbar: {
      show: false,
    },
    foreColor: isDark ? "#a1a1aa" : undefined,
  },
  theme: { mode: isDark ? "dark" : "light" },
  tooltip: { theme: isDark ? "dark" : "light" },
  colors: generateAllColors(palette, ["blue"]),
  dataLabels: {
    enabled: false,
  },
  legend: {
    show: true,
    position: "bottom",
    markers: {
      width: 0,
      height: 0,
    },
    onItemClick: {
      toggleDataSeries: false,
    },
    formatter: function (seriesName, opts) {
      const color = opts.w.globals.colors[opts.seriesIndex];
      const value = opts.w.globals.seriesPercent[opts.seriesIndex];
      const percentage = value?.[0]?.toFixed(0);
      return `<span style="font-size: 16px;display:flex;gap:8px;align-items: center;"><span style="width: 16px; height: 16px; background-color: ${color}"></span> <span>${seriesName}</span><span>${percentage}%</span></span>`;
    },
  },
});

const DonutChart = ({
  data = [],
  graphLabels,
  headerData,
  datasetIndex,
  plotOptions,
  options = {},
  type = "",
  height = 250,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const defaultOptions = useMemo(() => getDefaultOptions(isDark), [isDark]);
  const chartRef = useRef(null);
  const [visibleSeries, setVisibleSeries] = useState([]);

  useEffect(() => {
    setVisibleSeries(
      data?.map((item, index) => ({ ...item, active: index === 0 })) || [],
    );
  }, [data]);

  const seriesData = useMemo(() => {
    return visibleSeries.find((item) => item.active);
  }, [visibleSeries]);

  const headerValue = useMemo(() => {
    const maxValue = Math.max(...Object.values(headerData?.average || {}));
    const headerValue = Object.keys(headerData?.average || {}).filter(
      (key) => headerData?.average[key] === maxValue,
    );
    return headerValue;
  }, [headerData?.average]);

  const handleLegendClick = (index) => {
    const current = [
      ...visibleSeries.map((temp, ind) => ({ ...temp, active: ind == index })),
    ];
    setVisibleSeries(current);
  };

  const CustomLegend = () => {
    if (visibleSeries?.length === 0) return <></>;
    return (
      <Box display="flex" flexWrap={"wrap"} sx={{ gap: "8px 16px" }}>
        {visibleSeries?.map((item, index) => {
          const currentIndex =
            datasetIndex || datasetIndex === 0
              ? datasetIndex
              : item.datasetIndex;
          if (visibleSeries.length === 1) return <></>;
          return (
            <Button
              key={index}
              size="small"
              variant={"outlined"}
              {...(item.active && { color: "primary" })}
              sx={{
                borderRadius: "4px",
                color: "text.secondary",
                ...((currentIndex != null || currentIndex != undefined) && {
                  padding: "8px",
                }),
              }}
              startIcon={
                <CompareDatasetSummaryIcon
                  style={{ backgroundColor: "background.paper" }}
                  index={currentIndex}
                />
              }
              onClick={() => handleLegendClick(index)}
            >
              <Checkbox
                sx={{ padding: 0, paddingRight: "4px" }}
                checked={item.active}
              />
              {item.name}
              <TotalRowCount
                value={item.totalCells}
                sx={{ marginTop: "4px" }}
              />
            </Button>
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
      {type !== "annotation" && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <Box display={"flex"} gap={0.5} alignItems={"end"}>
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
              color="green.500"
            >
              {headerValue.map(cleanChoiceLabel).join(", ")}
              {/* {headerValue.map((item) => `${item} ${(headerData?.average[item])?.toFixed(0)}%`).join(", ")} */}
            </Typography>
            {visibleSeries?.length === 1 && (
              <TotalRowCount value={visibleSeries?.[0]?.totalCells} />
            )}
          </Box>
          <CustomLegend />
        </Box>
      )}
      <ReactApexChart
        ref={chartRef}
        // @ts-ignore
        options={{
          ...defaultOptions,
          labels: (graphLabels || []).map(cleanChoiceLabel),
          ...(plotOptions && { plotOptions }),
          ...options,
        }}
        series={seriesData?.value || []}
        type="donut"
        height={height}
      />
    </Box>
  );
};

export default DonutChart;

DonutChart.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
  headerData: PropTypes.object,
  datasetIndex: PropTypes.number,
  plotOptions: PropTypes.object,
  options: PropTypes.object,
  type: PropTypes.string,
  height: PropTypes.number,
};
