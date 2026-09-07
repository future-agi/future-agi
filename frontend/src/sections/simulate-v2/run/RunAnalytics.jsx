import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, Stack, Typography, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { subTasksFor } from "../_mock/contract";
import { deriveUseCaseLabel } from "./TraceTable";
import RunMetrics from "./RunMetrics";

/**
 * Analytics.
 *
 * Mixes familiar chart types executed with polish — the shapes people
 * already read fluently (donut, bar, line, area) instead of exotic
 * ones (treemap, beeswarm, radial gauge). Enough variety per section
 * that the tab doesn't read as one chart on repeat, without making
 * anyone learn a new visualization.
 *
 *   1. Hero — big pass rate + trend area + sub-stats
 *   2. Donut (composition) + Horizontal bars (ranked goals) side by side
 *   3. Pass-rate line chart across the run's task sequence
 *   4. Metric comparisons — 2×2 grouped bar with mini distributions
 *   5. Grader table with per-row sparkline
 */

/* ── palette ─────────────────────────────────────────────────────────────── */

/*
  Restrained palette in the Stripe / Linear / Vercel spirit:
  – ACCENT (indigo) carries all neutral chart data — lines, area fills,
    KPI trends. If it's just "the metric", it's this color.
  – PASS / FAIL are outcome markers only — used for delta arrows,
    per-outcome bars, failure ticks, tier tinting on graders. Never
    on a plain trend chart.
  – Failure patterns share one hue (soft red) with an opacity ladder,
    plus muted slate for grader-side / runtime patterns. No rainbow.
  – Everything else is slate, alpha of text.primary.
*/
const ACCENT = "#6366F1";      // indigo-500 — the neutral data color
const ACCENT_D = "#4F46E5";    // indigo-600 — hover / active accent
const PASS = "#059669";        // emerald-600 — pass indicator only
const FAIL = "#DC2626";        // red-600 — fail indicator only
const AMBER = "#D97706";       // amber-600 — used only for grader mid-tier
const NEUTRAL = "#64748B";     // slate-500
const NEUTRAL_L = "#94A3B8";   // slate-400
const NEUTRAL_D = "#475569";   // slate-600

/*
  Patterns are all *failures*, so the palette stays in the red family
  with slate for grader/runtime concerns. Category is read from
  hue+opacity, not from a categorical rainbow.
*/
const PATTERN_TONES = {
  "Critical rule broken": FAIL,        // full red — the release blocker
  "Said, not done":       "#F87171",   // soft red — agent said/did divergence
  "Evaluation failed":        ACCENT,      // indigo — grader concern, not agent
  Errored:                NEUTRAL_D,   // deep slate — runtime concern
  "Other failure":        NEUTRAL_L,   // light slate — uncategorised
};
const PATTERN_ORDER = [
  "Critical rule broken",
  "Said, not done",
  "Evaluation failed",
  "Errored",
  "Other failure",
];

/* ── helpers ─────────────────────────────────────────────────────────────── */

const patternOf = (t) => {
  if (!t || t.status === "passed" || t.status === "unmeasured") return null;
  if (t.status === "error") return "Errored";
  if (t.critical) return "Critical rule broken";
  if (t.callLog?.unsupportedClaim) return "Said, not done";
  if ((t.evalResults || []).some((r) => !r.passed)) return "Evaluation failed";
  return "Other failure";
};

const hash = (s) => {
  let h = 0;
  for (let i = 0; i < String(s).length; i += 1) h = (h * 31 + String(s).charCodeAt(i)) >>> 0;
  return h;
};
const csatOf = (t) => Math.max(1, Math.round((t.evalResults?.[0]?.score ?? 0.5) * 10) - 4);
const latencyOf = (t) => 280 + (hash(t.id) % 320);
const percentile = (arr, p) => {
  if (!arr.length) return 0;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
};
const median = (arr) => percentile(arr, 50);
const avg = (arr) => (arr.length ? arr.reduce((a, n) => a + n, 0) / arr.length : 0);
const compact = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(Math.round(n)));

const monotonePath = (points) => {
  if (points.length < 2) return points[0] ? `M ${points[0][0]},${points[0][1]}` : "";
  const n = points.length;
  const dx = []; const dy = []; const slope = [];
  for (let i = 0; i < n - 1; i += 1) {
    dx.push(points[i + 1][0] - points[i][0]);
    dy.push(points[i + 1][1] - points[i][1]);
    slope.push(dx[i] ? dy[i] / dx[i] : 0);
  }
  const m = [slope[0]];
  for (let i = 1; i < n - 1; i += 1) m.push(slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2);
  m.push(slope[slope.length - 1]);
  let d = `M ${points[0][0]},${points[0][1]}`;
  for (let i = 0; i < n - 1; i += 1) {
    const cp1x = points[i][0] + dx[i] / 3;
    const cp1y = points[i][1] + (m[i] * dx[i]) / 3;
    const cp2x = points[i + 1][0] - dx[i] / 3;
    const cp2y = points[i + 1][1] - (m[i + 1] * dx[i]) / 3;
    d += ` C ${cp1x},${cp1y} ${cp2x},${cp2y} ${points[i + 1][0]},${points[i + 1][1]}`;
  }
  return d;
};

const fakeTrend = (current, key, len = 12) => {
  const arr = [];
  let v = Math.max(1, current * (0.55 + (hash(key) % 100) / 240));
  for (let i = 0; i < len; i += 1) {
    v = Math.max(0.5, Math.min(v + ((hash(`${key}-${i}`) % 100) - 50) / 26, current * 1.6));
    arr.push(v);
  }
  arr[arr.length - 1] = current;
  return arr;
};

/* ── shared panel shell ──────────────────────────────────────────────────── */

function Panel({ title, subtitle, action, children, sx }) {
  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider", borderRadius: 1.5,
      bgcolor: "background.paper", overflow: "hidden", ...sx,
    }}>
      {(title || action) && (
        <Stack direction="row" alignItems="center" spacing={2} sx={{
          px: 2.5, py: 1.75, borderBottom: "1px solid", borderColor: "divider",
        }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            {title && <Typography sx={{ typography: "s1", fontWeight: 700 }}>{title}</Typography>}
            {subtitle && <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>{subtitle}</Typography>}
          </Box>
          {action}
        </Stack>
      )}
      {children}
    </Box>
  );
}
Panel.propTypes = { title: PropTypes.string, subtitle: PropTypes.string, action: PropTypes.node, children: PropTypes.node, sx: PropTypes.object };

/* ── SECTION 1 · Hero card (big number + area chart + sub-stats) ─────────── */

