import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  FormControlLabel,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { costQualityPoints, pairedPoints } from "../workbenchSeries";
import { fmtMoney, fmtValue } from "../workbenchFormat";
import { ChartFrame, TipBox, colColor, useChartTheme } from "./chartKit";

function CostDot({ cx, cy, payload, colColors, theme, onOpenColumn }) {
  if (cx == null || cy == null || !payload?.col) return null;
  return (
    <g style={{ cursor: "pointer" }} onClick={() => onOpenColumn(payload.col)}>
      <circle
        cx={cx}
        cy={cy}
        r={7}
        fill={colColor(colColors, payload.col, theme)}
        stroke={theme.surface}
        strokeWidth={2}
      />
      <text x={cx + 9} y={cy - 9} fontSize={12} fill={theme.ink}>
        {payload.col.label}
      </text>
    </g>
  );
}

CostDot.propTypes = {
  cx: PropTypes.number,
  cy: PropTypes.number,
  payload: PropTypes.object,
  colColors: PropTypes.object,
  theme: PropTypes.object,
  onOpenColumn: PropTypes.func,
};

function CostTip({ active, payload, qMeasure, columns }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  if (!p?.col) return null;
  return (
    <TipBox
      lines={[
        p.col.label,
        `${qMeasure.short ?? qMeasure.label} ${fmtValue(qMeasure, p.quality, { precise: true })} · ${fmtMoney(p.cost)} per attempt`,
        p.frontier
          ? "On the frontier"
          : `Behind ${columns.find((c) => c.key === p.dominatedBy)?.label ?? "another run"}: costs more and scores no better`,
        "Click to open its failing rows",
      ]}
    />
  );
}

CostTip.propTypes = {
  active: PropTypes.bool,
  payload: PropTypes.array,
  qMeasure: PropTypes.object,
  columns: PropTypes.array,
};

/**
 * Cost vs quality (R2-SPEC §6.3.3 #2): one dot per column, the Pareto
 * frontier dashed (same rule as the Matrix strip). With rows set and
 * exactly 2 columns: the paired scatter (one bubble per row value pair,
 * x = baseline, y = candidate, diagonal = same) and its three sides.
 */
