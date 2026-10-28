import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import ReactApexChart from "react-apexcharts";
import ChartLegend from "src/sections/dashboards/ChartLegend";
import WidgetPieCharts from "src/sections/dashboards/WidgetPieCharts";
import {
  getSeriesScalar,
  groupPieSeries,
} from "src/sections/dashboards/widgetUtils";
import {
  buildSeriesColorMap,
  getSeriesColor,
} from "src/sections/dashboards/widgetEditorConstants";
import { formatRunWidgetValue } from "./formatRunWidgetValue";

/**
 * Draws a run widget's query result with the same chart types, palette,
 * legend and pie renderer as an Observe dashboard widget. The x-axis is the
 * run's calls or an attribute, so points are categories, not timestamps.
 */

const APEX_TYPE = {
  line: "line",
  stacked_line: "area",
  column: "bar",
  stacked_column: "bar",
  bar: "bar",
  stacked_bar: "bar",
};

export const chartKindOf = (chartType) =>
  ["pie", "table", "metric"].includes(chartType) ? chartType : null;

/** Colour per series, from the dashboards palette. */
export function useSeriesColors(series) {
  const map = useMemo(
    () => buildSeriesColorMap(series.map((item) => item.name)),
    [series],
  );
  return (name) => getSeriesColor(name, map);
}

function Message({ children }) {
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      sx={{ height: "100%", minHeight: 120, px: 2 }}
    >
      <Typography
        typography="s1"
        sx={{ color: "text.secondary", textAlign: "center" }}
      >
        {children}
      </Typography>
    </Stack>
  );
}
Message.propTypes = { children: PropTypes.node };