function AreaChart({ values, color, width = 400, height = 100, showAxis = false }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const padT = 6;
  const padB = showAxis ? 22 : 4;
  const plotH = height - padT - padB;
  const pts = values.map((v, i) => [
    (i / (values.length - 1)) * width,
    padT + plotH - ((v - min) / span) * plotH,
  ]);
  const path = monotonePath(pts);
  const area = `${path} L ${width},${padT + plotH} L 0,${padT + plotH} Z`;
  const gid = `ac-${color.replace("#", "")}-${values.length}`;
  return (
    <Box component="svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" sx={{ width: "100%", height, display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="60%" stopColor={color} stopOpacity="0.1" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {showAxis && (
        <line
          x1={0} x2={width}
          y1={padT + plotH} y2={padT + plotH}
          stroke="currentColor" strokeOpacity={dark ? 0.15 : 0.12}
          strokeWidth={0.5} shapeRendering="crispEdges"
        />
      )}
      <path d={area} fill={`url(#${gid})`} />
      <path d={path} stroke={color} strokeWidth="2" fill="none" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="3" fill={color} />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="7" fill={color} opacity="0.2" />
    </Box>
  );
}
AreaChart.propTypes = { values: PropTypes.array, color: PropTypes.string, width: PropTypes.number, height: PropTypes.number, showAxis: PropTypes.bool };

function HeroCard({ tasks }) {
  const stats = useMemo(() => {
    const total = tasks.length;
    const passed = tasks.filter((t) => t.status === "passed").length;
    const failed = tasks.filter((t) => t.status === "failed" || t.status === "error").length;
    const critical = tasks.filter((t) => t.critical && (t.status === "failed" || t.status === "error")).length;
    const measured = tasks.filter((t) => t.status !== "unmeasured").length;
    const durations = tasks.map((t) => (t.durationMs || 0) / 1000).filter(Boolean);
    return {
      total, passed, failed, critical, measured,
      passRate: measured ? Math.round((passed / measured) * 100) : 0,
      medianDuration: median(durations),
    };
  }, [tasks]);

  const trend = useMemo(() => fakeTrend(stats.passRate, "hero"), [stats.passRate]);
  const delta = -12;

  const subStats = [
    { label: "Critical", value: stats.critical, color: FAIL, sub: "release blockers" },
    { label: "Failed", value: stats.failed, color: FAIL, sub: "total failures" },
    { label: "Median duration", value: `${stats.medianDuration.toFixed(1)}s`, color: ACCENT, sub: "per task" },
  ];

  return (
    <Panel>
      <Box sx={{
        display: "grid", gap: 0,
        gridTemplateColumns: { xs: "1fr", md: "minmax(280px, 380px) 1fr" },
      }}>
        {/* left: big number + delta */}
        <Box sx={{ p: 3, borderRight: { md: "1px solid" }, borderColor: "divider" }}>
          <Typography sx={{
            typography: "s3", color: "text.subtitle", fontWeight: 700,
            textTransform: "uppercase", letterSpacing: 0.5,
          }}>
            Pass rate
          </Typography>
          <Stack direction="row" alignItems="baseline" spacing={1.25} sx={{ mt: 1 }}>
            <Typography sx={{
              fontSize: 60, fontWeight: 700, lineHeight: 1,
              color: "text.primary", fontVariantNumeric: "tabular-nums",
            }}>
              {stats.passRate}
            </Typography>
            <Typography sx={{
              typography: "h4", fontWeight: 700, color: "text.subtitle",
              lineHeight: 1,
            }}>
              %
            </Typography>
            <Stack direction="row" alignItems="center" spacing={0.25} sx={{ color: delta < 0 ? FAIL : PASS, ml: 1 }}>
              <Iconify icon={delta > 0 ? "solar:arrow-up-linear" : "solar:arrow-down-linear"} width={16} />
              <Typography sx={{ typography: "s1", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                {Math.abs(delta)}pt
              </Typography>
            </Stack>
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 1.5 }}>
            {stats.passed} of {stats.measured} tasks passed
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            vs previous run of the same environment
          </Typography>
        </Box>

        {/* right: area chart + sub-stats */}
        <Box sx={{ p: 3 }}>
          <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1 }}>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>
              Pass rate · last 12 runs
            </Typography>
            <Box sx={{ flex: 1 }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Range <Box component="span" sx={{ color: "text.primary", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{Math.round(Math.min(...trend))}–{Math.round(Math.max(...trend))}%</Box>
            </Typography>
          </Stack>
          {/* Trend chart color = accent (it's a metric, not a status).
              Whether the metric is good/bad reads from the delta arrow. */}
          <AreaChart values={trend} color={ACCENT} height={120} showAxis />
          <Box sx={{
            display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 0,
            mt: 2, pt: 2, borderTop: "1px solid", borderColor: "divider",
          }}>
            {subStats.map((s, i) => (
              <Box key={s.label} sx={{
                px: 2,
                borderRight: i < subStats.length - 1 ? "1px solid" : "none", borderColor: "divider",
                "&:first-of-type": { pl: 0 },
              }}>
                <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>
                  {s.label}
                </Typography>
                <Typography sx={{
                  typography: "h4", fontWeight: 700, mt: 0.5, lineHeight: 1,
                  color: s.color, fontVariantNumeric: "tabular-nums",
                }}>
                  {s.value}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
                  {s.sub}
                </Typography>
              </Box>
            ))}
          </Box>
        </Box>
      </Box>
    </Panel>
  );
}
HeroCard.propTypes = { tasks: PropTypes.array };

/* ── SECTION 2 · Donut (composition) + Horizontal bar (ranked) ───────────── */

function Donut({ items, total, size = 200, stroke = 22 }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <Box component="svg" viewBox={`0 0 ${size} ${size}`} sx={{ width: size, height: size, display: "block", flexShrink: 0 }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeOpacity="0.06" strokeWidth={stroke} />
      {items.map((it) => {
        const dash = (it.count / total) * c;
        const el = (
          <circle
            key={it.label}
            cx={size / 2} cy={size / 2} r={r}
            fill="none"
            stroke={it.color}
            strokeWidth={stroke}
            strokeDasharray={`${dash} ${c - dash}`}
            strokeDashoffset={-offset}
            strokeLinecap="butt"
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        );
        offset += dash;
        return el;
      })}
      <text x={size / 2} y={size / 2 - 4} textAnchor="middle" fontSize="32" fontWeight="700" fill="currentColor" style={{ fontVariantNumeric: "tabular-nums" }}>
        {total}
      </text>
      <text x={size / 2} y={size / 2 + 18} textAnchor="middle" fontSize="10" fill="currentColor" opacity="0.55" letterSpacing="1.5">
        FAILURES
      </text>
    </Box>
  );
}
Donut.propTypes = { items: PropTypes.array, total: PropTypes.number, size: PropTypes.number, stroke: PropTypes.number };

function FailureComposition({ tasks }) {
  const { items, total } = useMemo(() => {
    const map = new Map();
    tasks.forEach((t) => {
      const p = patternOf(t);
      if (!p) return;
      map.set(p, (map.get(p) || 0) + 1);
    });
    const arr = PATTERN_ORDER.filter((p) => map.has(p)).map((p) => ({
      label: p, count: map.get(p), color: PATTERN_TONES[p],
    }));
    return { items: arr, total: arr.reduce((a, x) => a + x.count, 0) };
  }, [tasks]);

  if (!total) {
    return (
      <Panel title="Failure composition">
        <Box sx={{ py: 5, textAlign: "center" }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>Every task passed.</Typography>
        </Box>
      </Panel>
    );
  }

  return (
    <Panel title="Failure composition" subtitle="Share of failures by pattern">
      <Box sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "auto 1fr" },
        gap: 3, alignItems: "center", p: 3,
      }}>
        <Donut items={items} total={total} />
        <Stack spacing={1.25} sx={{ minWidth: 200 }}>
          {items.map((it) => {
            const pct = Math.round((it.count / total) * 100);
            return (
              <Box key={it.label}>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                  <Box sx={{ width: 10, height: 10, borderRadius: 0.5, bgcolor: it.color, flexShrink: 0 }} />
                  <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
                    {it.label}
                  </Typography>
                  <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", fontVariantNumeric: "tabular-nums" }}>
                    {it.count}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", width: 36, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {pct}%
                  </Typography>
                </Stack>
                <Box sx={{ height: 4, borderRadius: 2, bgcolor: (t) => alpha(t.palette.text.primary, 0.05), overflow: "hidden" }}>
                  <Box sx={{ height: "100%", width: `${pct}%`, bgcolor: it.color, borderRadius: 2, opacity: 0.85 }} />
                </Box>
              </Box>
            );
          })}
        </Stack>
      </Box>
    </Panel>
  );
}
FailureComposition.propTypes = { tasks: PropTypes.array };

function TopFailingGoals({ tasks }) {
  const goals = useMemo(() => {
    const map = new Map();
    tasks.forEach((t) => {
      if (!patternOf(t)) return;
      const g = deriveUseCaseLabel(t) || "Uncategorised";
      map.set(g, (map.get(g) || 0) + 1);
    });
    return [...map.entries()]
      .map(([label, count]) => ({ label, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 8);
  }, [tasks]);
  const max = goals[0]?.count || 1;

  return (
    <Panel title="Top failing goals" subtitle={`Top ${goals.length} by failure count`}>
      {goals.length === 0 ? (
        <Box sx={{ py: 5, textAlign: "center" }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>No failures to rank.</Typography>
        </Box>
      ) : (
        <Stack sx={{ p: 2 }} spacing={1.75}>
          {goals.map((g) => {
            const pct = (g.count / max) * 100;
            return (
              <Box key={g.label}>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                  <Tooltip arrow title={g.label} placement="top-start">
                    <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
                      {g.label}
                    </Typography>
                  </Tooltip>
                  <Typography sx={{ typography: "s2", fontWeight: 700, color: FAIL, fontVariantNumeric: "tabular-nums" }}>
                    {g.count}
                  </Typography>
                </Stack>
                <Box sx={{ height: 8, borderRadius: 1, bgcolor: (t) => alpha(t.palette.text.primary, 0.05), overflow: "hidden" }}>
                  <Box sx={{
                    height: "100%", width: `${pct}%`, borderRadius: 1,
                    background: `linear-gradient(90deg, ${FAIL}, ${alpha(FAIL, 0.6)})`,
                  }} />
                </Box>
              </Box>
            );
          })}
        </Stack>
      )}
    </Panel>
  );
}
TopFailingGoals.propTypes = { tasks: PropTypes.array };

/* ── SECTION 3 · Pass rate line chart across the run ─────────────────────── */

function PassRateTimeseries({ tasks }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";

  const { series, avgPass } = useMemo(() => {
    const window = 6;
    const s = [];
    const measurable = tasks.filter((t) => t.status !== "unmeasured");
    measurable.forEach((_, i) => {
      const start = Math.max(0, i - window + 1);
      const slice = measurable.slice(start, i + 1);
      const passed = slice.filter((t) => t.status === "passed").length;
      s.push({ x: i, y: (passed / slice.length) * 100 });
    });
    const avgV = s.length ? s.reduce((a, p) => a + p.y, 0) / s.length : 0;
    return { series: s, avgPass: avgV };
  }, [tasks]);

  const width = 1200;
  const height = 260;
  const padT = 20;
  const padB = 34;
  const padL = 44;
  const padR = 20;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  if (!series.length) {
    return (
      <Panel title="Pass rate over the run">
        <Box sx={{ p: 5, textAlign: "center" }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>Not enough measured tasks.</Typography>
        </Box>
      </Panel>
    );
  }

  const xOf = (i) => padL + (i / (series.length - 1)) * plotW;
  const yOf = (v) => padT + plotH - (v / 100) * plotH;
  const pts = series.map((p) => [xOf(p.x), yOf(p.y)]);
  const path = monotonePath(pts);

  return (
    <Panel
      title="Pass rate over the run"
      subtitle="Rolling 6-task window · shows where failure streaks concentrated"
      action={
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          Avg <Box component="span" sx={{ color: "text.primary", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{Math.round(avgPass)}%</Box>
        </Typography>
      }
    >
      <Box sx={{ p: 2 }}>
        <Box component="svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" sx={{ width: "100%", height, display: "block", overflow: "visible" }}>
          {/* Y grid + tick labels */}
          {[0, 25, 50, 75, 100].map((v) => {
            const y = yOf(v);
            const isBase = v === 0;
            return (
              <g key={v}>
                <line
                  x1={padL} x2={padL + plotW}
                  y1={y} y2={y}
                  stroke="currentColor"
                  strokeOpacity={isBase ? (dark ? 0.22 : 0.16) : (dark ? 0.06 : 0.04)}
                  strokeWidth={isBase ? 0.75 : 0.5}
                  strokeDasharray={isBase ? "0" : "3 4"}
                  shapeRendering="crispEdges"
                />
                <text x={padL - 10} y={y + 3} textAnchor="end" fontSize="10.5" fill="currentColor" fillOpacity="0.55" style={{ fontVariantNumeric: "tabular-nums" }}>
                  {v}%
                </text>
              </g>
            );
          })}

          {/* Average reference */}
          <line
            x1={padL} x2={padL + plotW}
            y1={yOf(avgPass)} y2={yOf(avgPass)}
            stroke={ACCENT} strokeOpacity="0.4"
            strokeWidth={0.75} strokeDasharray="4 4"
            vectorEffect="non-scaling-stroke"
          />

          <path d={path} stroke={ACCENT} strokeWidth="2" fill="none" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />

          {/* Failure markers underneath */}
          {tasks.filter((t) => t.status !== "unmeasured").map((t, i) => {
            if (t.status === "passed") return null;
            const critical = t.critical;
            const x = xOf(i);
            const y = padT + plotH + 8;
            return (
              <line
                key={t.id}
                x1={x} x2={x}
                y1={y} y2={y + (critical ? 6 : 4)}
                stroke={FAIL} strokeOpacity={critical ? 0.9 : 0.5}
                strokeWidth={critical ? 2 : 1.25}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}

          {/* X tick labels */}
          {[0, 0.25, 0.5, 0.75, 1].map((f) => {
            const idx = Math.round(f * (series.length - 1));
            const x = xOf(idx);
            return (
              <text
                key={f} x={x} y={height - 8} textAnchor="middle"
                fontSize="10.5" fill="currentColor" fillOpacity="0.55"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                Task {idx + 1}
              </text>
            );
          })}
        </Box>
        {/* legend */}
        <Stack direction="row" spacing={2.5} sx={{ mt: 1.5, px: 2 }}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 12, height: 2, bgcolor: ACCENT }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Pass rate</Typography>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 12, height: 2, bgcolor: ACCENT, opacity: 0.4, borderTop: "1px dashed", borderColor: ACCENT }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Run average</Typography>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 2, height: 8, bgcolor: FAIL }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Failure marker</Typography>
          </Stack>
        </Stack>
      </Box>
    </Panel>
  );
}
PassRateTimeseries.propTypes = { tasks: PropTypes.array };

/* ── SECTION 4 · Metric comparisons (grouped bars, 2×2) ──────────────────── */

const METRIC_SPECS = [
  { key: "turns",   label: "Turns",   unit: "" },
  { key: "latency", label: "Latency", unit: "ms" },
  { key: "tokens",  label: "Tokens",  unit: "" },
  { key: "csat",    label: "CSAT",    unit: "" },
];

const metricValueOf = (t, key) => {
  if (key === "turns") return t.steps?.length || 0;
  if (key === "latency") return latencyOf(t);
  if (key === "tokens") return t.tokens || 0;
  if (key === "csat") return csatOf(t);
  return 0;
};

/*
  Signal gauge per metric — a semi-circular arc where the arc's fill
  and color represent how strongly the metric separates passed from
  failed traces. Vercel Speed Insights / Datadog SLO gauge pattern:
  one dominant visual, unambiguous meaning, minimal cognitive load.
  The pass and fail averages sit below as compact tokens.
*/
/*
  Strip plot per metric — every measurable task is one dot on a
  horizontal axis of the metric's value. Pass dots on top, fail dots
  underneath. If the red dots cluster to one side of the axis, the
  metric is a signal — the actual data does the arguing, not a
  synthesized bar. Pattern used in Grafana / Datadog metric explorers
  and LangSmith's trace analytics.
*/
function MetricStripPlot({ spec, tasks }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const pass = [];
  const fail = [];
  tasks.forEach((t) => {
    if (t.status === "unmeasured") return;
    const v = metricValueOf(t, spec.key);
    if (t.status === "passed") pass.push({ t, v }); else fail.push({ t, v });
  });
  const passAvg = avg(pass.map((x) => x.v));
  const failAvg = avg(fail.map((x) => x.v));
  const delta = passAvg ? Math.round(((failAvg - passAvg) / passAvg) * 100) : 0;
  const strong = Math.abs(delta) >= 15;

  /* Axis domain — observed range with a small pad so end dots aren't
     glued to the frame edges. */
  const allV = [...pass.map((x) => x.v), ...fail.map((x) => x.v)];
  const domainMin = allV.length ? Math.min(...allV) : 0;
  const domainMax = allV.length ? Math.max(...allV) : 1;
  const span = Math.max(1, domainMax - domainMin);
  const lo = Math.max(0, domainMin - span * 0.06);
  const hi = domainMax + span * 0.06;

  const width = 560;
  const height = 118;
  const padT = 20;
  const padB = 28;
  const padL = 12;
  const padR = 12;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  const bandPassY = padT + plotH * 0.32;
  const bandFailY = padT + plotH * 0.72;
  const jitter = plotH * 0.28;

  const xOf = (v) => padL + Math.max(0, Math.min(plotW, ((v - lo) / (hi - lo)) * plotW));

  /* Deterministic jitter from task-id hash so a task always renders
     at the same y-position, run to run. */
  const dotsFor = (arr, bandY) => arr.map(({ t, v }) => ({
    x: xOf(v),
    y: bandY + ((hash(`${spec.key}-${t.id}`) % 1000) / 1000 - 0.5) * jitter,
    v, id: t.id, title: t.title || t.id,
  }));

  const passDots = dotsFor(pass, bandPassY);
  const failDots = dotsFor(fail, bandFailY);

  const passAvgX = xOf(passAvg);
  const failAvgX = xOf(failAvg);

  const tickValues = [lo, (lo + hi) / 2, hi];

  return (
    <Box sx={{
      p: 2.5, border: "1px solid", borderColor: "divider", borderRadius: 1.5,
      bgcolor: "background.paper",
    }}>
      {/* header */}
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1.5 }}>
        <Typography sx={{ typography: "s1", fontWeight: 700, flex: 1 }}>{spec.label}</Typography>
        <Typography sx={{
          typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums",
          color: strong ? FAIL : "text.subtitle",
        }}>
          {delta > 0 ? "+" : ""}{delta}% on fail
        </Typography>
      </Stack>

      {/* strip plot */}
      <Box
        component="svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        sx={{ width: "100%", height, display: "block", overflow: "visible" }}
      >
        {/* baseline axis */}
        <line
          x1={padL} x2={padL + plotW}
          y1={padT + plotH} y2={padT + plotH}
          stroke="currentColor" strokeOpacity={dark ? 0.14 : 0.1}
          strokeWidth={1} shapeRendering="crispEdges"
        />

        {/* pass avg marker (vertical dashed) */}
        {pass.length > 0 && (
          <line
            x1={passAvgX} x2={passAvgX}
            y1={padT} y2={padT + plotH}
            stroke={PASS} strokeOpacity="0.55"
            strokeWidth={1} strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke"
          />
        )}
        {/* fail avg marker */}
        {fail.length > 0 && (
          <line
            x1={failAvgX} x2={failAvgX}
            y1={padT} y2={padT + plotH}
            stroke={FAIL} strokeOpacity="0.55"
            strokeWidth={1} strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke"
          />
        )}

        {/* pass dots */}
        {passDots.map((d) => (
          <circle key={`p-${d.id}`} cx={d.x} cy={d.y} r="3" fill={PASS} fillOpacity="0.75">
            <title>{d.title} · passed · {compact(d.v)}{spec.unit}</title>
          </circle>
        ))}
        {/* fail dots */}
        {failDots.map((d) => (
          <circle key={`f-${d.id}`} cx={d.x} cy={d.y} r="3" fill={FAIL} fillOpacity="0.8">
            <title>{d.title} · failed · {compact(d.v)}{spec.unit}</title>
          </circle>
        ))}

        {/* pass avg value label — above the axis */}
        {pass.length > 0 && (
          <text
            x={passAvgX} y={padT - 6} textAnchor="middle"
            fontSize="10" fontWeight="700" fill={PASS}
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {compact(passAvg)}{spec.unit}
          </text>
        )}
        {/* fail avg value label */}
        {fail.length > 0 && (
          <text
            x={failAvgX} y={padT - 6}
            textAnchor={Math.abs(passAvgX - failAvgX) < 40 && failAvgX >= passAvgX ? "start" : "middle"}
            fontSize="10" fontWeight="700" fill={FAIL}
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {compact(failAvg)}{spec.unit}
          </text>
        )}

        {/* x-axis ticks */}
        {tickValues.map((v, i) => {
          const x = padL + (i / (tickValues.length - 1)) * plotW;
          return (
            <text
              key={v} x={x} y={height - 10}
              textAnchor={i === 0 ? "start" : i === tickValues.length - 1 ? "end" : "middle"}
              fontSize="10" fill="currentColor" fillOpacity="0.5"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {compact(v)}{spec.unit}
            </text>
          );
        })}
      </Box>

      {/* footer summary line */}
      <Stack direction="row" spacing={2} sx={{ mt: 1.25, alignItems: "center" }}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: PASS }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {pass.length} passed
          </Typography>
        </Stack>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: FAIL }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {fail.length} failed
          </Typography>
        </Stack>
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          Δ avg <Box component="span" sx={{ color: strong ? FAIL : "text.primary", fontWeight: 700 }}>
            {failAvg > passAvg ? "+" : ""}{compact(failAvg - passAvg)}{spec.unit}
          </Box>
        </Typography>
      </Stack>
    </Box>
  );
}
MetricStripPlot.propTypes = { spec: PropTypes.object, tasks: PropTypes.array };

/* ── VARIATIONS PREVIEW ──────────────────────────────────────────────────── */
/*
  Ten different chart types for the same "does this metric explain
  failure?" question, rendered side-by-side with sample Turns data.
  Pick one, everything else gets removed and that variation rolls out
  for all four metrics.
*/

function useSampleData(tasks) {
  return useMemo(() => {
    const spec = { key: "turns", label: "Turns", unit: "" };
    const pass = [];
    const fail = [];
    tasks.forEach((t) => {
      if (t.status === "unmeasured") return;
      const v = metricValueOf(t, spec.key);
      if (t.status === "passed") pass.push({ t, v }); else fail.push({ t, v });
    });
    const passVals = pass.map((x) => x.v);
    const failVals = fail.map((x) => x.v);
    return {
      spec, pass, fail, passVals, failVals,
      passAvg: avg(passVals), failAvg: avg(failVals),
      passMed: median(passVals), failMed: median(failVals),
      passP25: percentile(passVals, 25), passP75: percentile(passVals, 75),
      failP25: percentile(failVals, 25), failP75: percentile(failVals, 75),
      passMin: passVals.length ? Math.min(...passVals) : 0,
      passMax: passVals.length ? Math.max(...passVals) : 0,
      failMin: failVals.length ? Math.min(...failVals) : 0,
      failMax: failVals.length ? Math.max(...failVals) : 0,
      delta: passVals.length && failVals.length ? Math.round(((avg(failVals) - avg(passVals)) / avg(passVals)) * 100) : 0,
      domain: (() => {
        const all = [...passVals, ...failVals];
        if (!all.length) return [0, 1];
        const mn = Math.min(...all);
        const mx = Math.max(...all);
        const span = Math.max(1, mx - mn);
        return [Math.max(0, mn - span * 0.08), mx + span * 0.08];
      })(),
    };
  }, [tasks]);
}

function VariationCard({ n, title, hint, children }) {
  return (
    <Box sx={{
      p: 2, border: "1px solid", borderColor: "divider", borderRadius: 1.25,
      bgcolor: "background.paper",
    }}>
      <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ mb: 1.5 }}>
        <Typography sx={{
          typography: "s3", fontWeight: 700, color: "text.subtitle",
          fontSize: 10.5, fontVariantNumeric: "tabular-nums",
        }}>
          {String(n).padStart(2, "0")}
        </Typography>
        <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>{title}</Typography>
      </Stack>
      <Box sx={{ minHeight: 130 }}>{children}</Box>
      {hint && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 10, mt: 1.25 }}>
          {hint}
        </Typography>
      )}
    </Box>
  );
}
VariationCard.propTypes = { n: PropTypes.number, title: PropTypes.string, hint: PropTypes.string, children: PropTypes.node };

