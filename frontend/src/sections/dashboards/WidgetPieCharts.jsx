/* eslint-disable react/prop-types */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";
import ChartLegend from "./ChartLegend";
import { formatValueWithConfig, getUnitRendering } from "./widgetUtils";
import {
  DONUT_ANIMATION_MS,
  GEOMETRY_SETTLE_MS,
  MIN_WIDTH_FOR_CONNECTORS,
  buildConnectors,
  fitCenterFontSize,
  getCenterValue,
  measureDonut,
  sameGeometry,
} from "./widgetPieUtils";
import { NO_DATA_FOR_RANGE_MESSAGE } from "./constants";

// One donut per metric (TH-6530). A pie encodes part-of-a-whole, so slices must
// belong to a single metric. Shared by the editor preview and the saved widget
// so both render identical numbers.

function PieDonut({ group, colorFor, formatValue }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const txtColor = isDark ? "#fff" : "#1a1a2e";
  const containerRef = useRef(null);
  const [box, setBox] = useState(null);
  const [geometry, setGeometry] = useState(null);

  const { slices } = group;
  const values = slices.map((s) => s.value);

  const centerValue = getCenterValue(group);
  const centerText =
    centerValue == null ? "" : formatValue(centerValue, { includeUnit: false });
  const centerFontSize = fitCenterFontSize(centerText, geometry?.radius);

  const frameRef = useRef(null);
  const settleRef = useRef(null);

  // Measure twice: once on the next frame so a static redraw updates promptly,
  // and again once Apex has finished sweeping the ring — a bbox read mid-sweep
  // describes a partial arc and would leave callouts pointing at empty space.
  const remeasure = useCallback(() => {
    const apply = () => {
      const next = measureDonut(containerRef.current);
      setGeometry((prev) => (sameGeometry(prev, next) ? prev : next));
    };
    cancelAnimationFrame(frameRef.current);
    clearTimeout(settleRef.current);
    frameRef.current = requestAnimationFrame(apply);
    settleRef.current = setTimeout(apply, GEOMETRY_SETTLE_MS);
  }, []);

  useEffect(
    () => () => {
      cancelAnimationFrame(frameRef.current);
      clearTimeout(settleRef.current);
    },
    [],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setBox((prev) =>
        prev &&
        Math.round(prev.width) === Math.round(width) &&
        Math.round(prev.height) === Math.round(height)
          ? prev
          : { width, height },
      );
      remeasure();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [remeasure]);

  // Redraws that change the ring without resizing the container.
  useEffect(() => {
    remeasure();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(values), box?.width, box?.height]);

  const showConnectors = geometry && geometry.width >= MIN_WIDTH_FOR_CONNECTORS;
  const connectors =
    showConnectors && slices.length
      ? buildConnectors({ geometry, slices, formatSlice: formatValue })
      : [];

  const options = {
    chart: {
      type: "donut",
      toolbar: { show: false },
      animations: {
        enabled: true,
        easing: "easeinout",
        speed: DONUT_ANIMATION_MS,
      },
    },
    labels: slices.map((s) => s.name),
    colors: slices.map((s) => colorFor(s.name)),
    plotOptions: {
      pie: {
        expandOnClick: false,
        donut: {
          size: "58%",
          labels: {
            show: true,
            name: { show: false },
            value: {
              show: Boolean(centerText),
              fontSize: `${centerFontSize}px`,
              fontWeight: 700,
              color: txtColor,
              offsetY: 8,
              formatter: () => centerText,
            },
            total: {
              show: Boolean(centerText),
              showAlways: Boolean(centerText),
              fontSize: `${centerFontSize}px`,
              fontWeight: 700,
              color: txtColor,
              label: "",
              formatter: () => centerText,
            },
          },
        },
      },
    },
    dataLabels: { enabled: false },
    legend: { show: false, height: 0 },
    stroke: { width: 4, colors: [isDark ? "#1e1e2e" : "#fff"] },
    states: {
      hover: { filter: { type: "darken", value: 0.92 } },
      active: { filter: { type: "none" } },
    },
    tooltip: {
      theme: theme.palette.mode,
      style: { fontSize: "12px" },
      y: { formatter: (val) => formatValue(val) },
    },
  };

  return (
    <Stack sx={{ flex: "1 1 0", minWidth: 180, minHeight: 0 }}>
      <Box
        ref={containerRef}
        sx={{ position: "relative", flex: 1, minHeight: 0 }}
      >
        {/* Out of flow so the chart's own size cannot feed back into the
            measured container. */}
        <Box sx={{ position: "absolute", inset: 0 }}>
          {slices.length ? (
            <ReactApexChart
              options={options}
              series={values}
              type="donut"
              width="100%"
              height="100%"
            />
          ) : (
            <Stack
              alignItems="center"
              justifyContent="center"
              sx={{ width: "100%", height: "100%", px: 1 }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                textAlign="center"
              >
                Nothing to chart for this metric
              </Typography>
            </Stack>
          )}
        </Box>
        {connectors.length > 0 && (
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
            {connectors.map((c, i) => (
              <g key={i}>
                <polyline
                  points={`${c.edgeX},${c.edgeY} ${c.elbowX},${c.elbowY} ${c.endX},${c.elbowY}`}
                  fill="none"
                  stroke={
                    isDark ? "rgba(255,255,255,0.35)" : "rgba(0,0,0,0.25)"
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
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ textAlign: "center", pt: 0.5 }}
      >
        {group.metricName} ({group.aggregation})
      </Typography>
    </Stack>
  );
}

export default function WidgetPieCharts({
  groups,
  colorFor,
  baseFormatConfig,
  fallbackDecimals,
}) {
  const legendNames = [
    ...new Set(groups.flatMap((g) => g.slices.map((s) => s.name))),
  ];

  // Buckets can come back holding only nulls, which clears the caller's
  // "has data points" check yet leaves nothing to draw. Answering that here
  // rather than at a call site keeps the editor and the saved widget in step.
  // An all-zero metric is different: it has values, so its panel stays and
  // explains itself.
  if (!groups.some((g) => g.hasValues)) {
    return (
      <Stack
        alignItems="center"
        justifyContent="center"
        sx={{ width: "100%", height: "100%", p: 2 }}
      >
        <Typography variant="body2" color="text.secondary" textAlign="center">
          {NO_DATA_FOR_RANGE_MESSAGE}
        </Typography>
      </Stack>
    );
  }

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
      {legendNames.length > 1 && (
        <ChartLegend
          items={legendNames}
          colors={legendNames.map((n) => colorFor(n))}
        />
      )}
      <Stack
        direction="row"
        flexWrap="wrap"
        sx={{ flex: 1, minHeight: 0, gap: 1 }}
      >
        {groups.map((group) => {
          // Each pie formats in its own metric's unit; the shared axis config
          // blanks the unit whenever the widget mixes them.
          const cfg = group.unit
            ? { ...baseFormatConfig, ...getUnitRendering(group.unit) }
            : baseFormatConfig;
          const formatValue = (val, { includeUnit = true } = {}) =>
            formatValueWithConfig(val, cfg, { fallbackDecimals, includeUnit });
          return (
            <PieDonut
              key={group.metricIndex}
              group={group}
              colorFor={colorFor}
              formatValue={formatValue}
            />
          );
        })}
      </Stack>
    </Box>
  );
}
