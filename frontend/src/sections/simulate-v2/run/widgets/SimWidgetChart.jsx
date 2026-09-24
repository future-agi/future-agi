import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import ReactApexChart from "../../components/SafeApexChart";
import ChartLegend from "src/sections/dashboards/ChartLegend";
import WidgetPieCharts from "src/sections/dashboards/WidgetPieCharts";
import {
  formatValueWithConfig,
  getSeriesScalar,
  getUnitRendering,
  groupPieSeries,
} from "src/sections/dashboards/widgetUtils";
import { buildSeriesColorMap, getSeriesColor } from "src/sections/dashboards/widgetEditorParts";
import { COST_STAGES } from "../costSplit";

/**
 * Draws a run-analytics widget's query result with the same chart types,
 * palette, legend and pie renderer as an Observe dashboard widget. The
 * x-axis is runs, so points are categories ("Run 3") rather than timestamps.
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
  (chartType === "pie" ? "pie" : chartType === "table" ? "table" : chartType === "metric" ? "metric" : null);

/* Series that stand for a fixed thing keep that thing's colour everywhere —
   the pipeline-stage costs use the same colours as the built-in chart. */
const FIXED_COLORS = Object.fromEntries(COST_STAGES.map((st) => [`cost_${st.key}`, st.color]));

/**
 * Colour per series. A widget can carry its own colours in `display` — a
 * built-in opens with the exact colours it has on the Analytics tab:
 *   display.colorMap     { [breakdown value | metric name | series name]: colour }
 *   display.seriesColor  one colour for a single-series chart
 */
export function useSeriesColors(series, display) {
  const map = useMemo(() => {
    const base = buildSeriesColorMap(series.map((s) => s.name));
    series.forEach((s) => {
      const metricId = String(s.key || "").split("|")[0];
      if (FIXED_COLORS[metricId] && !s.breakdownName) base[s.name] = FIXED_COLORS[metricId];
      const fixed = display?.colorMap?.[s.breakdownName] || display?.colorMap?.[s.metricName] || display?.colorMap?.[s.name];
      if (fixed) base[s.name] = fixed;
    });
    if (display?.seriesColor && series.length === 1) base[series[0].name] = display.seriesColor;
    /* Pie slices are coloured by their breakdown value. */
    Object.entries(display?.colorMap || {}).forEach(([k, v]) => { if (!base[k]) base[k] = v; });
    return base;
  }, [series, display]);
  return (name) => getSeriesColor(name, map);
}

/* Colour for one x-axis category of a single-series chart: a threshold rule
   (CSAT ≤ 4 red) or a palette by position (a colour per tool). */
const categoryColor = (display, label, index, fallback) => {
  const rule = display?.categoryRule;
  if (rule) {
    const m = String(label).match(/^(-?\d+(?:\.\d+)?)(ms|s)?$/);
    const v = m ? (m[2] === "s" ? Number(m[1]) * 1000 : Number(m[1])) : null;
    if (v != null) {
      const hit = rule.op === "lte" ? v <= rule.value : v >= rule.value;
      return hit ? rule.match : rule.other;
    }
  }
  if (display?.categoryPalette?.length) return display.categoryPalette[index % display.categoryPalette.length];
  return fallback;
};

/* Axis number format. `showSeriesUnit: false` keeps the axis unit-less
   (plain "20000"), like the built-in charts that print raw numbers. */
const fmtFor = (axis, unit) => {
  if (axis?.showSeriesUnit === false) return axis;
  return unit && !axis?.unit ? { ...axis, ...getUnitRendering(unit) } : axis;
};

