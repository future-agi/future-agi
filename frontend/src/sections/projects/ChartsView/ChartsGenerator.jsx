import { Box, Typography, useTheme } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { format } from "date-fns";
import { palette } from "src/theme/palette";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { throttle } from "src/utils/utils";

import ChartsTheme from "./ChartsTheme";
import { generateAllColors } from "./common";

const ChartsGenerator = ({
  id,
  series,
  label,
  unit,
  isEvaluationChart,
  onZoom,
  height,
  chartType = "line",
  headerComponent,
  groupName,
}) => {
  const theme = useTheme();
  const seriesTotal = React.useMemo(
    () =>
      (series || []).reduce(
        (seriesSum, item) =>
          seriesSum +
          (item?.data || []).reduce((pointSum, point) => {
            const value = Array.isArray(point) ? point[1] : point?.y;
            const numeric = Number(value);
            return pointSum + (Number.isFinite(numeric) ? numeric : 0);
          }, 0),
        0,
      ),
    [series],
  );
  const onMouseMove = throttle(() => {
    trackEvent(Events.hover, {
      [PropertyName.formFields]: { "Chart Name": label },
    });
  }, 30000);

  const chartOptions = {
    chart: {
      id: id,
      group: groupName,
      type: chartType,
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
    xaxis: {
      type: "datetime",
      // title: { text: "Time Period" },
    },
    yaxis: {
      forceNiceScale: true,
      // title: { text: yAxisLabel },
      min: isEvaluationChart ? 0 : undefined,
      max: isEvaluationChart ? 100 : undefined,
      labels: {
        formatter: function (value) {
          const isCost = unit === "$";
          const formattedValue =
            value >= 1000000
              ? (value / 1000000).toFixed(1) + "M"
              : value >= 1000
                ? (value / 1000).toFixed(1) + "K"
                : value;

          return isCost
            ? `$${formattedValue}`
            : `${formattedValue}${unit ? ` ${unit}` : ""}`;
        },
      },
    },
    stroke: {
      curve: chartType === "bar" ? "straight" : "smooth",
      width: 3,
      dropShadow: {
        enabled: true,
        top: 10,
        left: 0,
        blur: 8,
        opacity: 0.5,
        color: "text.primary",
      },
    },
    markers: {
      size: 0,
    },
    colors: generateAllColors(palette),
    tooltip: {
      custom: function ({ series, seriesIndex, dataPointIndex, w }) {
        const xValue = w.globals.seriesX[seriesIndex][dataPointIndex];
        const formattedDate = format(new Date(xValue), "dd/MM/yyyy, HH:mm:ss");
        const gradientColor =
          theme.palette.mode === "light"
            ? "linear-gradient(180deg, var(--primary-main) 0%, #CF6BE8 100%)"
            : "linear-gradient(180deg, #FFFFFF 0%, #E6E6E7 100%)";

        let tooltipContent = `
      <div style="display: flex; min-width: 200px; background: var(--bg-paper); border-radius: 8px; overflow: hidden; font-size: 14px;">
        <div style="width: 4px; background: ${gradientColor}; border-radius: 8px 0 0 8px;"></div>
        <div style="padding: 8px; color: var(--text-primary);">
          <div style="margin-bottom: 6px; color: var(--text-primary); font-weight: 500; font-size: 11px;">${formattedDate}</div>`;

        series.forEach((s, index) => {
          // Check if the series is visible (not hidden via legend click)
          const isSeriesVisible =
            w.globals.collapsedSeriesIndices.indexOf(index) === -1;

          if (isSeriesVisible) {
            const yValue = w.globals.series[index][dataPointIndex];
            const label = w.config.series[index]?.name || `Series ${index + 1}`;
            const formattedYValue =
              unit === "$" ? `${unit}${yValue}` : `${yValue} ${unit}`;

            tooltipContent += `
          <div style="font-weight: normal; color: var(--text-secondary); font-size: 11px;">${label}: ${formattedYValue}</div>`;
          }
        });

        tooltipContent += `
        </div>
      </div>`;

        return tooltipContent;
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
        strokeWidth: 0,
        radius: 12,
      },
    },
  };
  return (
    <Box
      data-chart-label={label}
      data-chart-total={seriesTotal}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        paddingTop: theme.spacing(1.5),
        borderRadius: theme.spacing(0.5),
        boxShadow: "0px",
      }}
    >
      {headerComponent ? (
        headerComponent
      ) : (
        <Typography
          variant="s1"
          color={theme.palette.text.primary}
          fontWeight={"fontWeightMedium"}
          sx={{ paddingLeft: theme.spacing(2) }}
        >
          {label}
        </Typography>
      )}
      <ChartsTheme
        options={chartOptions}
        series={series}
        type={chartType}
        {...(height ? { height } : {})}
      />
    </Box>
  );
};

ChartsGenerator.propTypes = {
  id: PropTypes.string.isRequired,
  series: PropTypes.array.isRequired,
  label: PropTypes.string.isRequired,
  unit: PropTypes.string,
  isEvaluationChart: PropTypes.bool,
  onZoom: PropTypes.func,
  height: PropTypes.string,
  headerComponent: PropTypes.node,
  chartType: PropTypes.string,
  groupName: PropTypes.string,
};

ChartsGenerator.defaultProps = {
  unit: "",
  isEvaluationChart: false,
  onZoom: () => {},
};

export default React.memo(ChartsGenerator);