/* ---- Variation 1: Grouped horizontal bars (current baseline) ---- */
function V1_GroupedBars({ d }) {
  const max = Math.max(d.passAvg, d.failAvg, 1);
  return (
    <Stack spacing={1.5}>
      <Box>
        <Stack direction="row" alignItems="baseline" sx={{ mb: 0.5 }}>
          <Typography sx={{ typography: "s3", color: PASS, fontWeight: 700, flex: 1, fontSize: 10.5 }}>Pass</Typography>
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", fontVariantNumeric: "tabular-nums" }}>{compact(d.passAvg)}</Typography>
        </Stack>
        <Box sx={{ height: 10, borderRadius: 0.75, bgcolor: (t) => alpha(t.palette.text.primary, 0.05), overflow: "hidden" }}>
          <Box sx={{ height: "100%", width: `${(d.passAvg / max) * 100}%`, bgcolor: PASS }} />
        </Box>
      </Box>
      <Box>
        <Stack direction="row" alignItems="baseline" sx={{ mb: 0.5 }}>
          <Typography sx={{ typography: "s3", color: FAIL, fontWeight: 700, flex: 1, fontSize: 10.5 }}>Fail</Typography>
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", fontVariantNumeric: "tabular-nums" }}>{compact(d.failAvg)}</Typography>
        </Stack>
        <Box sx={{ height: 10, borderRadius: 0.75, bgcolor: (t) => alpha(t.palette.text.primary, 0.05), overflow: "hidden" }}>
          <Box sx={{ height: "100%", width: `${(d.failAvg / max) * 100}%`, bgcolor: FAIL }} />
        </Box>
      </Box>
    </Stack>
  );
}

