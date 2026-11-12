import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import {
  Bar,
  BarChart,
  Brush,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { distributionSeries } from "../workbenchSeries";
import { fmtValue } from "../workbenchFormat";
import {
  ChartFrame,
  RunLegend,
  TipBox,
  colColor,
  useChartTheme,
} from "./chartKit";

/**
 * Distribution (R2-SPEC §6.3.3 #1): binary measures → rows by per-row pass
 * rate (0, 1/3, 2/3, 1); scores and numbers → 20 bins per run with the
 * pass threshold (`Pass ≥ 0.70`) and `#2A 38 below`. A bar opens its rows;
 * the brush picks a range to open.
 */
const DistributionChart = ({
  matrix,
  columns,
  measure,
  colColors,
  onOpenRows,
  title,
}) => {
  const t = useChartTheme();
  const series = useMemo(
    () => distributionSeries(matrix, columns, measure),
    [matrix, columns, measure],
  );
  const [range, setRange] = useState(null);
  const levels = series.kind === "levels";
  const data = series.bins.map((b) => ({
    label: b.label,
    ...b.counts,
    bin: b,
  }));
  const empty = !series.bins.some((b) => Object.keys(b.counts).length);
  const unit = levels ? "rows" : "attempts";
  const legend = columns.map((c) => ({
    key: c.key,
    label: c.label,
    color: colColor(colColors, c, t),
    note: series.threshold != null ? `${series.below[c.key] ?? 0} below` : null,
  }));
  const thresholdLabel =
    series.threshold != null
      ? `Pass ≥ ${measure?.kind === "score" ? series.threshold.toFixed(2) : fmtValue(measure, series.threshold)}`
      : null;
  const thresholdBin =
    series.threshold != null
      ? series.bins.find(
          (b) => series.threshold >= b.from && series.threshold < b.to,
        )?.label
      : null;
  const rangeRows = () => {
    if (!range) return [];
    const ids = new Set();
    series.bins
      .slice(range[0], range[1] + 1)
      .forEach((b) =>
        Object.values(b.rows).forEach((list) =>
          list.forEach((id) => ids.add(id)),
        ),
      );
    return [...ids];
  };
  const rangeText = range
    ? `${series.bins[range[0]].label}–${levels ? series.bins[range[1]].label : fmtValue(measure, series.bins[range[1]].to, { precise: true })}`
    : null;
  return (
    <ChartFrame
      title={title}
      empty={empty}
      legend={<RunLegend items={legend} />}
      footer={
        !empty && !levels ? (
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ minHeight: 30 }}
          >
            <Typography typography="s3" color="text.secondary">
              {range
                ? `Range ${rangeText}`
                : "Drag the handles under the chart to pick a range."}
            </Typography>
            {range && (
              <Button
                size="small"
                onClick={() =>
                  onOpenRows(
                    rangeRows(),
                    `${measure?.short ?? "Value"} ${rangeText}`,
                  )
                }
              >
                View {rangeRows().length.toLocaleString("en-US")} rows
              </Button>
            )}
          </Stack>
        ) : null
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          margin={{ top: 16, right: 8, bottom: 4, left: 4 }}
          barGap={2}
          barCategoryGap={levels ? "24%" : "8%"}
        >
          <CartesianGrid vertical={false} stroke={t.grid} />
          <XAxis
            dataKey="label"
            tick={t.tick}
            tickLine={false}
            axisLine={{ stroke: t.grid }}
            interval={levels ? 0 : 3}
            label={
              levels
                ? {
                    value: "Per-row pass rate (passed trials / trials)",
                    position: "insideBottom",
                    offset: -2,
                    fill: t.axis,
                    fontSize: 12,
                  }
                : undefined
            }
            height={levels ? 40 : 24}
          />
          <YAxis
            tick={t.tick}
            tickLine={false}
            axisLine={false}
            width={40}
            allowDecimals={false}
            label={{
              value: unit === "rows" ? "Rows" : "Attempts",
              angle: -90,
              position: "insideLeft",
              fill: t.axis,
              fontSize: 12,
              dy: 30,
            }}
          />
          <Tooltip
            cursor={{ fill: t.grid, opacity: 0.4 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const b = payload[0].payload.bin;
              return (
                <TipBox
                  lines={[
                    levels
                      ? `Passed ${b.label} trials`
                      : `${fmtValue(measure, b.from, { precise: true })}–${fmtValue(measure, b.to, { precise: true })}`,
                    ...columns.map(
                      (c) =>
                        `${c.label} · ${(b.counts[c.key] ?? 0).toLocaleString("en-US")} ${unit}`,
                    ),
                    "Click a bar to open its rows",
                  ]}
                />
              );
            }}
          />
          {thresholdBin && (
            <ReferenceLine
              x={thresholdBin}
              stroke={t.ink}
              strokeDasharray="4 3"
              label={{
                value: thresholdLabel,
                position: "top",
                fill: t.ink,
                fontSize: 12,
              }}
            />
          )}
          {columns.map((c) => (
            <Bar
              key={c.key}
              dataKey={c.key}
              name={c.label}
              fill={colColor(colColors, c, t)}
              radius={[4, 4, 0, 0]}
              maxBarSize={levels ? 28 : 14}
              cursor="pointer"
              isAnimationActive={false}
              onClick={(d) => {
                const ids = d.bin.rows[c.key] ?? [];
                if (ids.length)
                  onOpenRows(
                    ids,
                    levels
                      ? `${c.label} passed ${d.bin.label} trials`
                      : `${c.label} · ${measure?.short} ${d.bin.label}`,
                  );
              }}
            />
          ))}
          {!levels && (
            <Brush
              dataKey="label"
              height={18}
              stroke={t.axis}
              fill={t.surface}
              travellerWidth={8}
              onChange={(r) => {
                if (r?.startIndex == null) return;
                setRange(
                  r.startIndex === 0 && r.endIndex === series.bins.length - 1
                    ? null
                    : [r.startIndex, r.endIndex],
                );
              }}
            />
          )}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
};

DistributionChart.propTypes = {
  matrix: PropTypes.object,
  columns: PropTypes.array,
  measure: PropTypes.object,
  colColors: PropTypes.object,
  onOpenRows: PropTypes.func.isRequired,
  title: PropTypes.node,
};

export default DistributionChart;
