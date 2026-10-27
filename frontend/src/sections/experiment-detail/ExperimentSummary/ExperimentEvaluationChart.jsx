import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import ReactApexChart from "react-apexcharts";
import Iconify from "src/components/iconify";
import { getUniqueColorPalette } from "src/utils/utils";

const ExperimentEvaluationChart = ({ col, rows }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const data = rows?.map((row, idx) => ({
    x: row?.experiment_dataset_name ?? row?.experimentDatasetName,
    y: row?.[col?.name],
    fillColor: isDark
      ? getUniqueColorPalette(idx).tagForeground
      : getUniqueColorPalette(idx).solid,
  }));

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        paddingX: "12px",
        paddingY: 2,
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Iconify
          icon="material-symbols:check-circle-outline"
          sx={{ color: "text.secondary" }}
        />
        <Typography color="text.secondary" fontSize="13px" fontWeight={700}>
          {col?.name?.split("-")?.[0]}
        </Typography>
      </Box>
      <Box>
        <ReactApexChart
          options={{
            chart: {
              type: "bar",
              background: "transparent",
              toolbar: {
                show: false,
              },
              foreColor: isDark ? "#a1a1aa" : undefined,
            },
            theme: {
              mode: isDark ? "dark" : "light",
            },
            plotOptions: {
              bar: {
                horizontal: true,
                columnWidth: "70%",
              },
            },
            xaxis: {
              type: "category",
            },
            yaxis: {
              min: 0,
              max: 100,
            },
            dataLabels: {
              enabled: false,
            },
            tooltip: {
              theme: isDark ? "dark" : "light",
            },
            grid: {
              borderColor: isDark ? "#27272a" : undefined,
            },
          }}
          series={[
            {
              name: col?.name?.split("-")?.[0],
              data: data,
            },
          ]}
          type="bar"
          width="100%"
          height={120 * Math.log2((rows?.length || 0) + 1)}
        />
      </Box>
    </Box>
  );
};

ExperimentEvaluationChart.propTypes = {
  col: PropTypes.object,
  rows: PropTypes.array,
};

export default ExperimentEvaluationChart;
