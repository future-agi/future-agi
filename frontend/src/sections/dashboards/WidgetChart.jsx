/* eslint-disable react/prop-types */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import ChartLegend from "./ChartLegend";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";
import { useDashboardQuery } from "src/hooks/useDashboards";
import { format } from "date-fns";
import {
  DEFAULT_DECIMALS,
  escapeHtml,
  formatValueWithConfig,
  getAutoDecimals,
  getSeriesAverage,
  getSuggestedUnitConfig,
} from "./widgetUtils";

const CHART_HEIGHT_FALLBACK = 280;
const COLORS = [
  "#7B56DB", // purple (primary)
  "#1ABCFE", // cyan
  "#FF6B6B", // coral red
  "#2ECB71", // emerald green
  "#F7B731", // amber
  "#E84393", // magenta pink
  "#0984E3", // ocean blue
  "#FD7E14", // tangerine orange
  "#00CEC9", // teal
  "#A29BFE", // lavender
];

function getApexType(chartType) {
  const map = {
    line: "line",
    stacked_line: "area",
    column: "bar",
    stacked_column: "bar",
    bar: "bar",
    stacked_bar: "bar",
    pie: "pie",
  };
  return map[chartType] || "line";
}

export default function WidgetChart({ widget, globalDateRange }) {
  const theme = useTheme();
  const queryMutation = useDashboardQuery();
  const rawQueryConfig = widget.query_config;
  // If globalDateRange is provided, override the widget's time range
  const queryConfig = useMemo(() => {
    if (!rawQueryConfig) return rawQueryConfig;
    if (!globalDateRange) return rawQueryConfig;
    // Convert globalDateRange {start, end} to the format the backend expects
    const timeOverride = {
      preset: "custom",
      custom_start: globalDateRange.start,
      custom_end: globalDateRange.end,
    };
    return {
      ...rawQueryConfig,
      time_range: timeOverride,
    };
  }, [rawQueryConfig, globalDateRange]);
  const chartConfig = widget.chart_config || {};
  const chartType = chartConfig.chart_type || "line";
  const axisConfig = chartConfig.axis_config || null;

  const apexType = getApexType(chartType);
  const isStacked = chartType.startsWith("stacked_");
  const isHorizontal = chartType === "bar" || chartType === "stacked_bar";
  const isPie = chartType === "pie";
  const isTable = chartType === "table";
  const isMetricCard = chartType === "metric";

  // Measure container height so charts fill available space
  const containerRef = useRef(null);
  const [chartHeight, setChartHeight] = useState(CHART_HEIGHT_FALLBACK);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const h = entry.contentRect.height;
        if (h > 20) setChartHeight(Math.round(h));
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pieChartRef = useRef(null);
  const [pieConnectors, setPieConnectors] = useState([]);

  // Re-query whenever the effective query config changes (including
  // metric aggregation/value type), or when global date override changes.
  const querySignature = useMemo(
    () => JSON.stringify(queryConfig || {}),
    [queryConfig],
  );
  useEffect(() => {
    if (queryConfig?.metrics?.length > 0) {
      queryMutation.mutate(queryConfig);
    }
  }, [querySignature, queryConfig]);

  const result = queryMutation.data?.data?.result;
  const series = useMemo(() => {
    const s = [];
    if (result?.metrics) {
      for (const metric of result.metrics) {
        for (const ms of metric.series || []) {
          const isSingleMetric = result.metrics.length === 1;
          let label;
          if (ms.name === "total") {
            label = `${metric.name} (${metric.aggregation})`;
          } else if (isSingleMetric) {
            label = ms.name;
          } else {
            label = `${metric.name} / ${ms.name} (${metric.aggregation})`;
          }
          s.push({
            name: label,
            data: (ms.data || []).map((point) => ({
              x: new Date(point.timestamp).getTime(),
              y: point.value != null ? Number(point.value) : null,
            })),
          });
        }
      }
    }
    return s;
  }, [result]);

  // Auto-select top 10 series by total value when there are many breakdown series
  const MAX_CHART_SERIES = 10;
  const [visibleSeries, setVisibleSeries] = useState(null); // null = all visible

  useEffect(() => {
    if (series.length <= MAX_CHART_SERIES) {
      if (visibleSeries !== null) setVisibleSeries(null);
      return;
    }
    const ranked = series
      .map((s, i) => ({
        i,
        total: s.data.reduce((sum, pt) => sum + (pt.y || 0), 0),
      }))
      .sort((a, b) => b.total - a.total);
    const topIndices = new Set(
      ranked.slice(0, MAX_CHART_SERIES).map((r) => r.i),
    );
    setVisibleSeries(topIndices);
  }, [series]);

  const chartSeries = useMemo(() => {
    if (visibleSeries === null) return series;
    return series.filter((_, i) => visibleSeries.has(i));
  }, [series, visibleSeries]);

  const pieValues = useMemo(
    () =>
      isPie
        ? chartSeries.map((s) =>
            s.data.reduce((sum, pt) => sum + (pt.y || 0), 0),
          )
        : [],
    [isPie, chartSeries],
  );

  // Compute Y-axis precision once from the data range so all ticks use the
  // same number of decimals (avoids "0.0 / 0.0 / 0.02" inconsistency).
  const autoDecimals = useMemo(() => getAutoDecimals(chartSeries), [chartSeries]);
  const leftAxisFormatConfig = useMemo(() => {
    const suggested = getSuggestedUnitConfig(result?.metrics || []);
    const leftAxis = axisConfig?.leftY || {};
    return {
      ...leftAxis,
      unit: leftAxis.unit || suggested.unit,
      prefixSuffix:
        leftAxis.unit || !suggested.unit
          ? leftAxis.prefixSuffix || "prefix"
          : suggested.prefixSuffix,
    };
  }, [axisConfig?.leftY, result?.metrics]);

  useEffect(() => {
    if (!isPie || !pieValues.length) {
      setPieConnectors([]);
      return;
    }
    const timer = setTimeout(() => {
      const container = pieChartRef.current;
      if (!container) return;
      const w = container.offsetWidth;
      const h = chartHeight;
      const cx = w / 2;
      const cy = h * 0.48 + 12;
      const outerR = Math.min(w, h - 35) * 0.32;
      const total = pieValues.reduce((a, b) => a + b, 0);
      if (total === 0) return;
      const items = [];
      let cumAngle = -90;
      pieValues.forEach((val, i) => {
        const sliceAngle = (val / total) * 360;
        const midAngle = cumAngle + sliceAngle / 2;
        const midRad = (midAngle * Math.PI) / 180;
        cumAngle += sliceAngle;
        if (sliceAngle < 3) return;
        const letter = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i] || "";
        const name = chartSeries[i]?.name || "";
        const fv =
          val >= 1000
            ? val.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")
            : val.toLocaleString(undefined, { maximumFractionDigits: 2 });
        const edgeX = cx + outerR * Math.cos(midRad);
        const edgeY = cy + outerR * Math.sin(midRad);
        const elbowDist = outerR + 18;
        const elbowX = cx + elbowDist * Math.cos(midRad);
        const elbowY = cy + elbowDist * Math.sin(midRad);
        const isRight = Math.cos(midRad) >= 0;
        const endX = isRight ? elbowX + 18 : elbowX - 18;
        const textX = isRight ? endX + 4 : endX - 4;
        items.push({
          edgeX,
          edgeY,
          elbowX,
          elbowY,
          endX,
          textX,
          isRight,
          line1: `${letter}. ${name}`,
          line2: fv,
        });
      });
      setPieConnectors(items);
    }, 400);
    return () => clearTimeout(timer);
  }, [isPie, pieValues, chartSeries]);

  const isDark = theme.palette.mode === "dark";
  const makeFormatter =
    (cfg, fallbackDecimals = autoDecimals, includeUnit = true) =>
    (val) =>
      formatValueWithConfig(val, cfg, { fallbackDecimals, includeUnit });
  const formatVal = makeFormatter(leftAxisFormatConfig);

  if (queryMutation.isPending) {
    return (
      <Box
        ref={containerRef}
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          width: "100%",
          height: "100%",
          minHeight: 0,
        }}
      >
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (queryMutation.isError) {
    return (
      <Box
        ref={containerRef}
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          width: "100%",
          height: "100%",
          minHeight: 0,
        }}
      >
        <Typography variant="body2" color="error">
          Failed to load chart data
        </Typography>
      </Box>
    );
  }

  if (!series.length) {
    return (
      <Box
        ref={containerRef}
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          width: "100%",
          height: "100%",
          minHeight: 0,
        }}
      >
        <Typography variant="body2" color="text.disabled">
          No output for the selected inputs.
        </Typography>
      </Box>
    );
  }

  // Metric card
  if (isMetricCard) {
    return (
      <Stack
        ref={containerRef}
        direction="row"
        gap={3}
        justifyContent="center"
        alignItems="center"
        sx={{ width: "100%", height: "100%", minHeight: 0 }}
      >
        {series.map((s, i) => {
          const avg = getSeriesAverage(s.data);
          return (
            <Box key={i} sx={{ textAlign: "center" }}>
              <Typography
                variant="h3"
                sx={{ color: COLORS[i % COLORS.length] }}
              >
                {avg == null
                  ? "—"
                  : formatVal(avg)}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {s.name}
              </Typography>
            </Box>
          );
        })}
      </Stack>
    );
  }

  // Table
  if (isTable) {
    // Time as rows, Segments as columns
    const timeData = series[0]?.data || [];
    const granLabel = (queryConfig?.granularity || "day").toLowerCase();
    const dateFmt =
      granLabel === "minute"
        ? "HH:mm"
        : granLabel === "hour"
          ? "MMM d, HH:mm"
          : granLabel === "month"
            ? "MMM yyyy"
            : granLabel === "week"
              ? "'W'w MMM d"
              : "MMM d";

    return (
      <Box
        ref={containerRef}
        sx={{
          overflow: "auto",
          width: "100%",
          height: "100%",
          minHeight: 0,
        }}
      >
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: "12px",
          }}
        >
          <thead>
            <tr>
              <th
                style={{
                  textAlign: "left",
                  padding: "6px 10px",
                  fontWeight: 600,
                  fontSize: "11px",
                  color: theme.palette.text.secondary,
                  borderBottom: `2px solid ${theme.palette.divider}`,
                  position: "sticky",
                  top: 0,
                  left: 0,
                  background: theme.palette.background.paper,
                  zIndex: 3,
                  minWidth: 100,
                }}
              >
                Time
              </th>
              {series.map((s, i) => (
                <th
                  key={i}
                  style={{
                    textAlign: "right",
                    padding: "6px 10px",
                    fontWeight: 500,
                    fontSize: "11px",
                    color: theme.palette.text.secondary,
                    borderBottom: `2px solid ${theme.palette.divider}`,
                    position: "sticky",
                    top: 0,
                    background: theme.palette.background.paper,
                    zIndex: 2,
                    whiteSpace: "nowrap",
                    minWidth: 70,
                  }}
                >
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                    }}
                  >
                    <Box
                      component="span"
                      sx={{
                        width: 8,
                        height: 8,
                        borderRadius: "2px",
                        bgcolor: COLORS[i % COLORS.length],
                        display: "inline-block",
                        flexShrink: 0,
                      }}
                    />
                    {s.name === "total"
                      ? queryConfig?.metrics?.[0]?.display_name ||
                        queryConfig?.metrics?.[0]?.name ||
                        "Total"
                      : s.name}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {timeData.map((pt, ri) => {
              const hasNonZero = series.some(
                (s) => s.data[ri]?.y != null && s.data[ri].y !== 0,
              );
              return (
                <tr
                  key={ri}
                  style={{
                    borderBottom: `1px solid ${theme.palette.divider}`,
                    opacity: hasNonZero ? 1 : 0.5,
                  }}
                >
                  <td
                    style={{
                      padding: "5px 10px",
                      fontWeight: 500,
                      fontSize: "12px",
                      color: theme.palette.text.primary,
                      position: "sticky",
                      left: 0,
                      background: theme.palette.background.paper,
                      zIndex: 1,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {format(new Date(pt.x), dateFmt)}
                  </td>
                  {series.map((s, si) => {
                    const val = s.data[ri]?.y;
                    return (
                      <td
                        key={si}
                        style={{
                          textAlign: "right",
                          padding: "5px 10px",
                          fontVariantNumeric: "tabular-nums",
                          fontSize: "12px",
                          color:
                            val && val !== 0
                              ? theme.palette.text.primary
                              : theme.palette.text.disabled,
                        }}
                      >
                        {val != null
                          ? formatValueWithConfig(val, leftAxisFormatConfig, {
                              fallbackDecimals: autoDecimals,
                              includeUnit: false,
                            })
                          : "-"}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Box>
    );
  }

  if (isPie) {
    const isDarkPie = theme.palette.mode === "dark";
    const txtColor = isDarkPie ? "#fff" : "#1a1a2e";
    const pieTotal = pieValues.reduce((a, b) => a + b, 0);
    const fmtTotal =
      pieTotal >= 1000000
        ? `${(pieTotal / 1000000).toFixed(1)}M`
        : pieTotal >= 1000
          ? pieTotal.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")
          : pieTotal.toFixed(0);
    const pieOptions = {
      chart: {
        type: "donut",
        toolbar: { show: false },
        animations: { enabled: true, easing: "easeinout", speed: 400 },
      },
      labels: chartSeries.map((s) => s.name),
      colors: COLORS,
      plotOptions: {
        pie: {
          expandOnClick: false,
          donut: {
            size: "58%",
            labels: {
              show: true,
              name: { show: false },
              value: {
                show: true,
                fontSize: "28px",
                fontWeight: 700,
                color: txtColor,
                offsetY: 10,
                formatter: () => fmtTotal,
              },
              total: {
                show: true,
                showAlways: true,
                fontSize: "28px",
                fontWeight: 700,
                color: txtColor,
                label: "",
                formatter: () => fmtTotal,
              },
            },
          },
        },
      },
      dataLabels: { enabled: false },
      legend: { show: false, height: 0 },
      stroke: { width: 4, colors: [isDarkPie ? "#1e1e2e" : "#fff"] },
      states: {
        hover: { filter: { type: "darken", value: 0.92 } },
        active: { filter: { type: "none" } },
      },
      tooltip: {
        theme: theme.palette.mode,
        style: { fontSize: "12px" },
        y: {
          formatter: (val) =>
            val >= 1000
              ? val.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")
              : val.toLocaleString(undefined, { maximumFractionDigits: 2 }),
        },
      },
    };
    const pieLegendNames = chartSeries.map((s) => s.name);
    const pieLegendH = pieLegendNames.length > 1 ? 28 : 0;
    return (
      <Box
        sx={{
          width: "100%",
          height: "100%",
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        {pieLegendNames.length > 1 && (
          <ChartLegend items={pieLegendNames} colors={COLORS} />
        )}
        <Box
          ref={(el) => {
            pieChartRef.current = el;
            containerRef.current = el;
          }}
          sx={{
            position: "relative",
            flex: 1,
            minHeight: 0,
          }}
        >
          <ReactApexChart
            key={`pie-${axisConfig?.leftY?.unit}-${axisConfig?.leftY?.prefixSuffix}-${axisConfig?.leftY?.abbreviation}-${axisConfig?.leftY?.decimals}`}
            options={pieOptions}
            series={pieValues}
            type="donut"
            height={chartHeight - pieLegendH}
          />
          {pieConnectors.length > 0 && (
            <svg
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: "100%",
                pointerEvents: "none",
                overflow: "visible",
              }}
            >
              {pieConnectors.map((c, i) => (
                <g key={i}>
                  <polyline
                    points={`${c.edgeX},${c.edgeY} ${c.elbowX},${c.elbowY} ${c.endX},${c.elbowY}`}
                    fill="none"
                    stroke={
                      isDarkPie ? "rgba(255,255,255,0.35)" : "rgba(0,0,0,0.25)"
                    }
                    strokeWidth="1"
                  />
                  <text
                    x={c.textX}
                    y={c.elbowY - 5}
                    textAnchor={c.isRight ? "start" : "end"}
                    fill={txtColor}
                    fontSize="11"
                    fontWeight="500"
                    fontFamily="inherit"
                  >
                    <tspan x={c.textX} dy="0">
                      {c.line1}
                    </tspan>
                    <tspan x={c.textX} dy="14">
                      {c.line2}
                    </tspan>
                  </text>
                </g>
              ))}
            </svg>
          )}
        </Box>
      </Box>
    );
  }

  // Bar chart — horizontal bar table
  if (isHorizontal) {
    const barRows = chartSeries.map((s) => {
      const avg = getSeriesAverage(s.data);
      return {
        value: avg,
        numericValue: avg == null ? 0 : avg,
      };
    });
    const maxVal = Math.max(...barRows.map((row) => Math.abs(row.numericValue)), 1);
    return (
      <Box
        ref={containerRef}
        sx={{
          width: "100%",
          height: "100%",
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Legend */}
        <Stack
          direction="row"
          gap={2}
          flexWrap="wrap"
          justifyContent="center"
          sx={{ px: 2, pt: 1.5, pb: 1 }}
        >
          {chartSeries.map((s, i) => (
            <Stack key={i} direction="row" alignItems="center" gap={0.5}>
              <Box
                sx={{
                  width: 10,
                  height: 10,
                  borderRadius: "2px",
                  bgcolor: COLORS[i % COLORS.length],
                  flexShrink: 0,
                }}
              />
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  fontWeight: 500,
                  fontSize: "12px",
                }}
              >
                {s.name}
              </Typography>
            </Stack>
          ))}
        </Stack>
        {/* Column headers */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            px: 2,
            py: 0.5,
            borderBottom: `1px solid ${theme.palette.divider}`,
          }}
        >
          <Typography
            variant="caption"
            sx={{
              width: 140,
              minWidth: 140,
              flexShrink: 0,
              fontWeight: 600,
              color: "text.secondary",
              fontSize: "11px",
            }}
          >
            Metric
          </Typography>
          <Typography
            variant="caption"
            sx={{
              flex: 1,
              fontWeight: 600,
              color: "text.secondary",
              fontSize: "11px",
            }}
          >
            Value
          </Typography>
        </Box>
        {/* Bar rows */}
        <Box sx={{ flex: 1, overflow: "auto", px: 2 }}>
          {barRows.map((row, i) => {
            const val = row.numericValue;
            const color = COLORS[i % COLORS.length];
            const pct = maxVal > 0 ? (Math.abs(val) / maxVal) * 100 : 0;
            const name = chartSeries[i]?.name || "";
            const shortName =
              name.length > 20 ? name.slice(0, 18) + "..." : name;
            return (
              <Box
                key={i}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  py: 0.8,
                  borderBottom: `1px solid ${theme.palette.divider}`,
                  "&:last-child": { borderBottom: "none" },
                }}
              >
                <Typography
                  variant="body2"
                  sx={{
                    width: 140,
                    minWidth: 140,
                    flexShrink: 0,
                    fontWeight: 500,
                    fontSize: "12px",
                    color: "text.primary",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    pr: 1,
                  }}
                  title={name}
                >
                  {shortName}
                </Typography>
                <Box
                  sx={{
                    flex: 1,
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                  }}
                >
                  <Box
                    sx={{
                      flex: 1,
                      height: 18,
                      bgcolor: isDark
                        ? "rgba(255,255,255,0.04)"
                        : "rgba(0,0,0,0.02)",
                      borderRadius: "3px",
                      overflow: "hidden",
                    }}
                  >
                    <Box
                      sx={{
                        height: "100%",
                        width: `${Math.max(pct, 1)}%`,
                        bgcolor: color,
                        borderRadius: "3px",
                        transition: "width 0.4s ease",
                      }}
                    />
                  </Box>
                  <Typography
                    variant="body2"
                    sx={{
                      minWidth: 60,
                      textAlign: "right",
                      fontWeight: 600,
                      fontSize: "12px",
                      color: "text.primary",
                      fontVariantNumeric: "tabular-nums",
                      flexShrink: 0,
                    }}
                  >
                    {row.value == null ? "—" : formatVal(row.value)}
                  </Typography>
                </Box>
              </Box>
            );
          })}
        </Box>
      </Box>
    );
  }

  const options = {
    chart: {
      type: apexType,
      toolbar: { show: false },
      zoom: { enabled: true },
      stacked: isStacked,
      animations: { enabled: true, easing: "easeinout", speed: 400 },
      events: {
        mouseMove: (event, chartContext, config) => {
          const el = chartContext?.el;
          if (!el) return;
          if (el.getAttribute("data-legend-highlight")) return;
          const paths = el.querySelectorAll(".apexcharts-series");
          let si = config.seriesIndex;
          const dpi = config.dataPointIndex;

          if (isStacked && dpi >= 0) {
            const w = chartContext.w;
            const gridRect = w.globals.gridRect;
            const chartRect = el.getBoundingClientRect();
            const mouseY = event.clientY - chartRect.top - gridRect.y;
            const plotH = gridRect.height;
            const minY = w.globals.minY;
            const maxY = w.globals.maxY;
            const mouseVal = maxY - (mouseY / plotH) * (maxY - minY);

            let cumSum = 0;
            si = w.globals.series.length - 1;
            for (let i = 0; i < w.globals.series.length; i++) {
              cumSum += w.globals.series[i]?.[dpi] || 0;
              if (mouseVal <= cumSum) {
                si = i;
                break;
              }
            }
          }

          if (si >= 0 && paths.length > 1) {
            el.setAttribute("data-custom-highlight", "1");
            paths.forEach((p, i) => {
              p.style.transition = "opacity 0.15s ease";
              p.style.opacity = i === si ? "1" : "0.15";
            });
          } else if (el.getAttribute("data-custom-highlight")) {
            el.removeAttribute("data-custom-highlight");
            paths.forEach((p) => {
              p.style.opacity = "1";
            });
          }
        },
        mouseLeave: (event, chartContext) => {
          const el = chartContext?.el;
          if (!el) return;
          if (el.getAttribute("data-custom-highlight")) {
            el.removeAttribute("data-custom-highlight");
            el.querySelectorAll(".apexcharts-series").forEach((p) => {
              p.style.transition = "opacity 0.2s ease";
              p.style.opacity = "1";
            });
          }
        },
        mounted: (chartContext) => {
          const el = chartContext?.el;
          if (!el) return;
          el.querySelectorAll(".apexcharts-legend-series").forEach(
            (item, idx) => {
              if (item.getAttribute("data-hover-bound")) return;
              item.setAttribute("data-hover-bound", "1");
              item.addEventListener("mouseenter", () => {
                el.setAttribute("data-legend-highlight", "1");
                el.querySelectorAll(".apexcharts-series").forEach((p, i) => {
                  p.style.transition = "opacity 0.15s ease";
                  p.style.opacity = i === idx ? "1" : "0.15";
                });
              });
              item.addEventListener("mouseleave", () => {
                el.removeAttribute("data-legend-highlight");
                el.querySelectorAll(".apexcharts-series").forEach((p) => {
                  p.style.transition = "opacity 0.2s ease";
                  p.style.opacity = "1";
                });
              });
            },
          );
        },
        updated: (chartContext) => {
          const el = chartContext?.el;
          if (!el) return;
          el.querySelectorAll(".apexcharts-legend-series").forEach(
            (item, idx) => {
              if (item.getAttribute("data-hover-bound")) return;
              item.setAttribute("data-hover-bound", "1");
              item.addEventListener("mouseenter", () => {
                el.setAttribute("data-legend-highlight", "1");
                el.querySelectorAll(".apexcharts-series").forEach((p, i) => {
                  p.style.transition = "opacity 0.15s ease";
                  p.style.opacity = i === idx ? "1" : "0.15";
                });
              });
              item.addEventListener("mouseleave", () => {
                el.removeAttribute("data-legend-highlight");
                el.querySelectorAll(".apexcharts-series").forEach((p) => {
                  p.style.transition = "opacity 0.2s ease";
                  p.style.opacity = "1";
                });
              });
            },
          );
        },
      },
    },
    dataLabels: { enabled: false },
    plotOptions: { bar: { horizontal: isHorizontal } },
    xaxis: {
      type: isHorizontal ? undefined : "datetime",
      tickAmount: Math.min(chartSeries[0]?.data?.length || 10, 12),
      labels: {
        show: axisConfig?.xAxis?.visible !== false,
        style: { colors: theme.palette.text.secondary, fontSize: "11px" },
        datetimeUTC: false,
        ...(!isHorizontal && {
          datetimeFormatter: {
            year: "MMMM",
            month: "MMMM",
            day: "MMM dd",
            hour: "HH:mm",
          },
        }),
      },
      axisBorder: { show: false },
      axisTicks: { show: false },
      ...(axisConfig?.xAxis?.label && {
        title: {
          text: axisConfig.xAxis.label,
          style: { fontSize: "12px", color: theme.palette.text.secondary },
        },
      }),
      crosshairs: {
        show: true,
        width: 1,
        position: "back",
        stroke: { color: theme.palette.text.disabled, width: 1, dashArray: 3 },
      },
    },
    yaxis: (() => {
      const leftCfg = axisConfig?.leftY || {};
      const rightCfg = axisConfig?.rightY || {};
      const sa = axisConfig?.seriesAxis || {};
      const hasRightAxis =
        rightCfg.visible && Object.values(sa).some((s) => s === "right");
      if (!hasRightAxis) {
        const hideOOB = leftCfg.outOfBounds === "hidden";
        return {
          show: leftCfg.visible !== false,
          tickAmount: 5,
          forceNiceScale: !hideOOB,
          logarithmic: leftCfg.scale === "logarithmic",
          ...(leftCfg.min !== undefined &&
            leftCfg.min !== "" && { min: Number(leftCfg.min) }),
          ...(leftCfg.max !== undefined &&
            leftCfg.max !== "" && { max: Number(leftCfg.max) }),
          ...(leftCfg.label && {
            title: {
              text: leftCfg.label,
              style: { fontSize: "12px", color: theme.palette.text.secondary },
            },
          }),
          labels: {
            style: { colors: theme.palette.text.secondary, fontSize: "11px" },
            formatter: formatVal,
          },
        };
      }
      return chartSeries.map((_, i) => {
        const side = sa[i] || "left";
        const cfg = side === "right" ? rightCfg : leftCfg;
        return {
          show:
            i === 0 ||
            (side === "right" &&
              !chartSeries
                .slice(0, i)
                .some((__, j) => (sa[j] || "left") === "right")),
          opposite: side === "right",
          tickAmount: 5,
          forceNiceScale: cfg.outOfBounds !== "hidden",
          logarithmic: cfg.scale === "logarithmic",
          ...(cfg.min !== undefined &&
            cfg.min !== "" && { min: Number(cfg.min) }),
          ...(cfg.max !== undefined &&
            cfg.max !== "" && { max: Number(cfg.max) }),
          ...(cfg.label && {
            title: {
              text: cfg.label,
              style: { fontSize: "12px", color: theme.palette.text.secondary },
            },
          }),
          labels: {
            style: { colors: theme.palette.text.secondary, fontSize: "11px" },
            formatter: makeFormatter(cfg),
          },
        };
      });
    })(),
    stroke: {
      curve: "monotoneCubic",
      width: apexType === "area" ? 2 : apexType === "line" ? 2.5 : 0,
    },
    fill: (() => {
      if (apexType !== "area") return { type: "solid", opacity: 1 };
      if (isStacked) return { type: "solid", opacity: 0.7 };
      return {
        type: "gradient",
        opacity: 1,
        gradient: {
          shadeIntensity: 1,
          opacityFrom: 0.35,
          opacityTo: 0.05,
          stops: [0, 90, 100],
        },
      };
    })(),
    markers: {
      size: apexType === "line" || apexType === "area" ? 4 : 0,
      strokeWidth: 2,
      strokeColors: isDark ? theme.palette.background.paper : "#fff",
      hover: { size: 6, sizeOffset: 3 },
    },
    states: {
      hover: {
        filter: { type: "none" },
      },
      active: {
        allowMultipleDataPointsSelection: false,
        filter: { type: "none" },
      },
    },
    tooltip: isStacked
      ? {
          enabled: true,
          shared: true,
          intersect: false,
          theme: theme.palette.mode,
          style: { fontSize: "12px" },
          x: {
            format: "MMM dd, yyyy",
          },
          y: {
            formatter: formatVal,
          },
        }
      : {
          enabled: true,
          shared: false,
          intersect: false,
          custom: ({ series: s, seriesIndex, dataPointIndex, w }) => {
            const sName = w.globals.seriesNames[seriesIndex] || "";
            const color = w.globals.colors[seriesIndex] || "#6366F1";
            const val = s[seriesIndex]?.[dataPointIndex];
            const prevVal =
              dataPointIndex > 0 ? s[seriesIndex]?.[dataPointIndex - 1] : null;
            const ts = w.globals.seriesX[seriesIndex]?.[dataPointIndex];
            const dateStr = ts ? format(new Date(ts), "MMM dd, yyyy") : "";
            const fmtVal = formatVal(val);
            const bg = isDark ? "#1e1e2e" : "#fff";
            const _border = isDark
              ? "rgba(255,255,255,0.08)"
              : "rgba(0,0,0,0.06)";
            const textPrimary = isDark ? "#fff" : "#1a1a2e";
            const textSecondary = isDark
              ? "rgba(255,255,255,0.5)"
              : "rgba(0,0,0,0.45)";
            let changeHtml = "";
            if (prevVal != null && prevVal !== 0 && val != null) {
              const pct = ((val - prevVal) / Math.abs(prevVal)) * 100;
              const sign = pct >= 0 ? "+" : "";
              const changeColor = pct >= 0 ? "#22C55E" : "#FF4842";
              changeHtml = `<div style="display:flex;align-items:center;gap:6px;margin-top:6px"><span style="color:${changeColor};font-weight:600;font-size:14px">${sign}${pct.toFixed(2)}%</span><span style="color:${textSecondary};font-size:13px">from previous</span></div>`;
            }
            return `<div style="display:flex;background:${bg};border:none;border-radius:12px;box-shadow:0 8px 24px ${isDark ? "rgba(0,0,0,0.5)" : "rgba(0,0,0,0.08)"};overflow:hidden;min-width:200px">
              <div style="width:4px;flex-shrink:0;background:${color}"></div>
              <div style="padding:14px 16px;flex:1">
                <div style="font-weight:700;font-size:14px;color:${textPrimary};line-height:1.3">${escapeHtml(sName)}</div>
                <div style="font-size:12px;color:${textSecondary};margin-top:3px">${escapeHtml(dateStr)}</div>
                <div style="display:flex;align-items:baseline;gap:8px;margin-top:8px">
                  <span style="font-weight:700;font-size:20px;color:${textPrimary}">${escapeHtml(fmtVal)}</span>
                </div>
                ${changeHtml}
              </div>
            </div>`;
          },
        },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 3,
      xaxis: { lines: { show: false } },
      padding: { left: 8, right: 8 },
    },
    colors: COLORS,
    legend: { show: false, height: 0 },
  };

  const legendNames = chartSeries.map((s) => s.name);
  const legendHeight = legendNames.length > 1 ? 24 : 0;

  const handleLegendHover = (seriesIndex) => {
    const el = containerRef.current;
    if (!el) return;
    el.querySelectorAll(".apexcharts-series").forEach((p, i) => {
      p.style.opacity = i === seriesIndex ? "1" : "0.15";
      p.style.transition = "opacity 0.15s";
    });
  };

  const handleLegendLeave = () => {
    const el = containerRef.current;
    if (!el) return;
    el.querySelectorAll(".apexcharts-series").forEach((p) => {
      p.style.opacity = "1";
    });
  };

  return (
    <Box
      ref={containerRef}
      sx={{
        width: "100%",
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {legendNames.length > 1 && (
        <ChartLegend
          items={legendNames}
          colors={COLORS}
          onHoverSeries={handleLegendHover}
          onLeaveSeries={handleLegendLeave}
        />
      )}
      <Box sx={{ flex: 1, minHeight: 0 }}>
        <ReactApexChart
          key={`${axisConfig?.leftY?.unit}-${axisConfig?.leftY?.prefixSuffix}-${axisConfig?.leftY?.abbreviation}-${axisConfig?.leftY?.decimals}-${axisConfig?.leftY?.outOfBounds}`}
          options={options}
          series={chartSeries}
          type={apexType}
          height={chartHeight - legendHeight}
        />
      </Box>
    </Box>
  );
}
