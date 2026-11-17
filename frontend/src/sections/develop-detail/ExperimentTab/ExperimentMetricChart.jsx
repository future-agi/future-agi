import React, { useMemo, useRef } from "react";
import PropTypes from "prop-types";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material";

const escapeHtml = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );

/**
 * One small chart of the metric strip (react-apexcharts, the app's chart
 * library). x = runs (`#1A`…), oldest to newest; a dashed vertical line
 * marks the baseline run. Points are plain; low sample, running and partial
 * states are in the tooltip. Click a point -> onPointClick(index, event).
 */
const ExperimentMetricChart = ({
  type,
  categories,
  series,
  height = 128,
  baselineIndex,
  yMin = 0,
  yMax,
  yFormatter,
  tooltipRows,
  onPointClick,
  compactLabels = false,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const lastClick = useRef(0);

  const options = useMemo(() => {
    const fire = (index, event) => {
      const now = Date.now();
      if (index == null || index < 0 || now - lastClick.current < 250) return;
      lastClick.current = now;
      onPointClick?.(index, event);
    };
    const muted = theme.palette.text.disabled;
    return {
      chart: {
        type,
        background: "transparent",
        foreColor: theme.palette.text.secondary,
        toolbar: { show: false },
        zoom: { enabled: false },
        animations: { enabled: false },
        parentHeightOffset: 0,
        sparkline: { enabled: false },
        fontFamily: theme.typography.fontFamily,
        events: {
          dataPointSelection: (event, ctx, cfg) =>
            fire(cfg?.dataPointIndex, event),
          markerClick: (event, ctx, cfg) => fire(cfg?.dataPointIndex, event),
          xAxisLabelClick: (event, ctx, cfg) => fire(cfg?.labelIndex, event),
        },
      },
      theme: { mode: isDark ? "dark" : "light" },
      colors: series.map((s) => s.color),
      // EL#44: series differ by marker shape and dash, not colour only.
      stroke:
        type === "line"
          ? {
              width: 2,
              curve: "straight",
              dashArray: series.map((_, i) => [0, 5, 2][i % 3]),
            }
          : { width: 0 },
      markers:
        type === "line"
          ? {
              size: 4,
              shape: series.map((_, i) => ["circle", "square", "rect"][i % 3]),
              strokeWidth: 2,
              strokeColors: theme.palette.background.paper,
              hover: { size: 6 },
            }
          : { size: 0 },
      plotOptions: {
        bar: {
          columnWidth: series.length > 1 ? "70%" : "55%",
          borderRadius: 2,
        },
      },
      dataLabels: { enabled: false },
      legend: { show: false },
      grid: {
        borderColor: theme.palette.divider,
        strokeDashArray: 3,
        padding: { left: 4, right: 8, top: -8, bottom: -4 },
        yaxis: { lines: { show: true } },
      },
      xaxis: {
        categories,
        labels: {
          rotate: 0,
          hideOverlappingLabels: true,
          style: { fontSize: "10px", colors: muted },
          // Every other id when the card is narrow (the tooltip names each).
          formatter: (v, ts, opts) => {
            const i = opts?.i ?? categories.indexOf(v);
            return compactLabels && categories.length > 6 && i % 2 === 1
              ? ""
              : v;
          },
        },
        axisBorder: { show: false },
        axisTicks: { show: false },
        tooltip: { enabled: false },
      },
      yaxis: {
        min: yMin,
        max: yMax,
        tickAmount: 2,
        forceNiceScale: yMax == null,
        labels: {
          style: { fontSize: "10px", colors: muted },
          formatter: (v) => (v == null ? "" : yFormatter(v)),
        },
      },
      annotations:
        baselineIndex != null && baselineIndex >= 0
          ? {
              xaxis: [
                {
                  x: categories[baselineIndex],
                  borderColor: theme.palette.text.secondary,
                  strokeDashArray: 4,
                  label: {
                    text: "Baseline",
                    orientation: "horizontal",
                    position: "top",
                    textAnchor: "start",
                    offsetX: 2,
                    offsetY: -2,
                    borderWidth: 0,
                    style: {
                      background: "transparent",
                      color: theme.palette.text.secondary,
                      fontSize: "10px",
                    },
                  },
                },
              ],
            }
          : {},
      tooltip: {
        shared: type === "line",
        intersect: false,
        theme: isDark ? "dark" : "light",
        custom: ({ dataPointIndex }) => {
          const rows = tooltipRows(dataPointIndex) ?? [];
          return `<div style="padding:8px 10px;font-size:12px;line-height:18px;max-width:260px">${rows
            .map(
              (r, i) =>
                `<div style="${i === 0 ? "font-weight:600;" : ""}${r.muted ? "opacity:.7;" : ""}display:flex;gap:6px;align-items:center">${
                  r.color
                    ? `<span style="width:8px;height:8px;border-radius:2px;background:${r.color};flex-shrink:0"></span>`
                    : ""
                }<span>${escapeHtml(r.text)}</span></div>`,
            )
            .join("")}</div>`;
        },
      },
      states: { active: { filter: { type: "none" } } },
    };
  }, [
    type,
    categories,
    series,
    baselineIndex,
    yMin,
    yMax,
    yFormatter,
    tooltipRows,
    onPointClick,
    compactLabels,
    theme,
    isDark,
  ]);

  const apexSeries = useMemo(
    () => series.map((s) => ({ name: s.name, data: s.data })),
    [series],
  );

  return (
    <ReactApexChart
      type={type}
      options={options}
      series={apexSeries}
      height={height}
      width="100%"
    />
  );
};

ExperimentMetricChart.propTypes = {
  type: PropTypes.oneOf(["line", "bar"]).isRequired,
  categories: PropTypes.arrayOf(PropTypes.string).isRequired,
  series: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      data: PropTypes.array,
      color: PropTypes.string,
    }),
  ).isRequired,
  height: PropTypes.number,
  baselineIndex: PropTypes.number,
  yMin: PropTypes.number,
  yMax: PropTypes.number,
  yFormatter: PropTypes.func.isRequired,
  tooltipRows: PropTypes.func.isRequired,
  onPointClick: PropTypes.func,
  compactLabels: PropTypes.bool,
};

export default ExperimentMetricChart;
