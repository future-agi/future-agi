import React from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { Box, Typography, Stack, useTheme } from "@mui/material";
import { formatPercentage } from "../../../utils/utils";
import { getLabel } from "./common";

/**
 * Reusable component for deterministic evaluation donut charts
 */
const DeterministicEvaluationChart = ({ title, data }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  // Empty-state placeholder: no data points (e.g. every call errored on this
  // eval) — render the title with a muted message instead of an empty chart.
  if (!data || data.length === 0) {
    return (
      <Box
        display="flex"
        flexDirection="column"
        justifyContent="center"
        sx={{ width: "100%", maxWidth: 320 }}
      >
        <Typography
          sx={{
            typography: "s2",
            fontWeight: "fontWeightMedium",
            color: "text.primary",
          }}
        >
          {getLabel(title)}
        </Typography>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: 115,
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.disabled",
              fontStyle: "italic",
            }}
          >
            No data — every eval run errored
          </Typography>
        </Box>
      </Box>
    );
  }

  const series = data.map((item) => item.value);
  const labels = data.map((item) => item.name);
  const colors = data.map((item) => item.color);

  const options = {
    chart: {
      type: "donut",
      background: "transparent",
      foreColor: isDark ? "#a1a1aa" : undefined,
      toolbar: { show: false },
    },
    theme: {
      mode: isDark ? "dark" : "light",
    },
    labels,
    colors,
    legend: { show: false },
    dataLabels: {
      enabled: true,
      formatter: (val) => `${val.toFixed(0)}%`,
      style: {
        fontSize: "11px",
        fontWeight: 600,
      },
    },
    plotOptions: {
      pie: {
        donut: {
          size: "30%",
        },
      },
    },
    tooltip: {
      theme: isDark ? "dark" : "light",
      y: {
        formatter: (val) => `${val}%`,
      },
    },
    grid: {
      borderColor: isDark ? "#27272a" : undefined,
    },
  };

  return (
    <Box
      display="flex"
      flexDirection="column"
      justifyContent="center"
      sx={{
        width: "100%",
        maxWidth: 320,
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightMedium",
          color: "text.primary",
        }}
      >
        {getLabel(title)}
      </Typography>

      <Stack direction={"row"} gap={0} alignItems={"center"}>
        <Box
          sx={{
            height: 115, // Fixed height for consistency
            width: 115, // Fixed width for 1:1 aspect ratio
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            flexShrink: 0, // Prevent shrinking
          }}
        >
          <ReactApexChart
            options={options}
            series={series}
            type="donut"
            height={115} // Use number, not string
            width={115} // Match the container
          />
        </Box>

        <Stack
          spacing={0.5}
          width="100%"
          sx={{
            maxHeight: "150px",
            overflowY: "auto",
          }}
        >
          {data.map((item) => (
            <Box
              key={item.name}
              display="flex"
              alignItems="center"
              justifyContent="space-between"
            >
              <Box display="flex" alignItems="center" gap={1}>
                <Box
                  sx={{
                    width: 12,
                    height: 12,
                    borderRadius: "3px",
                    backgroundColor: item.color,
                  }}
                />
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: "fontWeightRegular",
                    color: "text.primary",
                  }}
                >
                  {item.name}
                </Typography>
              </Box>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: "fontWeightMedium",
                  color: "text.primary",
                  ml: 0.5,
                }}
                fontWeight={600}
              >
                {formatPercentage(item?.value)}
              </Typography>
            </Box>
          ))}
        </Stack>
      </Stack>
    </Box>
  );
};

DeterministicEvaluationChart.propTypes = {
  title: PropTypes.string.isRequired,
  data: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string.isRequired,
      value: PropTypes.number.isRequired,
      color: PropTypes.string.isRequired,
    }),
  ).isRequired,
};

export default DeterministicEvaluationChart;
