import PropTypes from "prop-types";
import { alpha, useTheme } from "@mui/material/styles";
import { Box } from "@mui/material";
import ReactApexChart from "react-apexcharts";

const DIMMED = 0.2;
const DASH_PX = 8;
const COLUMN_PX = 84;
const MAX_TIGHT_COLUMNS = 7;
const UNSCORED_CLASS = "summary-run-unscored";
const scoreLabel = (v) => (v == null ? "-" : `${v}%`);

// Lines with identical scores across every run sit exactly on top of each
// other, so only the last-drawn colour shows. Interleave them instead: in a
// group of k, line j draws every k-th dash, offset by j, so each colour
// takes its turn along the shared path. Returns an SVG dash pattern per
// series index (null for a line nobody overlaps).
function overlapDashes(series) {
  const groups = new Map();
  series.forEach((s, i) => {
    if (!s.data.some((v) => v != null)) return;
    const key = JSON.stringify(s.data);
    groups.set(key, [...(groups.get(key) || []), i]);
  });
  const dashes = series.map(() => null);
  groups.forEach((members) => {
    if (members.length < 2) return;
    const k = members.length;
    members.forEach((index, j) => {
      dashes[index] = `0 ${j * DASH_PX} ${DASH_PX} ${(k - 1 - j) * DASH_PX}`;
    });
  });
  return dashes;
}

// The eval-score trend: one line per applied eval, its score across the runs in
// chronological order (a broken line where a run never scored that eval). Ported
// from the designer's RunsSummary graph — a flat 0–100 line chart with discrete
// per-run markers, no legend (the legend lives above the chart) and no toolbar.
// A single run has no trend, and its scores would pile up on one x point, so it
// gets one tight group of columns, one per eval, instead. A highlighted eval
// (legend hover) keeps its colour while the rest fade.
function applyDashes(el, dashes) {
  el.querySelectorAll(".apexcharts-series[data\\:realIndex]").forEach((g) => {
    const path = g.querySelector("path.apexcharts-line");
    if (!path) return;
    const dash = dashes[Number(g.getAttribute("data:realIndex"))];
    if (dash) path.setAttribute("stroke-dasharray", dash);
    else path.removeAttribute("stroke-dasharray");
  });
}