/* ---- Variation 2: Grouped vertical bars ---- */
function V2_VerticalBars({ d }) {
  const w = 300; const h = 120;
  const max = Math.max(d.passAvg, d.failAvg, 1);
  const barW = 60;
  const gap = 24;
  const startX = (w - barW * 2 - gap) / 2;
  const passH = (d.passAvg / max) * (h - 20);
  const failH = (d.failAvg / max) * (h - 20);
  return (
    <Box component="svg" viewBox={`0 0 ${w} ${h + 22}`} preserveAspectRatio="none" sx={{ width: "100%", height: h + 22, display: "block" }}>
      <line x1={0} y1={h} x2={w} y2={h} stroke="currentColor" strokeOpacity="0.12" />
      <rect x={startX} y={h - passH} width={barW} height={passH} fill={PASS} rx={2} />
      <rect x={startX + barW + gap} y={h - failH} width={barW} height={failH} fill={FAIL} rx={2} />
      <text x={startX + barW / 2} y={h - passH - 4} textAnchor="middle" fontSize="12" fontWeight="700" fill={PASS} style={{ fontVariantNumeric: "tabular-nums" }}>{compact(d.passAvg)}</text>
      <text x={startX + barW + gap + barW / 2} y={h - failH - 4} textAnchor="middle" fontSize="12" fontWeight="700" fill={FAIL} style={{ fontVariantNumeric: "tabular-nums" }}>{compact(d.failAvg)}</text>
      <text x={startX + barW / 2} y={h + 14} textAnchor="middle" fontSize="10" fill="currentColor" fillOpacity="0.55" letterSpacing="0.4">PASS</text>
      <text x={startX + barW + gap + barW / 2} y={h + 14} textAnchor="middle" fontSize="10" fill="currentColor" fillOpacity="0.55" letterSpacing="0.4">FAIL</text>
    </Box>
  );
}

