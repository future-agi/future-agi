import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Tooltip as Help,
  Typography,
} from "@mui/material";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  LabelList,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import Iconify from "src/components/iconify";

export const COLORS = [
  "#7954f8",
  "#32c99b",
  "#f56b74",
  "#f6b847",
  "#3898e8",
  "#9a9fac",
];
const OUTCOME_COLORS = {
  passed: COLORS[1],
  failed: COLORS[2],
  error: COLORS[3],
  inconclusive: COLORS[5],
  true: COLORS[0],
  false: COLORS[2],
  positive: COLORS[1],
  neutral: COLORS[5],
  negative: COLORS[2],
  unknown: COLORS[5],
  successful: COLORS[0],
  unsuccessful: COLORS[2],
  escalated: COLORS[0],
};
export const number = (value, digits = 1) =>
  value == null
    ? "—"
    : new Intl.NumberFormat(undefined, {
        maximumFractionDigits: digits,
      }).format(value);
export const format = (value, unit = "number") => {
  if (value == null) return "—";
  if (unit === "cents") return `$${number(value / 100, 3)}`;
  if (unit === "ratio") return `${number(value, 0)}/${number(100 - value, 0)}`;
  return `${number(value)}${{ ms: "ms", seconds: "s", percent: "%" }[unit] || ""}`;
};

export function Widget({
  id,
  title,
  subtitle,
  help,
  onHide,
  children,
  wide = false,
}) {
  return (
    <Box
      component="section"
      aria-label={title}
      data-analytics-widget={id}
      sx={{
        minWidth: 0,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        borderRadius: 1.5,
        overflow: "hidden",
        gridColumn: wide ? "1 / -1" : undefined,
        breakInside: "avoid",
      }}
    >
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        sx={{ px: 2, pt: 1.5, pb: 1 }}
      >
        <Box>
          <Typography component="h3" sx={{ fontSize: 13, fontWeight: 600 }}>
            {title}
            {help && (
              <Help title={help} arrow>
                <IconButton
                  size="small"
                  aria-label={`About ${title}`}
                  sx={{ ml: 0.5, fontSize: 13 }}
                >
                  ⓘ
                </IconButton>
              </Help>
            )}
          </Typography>
          {subtitle && (
            <Typography sx={{ fontSize: 11, color: "text.secondary", mt: 0.4 }}>
              {subtitle}
            </Typography>
          )}
        </Box>
        <Help title="Hide widget">
          <IconButton
            className="analytics-no-print"
            aria-label={`Hide ${title}`}
            size="small"
            onClick={() => onHide(id)}
          >
            <Iconify icon="solar:eye-closed-linear" width={16} />
          </IconButton>
        </Help>
      </Stack>
      {children}
    </Box>
  );
}
Widget.propTypes = {
  id: PropTypes.string,
  title: PropTypes.string,
  subtitle: PropTypes.string,
  help: PropTypes.string,
  onHide: PropTypes.func,
  children: PropTypes.node,
  wide: PropTypes.bool,
};
export function NoMeasurement({ text = "No measurements recorded" }) {
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      sx={{ minHeight: 190, p: 3 }}
    >
      <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
        {text}
      </Typography>
    </Stack>
  );
}
NoMeasurement.propTypes = { text: PropTypes.string };
const tooltipStyle = {
  backgroundColor: "#202025",
  borderColor: "#41414b",
  borderRadius: 8,
  fontSize: 11,
  color: "#fff",
};
const tick = { fontSize: 10, fill: "#92929e" };
const pieLabel = (label) =>
  ({
    true: "Successful",
    false: "Unsuccessful",
    successful: "Successful",
    unsuccessful: "Unsuccessful",
    escalated: "Escalated",
    passed: "Passed",
    failed: "Failed",
    error: "Errored",
    inconclusive: "Inconclusive",
    positive: "Positive",
    neutral: "Neutral",
    negative: "Negative",
    unknown: "Unknown",
  })[label.toLowerCase()] || label;
