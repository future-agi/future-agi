import PropTypes from "prop-types";
import { useState } from "react";
import ReactApexChart from "react-apexcharts";
import {
  Box,
  Button,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { formatMetricValue } from "./formatMetricValue";
import { formatMetricTime, formatMetricWindow } from "./metricTime";
import { pointValueState } from "./unitValueState";
import MetricValueCell from "./MetricValueCell";

import { metricTrendPoints } from "./metricPageValues";

// showWindow: the overview already states the effective window once in its KPI
// footer, so it hides the line here unless the KPI card is missing.
export default function MetricTrendChart({ data, name, showWindow = true }) {
  const theme = useTheme();
  const [table, setTable] = useState(false);
  // Keep the chosen view while reads are masked, without rendering stale data.
  if (!data) return null;
  const points = metricTrendPoints(data.results);
  const hasValues = points.some((point) => point.y !== null);
  const now = new Date();
  const today = Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate(),
  );
  const showToday =
    today >= Date.parse(data.effective_window.window_start) &&
    today <= Date.parse(data.effective_window.window_end);
  // Single-series emphasis, not categorical identity: foreground ink follows
  // the host theme. Missing buckets stay null; no smoothing or zero filling.
  const options = {
    chart: {
      type: "line",
      fontFamily: theme.typography.fontFamily,
      foreColor: theme.palette.text.secondary,
      animations: { enabled: false },
      toolbar: { show: false },
      zoom: { enabled: false },
      background: "transparent",
    },
    theme: { mode: theme.palette.mode },
    colors: [theme.palette.text.primary],
    stroke: { width: 2, curve: "straight" },
    markers: {
      size: 4,
      strokeWidth: 2,
      strokeColors: theme.palette.background.paper,
      hover: { sizeOffset: 2 },
      showNullDataPoints: false,
    },
    // Right padding keeps the last date label (at the window end) inside the card.
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 0,
      padding: { right: 28 },
    },
    dataLabels: { enabled: false },
    legend: { show: false },
    xaxis: {
      type: "datetime",
      min: Date.parse(data.effective_window.window_start),
      max: Date.parse(data.effective_window.window_end),
      labels: { datetimeUTC: true },
      axisBorder: { show: false },
      axisTicks: { show: false },
      crosshairs: {
        show: true,
        stroke: { width: 1, dashArray: 0, color: theme.palette.text.disabled },
      },
    },
    yaxis: {
      labels: {
        formatter: (value) =>
          formatMetricValue(value, {
            ...data.results[0],
            value_type: "number",
          }) || "",
      },
    },
    tooltip: {
      shared: true,
      intersect: false,
      // Apex accepts a DOM node. textContent keeps metric names/units out of HTML.
      custom: ({ dataPointIndex }) => {
        const point = data.results[dataPointIndex];
        const node = document.createElement("div");
        node.style.padding = theme.spacing(3);
        const value = document.createElement("strong");
        value.textContent =
          pointValueState(point).state === "value"
            ? `${point.weighted ? "≈ " : ""}${formatMetricValue(point.value, { ...point, value_type: "number" })}`
            : pointValueState(point).label;
        const label = document.createElement("div");
        label.textContent = `${name} · ${formatMetricTime(point?.window_start)}`;
        node.append(value, label);
        return node;
      },
    },
    annotations: {
      xaxis: showToday
        ? [
            {
              x: today,
              borderColor: theme.palette.text.disabled,
              label: {
                text: "Today (UTC)",
                orientation: "horizontal",
                textAnchor: "end",
                offsetX: -4,
                borderWidth: 0,
                style: {
                  color: theme.palette.text.secondary,
                  background: theme.palette.background.paper,
                  fontSize: "11px",
                },
              },
            },
          ]
        : [],
    },
    noData: { text: "No values in this window" },
  };
  return (
    <Stack
      spacing={1.5}
      sx={{ p: 2.5, border: 1, borderColor: "divider", borderRadius: 1 }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        justifyContent="space-between"
        spacing={2}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography component="h2" variant="subtitle1" fontWeight={600}>
            Trend
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {name} over time. Gaps mean no value, not zero. Times are UTC.
          </Typography>
        </Box>
        <Button
          color="inherit"
          variant="outlined"
          size="small"
          aria-pressed={table}
          startIcon={
            <Iconify
              icon={table ? "solar:graph-up-linear" : "solar:list-linear"}
              width={16}
            />
          }
          onClick={() => setTable(!table)}
          sx={{ flexShrink: 0 }}
        >
          {table ? "Show chart" : "View data table"}
        </Button>
      </Stack>
      {showWindow && (
        <Typography variant="caption" color="text.secondary">
          {formatMetricWindow(data.effective_window)} ·{" "}
          {data.effective_window.rounding}
        </Typography>
      )}
      {!hasValues && (
        <Typography role="status" color="text.secondary" sx={{ py: 4 }}>
          No values in this window
        </Typography>
      )}
      {hasValues && !table && (
        <Box
          role="img"
          aria-label={`${name} trend. Use View data table for values and missing-data reasons.`}
          sx={{
            minWidth: 0,
            mx: -1,
            // ApexCharts 3 hides marker groups until a line path completes. A
            // series of isolated values has no connecting paths, so with our
            // disabled animations those valid points otherwise stay invisible.
            "& .apexcharts-series-markers-wrap.apexcharts-element-hidden": {
              display: "initial",
            },
            "& .apexcharts-nullpoint": { display: "none" },
            "@media print, (forced-colors: active)": { display: "none" },
          }}
        >
          <ReactApexChart
            type="line"
            height={280}
            options={options}
            series={[{ name, data: points }]}
          />
        </Box>
      )}
      <TableContainer
        sx={{
          display: table ? "block" : "none",
          "@media print, (forced-colors: active)": { display: "block" },
        }}
      >
        <Table size="small" aria-label="Metric trend data">
          <TableHead>
            <TableRow>
              <TableCell>Bucket start (UTC)</TableCell>
              <TableCell>Bucket end (UTC)</TableCell>
              <TableCell>Value and coverage</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.results.map((point, index) => (
              <TableRow key={`${point.window_start}:${index}`}>
                <TableCell sx={{ whiteSpace: "nowrap" }}>
                  {formatMetricTime(point.window_start)}
                </TableCell>
                <TableCell sx={{ whiteSpace: "nowrap" }}>
                  {formatMetricTime(point.window_end)}
                </TableCell>
                <TableCell>
                  <MetricValueCell source="point" value={point} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Stack>
  );
}
MetricTrendChart.propTypes = {
  data: PropTypes.object,
  name: PropTypes.string,
  showWindow: PropTypes.bool,
};