/* ---- Variation 3: Dumbbell ---- */
function V3_Dumbbell({ d }) {
  const w = 300; const h = 90;
  const cy = h / 2;
  const padX = 20;
  const plotW = w - padX * 2;
  const [lo, hi] = d.domain;
  const xOf = (v) => padX + ((v - lo) / (hi - lo)) * plotW;
  const px = xOf(d.passAvg); const fx = xOf(d.failAvg);
  return (
    <Box component="svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" sx={{ width: "100%", height: h, display: "block" }}>
      <line x1={padX} y1={cy} x2={w - padX} y2={cy} stroke="currentColor" strokeOpacity="0.08" />
      <line x1={Math.min(px, fx)} y1={cy} x2={Math.max(px, fx)} y2={cy} stroke={NEUTRAL_L} strokeOpacity="0.5" strokeWidth={3} vectorEffect="non-scaling-stroke" strokeLinecap="round" />
      <circle cx={px} cy={cy} r="6" fill={PASS} />
      <circle cx={fx} cy={cy} r="6" fill={FAIL} />
      <text x={px} y={cy - 12} textAnchor="middle" fontSize="10" fontWeight="700" fill={PASS} style={{ fontVariantNumeric: "tabular-nums" }}>{compact(d.passAvg)}</text>
      <text x={fx} y={cy + 22} textAnchor="middle" fontSize="10" fontWeight="700" fill={FAIL} style={{ fontVariantNumeric: "tabular-nums" }}>{compact(d.failAvg)}</text>
      <text x={padX} y={h - 4} fontSize="9" fill="currentColor" fillOpacity="0.5" style={{ fontVariantNumeric: "tabular-nums" }}>{compact(lo)}</text>
      <text x={w - padX} y={h - 4} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity="0.5" style={{ fontVariantNumeric: "tabular-nums" }}>{compact(hi)}</text>
    </Box>
  );
}

/* ---- Variation 4: Strip plot ---- */
function V4_StripPlot({ d }) {
  const w = 300; const h = 120;
  const padX = 12; const padT = 12; const padB = 20;
  const plotW = w - padX * 2;
  const plotH = h - padT - padB;
  const [lo, hi] = d.domain;
  const xOf = (v) => padX + ((v - lo) / (hi - lo)) * plotW;
  const bandP = padT + plotH * 0.32;
  const bandF = padT + plotH * 0.72;
  const jit = plotH * 0.25;
  const dotsFor = (arr, by) => arr.map(({ t, v }) => ({
    x: xOf(v), y: by + ((hash(`v4-${t.id}`) % 1000) / 1000 - 0.5) * jit,
  }));
  const passDots = dotsFor(d.pass, bandP);
  const failDots = dotsFor(d.fail, bandF);
  return (
    <Box component="svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" sx={{ width: "100%", height: h, display: "block" }}>
      <line x1={padX} y1={padT + plotH} x2={padX + plotW} y2={padT + plotH} stroke="currentColor" strokeOpacity="0.12" />
      <line x1={xOf(d.passAvg)} y1={padT} x2={xOf(d.passAvg)} y2={padT + plotH} stroke={PASS} strokeOpacity="0.45" strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
      <line x1={xOf(d.failAvg)} y1={padT} x2={xOf(d.failAvg)} y2={padT + plotH} stroke={FAIL} strokeOpacity="0.45" strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
      {passDots.map((p, i) => <circle key={`p${i}`} cx={p.x} cy={p.y} r="2.5" fill={PASS} fillOpacity="0.7" />)}
      {failDots.map((p, i) => <circle key={`f${i}`} cx={p.x} cy={p.y} r="2.5" fill={FAIL} fillOpacity="0.75" />)}
      <text x={padX} y={h - 6} fontSize="9" fill="currentColor" fillOpacity="0.55">{compact(lo)}</text>
      <text x={padX + plotW} y={h - 6} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity="0.55">{compact(hi)}</text>
    </Box>
  );
}

/* ---- Variation 5: Box plots (pass vs fail) ---- */
function V5_BoxPlot({ d }) {
  const w = 300; const h = 120;
  const padX = 12; const padT = 12; const padB = 20;
  const plotW = w - padX * 2;
  const plotH = h - padT - padB;
  const [lo, hi] = d.domain;
  const xOf = (v) => padX + ((v - lo) / (hi - lo)) * plotW;
  const bandP = padT + plotH * 0.32;
  const bandF = padT + plotH * 0.72;
  const bh = 20;
  const drawBox = (stats, cy, color) => (
    <g>
      <line x1={xOf(stats.min)} x2={xOf(stats.max)} y1={cy} y2={cy} stroke={color} strokeOpacity="0.6" vectorEffect="non-scaling-stroke" />
      <line x1={xOf(stats.min)} x2={xOf(stats.min)} y1={cy - 4} y2={cy + 4} stroke={color} strokeOpacity="0.6" vectorEffect="non-scaling-stroke" />
      <line x1={xOf(stats.max)} x2={xOf(stats.max)} y1={cy - 4} y2={cy + 4} stroke={color} strokeOpacity="0.6" vectorEffect="non-scaling-stroke" />
      <rect x={xOf(stats.p25)} y={cy - bh / 2} width={xOf(stats.p75) - xOf(stats.p25) || 2} height={bh} fill={alpha(color, 0.2)} stroke={color} vectorEffect="non-scaling-stroke" />
      <line x1={xOf(stats.med)} x2={xOf(stats.med)} y1={cy - bh / 2} y2={cy + bh / 2} stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </g>
  );
  const passStats = { min: d.passMin, p25: d.passP25, med: d.passMed, p75: d.passP75, max: d.passMax };
  const failStats = { min: d.failMin, p25: d.failP25, med: d.failMed, p75: d.failP75, max: d.failMax };
  return (
    <Box component="svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" sx={{ width: "100%", height: h, display: "block" }}>
      <line x1={padX} y1={padT + plotH} x2={padX + plotW} y2={padT + plotH} stroke="currentColor" strokeOpacity="0.12" />
      {d.pass.length > 0 && drawBox(passStats, bandP, PASS)}
      {d.fail.length > 0 && drawBox(failStats, bandF, FAIL)}
      <text x={padX} y={bandP - 12} fontSize="9" fill={PASS} fontWeight="700" letterSpacing="0.4">PASS</text>
      <text x={padX} y={bandF - 12} fontSize="9" fill={FAIL} fontWeight="700" letterSpacing="0.4">FAIL</text>
      <text x={padX} y={h - 6} fontSize="9" fill="currentColor" fillOpacity="0.55">{compact(lo)}</text>
      <text x={padX + plotW} y={h - 6} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity="0.55">{compact(hi)}</text>
    </Box>
  );
}

