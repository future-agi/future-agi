import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { EMPTY, fmtPct, fmtPts, NUM_SX } from "src/sections/common/format/fmt";
import { ptsDelta } from "src/sections/common/Runs/runNumbers";

const RINGS = [0.25, 0.5, 0.75, 1];
const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/**
 * `86.6%` and, vs the baseline, `+9.3 pts` (difference of the shown values).
 * A metric may bring its own `fmt(v)` / `fmtDelta(v, base)` (scores: `0.74`).
 */
function valueBits(v, baseV, isBase, m) {
  if (!isNum(v)) return { text: EMPTY, delta: null };
  const text = m?.fmt ? m.fmt(v) : fmtPct(v);
  if (isBase || !isNum(baseV)) return { text, delta: null };
  return {
    text,
    delta: m?.fmtDelta ? m.fmtDelta(v, baseV) : fmtPts(ptsDelta(v, baseV)),
  };
}

/** Plain-text summary for screen readers (`role="img"`, AS#44). */
export function radarSummary(series, metrics) {
  return metrics
    .map(
      (m) =>
        `${m.name}: ${series
          .map(
            (s) =>
              `${s.label} ${valueBits(s.values[m.key], null, true, m).text}`,
          )
          .join(", ")}`,
    )
    .join(". ");
}

function Radar({ series, metrics, hover, setHover, baseline }) {
  const size = 340;
  const c = size / 2;
  const r = c - 56;
  const angle = (i) => (Math.PI * 2 * i) / metrics.length - Math.PI / 2;
  const pt = (i, v) => [
    c + Math.cos(angle(i)) * r * v,
    c + Math.sin(angle(i)) * r * v,
  ];
  return (
    <Box
      component="svg"
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={radarSummary(series, metrics)}
      sx={{ width: "100%", maxWidth: 380, height: "auto", flexShrink: 0 }}
    >
      {RINGS.map((ring) => (
        <g key={ring}>
          <polygon
            points={metrics.map((_, i) => pt(i, ring).join(",")).join(" ")}
            fill="none"
            strokeWidth={1}
            stroke="currentColor"
            opacity={0.25}
          />
          <text
            x={c + 3}
            y={c - r * ring + 10}
            fontSize={9}
            fill="currentColor"
            opacity={0.55}
          >
            {Math.round(ring * 100)}
          </text>
        </g>
      ))}
      {metrics.map((m, i) => {
        const [x, y] = pt(i, 1);
        const [lx, ly] = pt(i, 1.18);
        return (
          <g key={m.key}>
            <line
              x1={c}
              y1={c}
              x2={x}
              y2={y}
              stroke="currentColor"
              opacity={0.2}
            />
            <text
              x={lx}
              y={ly}
              fontSize={11}
              fill="currentColor"
              textAnchor={
                Math.abs(lx - c) < 8 ? "middle" : lx > c ? "start" : "end"
              }
              dominantBaseline="middle"
            >
              {m.name.length > 22 ? `${m.name.slice(0, 21)}…` : m.name}
            </text>
          </g>
        );
      })}
      {series.map((s) => {
        const dim = hover && hover !== s.id;
        const pts = metrics.map((m, i) => pt(i, s.values[m.key] ?? 0));
        return (
          <g
            key={s.id}
            opacity={dim ? 0.15 : 1}
            onMouseEnter={() => setHover(s.id)}
            onMouseLeave={() => setHover(null)}
          >
            <polygon
              points={pts.map((p) => p.join(",")).join(" ")}
              fill={s.color}
              fillOpacity={hover === s.id ? 0.22 : 0.08}
              stroke={s.color}
              strokeWidth={hover === s.id ? 2.5 : 1.75}
            />
            {pts.map(([x, y], i) => {
              const m = metrics[i];
              const v = valueBits(
                s.values[m.key],
                baseline?.values[m.key],
                s.id === baseline?.id,
                m,
              );
              return (
                <CustomTooltip
                  key={m.key}
                  show
                  size="small"
                  placement="top"
                  title={`${m.name} · ${s.label} ${v.text}${v.delta ? ` (${v.delta} vs ${baseline.label})` : ""}${isNum(s.n?.[m.key]) ? `, n=${s.n[m.key]}` : ""}`}
                >
                  <circle cx={x} cy={y} r={4} fill={s.color} />
                </CustomTooltip>
              );
            })}
          </g>
        );
      })}
    </Box>
  );
}
Radar.propTypes = {
  series: PropTypes.array,
  metrics: PropTypes.array,
  hover: PropTypes.string,
  setHover: PropTypes.func,
  baseline: PropTypes.object,
};

