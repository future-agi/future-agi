import React, { useMemo } from "react";
import PropTypes from "prop-types";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Typography } from "@mui/material";
import { fmtCi, fmtCellTip, fmtTick } from "../workbenchFormat";
import {
  ChartFrame,
  RunLegend,
  TipBox,
  colColor,
  useChartTheme,
} from "./chartKit";

const MAX_GROUPS = 24;

/**
 * A bar with its 95% CI whisker drawn in one shape (recharts' ErrorBar keys
 * collide when two whiskers share coordinates). The value axis starts at
 * 0, so pixels per unit come from the plot background (rates, 0–100) or
 * the bar itself.
 */
function WhiskerBar(props) {
  const { x, y, width, height, fill, payload, dataKey, background, rate, ink } =
    props;
  if (!Number.isFinite(height) || height < 0) return null;
  const v = payload?.[dataKey];
  const err = payload?.[`${dataKey}__ci`];
  const r = Math.min(4, width / 2, height);
  const path = `M${x},${y + height} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + width - r},${y} Q${x + width},${y} ${x + width},${y + r} L${x + width},${y + height} Z`;
  let whisker = null;
  if (err && v != null) {
    const base = background ? background.y + background.height : y + height;
    const ppu =
      rate && background ? background.height / 100 : v > 0 ? height / v : null;
    if (ppu) {
      const yLo = base - err[0] * ppu;
      const yHi = base - err[1] * ppu;
      const cx = x + width / 2;
      whisker = (
        <g stroke={ink} strokeWidth={1.5} pointerEvents="none">
          <line x1={cx} x2={cx} y1={yHi} y2={yLo} />
          <line x1={cx - 3} x2={cx + 3} y1={yHi} y2={yHi} />
          <line x1={cx - 3} x2={cx + 3} y1={yLo} y2={yLo} />
        </g>
      );
    }
  }
  return (
    <g>
      {height > 0 && <path d={path} fill={fill} />}
      {whisker}
    </g>
  );
}

WhiskerBar.propTypes = {
  x: PropTypes.number,
  y: PropTypes.number,
  width: PropTypes.number,
  height: PropTypes.number,
  fill: PropTypes.string,
  payload: PropTypes.object,
  dataKey: PropTypes.string,
  background: PropTypes.object,
  rate: PropTypes.bool,
  ink: PropTypes.string,
};

/**
 * Bars by group (R2-SPEC §6.3.3 #3): group × column, 95% CI whiskers, n under
 * each group; a bar opens that cell's rows. The only chart in All datasets.
 */