/* ---- Variation 6: Overlaid density curves ---- */
function V6_Density({ d }) {
  const bins = 20;
  const [lo, hi] = d.domain;
  const step = (hi - lo) / bins;
  const passBins = new Array(bins).fill(0);
  const failBins = new Array(bins).fill(0);
  d.passVals.forEach((v) => { passBins[Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / step)))] += 1; });
  d.failVals.forEach((v) => { failBins[Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / step)))] += 1; });
  const peak = Math.max(1, ...passBins, ...failBins);
  const w = 300; const h = 120;
  const padX = 8; const padT = 8; const padB = 20;
  const plotW = w - padX * 2; const plotH = h - padT - padB;
  const toPts = (arr) => arr.map((v, i) => [padX + (i / (bins - 1)) * plotW, padT + plotH - (v / peak) * plotH]);
  const passPath = monotonePath(toPts(passBins));
  const failPath = monotonePath(toPts(failBins));
  const passArea = `${passPath} L ${padX + plotW},${padT + plotH} L ${padX},${padT + plotH} Z`;
  const failArea = `${failPath} L ${padX + plotW},${padT + plotH} L ${padX},${padT + plotH} Z`;
  return (
    <Box component="svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" sx={{ width: "100%", height: h, display: "block" }}>
      <line x1={padX} y1={padT + plotH} x2={padX + plotW} y2={padT + plotH} stroke="currentColor" strokeOpacity="0.12" />
      <path d={passArea} fill={alpha(PASS, 0.25)} />
      <path d={passPath} stroke={PASS} strokeWidth="1.5" fill="none" vectorEffect="non-scaling-stroke" />
      <path d={failArea} fill={alpha(FAIL, 0.25)} />
      <path d={failPath} stroke={FAIL} strokeWidth="1.5" fill="none" vectorEffect="non-scaling-stroke" />
      <text x={padX} y={h - 6} fontSize="9" fill="currentColor" fillOpacity="0.55">{compact(lo)}</text>
      <text x={padX + plotW} y={h - 6} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity="0.55">{compact(hi)}</text>
    </Box>
  );
}

/* ---- Variation 7: Diverging bar (bars extend from center) ---- */
function V7_Diverging({ d }) {
  const w = 300; const h = 90;
  const cy = h / 2;
  const centerX = w / 2;
  const halfW = (w - 40) / 2;
  const mid = (d.passAvg + d.failAvg) / 2 || 1;
  const passDist = ((mid - d.passAvg) / mid);
  const failDist = ((d.failAvg - mid) / mid);
  const maxDist = Math.max(Math.abs(passDist), Math.abs(failDist), 0.01);
  const passBarW = (Math.abs(passDist) / maxDist) * halfW;
  const failBarW = (Math.abs(failDist) / maxDist) * halfW;
  return (
    <Box component="svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" sx={{ width: "100%", height: h, display: "block" }}>
      <line x1={centerX} y1={12} x2={centerX} y2={h - 20} stroke="currentColor" strokeOpacity="0.25" strokeDasharray="2 2" />
      <text x={centerX} y={h - 6} textAnchor="middle" fontSize="9" fill="currentColor" fillOpacity="0.55">midpoint</text>
      <rect x={centerX - passBarW} y={cy - 22} width={passBarW} height={16} fill={PASS} rx={2} />
      <rect x={centerX} y={cy + 6} width={failBarW} height={16} fill={FAIL} rx={2} />
      <text x={centerX - passBarW - 4} y={cy - 10} textAnchor="end" fontSize="10" fontWeight="700" fill={PASS} style={{ fontVariantNumeric: "tabular-nums" }}>{compact(d.passAvg)}</text>
      <text x={centerX + failBarW + 4} y={cy + 18} fontSize="10" fontWeight="700" fill={FAIL} style={{ fontVariantNumeric: "tabular-nums" }}>{compact(d.failAvg)}</text>
    </Box>
  );
}

/* ---- Variation 8: Slope chart (two vertical axes) ---- */
function V8_Slope({ d }) {
  const w = 300; const h = 120;
  const padT = 12; const padB = 24;
  const [lo, hi] = d.domain;
  const plotH = h - padT - padB;
  const yOf = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;
  const leftX = 60; const rightX = w - 60;
  const passY = yOf(d.passAvg); const failY = yOf(d.failAvg);
  return (
    <Box component="svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" sx={{ width: "100%", height: h, display: "block" }}>
      <line x1={leftX} y1={padT} x2={leftX} y2={padT + plotH} stroke="currentColor" strokeOpacity="0.15" />
      <line x1={rightX} y1={padT} x2={rightX} y2={padT + plotH} stroke="currentColor" strokeOpacity="0.15" />
      <line x1={leftX} y1={passY} x2={rightX} y2={failY} stroke={d.delta > 0 ? FAIL : PASS} strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <circle cx={leftX} cy={passY} r="5" fill={PASS} />
      <circle cx={rightX} cy={failY} r="5" fill={FAIL} />
      <text x={leftX - 8} y={passY + 4} textAnchor="end" fontSize="11" fontWeight="700" fill={PASS} style={{ fontVariantNumeric: "tabular-nums" }}>{compact(d.passAvg)}</text>
      <text x={rightX + 8} y={failY + 4} fontSize="11" fontWeight="700" fill={FAIL} style={{ fontVariantNumeric: "tabular-nums" }}>{compact(d.failAvg)}</text>
      <text x={leftX} y={h - 8} textAnchor="middle" fontSize="9" fill="currentColor" fillOpacity="0.55" letterSpacing="0.4">PASS</text>
      <text x={rightX} y={h - 8} textAnchor="middle" fontSize="9" fill="currentColor" fillOpacity="0.55" letterSpacing="0.4">FAIL</text>
    </Box>
  );
}

/* ---- Variation 9: Bullet chart (fail bar with pass reference) ---- */
function V9_Bullet({ d }) {
  const w = 300; const h = 90;
  const padX = 12;
  const [lo, hi] = d.domain;
  const plotW = w - padX * 2;
  const xOf = (v) => padX + ((v - lo) / (hi - lo)) * plotW;
  const barY = h / 2 - 12;
  const barH = 24;
  const failW = xOf(d.failAvg) - padX;
  return (
    <Box component="svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" sx={{ width: "100%", height: h, display: "block" }}>
      <rect x={padX} y={barY} width={plotW} height={barH} fill="currentColor" fillOpacity="0.06" rx={2} />
      <rect x={padX} y={barY} width={failW} height={barH} fill={FAIL} fillOpacity="0.85" rx={2} />
      <line x1={xOf(d.passAvg)} x2={xOf(d.passAvg)} y1={barY - 6} y2={barY + barH + 6} stroke={PASS} strokeWidth="3" vectorEffect="non-scaling-stroke" />
      <text x={padX + 4} y={barY - 6} fontSize="9" fill={FAIL} fontWeight="700" letterSpacing="0.4">FAIL {compact(d.failAvg)}</text>
      <text x={xOf(d.passAvg) + 6} y={barY + barH + 16} fontSize="9" fill={PASS} fontWeight="700" letterSpacing="0.4">PASS {compact(d.passAvg)}</text>
      <text x={padX} y={h - 4} fontSize="9" fill="currentColor" fillOpacity="0.55">{compact(lo)}</text>
      <text x={padX + plotW} y={h - 4} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity="0.55">{compact(hi)}</text>
    </Box>
  );
}

/* ---- Variation 10: Delta ticker (big number + sparkline) ---- */
function V10_DeltaTicker({ d }) {
  const trend = fakeTrend(d.delta || 1, "delta-turns");
  return (
    <Stack direction="row" alignItems="center" spacing={2} sx={{ height: "100%" }}>
      <Box sx={{ flex: 1 }}>
        <Stack direction="row" alignItems="baseline" spacing={0.5}>
          <Typography sx={{
            fontSize: 44, fontWeight: 700, lineHeight: 1,
            color: Math.abs(d.delta) >= 15 ? FAIL : "text.primary",
            fontVariantNumeric: "tabular-nums",
          }}>
            {d.delta > 0 ? "+" : ""}{d.delta}
          </Typography>
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.subtitle" }}>%</Typography>
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5, fontSize: 10.5 }}>
          on failing traces
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.75, fontVariantNumeric: "tabular-nums", fontSize: 10.5 }}>
          <Box component="span" sx={{ color: PASS, fontWeight: 700 }}>{compact(d.passAvg)}</Box>
          {" → "}
          <Box component="span" sx={{ color: FAIL, fontWeight: 700 }}>{compact(d.failAvg)}</Box>
        </Typography>
      </Box>
      <Box sx={{ width: 120, flexShrink: 0 }}>
        <AreaChart values={trend} color={Math.abs(d.delta) >= 15 ? FAIL : ACCENT} height={90} />
      </Box>
    </Stack>
  );
}

const VARIATIONS = [
  { n: 1,  title: "Grouped horizontal bars",     hint: "Two bars stacked — the current baseline",              Cmp: V1_GroupedBars },
  { n: 2,  title: "Grouped vertical bars",        hint: "Classic bar chart, side by side",                     Cmp: V2_VerticalBars },
  { n: 3,  title: "Dumbbell",                     hint: "Two dots on one axis — the gap IS the signal",        Cmp: V3_Dumbbell },
  { n: 4,  title: "Strip / dot plot",             hint: "One dot per task, pass on top / fail below",          Cmp: V4_StripPlot },
  { n: 5,  title: "Box plots",                    hint: "Median, quartiles, min/max whiskers",                 Cmp: V5_BoxPlot },
  { n: 6,  title: "Overlaid density curves",      hint: "Smooth pass/fail curves layered",                     Cmp: V6_Density },
  { n: 7,  title: "Diverging bar",                hint: "Bars extend from a shared midpoint",                  Cmp: V7_Diverging },
  { n: 8,  title: "Slope chart",                  hint: "Two vertical axes with a slope line between",         Cmp: V8_Slope },
  { n: 9,  title: "Bullet chart",                 hint: "Fail as the value bar, pass as the reference tick",   Cmp: V9_Bullet },
  { n: 10, title: "Delta ticker + sparkline",     hint: "Just the delta number with a trend line",             Cmp: V10_DeltaTicker },
];

