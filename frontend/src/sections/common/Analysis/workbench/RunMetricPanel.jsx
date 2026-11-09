// Explore › Pivot & charts with Measure = Run metrics ▸ <name> (W5-4 B2):
// a run metric is one number per run, so the panel draws it by run, as a
// trend over the runs in the order they ran, or against cost per row with
// the Pareto frontier (runStats.pareto, by the metric's own direction).
// Values come from GET …/derived-metrics/?runs= (w5DerivedMetrics.js over
// R1 aggregates); cost per row is the same R1 aggregate the Summary shows.
import React from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import {
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { pareto } from "src/sections/common/Analysis/runStats";
import {
  fmtMetric,
  partsLine,
} from "src/sections/common/DerivedMetrics/formulaModel";
import { useDerivedMetrics } from "src/sections/common/DerivedMetrics/useDerivedMetrics";
import { SEGMENTED_SX } from "src/sections/experiment-detail/ExperimentBarRightSection/ExperimentBarSummaryRightSection";
import RunMetricChart from "./charts/RunMetricChart";
import {
  ChartFrame,
  RunLegend,
  TipBox,
  colColor,
  useChartTheme,
} from "./charts/chartKit";
import { fmtMoney, fmtTick } from "./workbenchFormat";
import { RUN_METRIC_PREFIX } from "./runMetricModel";

const CHARTS = [
  { key: "bars", label: "Bars by run" },
  { key: "trend", label: "Trend" },
  { key: "cost", label: "Cost vs quality" },
];

const costOf = (run) => run?.metrics?.aggregates?.["ops:cost"]?.avg ?? null;
const timeOf = (run) => Date.parse(run?.created_at ?? "") || 0;

export default function RunMetricPanel({
  datasetId,
  runs,
  metricKey,
  colColors,
}) {
  const t = useChartTheme();
  const [sp, setSp] = useSearchParams();
  const chart = CHARTS.some((c) => c.key === sp.get("rmchart"))
    ? sp.get("rmchart")
    : "bars";
  const runIds = runs.map((r) => r.run_id);
  const q = useDerivedMetrics(datasetId, runIds);
  const metric = (q.data?.metrics ?? []).find(
    (m) => `${RUN_METRIC_PREFIX}${m.id}` === metricKey,
  );
  if (q.isLoading)
    return (
      <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
        Loading run metrics…
      </Typography>
    );
  if (!metric)
    return (
      <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
        This run metric is no longer on the dataset.
      </Typography>
    );
  const values = q.data?.values ?? {};
  const lower = metric.higher_is_better === false;
  const colorOf = (run) =>
    colColor(colColors, { key: run.run_id, runIds: [run.run_id] }, t);
  const points = runs
    .map((r) => ({
      id: r.run_id,
      label: r.label,
      at: timeOf(r),
      value: values[r.run_id]?.[metric.id]?.value ?? null,
      parts: values[r.run_id]?.[metric.id]?.parts ?? {},
      cost: costOf(r),
      color: colorOf(r),
    }))
    .filter((p) => p.value != null && Number.isFinite(p.value));
  const tip = (p) => [
    `${p.label} · ${metric.name}`,
    partsLine(metric.formula, p, metric.format),
    p.cost != null ? `Cost per row ${fmtMoney(p.cost)}` : null,
  ];
  const toggle = (
    <ToggleButtonGroup
      exclusive
      size="small"
      value={chart}
      onChange={(_, v) => {
        if (!v) return;
        const next = new URLSearchParams(sp);
        if (v === "bars") next.delete("rmchart");
        else next.set("rmchart", v);
        setSp(next, { replace: true });
      }}
      sx={SEGMENTED_SX}
    >
      {CHARTS.map((c) => (
        <ToggleButton key={c.key} value={c.key} aria-label={c.label}>
          {c.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
  let body;
  if (chart === "bars") {
    body = (
      <RunMetricChart
        metric={metric}
        values={values}
        columns={runs.map((r) => ({
          key: r.run_id,
          label: r.label,
          runIds: [r.run_id],
        }))}
        colColors={Object.fromEntries(
          runs.map((r) => [r.run_id, { solid: colorOf(r) }]),
        )}
        title={`${metric.name} by run`}
      />
    );
  } else if (chart === "trend") {
    const data = [...points].sort(
      (a, b) => a.at - b.at || a.label.localeCompare(b.label),
    );
    body = (
      <ChartFrame
        title={`${metric.name} over the runs, in the order they ran`}
        empty={data.length < 2}
        footer={
          <Typography
            typography="s3"
            color="text.secondary"
            component="p"
            data-testid="run-metric-trend"
            data-order={JSON.stringify(data.map((d) => [d.id, d.value]))}
            sx={{ mt: 0.5 }}
          >
            {data
              .map((d) => `${d.label} ${fmtMetric(d.value, metric.format)}`)
              .join(" → ")}
          </Typography>
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={data}
            margin={{ top: 16, right: 16, bottom: 8, left: 4 }}
          >
            <CartesianGrid vertical={false} stroke={t.grid} />
            <XAxis
              dataKey="label"
              tick={t.tick}
              tickLine={false}
              axisLine={{ stroke: t.grid }}
            />
            <YAxis
              tick={t.tick}
              tickLine={false}
              axisLine={false}
              width={64}
              domain={["auto", "auto"]}
              tickFormatter={(v) => fmtMetric(v, metric.format)}
            />
            <Tooltip
              content={({ active, payload }) =>
                active && payload?.[0] ? (
                  <TipBox lines={tip(payload[0].payload)} />
                ) : null
              }
            />
            <Line
              type="linear"
              dataKey="value"
              stroke={t.axis}
              strokeWidth={1.5}
              isAnimationActive={false}
              dot={({ cx, cy, payload }) => (
                <circle
                  key={payload.id}
                  cx={cx}
                  cy={cy}
                  r={5}
                  fill={payload.color}
                />
              )}
            />
          </LineChart>
        </ResponsiveContainer>
      </ChartFrame>
    );
  } else {
    const withCost = points.filter(
      (p) => p.cost != null && Number.isFinite(p.cost),
    );
    const front = pareto(
      withCost.map((p) => ({
        id: p.id,
        cost: p.cost,
        quality: lower ? -p.value : p.value,
      })),
    );
    const byId = Object.fromEntries(front.map((f) => [f.id, f]));
    const labelOf = (id) => points.find((p) => p.id === id)?.label ?? id;
    body = (
      <ChartFrame
        title={`${metric.name} vs cost per row`}
        empty={!withCost.length}
        legend={
          <RunLegend
            items={withCost.map((p) => ({
              key: p.id,
              label: p.label,
              color: p.color,
            }))}
          />
        }
        footer={
          <Stack direction="row" gap={1.5} flexWrap="wrap" sx={{ mt: 0.5 }}>
            {withCost.map((p) => (
              <Typography
                key={p.id}
                typography="s3"
                color="text.secondary"
                data-testid="run-metric-pareto"
                data-run={p.id}
                data-frontier={byId[p.id]?.frontier ? "1" : "0"}
                data-dominated-by={byId[p.id]?.dominated_by ?? ""}
              >
                {byId[p.id]?.frontier
                  ? `${p.label} · On the frontier`
                  : `${p.label} · Dominated by ${labelOf(byId[p.id]?.dominated_by)}`}
              </Typography>
            ))}
            <Typography typography="s3" color="text.secondary">
              {`Frontier: no run is both cheaper and ${lower ? "lower" : "higher"} on ${metric.name}.`}
            </Typography>
          </Stack>
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 16, right: 16, bottom: 8, left: 4 }}>
            <CartesianGrid stroke={t.grid} />
            <XAxis
              type="number"
              dataKey="cost"
              name="Cost per row"
              tick={t.tick}
              tickLine={false}
              domain={["auto", "auto"]}
              tickFormatter={(v) => fmtTick({ unit: "usd" }, v)}
            />
            <YAxis
              type="number"
              dataKey="value"
              name={metric.name}
              tick={t.tick}
              tickLine={false}
              width={64}
              domain={["auto", "auto"]}
              tickFormatter={(v) => fmtMetric(v, metric.format)}
            />
            <ZAxis range={[90, 90]} />
            <Tooltip
              content={({ active, payload }) =>
                active && payload?.[0] ? (
                  <TipBox lines={tip(payload[0].payload)} />
                ) : null
              }
            />
            {withCost.map((p) => (
              <Scatter
                key={p.id}
                data={[p]}
                fill={p.color}
                isAnimationActive={false}
              />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </ChartFrame>
    );
  }
  return (
    <Stack gap={1} data-testid="run-metric-panel">
      <Stack direction="row" alignItems="center" gap={1.5}>
        {toggle}
        <Typography typography="s2" color="text.secondary">
          A run metric is one number per run; group by does not split it.
        </Typography>
      </Stack>
      {body}
    </Stack>
  );
}

RunMetricPanel.propTypes = {
  datasetId: PropTypes.string.isRequired,
  runs: PropTypes.array.isRequired,
  metricKey: PropTypes.string.isRequired,
  colColors: PropTypes.object,
};