/** Fewer than 4 metrics: grouped bars read better than a triangle (AS#4). */
function Bars({ series, metrics, hover, setHover, baseline }) {
  return (
    <Stack
      gap={2}
      role="img"
      aria-label={radarSummary(series, metrics)}
      sx={{ flex: 1, minWidth: 0, maxWidth: 560 }}
    >
      {metrics.map((m) => (
        <Stack key={m.key} gap={0.5}>
          <Typography typography="s2" fontWeight="fontWeightSemiBold">
            {m.name}
          </Typography>
          {series.map((s) => {
            const v = s.values[m.key];
            const bits = valueBits(
              v,
              baseline?.values[m.key],
              s.id === baseline?.id,
              m,
            );
            return (
              <CustomTooltip
                key={s.id}
                show
                size="small"
                placement="top"
                title={`${m.name} · ${s.label} ${bits.text}${bits.delta ? ` (${bits.delta} vs ${baseline.label})` : ""}`}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={1}
                  onMouseEnter={() => setHover(s.id)}
                  onMouseLeave={() => setHover(null)}
                  sx={{ opacity: hover && hover !== s.id ? 0.35 : 1 }}
                >
                  <Typography typography="s3" sx={{ width: 36, flexShrink: 0 }}>
                    {s.label}
                  </Typography>
                  <Box
                    sx={{
                      flex: 1,
                      height: 10,
                      borderRadius: 0.5,
                      bgcolor: "action.hover",
                      position: "relative",
                    }}
                  >
                    <Box
                      sx={{
                        position: "absolute",
                        inset: 0,
                        width: `${Math.max(0, Math.min(1, v ?? 0)) * 100}%`,
                        bgcolor: s.color,
                        borderRadius: 0.5,
                      }}
                    />
                  </Box>
                  <Typography typography="s3" sx={{ ...NUM_SX, width: 44 }}>
                    {bits.text}
                  </Typography>
                </Stack>
              </CustomTooltip>
            );
          })}
        </Stack>
      ))}
    </Stack>
  );
}
Bars.propTypes = Radar.propTypes;

/**
 * Eval profile of every run (AS#2–4, SVP-4.9; Advanced only): a radar with
 * ring labels 25/50/75/100 and a hover card per vertex, or grouped bars
 * when there are fewer than 4 evals. Legend: one block per eval, one row
 * per run `#2B Support Agent v3  86.6%  +9.3 pts` (one formatter); hovering
 * a row highlights that run's polygon.
 *
 * @param {{id, label, name, color, values: Record<string, number>, n?: Record<string, number>}[]} props.series
 *   values are 0–1
 * @param {{key, name}[]} props.metrics
 * @param {string} [props.baselineId]
 */
const SpiderChartExperiment = ({ series = [], metrics = [], baselineId }) => {
  const [hover, setHover] = useState(null);
  const baseline = series.find((s) => s.id === baselineId) ?? null;
  const Chart = metrics.length >= 4 ? Radar : Bars;
  return (
    <Stack
      direction={{ xs: "column", lg: "row" }}
      gap={3}
      alignItems="flex-start"
      sx={{ color: "text.secondary" }}
    >
      <Chart
        series={series}
        metrics={metrics}
        hover={hover}
        setHover={setHover}
        baseline={baseline}
      />
      <Stack gap={1.5} sx={{ flex: 1, minWidth: 260 }}>
        {metrics.map((m) => (
          <Stack key={m.key} gap={0.25}>
            <Typography
              typography="s2"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
            >
              {m.name}
            </Typography>
            {series.map((s) => {
              const bits = valueBits(
                s.values[m.key],
                baseline?.values[m.key],
                s.id === baseline?.id,
                m,
              );
              return (
                <Stack
                  key={s.id}
                  direction="row"
                  alignItems="center"
                  gap={1}
                  onMouseEnter={() => setHover(s.id)}
                  onMouseLeave={() => setHover(null)}
                  sx={{
                    px: 0.5,
                    borderRadius: 0.5,
                    bgcolor: hover === s.id ? "action.hover" : "transparent",
                    cursor: "default",
                  }}
                >
                  <Box
                    sx={{
                      width: 10,
                      height: 10,
                      borderRadius: "4px",
                      bgcolor: s.color,
                      flexShrink: 0,
                    }}
                  />
                  <Typography
                    typography="s2"
                    fontWeight="fontWeightSemiBold"
                    color="text.primary"
                    sx={{ flexShrink: 0 }}
                  >
                    {s.label}
                  </Typography>
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    noWrap
                    sx={{ flex: 1, minWidth: 0 }}
                  >
                    {s.name}
                  </Typography>
                  <Typography typography="s2" color="text.primary" sx={NUM_SX}>
                    {bits.text}
                  </Typography>
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    sx={{ ...NUM_SX, width: 64 }}
                  >
                    {s.id === baseline?.id ? "baseline" : bits.delta ?? ""}
                  </Typography>
                </Stack>
              );
            })}
          </Stack>
        ))}
      </Stack>
    </Stack>
  );
};

SpiderChartExperiment.propTypes = {
  series: PropTypes.array,
  metrics: PropTypes.array,
  baselineId: PropTypes.string,
};

export default SpiderChartExperiment;