function MetricsCompare({ tasks }) {
  const d = useSampleData(tasks);
  return (
    <Box>
      <Box sx={{ mb: 1.5, px: 0.5 }}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Metric chart variations · pick one</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          Same data (Turns · pass avg {compact(d.passAvg)}, fail avg {compact(d.failAvg)}, delta {d.delta > 0 ? "+" : ""}{d.delta}%), ten different visual approaches. Point at the one you want and I'll roll it out for all four metrics.
        </Typography>
      </Box>
      <Box sx={{
        display: "grid", gap: 1.5,
        gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "1fr 1fr 1fr" },
      }}>
        {VARIATIONS.map(({ n, title, hint, Cmp }) => (
          <VariationCard key={n} n={n} title={title} hint={hint}>
            <Cmp d={d} />
          </VariationCard>
        ))}
      </Box>
    </Box>
  );
}
MetricsCompare.propTypes = { tasks: PropTypes.array };

/* ── SECTION 5 · Evaluations breakdown ─────────────────────────────
   One card per evaluation with a proper radial gauge for the pass
   rate, a smoothed density chart of the score distribution, and a
   stat strip below. Two-column grid on desktop so the reader can
   compare two graders side by side. */

/* Fritsch–Carlson monotone cubic path — shape follows the histogram
   bins but reads as a distribution curve, not a bar chart. */