const GroupBarsChart = ({
  pivot,
  groups,
  measure,
  colColors,
  onOpenCell,
  title,
  axisTitle,
}) => {
  const t = useChartTheme();
  const cols = pivot?.columns ?? [];
  const counts = Boolean(pivot?.failedEval);
  const rate = measure?.kind === "rate" && !counts;
  const data = useMemo(
    () =>
      (groups ?? []).slice(0, MAX_GROUPS).map((g) => {
        const row = { group: g, n: pivot.rowTotals[g]?.rows ?? 0 };
        cols.forEach((c) => {
          const cell = pivot.cells[g]?.[c.key];
          const v = cell?.value;
          row[c.key] = v == null ? null : rate ? v * 100 : v;
          row[`${c.key}__ci`] =
            v != null && cell?.ci && !counts
              ? [
                  Math.max(0, cell.ci.low) * (rate ? 100 : 1),
                  (rate ? Math.min(1, cell.ci.high) : cell.ci.high) *
                    (rate ? 100 : 1),
                ]
              : null;
        });
        return row;
      }),
    [pivot, groups, cols, rate, counts],
  );
  const empty = !data.length || !cols.length;
  const maxChars = data.length > 6 ? 12 : data.length > 4 ? 16 : 22;
  const tick = ({ x, y, payload }) => {
    const row = data.find((d) => d.group === payload.value);
    const label = String(payload.value);
    return (
      <g transform={`translate(${x},${y})`}>
        <title>{label}</title>
        <text
          x={0}
          y={0}
          dy={12}
          textAnchor="middle"
          fontSize={12}
          fill={t.ink}
        >
          {label.length > maxChars ? `${label.slice(0, maxChars - 1)}…` : label}
        </text>
        <text
          x={0}
          y={0}
          dy={26}
          textAnchor="middle"
          fontSize={11}
          fill={t.axis}
        >
          {row ? `${row.n} ${row.n === 1 ? "row" : "rows"}` : ""}
        </text>
      </g>
    );
  };
  return (
    <ChartFrame
      title={title}
      empty={empty}
      legend={
        <RunLegend
          items={cols.map((c) => ({
            key: c.key,
            label: c.label,
            color: colColor(colColors, c, t),
          }))}
        />
      }
      footer={
        counts ? null : (
          <Typography
            typography="s3"
            color="text.secondary"
            component="p"
            data-testid="bars-ci-caption"
            sx={{ mt: 0.5 }}
          >
            Whiskers show the 95% range: where the true{" "}
            {rate ? "rate" : "value"} likely is, given how many trials ran. Bars
            whose ranges overlap may not really differ.
          </Typography>
        )
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          margin={{ top: 8, right: 8, bottom: 20, left: 4 }}
          barGap={2}
          barCategoryGap="22%"
        >
          <CartesianGrid vertical={false} stroke={t.grid} />
          <XAxis
            dataKey="group"
            tick={tick}
            interval={0}
            tickLine={false}
            axisLine={{ stroke: t.grid }}
            height={36}
          />
          <YAxis
            tick={t.tick}
            tickLine={false}
            axisLine={false}
            width={measure?.unit === "usd" ? 72 : 48}
            domain={rate ? [0, 100] : [0, "auto"]}
            ticks={rate ? [0, 25, 50, 75, 100] : undefined}
            allowDataOverflow={rate}
            allowDecimals={!counts}
            tickFormatter={(v) =>
              rate
                ? `${Math.round(v)}%`
                : counts
                  ? String(v)
                  : fmtTick(measure, v)
            }
            label={{
              value: counts ? "Failed attempts" : axisTitle,
              angle: -90,
              position: "insideLeft",
              fill: t.axis,
              fontSize: 12,
              dy: 40,
            }}
          />
          <Tooltip
            cursor={{ fill: t.grid, opacity: 0.4 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0];
              const g = p.payload.group;
              return (
                <TipBox
                  lines={[
                    g,
                    ...cols.map((c) => {
                      const cell = pivot.cells[g]?.[c.key];
                      if (!cell?.rows) return `${c.label} · no rows`;
                      if (counts)
                        return `${c.label} · ${cell.n} failed attempts on ${cell.rows} rows`;
                      return [
                        c.label,
                        fmtCellTip(measure, cell),
                        fmtCi(measure, cell.ci),
                      ]
                        .filter(Boolean)
                        .join(" · ");
                    }),
                    "Click a bar to open its rows",
                  ]}
                />
              );
            }}
          />
          {cols.map((c) => (
            <Bar
              key={c.key}
              dataKey={c.key}
              name={c.label}
              fill={colColor(colColors, c, t)}
              radius={[4, 4, 0, 0]}
              maxBarSize={28}
              cursor="pointer"
              isAnimationActive={false}
              shape={<WhiskerBar rate={rate} ink={t.ink} dataKey={c.key} />}
              onClick={(d) => {
                const cell = pivot.cells[d.group]?.[c.key];
                if (cell?.rows) onOpenCell(d.group, c, cell);
              }}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
};

GroupBarsChart.propTypes = {
  pivot: PropTypes.object,
  groups: PropTypes.array,
  measure: PropTypes.object,
  colColors: PropTypes.object,
  onOpenCell: PropTypes.func.isRequired,
  title: PropTypes.node,
  axisTitle: PropTypes.string,
};

export default GroupBarsChart;