const CostQualityChart = ({
  matrix,
  pivot,
  columns,
  measure,
  colColors,
  paired,
  baseCol,
  onOpenRows,
  onOpenColumn,
  title,
}) => {
  const t = useChartTheme();
  // W5 B4: cost is lower-is-better; reversing the axis puts the better
  // runs top-right.
  const [reverseCost, setReverseCost] = useState(false);
  const rate = measure?.kind === "rate";
  const qMeasure =
    rate || measure?.kind === "score"
      ? measure
      : { kind: "rate", short: "pass@1", label: "Pass rate (pass@1)" };
  const pts = useMemo(
    () => (paired ? null : costQualityPoints(matrix, columns, measure, pivot)),
    [paired, matrix, columns, measure, pivot],
  );
  const pair = useMemo(() => {
    if (!paired) return null;
    const base = columns.find((c) => c.key === baseCol) ?? columns[0];
    const cand = columns.find((c) => c.key !== base.key);
    return { base, cand, ...pairedPoints(matrix, [base, cand], measure) };
  }, [paired, columns, baseCol, matrix, measure]);

  if (paired && pair) {
    const k = rate ? 100 : 1;
    const data = pair.points.map((p) => ({
      ...p,
      px: p.x * k,
      py: p.y * k,
      z: p.rows.length,
    }));
    const vals = data.flatMap((d) => [d.px, d.py]);
    const lo0 = rate ? 0 : Math.min(...vals);
    const hi0 = rate ? 100 : Math.max(...vals);
    const pad = (hi0 - lo0 || 1) * 0.05;
    const lo = lo0 - pad;
    const hi = hi0 + pad;
    const ticks = rate ? [0, 25, 50, 75, 100] : undefined;
    const side = (key, label) => (
      <Chip
        key={key}
        size="small"
        variant="outlined"
        clickable={pair.sides[key].length > 0}
        disabled={!pair.sides[key].length}
        label={`${label} · ${pair.sides[key].length}`}
        onClick={() =>
          onOpenRows(
            pair.sides[key],
            `${pair.cand.label} ${label.toLowerCase()} than ${pair.base.label}`,
          )
        }
        sx={{ height: 24, typography: "s3" }}
      />
    );
    return (
      <ChartFrame
        title={title}
        empty={!data.length}
        legend={
          <Stack direction="row" gap={0.75}>
            {side("better", "Better")}
            {side("same", "Same")}
            {side("worse", "Worse")}
          </Stack>
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 12, right: 16, bottom: 18, left: 4 }}>
            <CartesianGrid stroke={t.grid} />
            <XAxis
              type="number"
              dataKey="px"
              ticks={ticks}
              domain={[lo, hi]}
              tick={t.tick}
              tickLine={false}
              axisLine={{ stroke: t.grid }}
              tickFormatter={(v) =>
                rate ? `${Math.round(v)}%` : fmtValue(measure, v)
              }
              label={{
                value: `${pair.base.label} (baseline)`,
                position: "insideBottom",
                offset: -10,
                fill: t.axis,
                fontSize: 12,
              }}
            />
            <YAxis
              type="number"
              dataKey="py"
              ticks={ticks}
              domain={[lo, hi]}
              tick={t.tick}
              tickLine={false}
              axisLine={false}
              width={48}
              tickFormatter={(v) =>
                rate ? `${Math.round(v)}%` : fmtValue(measure, v)
              }
              label={{
                value: pair.cand.label,
                angle: -90,
                position: "insideLeft",
                fill: t.axis,
                fontSize: 12,
                dy: 20,
              }}
            />
            <ZAxis type="number" dataKey="z" range={[60, 600]} />
            <ReferenceLine
              segment={[
                { x: lo0, y: lo0 },
                { x: hi0, y: hi0 },
              ]}
              stroke={t.axis}
              strokeDasharray="4 3"
            />
            <Tooltip
              cursor={false}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload;
                return (
                  <TipBox
                    lines={[
                      `${p.rows.length} ${p.rows.length === 1 ? "row" : "rows"}: ${p.labels.slice(0, 6).join(", ")}${p.labels.length > 6 ? "…" : ""}`,
                      `${pair.base.label} ${fmtValue(measure, p.x, { precise: true })} · ${pair.cand.label} ${fmtValue(measure, p.y, { precise: true })}`,
                      "Click to open these rows",
                    ]}
                  />
                );
              }}
            />
            <Scatter
              data={data}
              fill={colColor(colColors, pair.cand, t)}
              fillOpacity={0.75}
              stroke={t.surface}
              strokeWidth={2}
              cursor="pointer"
              isAnimationActive={false}
              onClick={(p) =>
                onOpenRows(
                  p.rows,
                  `${p.labels.length} rows · ${pair.base.label} ${fmtValue(measure, p.x)} → ${pair.cand.label} ${fmtValue(measure, p.y)}`,
                )
              }
            />
          </ScatterChart>
        </ResponsiveContainer>
      </ChartFrame>
    );
  }

  const valid = (pts ?? []).filter((p) => p.cost != null && p.quality != null);
  const k = qMeasure.kind === "rate" ? 100 : 1;
  const data = valid.map((p) => ({ ...p, x: p.cost, y: p.quality * k }));
  const frontier = data.filter((d) => d.frontier).sort((a, b) => a.x - b.x);
  const labelOf = (key) => columns.find((c) => c.key === key)?.label ?? key;
  // W5 B4: the frontier, named run by run (runStats.pareto via
  // costQualityPoints), and the reversed-axis toggle.
  const paretoLegend = (
    <Stack gap={0.5} sx={{ mt: 1 }}>
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <FormControlLabel
          sx={{ mr: 0 }}
          control={
            <Switch
              size="small"
              checked={reverseCost}
              onChange={(e) => setReverseCost(e.target.checked)}
              inputProps={{ "aria-label": "Reverse cost axis" }}
            />
          }
          label={<Typography typography="s2">Reverse cost axis</Typography>}
        />
        <Typography typography="s3" color="text.secondary">
          {reverseCost
            ? "Cost: lower is better (axis reversed)"
            : "Cost: lower is better"}
        </Typography>
      </Stack>
      <Stack direction="row" gap={1.5} flexWrap="wrap">
        {data.map((d) => (
          <Stack
            key={d.id}
            direction="row"
            alignItems="center"
            gap={0.5}
            data-testid="pareto-legend-item"
            data-run={d.col.label}
            data-frontier={d.frontier ? "1" : "0"}
            data-dominated-by={d.dominatedBy ? labelOf(d.dominatedBy) : ""}
          >
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                bgcolor: colColor(colColors, d.col, t),
              }}
            />
            <Typography typography="s2">
              {d.col.label} ·{" "}
              {d.frontier
                ? "On the frontier"
                : `Dominated by ${labelOf(d.dominatedBy)}`}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Stack>
  );
  return (
    <ChartFrame
      title={title}
      empty={data.length < 1}
      footer={data.length ? paretoLegend : null}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 12, right: 40, bottom: 18, left: 4 }}>
          <CartesianGrid stroke={t.grid} />
          <XAxis
            type="number"
            dataKey="x"
            tick={t.tick}
            tickLine={false}
            axisLine={{ stroke: t.grid }}
            domain={["auto", "auto"]}
            reversed={reverseCost}
            tickFormatter={(v) => (v === 0 ? "$0" : fmtMoney(v))}
            label={{
              value: reverseCost
                ? "Cost per attempt ($, reversed)"
                : "Cost per attempt ($)",
              position: "insideBottom",
              offset: -10,
              fill: t.axis,
              fontSize: 12,
            }}
          />
          <YAxis
            type="number"
            dataKey="y"
            tick={t.tick}
            tickLine={false}
            axisLine={false}
            width={48}
            domain={qMeasure.kind === "rate" ? [0, 100] : ["auto", "auto"]}
            tickFormatter={(v) =>
              qMeasure.kind === "rate" ? `${Math.round(v)}%` : v.toFixed(2)
            }
            label={{
              value: qMeasure.label,
              angle: -90,
              position: "insideLeft",
              fill: t.axis,
              fontSize: 12,
              dy: 60,
            }}
          />
          <Tooltip
            cursor={false}
            content={<CostTip qMeasure={qMeasure} columns={columns} />}
          />
          {frontier.length > 1 && (
            <Scatter
              data={frontier}
              line={{ stroke: t.axis, strokeDasharray: "5 4", strokeWidth: 2 }}
              shape={() => null}
              isAnimationActive={false}
            />
          )}
          <Scatter
            data={data}
            shape={
              <CostDot
                colColors={colColors}
                theme={t}
                onOpenColumn={onOpenColumn}
              />
            }
            isAnimationActive={false}
          />
        </ScatterChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
};

CostQualityChart.propTypes = {
  matrix: PropTypes.object,
  pivot: PropTypes.object,
  columns: PropTypes.array,
  measure: PropTypes.object,
  colColors: PropTypes.object,
  paired: PropTypes.bool,
  baseCol: PropTypes.string,
  onOpenRows: PropTypes.func.isRequired,
  onOpenColumn: PropTypes.func.isRequired,
  title: PropTypes.node,
};

export default CostQualityChart;
