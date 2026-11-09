import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import {
  CartesianGrid,
  Customized,
  ErrorBar,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { runShortName, runSpokenName } from "src/sections/common/Runs/runLabel";
import { agentVersionOf } from "src/sections/common/Runs/provenanceModel";
import { runKindOf } from "src/sections/common/Runs/runKind";
import Term from "src/sections/common/ux/Term";
import { fmtPct, ptsBetween } from "src/sections/common/format/fmt";
import { BEST_VALUE_WITHIN_PTS } from "src/sections/common/Runs/runNumbers";
import { fmtDuration, fmtMoney, frontierSummary, pct1 } from "./matrixModel";
import { placeLabels, shortPointName } from "./paretoLabels";

export const PARETO_HEIGHT = 180;
export const PARETO_COMPACT_HEIGHT = 72;

const FOOTNOTE =
  "model tokens at list price (prices from Settings › Models) · harness runs include the simulated customer · sandbox compute not included.";

const xTick = (x) => (v) =>
  x === "time" ? fmtDuration(v * 1000) : fmtMoney(v);

/**
 * IT-R1 G2 P5: at most `max` log-axis ticks on a 1-2-5 ladder inside
 * [lo, hi] (never one tick per point: `$0.$0.$0.041` overlapped). Pure.
 */
export function logTicks(lo, hi, max = 5, min = 5) {
  if (!(lo > 0) || !(hi > lo)) return lo > 0 ? [lo] : [];
  const ladder = (marks) => {
    const out = [];
    for (
      let e = Math.floor(Math.log10(lo));
      e <= Math.ceil(Math.log10(hi));
      e += 1
    )
      marks.forEach((m) => {
        const v = Number((m * 10 ** e).toPrecision(3));
        if (v >= lo && v <= hi) out.push(v);
      });
    return out;
  };
  const thin = (list) => {
    if (list.length <= max) return list;
    const step = Math.ceil(list.length / max);
    return list.filter((_, i) => i % step === 0);
  };
  // 1-2-5 first; IT-R2 G2 P12: at least `min` ticks (3 ticks over a 50×
  // range read as empty), so densify with 1-1.5-2-3-5-7 when short.
  const base = thin(ladder([1, 2, 5]));
  if (base.length >= min) return base;
  const dense = ladder([1, 1.5, 2, 3, 5, 7]);
  if (dense.length <= max) return dense.length > base.length ? dense : base;
  // Pick `max` ticks spread evenly (by index) over the dense ladder.
  const picks = Array.from({ length: max }, (_, i) =>
    Math.round((i * (dense.length - 1)) / (max - 1)),
  );
  return [...new Set(picks)].map((i) => dense[i]);
}

/**
 * The `Best value` badge: the frontier point with the lowest cost per SOLVED
 * task (cost per try ÷ share solved) among those within BEST_VALUE_WITHIN_PTS
 * of the most solved point (IT-R2 G2 P4, the rule runNumbers.bestValueOf
 * applies everywhere else). Null without a frontier.
 */
export function bestValuePoint(frontier) {
  const pts = (frontier ?? []).filter((p) => p.quality > 0 && p.cost > 0);
  if (!pts.length) return null;
  const top = Math.max(...pts.map((p) => p.quality));
  let best = null;
  pts
    .filter((p) => ptsBetween(top, p.quality) <= BEST_VALUE_WITHIN_PTS)
    .forEach((p) => {
      const per = p.cost / p.quality;
      if (!best || per < best.per) best = { id: p.id, per };
    });
  return best;
}

/** A run point: filled on the frontier, hollow when dominated; label above. */
function PointShape({ cx, cy, payload, colors, highlighted, onPick }) {
  if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null;
  const color = colors[payload.id]?.solid ?? "#888";
  const hi = highlighted === payload.id;
  return (
    <g
      style={{ cursor: "pointer" }}
      onClick={(e) => onPick(payload.id, e.shiftKey)}
      role="button"
      aria-label={`${payload.label}${payload.dominatedBy ? " (dominated)" : ""}`}
    >
      <circle cx={cx} cy={cy} r={12} fill="transparent" />
      <circle
        cx={cx}
        cy={cy}
        r={hi ? 7 : 5.5}
        fill={payload.frontier ? color : "var(--pareto-bg)"}
        stroke={color}
        strokeWidth={payload.frontier ? 1 : 2}
      />
    </g>
  );
}

PointShape.propTypes = {
  cx: PropTypes.number,
  cy: PropTypes.number,
  payload: PropTypes.object,
  colors: PropTypes.object,
  highlighted: PropTypes.string,
  onPick: PropTypes.func,
};

/**
 * All point labels in one layer, placed without collisions (paretoLabels):
 * crowded labels move to a free slot with a leader line back to the point.
 */
function PointLabels({
  xAxisMap,
  yAxisMap,
  offset,
  points,
  highlighted,
  bestValueId,
}) {
  const xAxis = xAxisMap && Object.values(xAxisMap)[0];
  const yAxis = yAxisMap && Object.values(yAxisMap)[0];
  if (!xAxis?.scale || !yAxis?.scale || !offset) return null;
  const px = points.map((p) => ({
    id: p.id,
    label: p.text ?? p.label,
    x: xAxis.scale(p.cost),
    y: yAxis.scale(p.q100),
  }));
  const bv = px.find((p) => p.id === bestValueId);
  const pos = placeLabels(
    px,
    {
      left: offset.left,
      right: offset.left + offset.width,
      top: offset.top,
      bottom: offset.top + offset.height,
    },
    bv ? [{ x0: bv.x - 34, x1: bv.x + 34, y0: bv.y + 9, y1: bv.y + 25 }] : [],
  );
  return (
    <g data-testid="pareto-labels" style={{ pointerEvents: "none" }}>
      {bv ? (
        <g data-testid="pareto-best-value">
          <rect
            x={bv.x - 34}
            y={bv.y + 9}
            width={68}
            height={16}
            rx={8}
            fill="var(--pareto-good)"
          />
          <text
            x={bv.x}
            y={bv.y + 21}
            textAnchor="middle"
            fontSize={10.5}
            fontWeight={700}
            fill="#fff"
          >
            Best value
          </text>
        </g>
      ) : null}
      {px.map((p) => {
        const l = pos[p.id];
        if (!l) return null;
        return (
          <g key={p.id}>
            {l.leader && (
              <line
                x1={l.leader.x1}
                y1={l.leader.y1}
                x2={l.leader.x2}
                y2={l.leader.y2}
                stroke="currentColor"
                strokeOpacity={0.35}
                strokeWidth={1}
              />
            )}
            <text
              x={l.x}
              y={l.y}
              textAnchor={l.anchor}
              fontSize={11}
              fontWeight={highlighted === p.id ? 700 : 600}
              fill="currentColor"
            >
              {p.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}

PointLabels.propTypes = {
  bestValueId: PropTypes.string,
  xAxisMap: PropTypes.object,
  yAxisMap: PropTypes.object,
  offset: PropTypes.object,
  points: PropTypes.array,
  highlighted: PropTypes.string,
};

/** `Frontier: #2D most solved · #2C best value` + Show chart (Simple). */
function CompactStrip({ model, summary, extraSentence, x, onToggle }) {
  const tip = summary
    ? [
        `${summary.best.label}: ${fmtPct(summary.best.quality)} success, ${fmtMoney(summary.best.costPerRow)} per task.`,
        summary.value
          ? `${summary.value.label}: ${fmtPct(summary.value.quality)} success, ${fmtMoney(summary.value.costPerRow)} per task. Best value = the cheapest run within 10 pts of the top.`
          : null,
      ]
        .filter(Boolean)
        .join(" ")
    : "";
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      data-testid="pareto-compact"
      sx={{
        mx: 1,
        px: 1.5,
        minHeight: PARETO_COMPACT_HEIGHT,
        py: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
      }}
    >
      <Iconify
        icon="mdi:chart-scatter-plot"
        width={20}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {summary && (
          <CustomTooltip show size="small" placement="top" arrow title={tip}>
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              noWrap
              sx={{ width: "fit-content", maxWidth: "100%" }}
            >
              {summary.text}
            </Typography>
          </CustomTooltip>
        )}
        {(model.sentence || extraSentence) && (
          <Typography typography="s2" color="text.secondary" noWrap>
            {[model.sentence, extraSentence].filter(Boolean).join(" ")}
          </Typography>
        )}
      </Box>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        startIcon={<Iconify icon="mdi:chart-scatter-plot" width={16} />}
        onClick={onToggle}
        sx={{ typography: "s2", whiteSpace: "nowrap", flexShrink: 0 }}
      >
        {x === "time" ? "Show time chart" : "Show chart"}
      </Button>
    </Stack>
  );
}

CompactStrip.propTypes = {
  model: PropTypes.object,
  summary: PropTypes.object,
  extraSentence: PropTypes.string,
  x: PropTypes.string,
  onToggle: PropTypes.func,
};

/** True when the strip has at least two plottable runs (finite cost > 0). */
export const paretoPlottable = (model) =>
  (model?.points ?? []).filter(
    (p) => Number.isFinite(p.cost) && p.cost > 0 && Number.isFinite(p.quality),
  ).length >= 2;

/**
 * The collapsed Frontier section's one line (one view, A3-17): `#2D most
 * solved · the coding harness Code passes 25.4 pts more trials than #2B for 19× the cost
 * per task.` Pure.
 */
export function frontierLine(model, extraSentence) {
  const head = frontierSummary(model)?.text?.replace(/^Frontier:\s*/, "");
  return [head, [model?.sentence, extraSentence].filter(Boolean).join(" ")]
    .filter(Boolean)
    .join(" · ");
}

function TooltipCard({ active, payload, runsById, x, y }) {
  const p = payload?.[0]?.payload;
  if (!active || !p || p.frontierLine) return null;
  const run = runsById[p.id];
  const name = runShortName(run);
  const q = `${pct1(p.quality)} (${p.pass}/${p.n})`;
  const yName = y === "pass_hat_k" ? "Reliable on all tries " : "Success rate ";
  return (
    <Box
      sx={{
        p: 1,
        borderRadius: 0.5,
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        boxShadow: 2,
        maxWidth: 360,
      }}
    >
      <Typography typography="s2" fontWeight="fontWeightSemiBold">
        {[p.label, name].filter(Boolean).join(" ")}
      </Typography>
      <Typography typography="s2" sx={{ fontVariantNumeric: "tabular-nums" }}>
        {`${yName}${q} · ${fmtMoney(p.costPerRow)} per task · ${fmtDuration(p.p50)} per task (p50)`}
      </Typography>
      {p.ci && (
        <Typography typography="s3" color="text.secondary">
          {`95% interval ${pct1(p.ci.low)}–${pct1(p.ci.high)}`}
        </Typography>
      )}
      {p.tooltip && (
        <Typography typography="s2" color="warning.dark" sx={{ mt: 0.25 }}>
          {p.tooltip}
        </Typography>
      )}
    </Box>
  );
}

TooltipCard.propTypes = {
  active: PropTypes.bool,
  payload: PropTypes.array,
  runsById: PropTypes.object,
  x: PropTypes.string,
  y: PropTypes.string,
};

/**
 * Cost (or time) vs quality, one point per run (R2-SPEC §3.3.2): log x,
 * dashed frontier, hollow dominated points, 95% CI bars, the one sentence
 * and the cost footnote. Click a point to highlight its column; shift-click
 * to show only that run.
 */
const ParetoStrip = ({
  model,
  extraSentence,
  colors,
  runsById,
  open,
  onToggle,
  x,
  y,
  onAxis,
  highlighted,
  onPick,
  inSection = false,
  height = PARETO_HEIGHT,
  names = false,
}) => {
  const theme = useTheme();
  const [menu, setMenu] = useState(null);
  const pts = useMemo(
    () =>
      (model?.points ?? [])
        .filter(
          (p) =>
            Number.isFinite(p.cost) && p.cost > 0 && Number.isFinite(p.quality),
        )
        .map((p) => ({
          ...p,
          q100: p.quality * 100,
          // IT-R1 G2 P5: run names on the points (`#2D the coding harness Code`); a
          // label with no free slot is dropped (the tooltip still names it).
          // IT-R4 G6 P9: short names (`#2F v3.1`, `#2D the coding harness Code`).
          text: names
            ? shortPointName(p.label, {
                // Versions name agent / prompt runs; a commit sha or a
                // harness build is not a name.
                version: ["agent", "prompt"].includes(
                  runKindOf(runsById?.[p.id]),
                )
                  ? agentVersionOf(runsById?.[p.id] ?? {})?.version
                  : null,
                name: runSpokenName(runsById?.[p.id]),
              })
            : p.label,
          err: p.ci
            ? [
                Math.max(0, (p.quality - p.ci.low) * 100),
                Math.max(0, (p.ci.high - p.quality) * 100),
              ]
            : [0, 0],
        })),
    [model, names, runsById],
  );
  const frontier = useMemo(
    () =>
      (model?.frontier ?? [])
        .filter((p) => Number.isFinite(p.cost) && p.cost > 0)
        .map((p) => ({ ...p, q100: p.quality * 100, frontierLine: true })),
    [model],
  );
  const summary = useMemo(() => frontierSummary(model), [model]);
  if (!model || pts.length < 2) return null;
  if (!open && !inSection)
    return (
      <CompactStrip
        model={model}
        summary={summary}
        extraSentence={extraSentence}
        x={x}
        onToggle={onToggle}
      />
    );

  const xs = pts.map((p) => p.cost);
  const lo = Math.min(...xs);
  const hi = Math.max(...xs);
  const xDomain = [lo / 1.6, hi * 1.6];
  const ys = pts.flatMap((p) => [
    p.q100 - (p.err?.[0] ?? 0),
    p.q100 + (p.err?.[1] ?? 0),
  ]);
  const yMin = Math.max(0, Math.floor((Math.min(...ys) - 5) / 10) * 10);
  const yMax = Math.min(100, Math.ceil((Math.max(...ys) + 2) / 10) * 10);
  const tickColor = theme.palette.text.secondary;
  const step = yMax - yMin > 50 ? 20 : 10;
  const yTicks = [];
  for (let v = yMin; v <= yMax; v += step) yTicks.push(v);
  const xTicks = logTicks(xDomain[0], xDomain[1], 5);
  const bestValue = bestValuePoint(model.frontier);

  return (
    <Box
      sx={{
        mx: inSection ? 0 : 1,
        px: 1.5,
        pt: 1,
        pb: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        color: "text.primary",
        "--pareto-bg": theme.palette.background.paper,
        "--pareto-good": theme.palette.success.main,
      }}
      data-testid="pareto-strip"
    >
      <Stack direction="row" alignItems="flex-start" gap={1}>
        <Box sx={{ flex: 1, minWidth: 0, height }}>
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 14, right: 36, bottom: 4, left: 0 }}>
              <CartesianGrid
                stroke={theme.palette.divider}
                strokeDasharray="2 3"
              />
              <XAxis
                type="number"
                dataKey="cost"
                scale="log"
                domain={xDomain}
                ticks={xTicks}
                interval={0}
                tickFormatter={xTick(x)}
                tick={{ fontSize: 11, fill: tickColor }}
                axisLine={{ stroke: theme.palette.divider }}
                tickLine={false}
                allowDataOverflow
                name={x === "time" ? "Time per task" : "Cost per task"}
              />
              <YAxis
                type="number"
                dataKey="q100"
                domain={[yMin, yMax]}
                ticks={yTicks}
                tickFormatter={(v) => `${v}%`}
                tick={{ fontSize: 11, fill: tickColor }}
                axisLine={false}
                tickLine={false}
                width={44}
              />
              <Tooltip
                cursor={false}
                content={<TooltipCard runsById={runsById} x={x} y={y} />}
                isAnimationActive={false}
              />
              <Scatter
                data={frontier}
                line={{
                  stroke: theme.palette.text.disabled,
                  strokeDasharray: "5 4",
                  strokeWidth: 1.25,
                }}
                shape={() => null}
                isAnimationActive={false}
                legendType="none"
                tooltipType="none"
              />
              <Scatter
                data={pts}
                isAnimationActive={false}
                shape={(props) => (
                  <PointShape
                    {...props}
                    colors={colors}
                    highlighted={highlighted}
                    onPick={onPick}
                  />
                )}
              >
                <ErrorBar
                  dataKey="err"
                  direction="y"
                  width={0}
                  stroke={theme.palette.text.disabled}
                  strokeWidth={1.25}
                />
              </Scatter>
              <Customized
                component={(props) => (
                  <PointLabels
                    {...props}
                    points={pts}
                    highlighted={highlighted}
                    bestValueId={bestValue?.id}
                  />
                )}
              />
            </ScatterChart>
          </ResponsiveContainer>
        </Box>
        <Stack alignItems="flex-end" gap={0.5} sx={{ flexShrink: 0 }}>
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title="Chart options"
          >
            <IconButton
              size="small"
              aria-label="Chart options"
              onClick={(e) => setMenu(e.currentTarget)}
              sx={{ p: 0.5 }}
            >
              <Iconify icon="mdi:dots-horizontal" width={18} />
            </IconButton>
          </CustomTooltip>
          <Button
            size="small"
            color="inherit"
            onClick={onToggle}
            sx={{
              typography: "s2",
              color: "text.secondary",
              whiteSpace: "nowrap",
            }}
          >
            Hide chart
          </Button>
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="pareto-caption"
            sx={{ maxWidth: 150, textAlign: "right", mt: 0.5 }}
          >
            {x === "time"
              ? "Up-left is better: more tasks solved, less time"
              : "Up-left is better: more tasks solved, lower cost"}
          </Typography>
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="pareto-log-note"
            sx={{ maxWidth: 150, textAlign: "right" }}
          >
            {`${x === "time" ? "Time" : "Cost"} axis is a log scale: equal distances are equal ratios, not equal amounts.`}
          </Typography>
          {pts.some((p) => p.ci) ? (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="pareto-bars-legend"
              sx={{ maxWidth: 150, textAlign: "right" }}
            >
              Bars = the 95% range of the success rate across the tries
            </Typography>
          ) : null}
          {bestValue && x !== "time" ? (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="pareto-best-value-text"
              sx={{ maxWidth: 150, textAlign: "right" }}
            >
              <Term k="bestValue">Best value</Term>
              {`: ${
                pts.find((p) => p.id === bestValue.id)?.text ?? ""
              } · ${fmtMoney(bestValue.per)} per solved task`}
            </Typography>
          ) : null}
        </Stack>
        <Menu
          anchorEl={menu}
          open={Boolean(menu)}
          onClose={() => setMenu(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
        >
          <MenuItem disabled sx={{ typography: "s3", opacity: "1 !important" }}>
            X axis
          </MenuItem>
          {[
            ["cost", "Cost"],
            ["time", "Time"],
          ].map(([v, label]) => (
            <MenuItem
              key={v}
              selected={x === v}
              onClick={() => {
                onAxis({ x: v });
                setMenu(null);
              }}
              sx={{ typography: "s2", pl: 3 }}
            >
              {label}
            </MenuItem>
          ))}
          <Divider />
          <MenuItem disabled sx={{ typography: "s3", opacity: "1 !important" }}>
            Y axis
          </MenuItem>
          {[
            ["pass_at_1", "Success rate (pass@1)"],
            ["pass_hat_k", "Reliable on all tries (pass^k)"],
          ].map(([v, label]) => (
            <MenuItem
              key={v}
              selected={y === v}
              onClick={() => {
                onAxis({ y: v });
                setMenu(null);
              }}
              sx={{ typography: "s2", pl: 3 }}
            >
              <ListItemText primaryTypographyProps={{ typography: "s2" }}>
                {label}
              </ListItemText>
            </MenuItem>
          ))}
          <CustomTooltip
            show
            size="small"
            placement="left"
            arrow
            title="Pick an eval on the Summary tab's Workbench to plot it here."
          >
            <span>
              <MenuItem disabled sx={{ typography: "s2", pl: 3 }}>
                <ListItemIcon>
                  <Iconify icon="mdi:lock-outline" width={14} />
                </ListItemIcon>
                Any eval
              </MenuItem>
            </span>
          </CustomTooltip>
        </Menu>
      </Stack>
      {(model.sentence || extraSentence) && (
        <Typography typography="s1" sx={{ mt: 0.5 }}>
          {[model.sentence, extraSentence].filter(Boolean).join(" ")}
        </Typography>
      )}
      <Typography typography="s3" color="text.disabled" sx={{ mt: 0.25 }}>
        <Term k="costPerTask">Cost per task</Term>
        {` = ${FOOTNOTE}`}
      </Typography>
    </Box>
  );
};

ParetoStrip.propTypes = {
  height: PropTypes.number,
  names: PropTypes.bool,
  model: PropTypes.object,
  extraSentence: PropTypes.string,
  colors: PropTypes.object,
  runsById: PropTypes.object,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  inSection: PropTypes.bool,
  x: PropTypes.string,
  y: PropTypes.string,
  onAxis: PropTypes.func,
  highlighted: PropTypes.string,
  onPick: PropTypes.func,
};

export default ParetoStrip;