function SeriesTableChart({ series, buckets, leftY, xLabel }) {
  const theme = useTheme();
  const cell = {
    padding: "8px 12px",
    borderBottom: `1px solid ${theme.palette.divider}`,
    fontVariantNumeric: "tabular-nums",
  };
  const head = {
    ...cell,
    color: theme.palette.text.secondary,
    fontWeight: 500,
    fontSize: 12,
    position: "sticky",
    top: 0,
    background: theme.palette.background.paper,
    whiteSpace: "nowrap",
  };
  // One bucket: a row per breakdown value (or metric), a column per
  // aggregation. Otherwise buckets down, series across.
  if (buckets.length === 1) {
    const rowKey = (item) => item.breakdownName ?? item.metricName;
    const colKey = (item) =>
      item.breakdownName != null
        ? `${item.metricName} (${item.aggregation})`
        : item.aggregation;
    const rows = [...new Set(series.map(rowKey))];
    const cols = [...new Set(series.map(colKey))];
    const at = (row, col) =>
      series.find((item) => rowKey(item) === row && colKey(item) === col);
    return (
      <Box sx={{ height: "100%", overflow: "auto" }}>
        <table
          style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
        >
          <thead>
            <tr>
              <th style={{ ...head, textAlign: "left" }}>{xLabel || ""}</th>
              {cols.map((col) => (
                <th key={col} style={{ ...head, textAlign: "right" }}>
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row}>
                <td style={{ ...cell, fontWeight: 500 }}>{row}</td>
                {cols.map((col) => {
                  const item = at(row, col);
                  const value = item?.data[0]?.y;
                  return (
                    <td key={col} style={{ ...cell, textAlign: "right" }}>
                      {value == null
                        ? "-"
                        : formatRunWidgetValue(value, leftY, item.unit)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </Box>
    );
  }
  return (
    <Box sx={{ height: "100%", overflow: "auto" }}>
      <table
        style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
      >
        <thead>
          <tr>
            <th style={{ ...head, textAlign: "left" }}>{xLabel || "Call"}</th>
            {series.map((item) => (
              <th key={item.key} style={{ ...head, textAlign: "right" }}>
                {item.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {buckets.map((bucket, index) => (
            <tr key={bucket.key}>
              <td style={{ ...cell, fontWeight: 500 }}>
                {bucket.label}
                {bucket.sub ? (
                  <span
                    style={{
                      color: theme.palette.text.disabled,
                      marginLeft: 6,
                      fontWeight: 400,
                    }}
                  >
                    {bucket.sub}
                  </span>
                ) : null}
              </td>
              {series.map((item) => (
                <td key={item.key} style={{ ...cell, textAlign: "right" }}>
                  {item.data[index]?.y == null
                    ? "-"
                    : formatRunWidgetValue(
                        item.data[index].y,
                        leftY,
                        item.unit,
                      )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </Box>
  );
}
SeriesTableChart.propTypes = {
  series: PropTypes.array.isRequired,
  buckets: PropTypes.array.isRequired,
  leftY: PropTypes.object,
  xLabel: PropTypes.string,
};

export default function RunWidgetChart({
  series,
  buckets,
  chartType,
  axisConfig,
  visible,
  height = "100%",
  compact = false,
}) {
  const theme = useTheme();
  const colorFor = useSeriesColors(series);
  const shown = useMemo(
    () => series.filter((item) => !visible || visible.has(item.key)),
    [series, visible],
  );
  const leftY = axisConfig?.leftY || {};
  const rightY = axisConfig?.rightY || {};
  const xAxis = axisConfig?.xAxis || { visible: true, label: "" };

  if (!series.length) return <Message>Add a metric to see data</Message>;
  if (
    !shown.some(
      (item) =>
        item.total != null || item.data.some((point) => point.y != null),
    )
  )
    return (
      <Message>
        No data in this run&apos;s calls. Try another metric, or check the
        filters.
      </Message>
    );

  if (chartType === "metric") {
    return (
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="center"
        flexWrap="wrap"
        gap={4}
        sx={{ height: "100%", p: 2 }}
      >
        {shown.map((item) => {
          const value =
            item.total ?? getSeriesScalar(item.data, item.aggregation);
          return (
            <Box key={item.key} sx={{ textAlign: "center", minWidth: 120 }}>
              <Typography
                sx={{
                  fontSize: compact ? 30 : 44,
                  fontWeight: 600,
                  lineHeight: 1.1,
                  color: colorFor(item.name),
                }}
              >
                {value == null
                  ? "-"
                  : formatRunWidgetValue(value, leftY, item.unit)}
              </Typography>
              <Typography
                typography="s2"
                sx={{ color: "text.secondary", display: "block", mt: 0.5 }}
              >
                {item.name}
              </Typography>
            </Box>
          );
        })}
      </Stack>
    );
  }

  if (chartType === "pie") {
    return (
      <Box sx={{ height: "100%", minHeight: 200 }}>
        <WidgetPieCharts
          // One slice per breakdown value, sized by its total over the run.
          groups={groupPieSeries(
            shown.map((item) => ({
              ...item,
              data: [
                {
                  x: "all",
                  y: item.total ?? getSeriesScalar(item.data, item.aggregation),
                },
              ],
            })),
          )}
          colorFor={colorFor}
          baseFormatConfig={leftY}
          fallbackDecimals={1}
        />
      </Box>
    );
  }

  if (chartType === "table") {
    return (
      <SeriesTableChart
        series={shown}
        buckets={buckets}
        leftY={leftY}
        xLabel={xAxis.label}
      />
    );
  }

  const horizontal = chartType === "bar" || chartType === "stacked_bar";
  const rankBars = chartType === "bar";
  const rightIdx = new Set(
    Object.entries(axisConfig?.seriesAxis || {})
      .filter(([, side]) => side === "right")
      .map(([index]) => Number(index)),
  );
  const yFmt = (value) => formatRunWidgetValue(value, leftY, shown[0]?.unit);

  // One bucket, one metric split by a breakdown: a column per value reads
  // better than one cluster. Numeric values (CSAT 0-10) stay in order,
  // anything else is ranked largest first.
  const oneBucketColumns =
    chartType === "column" &&
    buckets.length === 1 &&
    shown.length > 1 &&
    shown.every(
      (item) =>
        item.breakdownName != null && item.metricIndex === shown[0].metricIndex,
    );

  let apexSeries;
  let apexCategories = buckets.map((bucket) => bucket.label);
  let colors;
  if (oneBucketColumns) {
    const numeric = shown.every(
      (item) =>
        item.breakdownName !== "" && !Number.isNaN(Number(item.breakdownName)),
    );
    const ordered = shown
      .map((item) => ({ item, value: item.data[0]?.y }))
      .filter((entry) => entry.value != null)
      .sort((a, b) =>
        numeric
          ? Number(a.item.breakdownName) - Number(b.item.breakdownName)
          : b.value - a.value,
      );
    apexCategories = ordered.map((entry) => entry.item.breakdownName);
    apexSeries = [
      {
        name: shown[0]?.metricName || "Value",
        data: ordered.map((entry) => entry.value),
      },
    ];
    colors = ordered.map((entry) => colorFor(entry.item.name));
  } else if (rankBars) {
    const ranked = shown
      .map((item) => ({
        item,
        value: item.total ?? getSeriesScalar(item.data, item.aggregation),
      }))
      .filter((entry) => entry.value != null)
      .sort((a, b) => b.value - a.value);
    apexCategories = ranked.map(
      (entry) => entry.item.breakdownName ?? entry.item.name,
    );
    apexSeries = [
      {
        name: shown[0]?.metricName || "Value",
        data: ranked.map((entry) => Math.round(entry.value * 100) / 100),
      },
    ];
    colors = ranked.map((entry) => colorFor(entry.item.name));
  } else {
    apexSeries = shown.map((item) => ({
      name: item.name,
      data: item.data.map((point) => point.y),
    }));
    colors = shown.map((item) => colorFor(item.name));
  }

  const stacked = ["stacked_line", "stacked_column", "stacked_bar"].includes(
    chartType,
  );
  const axisTitleStyle = {
    color: theme.palette.text.secondary,
    fontSize: "12px",
    fontWeight: 500,
  };
  const labelStyle = { colors: theme.palette.text.secondary, fontSize: "12px" };
  const bound = (cfg, key) =>
    cfg[key] !== "" && cfg[key] != null ? { [key]: Number(cfg[key]) } : {};
  const yAxes =
    rightIdx.size && !rankBars
      ? apexSeries.map((item, index) => {
          const onRight = rightIdx.has(index);
          const cfg = onRight ? rightY : leftY;
          const firstOfSide =
            apexSeries.findIndex((_, j) => rightIdx.has(j) === onRight) ===
            index;
          return {
            seriesName: item.name,
            opposite: onRight,
            show: firstOfSide && cfg.visible !== false,
            title: { text: cfg.label || undefined, style: axisTitleStyle },
            labels: {
              style: labelStyle,
              formatter: (value) =>
                formatRunWidgetValue(value, cfg, shown[index]?.unit),
            },
            ...bound(cfg, "min"),
            ...bound(cfg, "max"),
            logarithmic: cfg.scale === "logarithmic",
          };
        })
      : {
          show: leftY.visible !== false,
          title: { text: leftY.label || undefined, style: axisTitleStyle },
          labels: {
            style: labelStyle,
            formatter: horizontal ? undefined : yFmt,
            maxWidth: horizontal ? 340 : 220,
          },
          ...bound(leftY, "min"),
          ...bound(leftY, "max"),
          logarithmic: leftY.scale === "logarithmic",
          forceNiceScale: true,
        };

  const options = {
    chart: {
      toolbar: { show: false },
      zoom: { enabled: false },
      animations: { enabled: false },
      stacked,
      background: "transparent",
      fontFamily: theme.typography.fontFamily,
    },
    theme: { mode: theme.palette.mode },
    colors,
    stroke: {
      curve: "monotoneCubic",
      width: chartType === "line" ? 2 : chartType === "stacked_line" ? 1.5 : 0,
    },
    fill:
      chartType === "stacked_line"
        ? { type: "solid", opacity: 0.25 }
        : { opacity: 1 },
    markers: {
      size: chartType === "line" ? 4 : 0,
      strokeWidth: 0,
      hover: { size: 6 },
    },
    dataLabels: { enabled: false },
    legend: { show: false },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 3,
      padding: { left: 8, right: 12 },
      xaxis: { lines: { show: horizontal } },
      yaxis: { lines: { show: !horizontal } },
    },
    plotOptions: {
      bar: {
        horizontal,
        borderRadius: 3,
        borderRadiusApplication: "end",
        columnWidth: "55%",
        barHeight: rankBars ? "60%" : "70%",
        distributed: !!(rankBars || oneBucketColumns),
      },
    },
    xaxis: {
      categories: apexCategories,
      // Long category axes (every call, p0-p100) show every Nth label.
      ...(!horizontal && apexCategories.length > 20 && { tickAmount: 10 }),
      labels: {
        show: xAxis.visible !== false,
        style: labelStyle,
        rotate: 0,
        hideOverlappingLabels: true,
        trim: false,
        ...(horizontal && {
          formatter: (value) =>
            typeof value === "number" ? yFmt(value) : value,
        }),
      },
      title: { text: xAxis.label || undefined, style: axisTitleStyle },
      axisBorder: { show: false },
      axisTicks: { show: false },
      tooltip: { enabled: false },
    },
    yaxis: yAxes,
    tooltip: {
      theme: theme.palette.mode,
      // A call's tooltip names its scenario.
      x: {
        formatter: (value, opts) => {
          const bucket = buckets[opts?.dataPointIndex];
          if (!bucket || rankBars || oneBucketColumns) return value;
          return bucket.sub ? `${bucket.label} · ${bucket.sub}` : bucket.label;
        },
      },
      shared: !rankBars && !horizontal && !oneBucketColumns,
      intersect: false,
      y: {
        formatter: (value, { seriesIndex } = {}) =>
          value == null
            ? "-"
            : formatRunWidgetValue(
                value,
                rightIdx.has(seriesIndex) ? rightY : leftY,
                (rankBars ? shown[0] : shown[seriesIndex])?.unit,
              ),
      },
    },
  };

  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", width: "100%", height }}
    >
      {!rankBars && !oneBucketColumns && shown.length > 1 && (
        <ChartLegend items={shown.map((item) => item.name)} colors={colors} />
      )}
      <Box sx={{ flex: 1, minHeight: 0 }}>
        <ReactApexChart
          key={`${chartType}-${apexSeries.length}-${JSON.stringify(axisConfig || {})}`}
          options={options}
          series={apexSeries}
          type={APEX_TYPE[chartType] || "line"}
          height="100%"
        />
      </Box>
    </Box>
  );
}

RunWidgetChart.propTypes = {
  series: PropTypes.array.isRequired,
  buckets: PropTypes.array.isRequired,
  chartType: PropTypes.string.isRequired,
  axisConfig: PropTypes.object,
  visible: PropTypes.instanceOf(Set),
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  compact: PropTypes.bool,
};
