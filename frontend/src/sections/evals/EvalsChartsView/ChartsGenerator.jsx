import { Box, Typography, useTheme } from "@mui/material";
import React from "react";
import ChartsTheme from "./ChartsTheme";
import PropTypes from "prop-types";
import { format } from "date-fns";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { throttle } from "src/utils/utils";

const EvalsChartsGenerator = ({ id, series, subLabel, label, onZoom }) => {
  const theme = useTheme();
  const onMouseMove = throttle(() => {
    trackEvent(Events.hover, {
      [PropertyName.formFields]: { "Chart Name": label },
    });
  }, 30000);

  const color = id == "chart-1" ? "#E2A6F1" : "#AE9AFD";

  const chartOptions = {
    chart: {
      id: id,
      type: "area",
      zoom: { type: "x", enabled: true, autoScaleYaxis: true },
      selection: {
        enabled: true,
        type: "x",
      },
      toolbar: {
        show: false,
      },
      events: {
        zoomed: function (chartContext, { xaxis }) {
          const startDate = format(new Date(xaxis.min), "yyyy-MM-dd HH:mm:ss");
          const endDate = format(new Date(xaxis.max), "yyyy-MM-dd HH:mm:ss");
          trackEvent(Events.selectionChoosen, {
            [PropertyName.formFields]: {
              "Chart Name": label,
              "Selection Start Time": startDate,
              "Selection End Time": endDate,
            },
          });
          if (onZoom) {
            onZoom([startDate, endDate]);
          }
        },
        mouseMove: onMouseMove,
      },
    },
    fill: {
      type: "gradient",
      gradient: {
        shade: "light",
        type: "vertical",
        shadeIntensity: 0.6,
        gradientToColors: [color, theme.palette.background.default],
        opacityFrom: 0.7,
        opacityTo: 0.1,
        stops: [0, 100],
      },
    },
    xaxis: {
      type: "datetime",
    },
    yaxis: {
      forceNiceScale: true,
      labels: {
        formatter: function (value) {
          if (value >= 1000000) {
            return (value / 1000000).toFixed(1) + "M";
          } else if (value >= 1000) {
            return (value / 1000).toFixed(1) + "K";
          }
          return value; // For values less than 1000
        },
      },
    },
    stroke: {
      curve: "smooth",
      width: 3,
      dropShadow: {
        enabled: true,
        top: 10,
        left: 0,
        blur: 8,
        opacity: 0.5,
        color: theme.palette.text.primary,
      },
    },
    colors: [color],
    dataLabels: {
      enabled: false,
    },
    markers: {
      size: 0,
      showNullDataPoints: false,
    },
    tooltip: {
      custom: function ({ seriesIndex, dataPointIndex, w }) {
        const xValue = w.globals.seriesX[seriesIndex][dataPointIndex];
        const yValue = w.globals.series[seriesIndex][dataPointIndex];
        const formattedDate = format(new Date(xValue), "dd/MM/yyyy HH:mm:ss");

        const label =
          w.config.series[seriesIndex]?.name || "Label: Not Available";

        return `
        <div style="padding: 8px; border-radius: 8px; border-left: 4px solid #007bff; font-size: 14px; min-width: 180px;">
        <div style="margin-bottom: 6px;">
          ${formattedDate}
        </div>
        <div style="font-weight: normal;">
          ${label}: ${yValue}
        </div>
      </div>`;
      },
    },
    grid: {
      borderColor: "var(--border-default)",
      strokeDashArray: 5,
    },
    legend: {
      show: true,
      position: "top",
      horizontalAlign: "left",
      fontSize: "14px",
      labels: {
        colors: "var(--text-primary)",
      },
      markers: {
        width: 12,
        height: 12,
        strokeWidth: 0, // Remove any border stroke
      },
    },
  };
  return (
    <Box
      sx={{
        border: "2px solid",
        borderColor: "divider",
        padding: 2,
        borderRadius: "15px",
        boxShadow: "0px",
      }}
    >
      <Typography fontWeight={500} color="text.disabled">
        {label}
      </Typography>
      <Typography fontWeight={500} sx={{ marginY: 1 }}>
        {subLabel}
      </Typography>
      <ChartsTheme options={chartOptions} series={series} type="area" />
    </Box>
  );
};

EvalsChartsGenerator.propTypes = {
  id: PropTypes.string.isRequired,
  series: PropTypes.array.isRequired,
  label: PropTypes.string.isRequired,
  onZoom: PropTypes.func,
  subLabel: PropTypes.string,
};

EvalsChartsGenerator.defaultProps = {
  onZoom: () => {},
};

export default EvalsChartsGenerator;