export default function SummaryGraph({
  categories,
  series,
  highlightedId = null,
}) {
  const theme = useTheme();
  const single = categories.length === 1;
  // A hidden eval has no line, so hovering it must not fade the ones shown.
  const highlight = series.some((s) => s.id === highlightedId)
    ? highlightedId
    : null;
  const colorOf = (s) =>
    highlight && s.id !== highlight ? alpha(s.color, DIMMED) : s.color;
  const colors = series.map(colorOf);
  const dashes = overlapDashes(series);
  const axisLabelStyle = {
    colors: theme.palette.text.secondary,
    fontSize: "11px",
  };

  const base = {
    chart: {
      toolbar: { show: false },
      zoom: { enabled: false },
      animations: { enabled: false },
      fontFamily: theme.typography.fontFamily,
      background: "transparent",
      parentHeightOffset: 0,
      sparkline: { enabled: false },
    },
    theme: { mode: theme.palette.mode },
    colors,
    legend: { show: false },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 4,
      xaxis: { lines: { show: false } },
      padding: { left: 0, right: 0, top: 0, bottom: 0 },
    },
    yaxis: {
      min: 0,
      max: 100,
      tickAmount: 4,
      labels: { style: axisLabelStyle, formatter: (v) => `${Math.round(v)}` },
    },
  };

  const chart = single
    ? {
        type: "bar",
        series: series.map((s) => ({
          name: s.name,
          data: [s.data[0] ?? null],
        })),
        options: {
          ...base,
          // Room above a full column for its "100%" label.
          grid: { ...base.grid, padding: { ...base.grid.padding, top: 16 } },
          plotOptions: {
            bar: {
              // A fixed width per column (Apex applies px per column, % to the
              // whole group) keeps the group tight in the middle; too many
              // evals to fit fall back to filling the width.
              columnWidth:
                series.length <= MAX_TIGHT_COLUMNS ? `${COLUMN_PX}px` : "92%",
              borderRadius: 3,
              dataLabels: { position: "top" },
            },
          },
          // The transparent stroke is the gap between neighbouring columns.
          stroke: { show: true, width: 4, colors: ["transparent"] },
          dataLabels: {
            enabled: true,
            formatter: scoreLabel,
            offsetY: -18,
            style: { fontSize: "11px", colors: [theme.palette.text.primary] },
          },
          xaxis: {
            categories,
            axisBorder: { show: false },
            axisTicks: { show: false },
            labels: { style: axisLabelStyle },
            tooltip: { enabled: false },
          },
          tooltip: {
            shared: true,
            intersect: false,
            y: { formatter: scoreLabel },
          },
        },
      }
    : {
        type: "line",
        series: series.map((s) => ({ name: s.name, data: s.data })),
        options: {
          ...base,
          // Keep the first run's markers off the y-axis labels.
          grid: { ...base.grid, padding: { ...base.grid.padding, left: 14 } },
          chart: {
            ...base.chart,
            // Apex has no dash offset, so set the interleaved patterns on its
            // drawn paths. Off while an eval is highlighted: that line is solid.
            events: {
              mounted: (ctx) => applyDashes(ctx.el, highlight ? [] : dashes),
              updated: (ctx) => applyDashes(ctx.el, highlight ? [] : dashes),
              // A run with no scores gives Apex no point to move the tooltip
              // and its guide to, so they stay on the previous run under the
              // new run's title. Hide them there instead.
              // Apex's own dataPointIndex isn't the run under the pointer when
              // runs have gaps, so work it out from the pointer's x.
              mouseMove: (event, ctx) => {
                const { gridWidth, translateX } = ctx.w.globals;
                const svg = ctx.el.querySelector("svg");
                if (!svg || categories.length < 2) return;
                // Apex routes touch drags through here too; a TouchEvent has no clientX.
                const clientX = event.touches?.[0]?.clientX ?? event.clientX;
                if (!Number.isFinite(clientX)) return;
                const x =
                  clientX - svg.getBoundingClientRect().left - translateX;
                const step = gridWidth / (categories.length - 1);
                const run = Math.min(
                  categories.length - 1,
                  Math.max(0, Math.round(x / step)),
                );
                const unscored = series.every((s) => s.data[run] == null);
                ctx.el.classList.toggle(UNSCORED_CLASS, unscored);
              },
            },
          },
          stroke: { width: 2, curve: "straight" },
          markers: {
            size: 0,
            strokeWidth: 0,
            hover: { size: 5 },
            discrete: series.flatMap((s, si) =>
              categories.map((_, di) => ({
                seriesIndex: si,
                dataPointIndex: di,
                fillColor: colors[si],
                strokeColor: colors[si],
                size: 4,
                shape: "circle",
              })),
            ),
          },
          dataLabels: { enabled: false },
          xaxis: {
            categories,
            axisBorder: { show: false },
            axisTicks: { show: false },
            labels: {
              style: axisLabelStyle,
              rotate: 0,
              hideOverlappingLabels: true,
              trim: false,
            },
            tooltip: { enabled: false },
          },
          tooltip: {
            shared: true,
            intersect: false,
            y: { formatter: scoreLabel },
          },
        },
      };

  return (
    <Box
      sx={{
        px: 0,
        pt: 0.5,
        pb: 0.5,
        width: "100%",
        flexShrink: 0,
        // The shared tooltip lists every eval, so it can outgrow the chart.
        // Apex centres it on the pointer, which pushes its run-name header up
        // past the card and the tab's scroll edge; pin it to the chart's top so
        // it only grows down. Apex still places it left or right of the run.
        "& .apexcharts-tooltip": { top: "0 !important" },
        [`& .${UNSCORED_CLASS} .apexcharts-tooltip, & .${UNSCORED_CLASS} .apexcharts-xcrosshairs`]:
          { opacity: "0 !important" },
      }}
    >
      <ReactApexChart
        key={chart.type}
        type={chart.type}
        height={single ? 200 : 160}
        width="100%"
        series={chart.series}
        options={chart.options}
      />
    </Box>
  );
}

SummaryGraph.propTypes = {
  categories: PropTypes.arrayOf(PropTypes.string).isRequired,
  series: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      color: PropTypes.string,
      data: PropTypes.arrayOf(PropTypes.number),
    }),
  ).isRequired,
  highlightedId: PropTypes.string,
};