function densityMonotonePath(pts) {
  const n = pts.length;
  if (n === 0) return "";
  if (n === 1) return `M ${pts[0][0]} ${pts[0][1]}`;
  const dx = new Array(n - 1);
  const dy = new Array(n - 1);
  const m = new Array(n - 1);
  for (let i = 0; i < n - 1; i += 1) {
    dx[i] = pts[i + 1][0] - pts[i][0];
    dy[i] = pts[i + 1][1] - pts[i][1];
    m[i] = dy[i] / dx[i];
  }
  const t = new Array(n);
  t[0] = m[0];
  t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i += 1) {
    if (m[i - 1] * m[i] <= 0) t[i] = 0;
    else {
      const w1 = 2 * dx[i] + dx[i - 1];
      const w2 = dx[i] + 2 * dx[i - 1];
      t[i] = (w1 + w2) / (w1 / m[i - 1] + w2 / m[i]);
    }
  }
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < n - 1; i += 1) {
    const c1x = pts[i][0] + dx[i] / 3;
    const c1y = pts[i][1] + (t[i] * dx[i]) / 3;
    const c2x = pts[i + 1][0] - dx[i] / 3;
    const c2y = pts[i + 1][1] - (t[i + 1] * dx[i]) / 3;
    d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${pts[i + 1][0]} ${pts[i + 1][1]}`;
  }
  return d;
}

function DensityChart({ hist, peak, threshold, mean, dark, theme }) {
  /* SVG draws only the shape and axis lines (with preserveAspectRatio="none"
     so the curve stretches to the container width). Text lives in absolutely-
     positioned HTML so it never stretches. */
  const W = 320;
  const H = 92;
  const padT = 10;
  const padB = 18;
  const plotH = H - padT - padB;
  const bins = hist.length;

  const gradId = React.useId();

  const pts = hist.map((v, i) => {
    const x = ((i + 0.5) / bins) * W;
    const y = padT + plotH - (v / peak) * plotH;
    return [x, y];
  });
  const baselineY = padT + plotH;
  const linePath = densityMonotonePath(pts);
  const areaPath = `M 0 ${baselineY} L ${pts[0][0]} ${baselineY} ${linePath.replace(/^M[^L]*/, "L ")} L ${W} ${baselineY} Z`;

  const axisColor = alpha(theme.palette.text.primary, dark ? 0.14 : 0.12);
  const gridColor = alpha(theme.palette.text.primary, dark ? 0.06 : 0.05);
  const tickColor = alpha(theme.palette.text.primary, dark ? 0.55 : 0.5);
  const passBand = alpha(PASS, dark ? 0.06 : 0.05);
  const areaTop = alpha(ACCENT, dark ? 0.42 : 0.5);
  const areaBottom = alpha(ACCENT, dark ? 0.02 : 0.03);
  const strokeColor = alpha(ACCENT, dark ? 0.85 : 0.9);

  const passLabelColor = dark ? alpha(PASS, 0.95) : "#0F766E";
  const thresholdPct = threshold * 100;
  const meanPct = mean * 100;

  const containerHeight = 108; // room for μ label above + axis labels below

  return (
    <Box sx={{ position: "relative", width: "100%", height: containerHeight }}>
      {/* the curve, stretchable */}
      <Box
        component="svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        sx={{
          position: "absolute",
          left: 0, right: 0, top: 8, // reserve top row for μ label
          width: "100%",
          height: H,
          display: "block",
          overflow: "visible",
        }}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={areaTop} />
            <stop offset="100%" stopColor={areaBottom} />
          </linearGradient>
        </defs>

        {/* pass-zone band */}
        <rect
          x={threshold * W} y={padT}
          width={(1 - threshold) * W} height={plotH}
          fill={passBand}
        />

        {/* mid gridline */}
        <line
          x1={0} x2={W}
          y1={padT + plotH / 2} y2={padT + plotH / 2}
          stroke={gridColor} strokeDasharray="2 4" vectorEffect="non-scaling-stroke"
        />

        {/* area + stroke */}
        <path d={areaPath} fill={`url(#${gradId})`} />
        <path d={linePath} fill="none" stroke={strokeColor} strokeWidth={1.5}
          strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />

        {/* baseline */}
        <line x1={0} x2={W} y1={baselineY} y2={baselineY}
          stroke={axisColor} vectorEffect="non-scaling-stroke" />

        {/* mean marker */}
        <line
          x1={mean * W} x2={mean * W} y1={padT} y2={baselineY}
          stroke={alpha(theme.palette.text.primary, dark ? 0.28 : 0.22)}
          strokeWidth={1} vectorEffect="non-scaling-stroke"
        />

        {/* threshold marker */}
        <line
          x1={threshold * W} x2={threshold * W} y1={padT - 4} y2={baselineY}
          stroke={alpha(PASS, dark ? 0.65 : 0.7)}
          strokeWidth={1.25} strokeDasharray="3 3" vectorEffect="non-scaling-stroke"
        />
      </Box>

      {/* HTML overlay: labels & tick text (never stretched) */}
      <Box sx={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
        {/* μ mean label — top row */}
        <Typography
          sx={{
            position: "absolute",
            left: `${meanPct}%`,
            top: 0,
            transform: "translateX(-50%)",
            typography: "s3",
            color: tickColor,
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: 0.3,
            textTransform: "uppercase",
            fontVariantNumeric: "tabular-nums",
            whiteSpace: "nowrap",
          }}
        >
          μ {meanPct.toFixed(0)}
        </Typography>

        {/* Pass ≥ 80 pill — below baseline */}
        <Box
          sx={{
            position: "absolute",
            left: `${thresholdPct}%`,
            top: 8 + baselineY + 4,
            transform: "translateX(-50%)",
            px: 0.75, py: 0.125,
            bgcolor: alpha(PASS, dark ? 0.16 : 0.14),
            borderRadius: 0.5,
          }}
        >
          <Typography sx={{
            typography: "s3",
            color: passLabelColor,
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: 0.4,
            textTransform: "uppercase",
            whiteSpace: "nowrap",
            lineHeight: 1.3,
          }}>
            Pass ≥ {thresholdPct.toFixed(0)}
          </Typography>
        </Box>

        {/* axis ticks 0 / 50 / 100 */}
        {[0, 50, 100].map((v, i) => (
          <Typography
            key={v}
            sx={{
              position: "absolute",
              left: `${v}%`,
              top: 8 + baselineY + 4,
              transform: i === 0 ? "translateX(0)" : i === 2 ? "translateX(-100%)" : "translateX(-50%)",
              typography: "s3",
              color: tickColor,
              fontSize: 9.5,
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
              lineHeight: 1.3,
            }}
          >
            {v}
          </Typography>
        ))}
      </Box>
    </Box>
  );
}
DensityChart.propTypes = {
  hist: PropTypes.array,
  peak: PropTypes.number,
  threshold: PropTypes.number,
  mean: PropTypes.number,
  dark: PropTypes.bool,
  theme: PropTypes.object,
};

function EvalCard({ grader, tasks }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const results = tasks
    .map((t) => ({ t, r: t.evalResults?.find((r) => r.id === grader.id) }))
    .filter((x) => x.r);
  const total = results.length;
  const passed = results.filter((x) => x.r.passed).length;
  const pct = total ? Math.round((passed / total) * 100) : null;

  const scores = results.map((x) => x.r.score);
  const bins = 10;
  const hist = new Array(bins).fill(0);
  scores.forEach((s) => { hist[Math.min(bins - 1, Math.floor(s * bins))] += 1; });
  const peak = Math.max(...hist, 1);

  const median = scores.length ? [...scores].sort((a, b) => a - b)[Math.floor(scores.length / 2)] : 0;
  const mean = scores.length ? scores.reduce((a, n) => a + n, 0) / scores.length : 0;
  const std = scores.length
    ? Math.sqrt(scores.reduce((a, n) => a + (n - mean) ** 2, 0) / scores.length)
    : 0;

  /* Which failure pattern the failing traces cluster in — same logic
     as elsewhere in this file. */
  const patternCount = new Map();
  results.forEach(({ t, r }) => {
    if (r.passed) return;
    let p = "Other failure";
    if (t.status === "error") p = "Errored";
    else if (t.critical) p = "Critical rule broken";
    else if (t.callLog?.unsupportedClaim) p = "Said, not done";
    else if ((t.evalResults || []).some((rr) => !rr.passed)) p = "Evaluation failed";
    patternCount.set(p, (patternCount.get(p) || 0) + 1);
  });
  const topPattern = [...patternCount.entries()].sort((a, b) => b[1] - a[1])[0];

  const tone = pct == null ? NEUTRAL : pct >= 80 ? PASS : pct >= 50 ? AMBER : FAIL;
  const threshold = 0.8;
  const softBorder = `1px solid ${alpha(theme.palette.text.primary, dark ? 0.06 : 0.05)}`;

  /* Radial gauge — hollow ring, colored arc for pct, big number inside. */
  const size = 116;
  const stroke = 10;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const dash = pct != null ? (pct / 100) * c : 0;

  /* Single hue for all bars — the shape carries the story, not the
     color. A soft background band on the pass side is what tells the
     reader "everything to the right of the threshold is a pass". */

  return (
    <Box sx={{
      border: softBorder, borderRadius: 1.5,
      bgcolor: "background.paper", overflow: "hidden",
    }}>
      {/* header */}
      <Box sx={{ px: 2.5, py: 1.75, borderBottom: softBorder }}>
        <Typography noWrap sx={{ typography: "s1", fontWeight: 700 }}>{grader.name}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, fontVariantNumeric: "tabular-nums" }}>
          {passed}/{total} passed · {total - passed} failed
          {topPattern && (
            <>
              {" · Failures land in "}
              <Box component="span" sx={{ color: FAIL, fontWeight: 700 }}>{topPattern[0]}</Box>
              {" "}({topPattern[1]})
            </>
          )}
        </Typography>
      </Box>

      {/* body: radial gauge + histogram */}
      <Box sx={{
        display: "grid", gap: 2.5,
        gridTemplateColumns: { xs: "1fr", sm: `${size + 24}px 1fr` },
        alignItems: "center", p: 2.5,
      }}>
        {/* radial gauge */}
        <Box sx={{ position: "relative", width: size, height: size, mx: "auto" }}>
          <Box component="svg" viewBox={`0 0 ${size} ${size}`} sx={{ width: size, height: size, display: "block" }}>
            <circle cx={size / 2} cy={size / 2} r={r} fill="none"
              stroke={alpha(theme.palette.text.primary, 0.06)} strokeWidth={stroke} />
            {pct != null && (
              <circle cx={size / 2} cy={size / 2} r={r} fill="none"
                stroke={tone} strokeWidth={stroke} strokeLinecap="round"
                strokeDasharray={`${dash} ${c - dash}`}
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
              />
            )}
          </Box>
          <Box sx={{
            position: "absolute", inset: 0, display: "flex",
            alignItems: "center", justifyContent: "center", flexDirection: "column",
          }}>
            <Typography sx={{
              typography: "h3", fontWeight: 700, color: tone,
              fontVariantNumeric: "tabular-nums", lineHeight: 1, fontSize: 28,
            }}>
              {pct != null ? pct : "—"}
              <Box component="span" sx={{ typography: "s2", fontWeight: 700, color: "text.subtitle", ml: 0.25 }}>%</Box>
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 10, mt: 0.25, letterSpacing: 0.4, textTransform: "uppercase" }}>
              pass rate
            </Typography>
          </Box>
        </Box>

        {/* score distribution — smoothed density area */}
        <Box sx={{ minWidth: 0 }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
            <Typography sx={{
              typography: "s3", color: "text.subtitle", fontWeight: 700,
              textTransform: "uppercase", letterSpacing: 0.4, fontSize: 10.5,
            }}>
              Score distribution
            </Typography>
            <Typography sx={{
              typography: "s3", color: "text.subtitle", fontSize: 10.5,
              fontVariantNumeric: "tabular-nums",
            }}>
              n = {total}
            </Typography>
          </Stack>
          <DensityChart
            hist={hist}
            peak={peak}
            threshold={threshold}
            mean={mean}
            dark={dark}
            theme={theme}
          />
        </Box>
      </Box>

      {/* stat strip */}
      <Box sx={{
        display: "grid", gridTemplateColumns: "repeat(4, 1fr)",
        borderTop: softBorder,
      }}>
        {[
          { label: "Median", value: (median * 100).toFixed(0) },
          { label: "Mean", value: (mean * 100).toFixed(0) },
          { label: "Std dev", value: (std * 100).toFixed(1) },
          { label: "Threshold", value: (threshold * 100).toFixed(0) },
        ].map((s, i) => (
          <Box key={s.label} sx={{
            px: 2, py: 1.25,
            borderRight: i < 3 ? softBorder : "none",
          }}>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4, fontSize: 10 }}>
              {s.label}
            </Typography>
            <Typography sx={{
              typography: "s1", fontWeight: 700, color: "text.primary",
              fontVariantNumeric: "tabular-nums", mt: 0.25,
            }}>
              {s.value}
            </Typography>
          </Box>
        ))}
      </Box>
    </Box>
  );
}
EvalCard.propTypes = { grader: PropTypes.object, tasks: PropTypes.array };

function GraderTable({ tasks, evals }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const softBorder = `1px solid ${alpha(theme.palette.text.primary, dark ? 0.06 : 0.05)}`;
  if (!evals.length) {
    return (
      <Box sx={{
        border: softBorder, borderRadius: 1.5, bgcolor: "background.paper",
        py: 4, textAlign: "center",
      }}>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>No evaluations were run.</Typography>
      </Box>
    );
  }
  return (
    <Box>
      <Box sx={{ mb: 1.5, px: 0.5 }}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Evaluations breakdown</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          Pass rate, score distribution, and dominant failure pattern per evaluation
        </Typography>
      </Box>
      <Box sx={{
        display: "grid", gap: 2,
        gridTemplateColumns: { xs: "1fr", md: evals.length > 1 ? "1fr 1fr" : "1fr" },
      }}>
        {evals.map((e) => <EvalCard key={e.id} grader={e} tasks={tasks} />)}
      </Box>
    </Box>
  );
}
GraderTable.propTypes = { tasks: PropTypes.array, evals: PropTypes.array };

/* ── shell ───────────────────────────────────────────────────────────────── */

export default function RunAnalytics({ tasks, evals, env, stats }) {
  return (
    <Stack spacing={2}>
      {/* Run overview + KPI panels sit at the top of the Analytics tab
          so the reader gets the headline story before drilling into
          failure composition. */}
      <RunMetrics env={env} tasks={tasks} stats={stats} evals={evals} activeFilter={null} onFilter={() => {}} />
      <Box sx={{
        display: "grid", gap: 2,
        gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
      }}>
        <FailureComposition tasks={tasks} />
        <TopFailingGoals tasks={tasks} />
      </Box>
      <GraderTable tasks={tasks} evals={evals} />
    </Stack>
  );
}

RunAnalytics.propTypes = { tasks: PropTypes.array, evals: PropTypes.array, env: PropTypes.object, stats: PropTypes.object };
