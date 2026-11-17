// Explore board · Run metrics ▸ <name> (W5-4 B2 on the W5-5 B4 board): one
// bar per run with the run metric's value, its parts on hover, best and
// worst named by the metric's direction. The values come from the server
// (GET …/derived-metrics/?runs=, w5DerivedMetrics.js over R1 aggregates),
// the same numbers as Explore › Run metrics; nothing is computed here.
import React from "react";
import PropTypes from "prop-types";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Typography } from "@mui/material";
import {
  fmtMetric,
  partsLine,
  rankRuns,
} from "src/sections/common/DerivedMetrics/formulaModel";
import { ChartFrame, TipBox, colColor, useChartTheme } from "./chartKit";

export default function RunMetricChart({
  metric,
  values,
  columns,
  colColors,
  title,
}) {
  const t = useChartTheme();
  const cols = columns.filter((c) => c.runIds.length === 1);
  const data = cols
    .map((c) => {
      const v = values?.[c.runIds[0]]?.[metric.id];
      return {
        key: c.key,
        run: c.label,
        runId: c.runIds[0],
        value: v?.value ?? null,
        parts: v?.parts ?? {},
        color: colColor(colColors, c, t),
      };
    })
    .filter((d) => d.value != null && Number.isFinite(d.value));
  const { best, worst } = rankRuns(values, metric);
  const name = (id) => data.find((d) => d.runId === id)?.run;
  const neg = data.some((d) => d.value < 0);
  return (
    <ChartFrame
      title={title}
      empty={!data.length}
      footer={
        <Typography
          typography="s3"
          color="text.secondary"
          component="p"
          data-testid="run-metric-chart-caption"
          data-values={JSON.stringify(
            Object.fromEntries(data.map((d) => [d.runId, d.value])),
          )}
          sx={{ mt: 0.5 }}
        >
          {`${metric.name} = ${metric.formula} · ${
            metric.higher_is_better === false ? "lower" : "higher"
          } is better`}
          {best && worst && best !== worst
            ? ` · best ${name(best)}, worst ${name(worst)}`
            : ""}
        </Typography>
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          margin={{ top: 20, right: 8, bottom: 8, left: 4 }}
          barCategoryGap="30%"
        >
          <CartesianGrid vertical={false} stroke={t.grid} />
          <XAxis
            dataKey="run"
            tick={t.tick}
            tickLine={false}
            axisLine={{ stroke: t.grid }}
          />
          <YAxis
            tick={t.tick}
            tickLine={false}
            axisLine={false}
            width={64}
            domain={neg ? ["auto", "auto"] : [0, "auto"]}
            tickFormatter={(v) => fmtMetric(v, metric.format)}
          />
          {neg && <ReferenceLine y={0} stroke={t.axis} />}
          <Tooltip
            cursor={{ fill: t.grid }}
            content={({ active, payload }) =>
              active && payload?.[0] ? (
                <TipBox
                  lines={[
                    `${payload[0].payload.run} · ${metric.name}`,
                    partsLine(
                      metric.formula,
                      payload[0].payload,
                      metric.format,
                    ),
                  ]}
                />
              ) : null
            }
          />
          <Bar dataKey="value" isAnimationActive={false} radius={[2, 2, 0, 0]}>
            {data.map((d) => (
              <Cell key={d.key} fill={d.color} />
            ))}
            <LabelList
              dataKey="value"
              position="top"
              formatter={(v) => fmtMetric(v, metric.format)}
              style={{ fontSize: 12, fill: t.ink }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

RunMetricChart.propTypes = {
  metric: PropTypes.object.isRequired,
  values: PropTypes.object,
  columns: PropTypes.array.isRequired,
  colColors: PropTypes.object,
  title: PropTypes.string,
};