export function Donut({ data, onOpen }) {
  if (!data?.total) return <NoMeasurement />;
  return (
    <Box sx={{ px: 1.5, pb: 2 }}>
      <Box sx={{ height: 165, position: "relative" }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data.segments}
              dataKey="count"
              nameKey="label"
              innerRadius={46}
              outerRadius={62}
              stroke="none"
              isAnimationActive={false}
              onClick={
                onOpen
                  ? (segment) =>
                      segment.count > 0 &&
                      segment.statuses?.length &&
                      onOpen({ status: segment.statuses })
                  : undefined
              }
              style={{ cursor: onOpen ? "pointer" : "default" }}
            >
              {data.segments.map((segment, index) => (
                <Cell
                  key={segment.label}
                  fill={
                    OUTCOME_COLORS[segment.label.toLowerCase()] ||
                    COLORS[index % COLORS.length]
                  }
                />
              ))}
            </Pie>
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(value, label) => [number(value, 0), pieLabel(label)]}
            />
          </PieChart>
        </ResponsiveContainer>
        <Stack
          sx={{ position: "absolute", inset: 0, pointerEvents: "none" }}
          alignItems="center"
          justifyContent="center"
        >
          <Typography sx={{ fontSize: 10, color: "text.secondary" }}>
            {data.headline ? pieLabel(data.headline.label) : "Calls"}
          </Typography>
          <Typography sx={{ fontSize: 22, fontWeight: 600 }}>
            {data.headline
              ? format(data.headline.share, "percent")
              : number(data.total, 0)}
          </Typography>
        </Stack>
      </Box>
      <Stack direction="row" flexWrap="wrap" gap={1} justifyContent="center">
        {data.segments.map((segment, index) => (
          <Stack
            key={segment.label}
            component={onOpen ? "button" : "div"}
            type={onOpen ? "button" : undefined}
            disabled={onOpen ? !segment.count : undefined}
            onClick={
              onOpen && segment.statuses?.length
                ? () => onOpen({ status: segment.statuses })
                : undefined
            }
            aria-label={
              onOpen ? `Show ${pieLabel(segment.label)} calls` : undefined
            }
            sx={
              onOpen
                ? {
                    border: 0,
                    bgcolor: "transparent",
                    p: 0,
                    cursor: "pointer",
                    "&:disabled": { opacity: 0.5, cursor: "default" },
                  }
                : undefined
            }
            direction="row"
            alignItems="center"
            spacing={0.5}
          >
            <Box
              sx={{
                height: 5,
                width: 5,
                borderRadius: "50%",
                bgcolor:
                  OUTCOME_COLORS[segment.label.toLowerCase()] ||
                  COLORS[index % COLORS.length],
              }}
            />
            <Typography sx={{ fontSize: 10, color: "text.secondary" }}>
              {pieLabel(segment.label)} {segment.count} (
              {number(segment.share, 0)}%)
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}
Donut.propTypes = { data: PropTypes.object, onOpen: PropTypes.func };
export function Bars({
  rows = [],
  xKey,
  series,
  horizontal = false,
  unit,
  height = 245,
  threshold,
  onOpen,
  legend = false,
  labels = false,
  labelKey,
  multicolor = false,
  histogram = false,
  angled = false,
  axisLabel,
}) {
  if (
    !rows.length ||
    !rows.some((row) => series.some(({ key }) => row[key] != null))
  )
    return <NoMeasurement />;
  return (
    <Box sx={{ px: 1.5, pb: 2 }}>
      <Box sx={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={rows}
            layout={horizontal ? "vertical" : "horizontal"}
            margin={{
              top: 12,
              right: horizontal && labels ? 95 : 12,
              bottom: angled ? 65 : axisLabel ? 25 : 12,
              left: horizontal ? 5 : 0,
            }}
          >
            <CartesianGrid
              stroke="#41414b"
              strokeDasharray="3 4"
              opacity={0.35}
              horizontal={!horizontal}
              vertical={horizontal}
            />
            <XAxis
              type={horizontal ? "number" : "category"}
              dataKey={horizontal ? undefined : xKey}
              tick={tick}
              tickLine={false}
              axisLine={false}
              angle={angled ? -30 : 0}
              textAnchor={angled ? "end" : "middle"}
              interval={angled ? 0 : "preserveStartEnd"}
              label={
                axisLabel
                  ? {
                      value: axisLabel,
                      position: "bottom",
                      offset: 10,
                      fill: tick.fill,
                      fontSize: 11,
                    }
                  : undefined
              }
              tickFormatter={
                horizontal ? (value) => format(value, unit) : undefined
              }
            />
            <YAxis
              type={horizontal ? "category" : "number"}
              dataKey={horizontal ? xKey : undefined}
              width={horizontal ? 180 : 52}
              tick={tick}
              tickLine={false}
              axisLine={false}
              tickFormatter={
                horizontal
                  ? (value) =>
                      value.length > 28 ? `${value.slice(0, 28)}…` : value
                  : (value) => format(value, unit)
              }
            />
            <Tooltip
              contentStyle={tooltipStyle}
              labelFormatter={(label, payload) =>
                onOpen ? payload?.[0]?.payload?.label || label : label
              }
              formatter={(value, name) => [format(value, unit), name]}
              cursor={{ fill: "#ffffff09" }}
            />
            {legend && (
              <Legend wrapperStyle={{ fontSize: 10, paddingTop: 8 }} />
            )}
            {threshold != null && (
              <ReferenceLine
                x={threshold}
                stroke={COLORS[2]}
                strokeDasharray="4 4"
              />
            )}
            {series.map((s, index) => (
              <Bar
                key={s.key}
                dataKey={s.key}
                name={s.label}
                fill={s.color || COLORS[index % COLORS.length]}
                stackId="value"
                maxBarSize={40}
                isAnimationActive={false}
                onClick={onOpen ? (row) => onOpen(row) : undefined}
                style={{ cursor: onOpen ? "pointer" : "default" }}
              >
                {(multicolor || histogram) &&
                  rows.map((row, rowIndex) => (
                    <Cell
                      key={row.id || row.name || row.label || rowIndex}
                      fill={
                        histogram
                          ? row.danger
                            ? COLORS[2]
                            : COLORS[0]
                          : COLORS[rowIndex % COLORS.length]
                      }
                    />
                  ))}
                {labels && (
                  <LabelList
                    dataKey={labelKey || s.key}
                    position={
                      horizontal
                        ? series.length > 1
                          ? "center"
                          : "right"
                        : "insideTop"
                    }
                    fill="#eeeeee"
                    fontSize={10}
                    formatter={(value) =>
                      labelKey ? value : value ? format(value, unit) : ""
                    }
                  />
                )}
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </Box>
      {onOpen && (
        <Stack direction="row" flexWrap="wrap" gap={0.5}>
          {rows.map((row, index) => (
            <Button
              key={row.id}
              size="small"
              onClick={() => onOpen(row)}
              sx={{ fontSize: 10 }}
              title={row.label}
            >
              Open task {index + 1}
            </Button>
          ))}
        </Stack>
      )}
    </Box>
  );
}
Bars.propTypes = {
  rows: PropTypes.array,
  xKey: PropTypes.string,
  series: PropTypes.array.isRequired,
  horizontal: PropTypes.bool,
  unit: PropTypes.string,
  height: PropTypes.number,
  threshold: PropTypes.number,
  onOpen: PropTypes.func,
  legend: PropTypes.bool,
  labels: PropTypes.bool,
  labelKey: PropTypes.string,
  multicolor: PropTypes.bool,
  histogram: PropTypes.bool,
  angled: PropTypes.bool,
  axisLabel: PropTypes.string,
};
export function TrendLine({
  rows = [],
  xKey,
  valueKey,
  percentile = false,
  bucketed = false,
}) {
  if (!rows.some((row) => row[valueKey] != null)) return <NoMeasurement />;
  return (
    <Box sx={{ height: 250, px: 1.5, pb: 2 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={rows}
          margin={{ top: 14, right: 20, left: 0, bottom: 5 }}
        >
          <CartesianGrid
            stroke="#41414b"
            strokeDasharray="3 4"
            opacity={0.35}
          />
          <XAxis
            dataKey={xKey}
            type={percentile ? "number" : "category"}
            domain={percentile ? [0, 100] : undefined}
            tick={tick}
            tickFormatter={percentile ? (v) => `p${v}` : (v) => `T${v}`}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={tick}
            width={55}
            tickFormatter={(value) => `${number(value / 1000)}s`}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value) => [format(value, "ms"), "Task duration"]}
            labelFormatter={(label) =>
              percentile
                ? `Percentile ${label}`
                : `${bucketed ? "Time bucket" : "Call"} ${label}`
            }
          />
          <Line
            type="linear"
            dataKey={valueKey}
            stroke={percentile ? COLORS[4] : COLORS[0]}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls={false}
          />
          {percentile &&
            [50, 90, 99].map((p) => (
              <ReferenceLine
                key={p}
                x={p}
                label={{ value: `p${p}`, fill: tick.fill, fontSize: 10 }}
                stroke="#5a5a65"
                strokeDasharray="3 3"
              />
            ))}
        </LineChart>
      </ResponsiveContainer>
    </Box>
  );
}
TrendLine.propTypes = {
  rows: PropTypes.array,
  xKey: PropTypes.string,
  valueKey: PropTypes.string,
  percentile: PropTypes.bool,
  bucketed: PropTypes.bool,
};
