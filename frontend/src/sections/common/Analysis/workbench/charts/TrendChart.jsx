import React, { useMemo } from "react";
import PropTypes from "prop-types";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtCi, fmtCellTip, fmtValue } from "../workbenchFormat";
import { ChartFrame, TipBox, colColor, useChartTheme } from "./chartKit";

function TrendTip({ active, payload, measure }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <TipBox
      lines={[
        `${p.label}${p.name ? ` · ${p.name}` : ""}`,
        fmtCellTip(measure, p.cell),
        fmtCi(measure, p.cell?.ci),
        p.hop ? `Change: ${p.hopDetail ?? p.hop}` : null,
        p.index > 0 ? "Click to compare this hop" : null,
      ]}
    />
  );
}

TrendTip.propTypes = {
  active: PropTypes.bool,
  payload: PropTypes.array,
  measure: PropTypes.object,
};

function TrendDot({ cx, cy, payload, index, colColors, theme, data, onHop }) {
  if (cx == null || cy == null) return null;
  return (
    <circle
      cx={cx}
      cy={cy}
      r={5}
      fill={colColor(colColors, payload.col, theme)}
      stroke={theme.surface}
      strokeWidth={2}
      style={{ cursor: index > 0 ? "pointer" : "default" }}
      onClick={() => index > 0 && onHop(data[index - 1], payload)}
    />
  );
}

TrendDot.propTypes = {
  cx: PropTypes.number,
  cy: PropTypes.number,
  payload: PropTypes.object,
  index: PropTypes.number,
  colColors: PropTypes.object,
  theme: PropTypes.object,
  data: PropTypes.array,
  onHop: PropTypes.func,
};

function TrendLabel({ x, y, index, data, measure, theme }) {
  const p = data?.[index];
  if (x == null || y == null || !p) return null;
  const anchor =
    index === data.length - 1 && data.length > 2
      ? "end"
      : index === 0
        ? "start"
        : "middle";
  return (
    <text x={x} y={y - 12} textAnchor={anchor} fontSize={12} fill={theme.ink}>
      {fmtValue(measure, p.cell?.value, { precise: true })}
      {p.hop ? (
        <tspan fill={theme.axis} fontSize={11}>{` · ${p.hop}`}</tspan>
      ) : null}
    </text>
  );
}

TrendLabel.propTypes = {
  x: PropTypes.number,
  y: PropTypes.number,
  index: PropTypes.number,
  data: PropTypes.array,
  measure: PropTypes.object,
  theme: PropTypes.object,
};

/**
 * Trend (R2-SPEC §6.3.3 #4): x = the columns in order (runs by time, the
 * iteration chain, prompt versions), y = the measure with a 95% CI band;
 * each point carries its hop label (`6 rows rerun`); a point opens the
 * compare of that hop.
 */
const TrendChart = ({
  points,
  measure,
  colColors,
  onHop,
  title,
  axisTitle,
  caption,
}) => {
  const t = useChartTheme();
  const rate = measure?.kind === "rate";
  const k = rate ? 100 : 1;
  const data = useMemo(
    () =>
      (points ?? []).map((p) => ({
        ...p,
        y: p.cell?.value == null ? null : p.cell.value * k,
        band: p.cell?.ci ? [p.cell.ci.low * k, p.cell.ci.high * k] : null,
      })),
    [points, k],
  );
  const empty = data.filter((d) => d.y != null).length < 2;
  return (
    <ChartFrame title={title} empty={empty} legend={caption}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={data}
          margin={{ top: 28, right: 56, bottom: 4, left: 24 }}
        >
          <CartesianGrid vertical={false} stroke={t.grid} />
          <XAxis
            dataKey="label"
            tick={{ ...t.tick, fill: t.ink }}
            tickLine={false}
            axisLine={{ stroke: t.grid }}
            interval={0}
          />
          <YAxis
            tick={t.tick}
            tickLine={false}
            axisLine={false}
            width={48}
            domain={rate ? [0, 100] : ["auto", "auto"]}
            tickFormatter={(v) => (rate ? `${v}%` : fmtValue(measure, v))}
            label={{
              value: axisTitle,
              angle: -90,
              position: "insideLeft",
              fill: t.axis,
              fontSize: 12,
              dy: 40,
            }}
          />
          <Tooltip
            cursor={{ stroke: t.grid }}
            content={<TrendTip measure={measure} />}
          />
          <Area
            dataKey="band"
            stroke="none"
            fill={t.primary}
            fillOpacity={0.1}
            isAnimationActive={false}
          />
          <Line
            dataKey="y"
            stroke={t.axis}
            strokeWidth={2}
            dot={
              <TrendDot
                colColors={colColors}
                theme={t}
                data={data}
                onHop={onHop}
              />
            }
            activeDot={false}
            isAnimationActive={false}
            connectNulls
          >
            <LabelList
              dataKey="y"
              position="top"
              content={<TrendLabel data={data} measure={measure} theme={t} />}
            />
          </Line>
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
};

TrendChart.propTypes = {
  points: PropTypes.array,
  measure: PropTypes.object,
  colColors: PropTypes.object,
  onHop: PropTypes.func.isRequired,
  title: PropTypes.node,
  axisTitle: PropTypes.string,
  caption: PropTypes.node,
};

export default TrendChart;