export default function SimWidgetChart({
  series,
  buckets,
  chartType,
  axisConfig,
  visible,
  height = "100%",
  compact = false,
  display,
}) {
  const theme = useTheme();
  const colorFor = useSeriesColors(series, display);
  const shown = useMemo(
    () => series.filter((s) => !visible || visible.has(s.key)),
    [series, visible],
  );
  const leftY = axisConfig?.leftY || {};
  const rightY = axisConfig?.rightY || {};
  const xAxis = axisConfig?.xAxis || { visible: true, label: "" };
  const categories = buckets.map((b) => b.label);

  if (!series.length) {
    return (
      <Stack alignItems="center" justifyContent="center" sx={{ height: "100%", minHeight: 120 }}>
        <Typography variant="body2" color="text.secondary">
          Add a metric to see data
        </Typography>
      </Stack>
    );
  }

  /* Runs-based wording rather than the dashboards' "time period" copy. */
  if (!shown.some((x) => x.total != null || x.data.some((p) => p.y != null))) {
    return (
      <Stack alignItems="center" justifyContent="center" sx={{ height: "100%", minHeight: 120, px: 2 }}>
        <Typography variant="body2" color="text.secondary" textAlign="center">
          No data for the runs in range. Try a wider range, or check the filters.
        </Typography>
      </Stack>
    );
  }

  /* ── metric card ── */
  if (chartType === "metric") {
    return (
      <Stack direction="row" alignItems="center" justifyContent="center" flexWrap="wrap" gap={4} sx={{ height: "100%", p: 2 }}>
        {shown.map((s) => {
          const v = s.total ?? getSeriesScalar(s.data, s.aggregation);
          return (
            <Box key={s.key} sx={{ textAlign: "center", minWidth: 120 }}>
              <Typography sx={{ fontSize: compact ? 30 : 44, fontWeight: 700, lineHeight: 1.1, color: colorFor(s.name) }}>
                {v == null ? "—" : formatValueWithConfig(v, fmtFor(leftY, s.unit))}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                {s.name}
              </Typography>
            </Box>
          );
        })}
      </Stack>
    );
  }

  /* ── pie: one donut per metric, sliced by the breakdown ── */
  if (chartType === "pie") {
    return (
      <Box sx={{ height: "100%", minHeight: 200 }}>
        <WidgetPieCharts
          /* One slice per breakdown value, sized by its total over the
             whole range (aggregated over all rows, not per-run values). */
          groups={groupPieSeries(shown.map((s) => ({ ...s, data: [{ x: "all", y: s.total ?? getSeriesScalar(s.data, s.aggregation) }] })))}
          colorFor={colorFor}
          baseFormatConfig={leftY}
          fallbackDecimals={1}
        />
      </Box>
    );
  }

  /* ── table: runs down, series across ── */
  if (chartType === "table" && buckets.length === 1) {
    /* One run: a row per breakdown value (or per metric), a column per
       aggregation — e.g. a row per voice segment with p50 / p90 / p99. */
    const cellS = { padding: "8px 12px", borderBottom: `1px solid ${theme.palette.divider}`, fontVariantNumeric: "tabular-nums" };
    const rowKey = (x) => x.breakdownName ?? x.metricName;
    const colKey = (x) => (x.breakdownName != null ? `${x.metricName} (${x.aggregation})` : x.aggregation);
    const rows = [...new Set(shown.map(rowKey))];
    const cols = [...new Set(shown.map(colKey))];
    const at = (r, c) => shown.find((x) => rowKey(x) === r && colKey(x) === c);
    const head = { ...cellS, color: theme.palette.text.secondary, fontWeight: 500, fontSize: 12, position: "sticky", top: 0, background: theme.palette.background.paper };
    return (
      <Box sx={{ height: "100%", overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
          <thead>
            <tr>
              <th style={{ ...head, textAlign: "left" }}>{xAxis.label || ""}</th>
              {cols.map((c) => <th key={c} style={{ ...head, textAlign: "right", whiteSpace: "nowrap" }}>{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r}>
                <td style={{ ...cellS, color: theme.palette.text.primary, fontWeight: 500 }}>{r}</td>
                {cols.map((c) => {
                  const x = at(r, c);
                  const v = x?.data[0]?.y;
                  return (
                    <td key={c} style={{ ...cellS, textAlign: "right", color: theme.palette.text.primary }}>
                      {v == null ? "—" : formatValueWithConfig(v, {
                        ...fmtFor(leftY, display?.units?.[x.metricName] ?? x.unit),
                        ...(display?.decimals?.[x.metricName] != null && { decimals: display.decimals[x.metricName] }),
                      })}
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
  if (chartType === "table") {
    const cell = { padding: "8px 12px", borderBottom: `1px solid ${theme.palette.divider}`, fontVariantNumeric: "tabular-nums" };
    return (
      <Box sx={{ height: "100%", overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
          <thead>
            <tr>
              <th style={{ ...cell, textAlign: "left", color: theme.palette.text.secondary, fontWeight: 500, fontSize: 12, position: "sticky", top: 0, background: theme.palette.background.paper }}>
                {xAxis.label || "Run"}
              </th>
              {shown.map((s) => (
                <th key={s.key} style={{ ...cell, textAlign: "right", color: theme.palette.text.secondary, fontWeight: 500, fontSize: 12, whiteSpace: "nowrap", position: "sticky", top: 0, background: theme.palette.background.paper }}>
                  {s.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {buckets.map((b, bi) => (
              <tr key={b.key}>
                <td style={{ ...cell, color: theme.palette.text.primary, fontWeight: 500 }}>
                  {b.label}
                  <span style={{ color: theme.palette.text.disabled, marginLeft: 6, fontWeight: 400 }}>{b.sub}</span>
                </td>
                {shown.map((s) => (
                  <td key={s.key} style={{ ...cell, textAlign: "right", color: theme.palette.text.primary }}>
                    {s.data[bi]?.y == null ? "-" : formatValueWithConfig(s.data[bi].y, fmtFor(leftY, s.unit))}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Box>
    );
  }

  /* ── horizontal bar: one bar per series (ranked), or stacked per run ── */
  const horizontal = chartType === "bar" || chartType === "stacked_bar";
  const rankBars = chartType === "bar";
  const rightIdx = new Set(
    Object.entries(axisConfig?.seriesAxis || {}).filter(([, v]) => v === "right").map(([k]) => Number(k)),
  );
  const unitOf = shown[0]?.unit;
  const yFmt = (v) => formatValueWithConfig(v, fmtFor(leftY, unitOf));

  /* One run, one metric split by a breakdown: a column per breakdown value
     reads better than one cluster — numeric values (CSAT 0–10) in order,
     anything else ranked largest first, like the built-in column charts. */
  const oneBucketColumns = chartType === "column" && buckets.length === 1 && shown.length > 1
    && shown.every((x) => x.breakdownName != null && x.metricIndex === shown[0].metricIndex);

  /* display.legend: "breakdown" / "metric" shows the short name a built-in
     chart uses ("Passed", "Tool failed") instead of the full series name. */
  const legendName = (x) => (display?.legend === "breakdown" ? (x.breakdownName ?? x.name)
    : display?.legend === "metric" ? x.metricName : x.name);

  let apexSeries;
  let apexCategories = categories;
  let colors;
  if (oneBucketColumns) {
    const numeric = shown.every((x) => x.breakdownName !== "" && !Number.isNaN(Number(x.breakdownName)));
    const ordered = [...shown]
      .map((x) => ({ s: x, v: x.data[0]?.y }))
      .filter((r) => r.v != null)
      .sort((a, b) => (numeric ? Number(a.s.breakdownName) - Number(b.s.breakdownName) : b.v - a.v));
    apexCategories = ordered.map((r) => r.s.breakdownName);
    apexSeries = [{ name: shown[0]?.metricName || "Value", data: ordered.map((r) => r.v) }];
    colors = ordered.map((r) => colorFor(r.s.name));
  } else if (rankBars) {
    const ranked = [...shown]
      .map((s) => ({ s, v: s.total ?? getSeriesScalar(s.data, s.aggregation) }))
      .filter((r) => r.v != null)
      .sort((a, b) => b.v - a.v);
    apexCategories = ranked.map((r) => (r.s.breakdownName ?? r.s.name));
    apexSeries = [{ name: shown[0]?.metricName || "Value", data: ranked.map((r) => Math.round(r.v * 100) / 100) }];
    colors = ranked.map((r) => colorFor(r.s.name));
  } else {
    apexSeries = shown.map((s) => ({ name: legendName(s), data: s.data.map((p) => p.y) }));
    colors = shown.map((s) => colorFor(s.name));
  }
  /* One series over categories (a histogram, a bar per tool): colour each
     category by the widget's rule / palette, like the built-in charts. */
  const perCategory = !rankBars && !oneBucketColumns && apexSeries.length === 1
    && (display?.categoryRule || display?.categoryPalette?.length);
  if (perCategory) {
    colors = apexCategories.map((label, i) => categoryColor(display, label, i, colors[0]));
  }

  const stacked = ["stacked_line", "stacked_column", "stacked_bar"].includes(chartType);
  const axisTitleStyle = { color: theme.palette.text.secondary, fontSize: "11px", fontWeight: 500 };
  const labelStyle = { colors: theme.palette.text.secondary, fontSize: "11px" };
  const yAxes = rightIdx.size && !rankBars
    ? apexSeries.map((s, i) => {
      const onRight = rightIdx.has(i);
      const cfg = onRight ? rightY : leftY;
      const firstOfSide = apexSeries.findIndex((_, j) => rightIdx.has(j) === onRight) === i;
      return {
        seriesName: s.name,
        opposite: onRight,
        show: firstOfSide && cfg.visible !== false,
        title: { text: cfg.label || undefined, style: axisTitleStyle },
        labels: { style: labelStyle, formatter: (v) => formatValueWithConfig(v, fmtFor(cfg, shown[i]?.unit)) },
        ...(cfg.min !== "" && cfg.min != null && { min: Number(cfg.min) }),
        ...(cfg.max !== "" && cfg.max != null && { max: Number(cfg.max) }),
        logarithmic: cfg.scale === "logarithmic",
      };
    })
    : {
      show: leftY.visible !== false,
      title: { text: leftY.label || undefined, style: axisTitleStyle },
      labels: { style: labelStyle, formatter: horizontal ? undefined : yFmt, maxWidth: horizontal ? 340 : 220 },
      ...(leftY.min !== "" && leftY.min != null && { min: Number(leftY.min) }),
      ...(leftY.max !== "" && leftY.max != null && { max: Number(leftY.max) }),
      /* Start the axis near the data rather than at zero (the built-in
         duration charts do), rounded to a readable step. */
      ...(display?.yMinAuto && (leftY.min === "" || leftY.min == null) && (() => {
        const ys = apexSeries.flatMap((x) => x.data).filter((v) => v != null);
        if (!ys.length) return {};
        const lo = Math.min(...ys);
        const step = display.yMinAuto;
        return { min: Math.max(0, Math.floor(lo / step) * step) };
      })()),
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
      curve: display?.curve || "monotoneCubic",
      width: chartType === "line" ? 2 : chartType === "stacked_line" ? 1.5 : 0,
    },
    fill: chartType === "stacked_line" ? { type: "solid", opacity: 0.25 } : { opacity: 1 },
    markers: { size: chartType === "line" && display?.markers !== false ? 4 : 0, strokeWidth: 0, hover: { size: 6 } },
    /* Values printed on the bars, like the built-in bar charts. */
    dataLabels: display?.dataLabels
      ? { enabled: true, formatter: (v) => (v == null || v === 0 ? "" : formatValueWithConfig(v, fmtFor(leftY, shown[0]?.unit))), style: { fontSize: "11px", fontWeight: 700, colors: ["#fff"] } }
      : { enabled: false },
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
        distributed: !!(rankBars || oneBucketColumns || perCategory),
      },
    },
    xaxis: {
      categories: apexCategories,
      /* Long category axes (every call, p0–p100) show every Nth label. */
      ...(!horizontal && apexCategories.length > 20 && { tickAmount: 10 }),
      labels: {
        show: xAxis.visible !== false,
        style: labelStyle,
        rotate: 0,
        hideOverlappingLabels: true,
        trim: false,
        ...(horizontal && { formatter: (v) => (typeof v === "number" ? yFmt(v) : v) }),
      },
      /* The value axis of a horizontal percent chart stops at 100%. */
      ...(horizontal && display?.valueMax != null && { max: display.valueMax }),
      title: { text: xAxis.label || undefined, style: axisTitleStyle },
      axisBorder: { show: false },
      axisTicks: { show: false },
      tooltip: { enabled: false },
    },
    yaxis: yAxes,
    tooltip: {
      theme: theme.palette.mode,
      /* A call's tooltip names the scenario; a ranked bar its real label. */
      x: {
        formatter: (v, opts) => {
          const b = buckets[opts?.dataPointIndex];
          if (!b || rankBars || oneBucketColumns) return v;
          const name = b.name && b.name !== b.label ? ` · ${b.name}` : "";
          return b.sub ? `${b.label}${name} · ${b.sub}` : `${b.label}${name}`;
        },
      },
      shared: !rankBars && !horizontal && !oneBucketColumns,
      intersect: false,
      y: {
        formatter: (v, { seriesIndex } = {}) => (v == null
          ? "—"
          : formatValueWithConfig(v, fmtFor(rightIdx.has(seriesIndex) ? rightY : leftY, (rankBars ? shown[0] : shown[seriesIndex])?.unit))),
      },
    },
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", width: "100%", height }}>
      {!rankBars && !oneBucketColumns && !perCategory && shown.length > 1 && (
        <ChartLegend items={shown.map(legendName)} colors={colors} />
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

SimWidgetChart.propTypes = {
  series: PropTypes.array.isRequired,
  buckets: PropTypes.array.isRequired,
  chartType: PropTypes.string.isRequired,
  axisConfig: PropTypes.object,
  visible: PropTypes.instanceOf(Set),
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  compact: PropTypes.bool,
  display: PropTypes.shape({
    colorMap: PropTypes.object,
    seriesColor: PropTypes.string,
    categoryRule: PropTypes.shape({ op: PropTypes.string, value: PropTypes.number, match: PropTypes.string, other: PropTypes.string }),
    categoryPalette: PropTypes.array,
    decimals: PropTypes.object,
    units: PropTypes.object,
    legend: PropTypes.oneOf(["breakdown", "metric"]),
    curve: PropTypes.string,
    markers: PropTypes.bool,
    dataLabels: PropTypes.bool,
    yMinAuto: PropTypes.number,
    valueMax: PropTypes.number,
  }),
};
