import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import Stack from "@mui/material/Stack";
import { useTheme } from "@mui/material/styles";
import Typography from "@mui/material/Typography";
import { fNumber, fPercent } from "src/utils/format-number";
import Iconify from "src/components/iconify";
import Chart from "src/components/charts/chart";

// ----------------------------------------------------------------------

export default function AppWidgetSummary({
  title,
  percent,
  total,
  chart,
  sx,
  ...other
}) {
  const theme = useTheme();

  const {
    colors = [theme.palette.primary.light, theme.palette.primary.main],
    series,
    options,
  } = chart;

  const chartOptions = {
    colors,
    fill: {
      type: "gradient",
      gradient: {
        colorStops: [
          { offset: 0, color: colors[0], opacity: 1 },
          { offset: 100, color: colors[1], opacity: 1 },
        ],
      },
    },
    chart: {
      sparkline: {
        enabled: true,
      },
    },
    plotOptions: {
      bar: {
        columnWidth: "68%",
        borderRadius: 2,
      },
    },
    tooltip: {
      x: { show: false },
      y: {
        formatter: (value) => fNumber(value),
        title: {
          formatter: () => "",
        },
      },
      marker: { show: false },
    },
    ...options,
  };

  return (
    <Card
      sx={{ display: "flex", alignItems: "center", p: 3, ...sx }}
      {...other}
    >
      <Box sx={{ flexGrow: 1 }}>
        <Typography variant="subtitle2">{title}</Typography>

        <Stack direction="row" alignItems="center" sx={{ mt: 2, mb: 1 }}>
          <Iconify
            width={24}
            icon={
              percent < 0
                ? "solar:double-alt-arrow-down-bold-duotone"
                : percent == 0
                  ? "mdi:tilde"
                  : "solar:double-alt-arrow-up-bold-duotone"
            }
            sx={{
              mr: 1,
              color: "success.main",
              ...(percent < 0 && {
                color: "error.main",
              }),
              ...(percent == 0 && {
                color: "warning.main",
              }),
            }}
          />

          <Typography component="div" variant="subtitle2">
            {percent > 0 && "+"}

            {fPercent(percent)}
          </Typography>
        </Stack>

        <Typography variant="h3">{fNumber(total)}</Typography>
      </Box>

      <Chart
        dir="ltr"
        type="bar"
        series={[{ data: series }]}
        options={chartOptions}
        width={60}
        height={36}
      />
    </Card>
  );
}

AppWidgetSummary.propTypes = {
  chart: PropTypes.object,
  percent: PropTypes.number,
  sx: PropTypes.object,
  title: PropTypes.string,
  total: PropTypes.number,
};
