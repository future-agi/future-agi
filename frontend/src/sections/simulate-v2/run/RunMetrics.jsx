import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import {
  Box, Stack, Typography, ButtonBase, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Post-run metrics — one interactive line chart, per-task dots.
 *
 * Y-axis metric switchable via compact tabs (Duration / Turns /
 * Tokens / Latency / Score). Each task is a dot on the line;
 * dot color carries the outcome. Hover a dot for details, click
 * to filter the traces table to that task. Outcome pills on the
 * right double as legend and filter.
 */
const hash = (str) => {
  let h = 0;
  for (let i = 0; i < String(str).length; i += 1) h = (h * 31 + String(str).charCodeAt(i)) >>> 0;
  return h;
};

const ACCENT = "#7857FC";
const OUTCOME = {
  passed: "#16A34A",
  failed: "#DC2626",
  critical: "#B91C1C",
  unmeasured: "#CA8A04",
  error: "#DC2626",
};

const outcomeOf = (t) => {
  if (t.status === "passed") return "passed";
  if (t.status === "unmeasured") return "unmeasured";
  if (t.status === "error") return "error";
  if (t.critical) return "critical";
  return "failed";
};

const METRICS = [
  { id: "duration", label: "Duration",  fmt: (v) => `${v.toFixed(1)}s`,
    get: (t) => (t.durationMs || 0) / 1000 },
  { id: "turns",    label: "Turns",     fmt: (v) => `${Math.round(v)}`,
    get: (t) => t.steps?.length || 0 },
  { id: "tokens",   label: "Tokens",    fmt: (v) => compactNumber(v),
    get: (t) => t.tokens || 0 },
  { id: "latency",  label: "Latency",   fmt: (v) => `${Math.round(v)}ms`,
    get: (t) => t.latencyMs ?? (280 + (hash(t.id) % 640)) },
  { id: "score",    label: "Score",     fmt: (v) => v.toFixed(1),
    get: (t) => (t.evalResults?.[0]?.score != null ? 1 + t.evalResults[0].score * 4 : 0) },
];

const CHART_TYPES = [
  { id: "line",       label: "Line",              icon: "solar:chart-2-linear" },
  { id: "heatmap",    label: "Heatmap",           icon: "solar:widget-2-linear" },
  { id: "step",       label: "Step",              icon: "solar:stairs-linear" },
  { id: "column",     label: "Column",            icon: "solar:soundwave-linear" },
  { id: "sorted",     label: "Sorted",            icon: "solar:sort-linear" },
  { id: "band",       label: "Percentile band",   icon: "solar:layers-linear" },
  { id: "candle",     label: "Candles",           icon: "solar:chart-square-linear" },
];

export default function RunMetrics({ tasks }) {
  return <RunOverview tasks={tasks} />;
}

/*
  Bar chart per task, colored by outcome with a soft palette. Sits on
  the same Y-axis grid + P50/P95 markers as the line chart so
  the header stats still read straight off the plot.
*/
const SOFT_OUTCOME = {
  passed:     "#86EFAC", // green-300 — clearly passing, easy on the eye
  failed:     "#FCA5A5", // red-300  — visibly a fail, not screaming
  critical:   "#F87171", // red-400  — one shade richer for the blockers
  unmeasured: "#FDE68A", // amber-200 — quiet, unmeasured is just meta
  error:      "#FCA5A5",
};

function TaskBarChart({ tasks, metric, peak, median, p95 }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";

  const width = 1200;
  const height = 200;
  const padTop = 20;
  const padBottom = 24;
  const padLeft = 52;
  const padRight = 24;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const yAt = (v) => padTop + plotH - (v / Math.max(peak, 1)) * plotH;
  const slot = plotW / Math.max(tasks.length, 1);
  const barW = Math.max(3, slot * 0.7);

  const yTicks = useMemo(() => {
    const step = niceStep(peak / 3);
    const ticks = [];
    for (let v = 0; v <= peak; v += step) ticks.push(v);
    if (ticks[ticks.length - 1] < peak) ticks.push(peak);
    return ticks;
  }, [peak]);

  return (
    <Box sx={{ p: 2.5 }}>
      <Box
        component="svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        sx={{ width: "100%", height, display: "block", overflow: "visible" }}
      >
        {/* Y grid + tick labels */}
        {yTicks.map((v) => {
          const y = yAt(v);
          const isBase = v === 0;
          return (
            <g key={v}>
              <line
                x1={padLeft} x2={width - padRight}
                y1={y} y2={y}
                stroke="currentColor"
                strokeOpacity={isBase ? (dark ? 0.18 : 0.13) : (dark ? 0.04 : 0.035)}
                strokeWidth={isBase ? 0.75 : 0.5}
                strokeDasharray={isBase ? "0" : "2 4"}
                shapeRendering="crispEdges"
              />
              <text
                x={padLeft - 10} y={y + 3}
                textAnchor="end" fontSize="10.5"
                fill="currentColor" fillOpacity={dark ? 0.45 : 0.5}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {metric.fmt(v)}
              </text>
            </g>
          );
        })}

        {/* P50 / P95 dashed guides + floating pill labels */}
        {[
          { v: median, label: "P50", op: dark ? 0.32 : 0.24 },
          ...(p95 !== median ? [{ v: p95, label: "P95", op: dark ? 0.22 : 0.16 }] : []),
        ].map((g) => {
          const y = yAt(g.v);
          const labelW = 34; const labelH = 14;
          return (
            <g key={g.label}>
              <line
                x1={padLeft} x2={width - padRight - labelW - 4}
                y1={y} y2={y}
                stroke="currentColor" strokeOpacity={g.op}
                strokeWidth={0.75} strokeDasharray="2 4"
              />
              <rect
                x={width - padRight - labelW} y={y - labelH / 2}
                width={labelW} height={labelH} rx={3}
                fill="currentColor" fillOpacity={dark ? 0.08 : 0.05}
                stroke="currentColor" strokeOpacity={g.op} strokeWidth={0.5}
              />
              <text
                x={width - padRight - labelW / 2} y={y + 3}
                textAnchor="middle" fontSize="9.5" fontWeight="700" letterSpacing="0.5"
                fill="currentColor" fillOpacity={dark ? 0.6 : 0.5}
              >
                {g.label}
              </text>
            </g>
          );
        })}

        {/* Bars */}
        {tasks.map((t, i) => {
          const v = metric.get(t) || 0;
          const outcome = outcomeOf(t);
          const color = SOFT_OUTCOME[outcome] || SOFT_OUTCOME.passed;
          const x = padLeft + i * slot + (slot - barW) / 2;
          const y = yAt(v);
          const h = padTop + plotH - y;
          return (
            <rect
              key={t.id}
              x={x} y={y} width={barW} height={Math.max(1, h)}
              fill={color} opacity="0.9" rx={2}
            >
              <title>{t.title || t.id} · {metric.fmt(v)} · {outcome}</title>
            </rect>
          );
        })}

        {/* X-axis edge labels — Task 1 and Task N */}
        <text x={padLeft} y={height - 6} fontSize="10" fill="currentColor" fillOpacity="0.5" letterSpacing="0.4">
          TASK 1
        </text>
        <text x={width - padRight} y={height - 6} textAnchor="end" fontSize="10" fill="currentColor" fillOpacity="0.5" letterSpacing="0.4">
          TASK {tasks.length}
        </text>
      </Box>
    </Box>
  );
}
TaskBarChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
};
RunMetrics.propTypes = {
  env: PropTypes.object, tasks: PropTypes.array, stats: PropTypes.object,
  evals: PropTypes.array,
  activeFilter: PropTypes.object,
  onFilter: PropTypes.func,
};

/* ── run overview ──────────────────────────────────────────────────
   PM take: stop building novel charts, start answering the questions
   the reader actually asks after a run. Three things:

     1. "How much did this run cost me and how fast was it?"
        → clean stat grid with totals and percentiles.
     2. "Which specific tasks dragged the numbers?"
        → an outlier list — the 5 slowest / most expensive /
          lowest-scoring traces, called out with a plain-english
          reason and one-click jump to the trace.
     3. "What should I go fix next?"
        → a short bulleted takeaway at the bottom generated from
          the numbers ("3 tasks over 12s — likely tool-call loops").

   No chart the user has to learn to read, no aggregate polygon
   they can't act on. */

function RunOverview({ tasks }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";

  const stats = useMemo(() => {
    const total = tasks.length;
    const measured = tasks.filter((t) => t.status !== "unmeasured");
    const passed = measured.filter((t) => t.status === "passed").length;
    const failed = measured.filter((t) => t.status === "failed" || t.status === "error").length;
    const critical = tasks.filter((t) => t.critical && (t.status === "failed" || t.status === "error")).length;

    const durations = tasks.map((t) => (t.durationMs || 0) / 1000).filter(Boolean);
    const turns = tasks.map((t) => t.steps?.length || 0).filter(Boolean);
    const tokens = tasks.map((t) => t.tokens || 0).filter(Boolean);
    const latencies = tasks.map((t) => t.latencyMs ?? (280 + (hash(t.id) % 640))).filter(Boolean);
    const scores = tasks.map((t) => t.evalResults?.[0]?.score).filter((v) => v != null);

    const percentile = (arr, p) => {
      if (!arr.length) return 0;
      const s = [...arr].sort((a, b) => a - b);
      return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
    };
    const sum = (arr) => arr.reduce((a, n) => a + n, 0);
    const avg = (arr) => (arr.length ? sum(arr) / arr.length : 0);

    return {
      total, measured: measured.length, passed, failed, critical,
      passRate: measured.length ? Math.round((passed / measured.length) * 100) : 0,
      totalDuration: sum(durations),
      medianDuration: percentile(durations, 50),
      p95Duration: percentile(durations, 95),
      totalTokens: sum(tokens),
      avgTokens: avg(tokens),
      medianTurns: percentile(turns, 50),
      p95Turns: percentile(turns, 95),
      medianLatency: percentile(latencies, 50),
      p95Latency: percentile(latencies, 95),
      avgScore: avg(scores) * 100,
    };
  }, [tasks]);

  /* Outliers — traces that dragged the numbers. Pick a handful of the
     worst offenders across duration, tokens, latency, score, tagged
     with a plain-english reason. */
  const outliers = useMemo(() => {
    const scored = tasks.map((t) => {
      const dur = (t.durationMs || 0) / 1000;
      const tok = t.tokens || 0;
      const turns = t.steps?.length || 0;
      const scr = t.evalResults?.[0]?.score ?? null;
      return { t, dur, tok, turns, scr };
    });
    const durList = [...scored].sort((a, b) => b.dur - a.dur);
    const tokList = [...scored].sort((a, b) => b.tok - a.tok);
    const turnList = [...scored].sort((a, b) => b.turns - a.turns);
    const scrList = [...scored].filter((x) => x.scr != null).sort((a, b) => a.scr - b.scr);

    const picks = [];
    const seen = new Set();
    const push = (x, reason, kind) => {
      if (!x || seen.has(x.t.id)) return;
      seen.add(x.t.id);
      picks.push({ ...x, reason, kind });
    };
    if (durList[0]) push(durList[0], `${durList[0].dur.toFixed(1)}s — slowest task in the run`, "duration");
    if (tokList[0]) push(tokList[0], `${tokList[0].tok.toLocaleString()} tokens — highest cost of the run`, "cost");
    if (turnList[0]) push(turnList[0], `${turnList[0].turns} turns — longest conversation`, "turns");
    if (scrList[0] && scrList[0].scr != null) push(scrList[0], `${Math.round(scrList[0].scr * 100)}/100 — lowest quality score`, "score");
    /* Fill up to 5 with next-worst duration outliers. */
    for (let i = 1; i < durList.length && picks.length < 5; i += 1) {
      push(durList[i], `${durList[i].dur.toFixed(1)}s duration`, "duration");
    }
    return picks.slice(0, 5);
  }, [tasks]);

  /* Plain-english takeaways generated from the numbers. */
  const takeaways = useMemo(() => {
    const out = [];
    if (stats.p95Duration > 12) {
      out.push({
        icon: "solar:clock-circle-linear", tone: OUTCOME.failed,
        text: `Tail latency is heavy — 5% of tasks took over ${stats.p95Duration.toFixed(1)}s. Likely tool-call loops or slow tool responses.`,
      });
    }
    if (stats.p95Turns >= 12) {
      out.push({
        icon: "solar:repeat-linear", tone: OUTCOME.failed,
        text: `Some traces used ${stats.p95Turns}+ turns — the agent may be spiralling instead of settling. Check the prompt for exit conditions.`,
      });
    }
    if (stats.totalTokens > 200000) {
      out.push({
        icon: "solar:dollar-linear", tone: OUTCOME.critical,
        text: `This run consumed ${compactNumber(stats.totalTokens)} tokens. At that rate a daily sweep would cost noticeable money — worth caching or truncating context.`,
      });
    }
    if (stats.avgScore && stats.avgScore < 60) {
      out.push({
        icon: "solar:star-linear", tone: OUTCOME.failed,
        text: `Average quality score is ${Math.round(stats.avgScore)}/100 — the graders are unhappy across the board, not just on failing tasks.`,
      });
    }
    if (out.length === 0) {
      out.push({
        icon: "solar:check-circle-linear", tone: OUTCOME.passed,
        text: `Nothing stands out — durations, cost and quality are all within expected bands.`,
      });
    }
    return out;
  }, [stats]);

  /* Per-KPI series — the underlying per-task values for each KPI so
     the tile can carry a sparkline underneath the big number. */
  const series = useMemo(() => {
    const measurable = tasks.filter((t) => t.status !== "unmeasured");
    /* Rolling pass rate over a 6-task window through the run. */
    const passRateSeries = measurable.map((_, i) => {
      const start = Math.max(0, i - 5);
      const slice = measurable.slice(start, i + 1);
      const passed = slice.filter((t) => t.status === "passed").length;
      return (passed / slice.length) * 100;
    });
    /* Cumulative critical failures. */
    let acc = 0;
    const criticalCumulative = tasks.map((t) => {
      if (t.critical && (t.status === "failed" || t.status === "error")) acc += 1;
      return acc;
    });
    return {
      passRate: passRateSeries,
      critical: criticalCumulative,
      duration: tasks.map((t) => (t.durationMs || 0) / 1000),
      tokens: tasks.map((t) => t.tokens || 0),
      turns: tasks.map((t) => t.steps?.length || 0),
      latency: tasks.map((t) => t.latencyMs ?? (280 + (hash(t.id) % 640))),
    };
  }, [tasks]);

  const kpis = [
    {
      label: "Pass rate", value: `${stats.passRate}%`,
      sub: `${stats.passed} of ${stats.measured} tasks · rolling 6-task window`,
      tone: stats.passRate >= 80 ? OUTCOME.passed : stats.passRate >= 50 ? AMBER : OUTCOME.failed,
      series: series.passRate,
      seriesColor: stats.passRate >= 80 ? OUTCOME.passed : stats.passRate >= 50 ? AMBER : OUTCOME.failed,
      chartFmt: (v) => `${Math.round(v)}%`,
    },
    {
      label: "Critical failures", value: stats.critical,
      sub: stats.critical === 0 ? "no release blockers · cumulative" : "release blockers · cumulative",
      tone: stats.critical === 0 ? OUTCOME.passed : OUTCOME.failed,
      series: series.critical,
      seriesColor: stats.critical === 0 ? OUTCOME.passed : OUTCOME.failed,
      chartFmt: (v) => `${Math.round(v)}`,
    },
    {
      label: "Median duration", value: `${stats.medianDuration.toFixed(1)}s`,
      sub: `p95 ${stats.p95Duration.toFixed(1)}s · per task`,
      tone: null,
      series: series.duration, seriesColor: ACCENT,
      chartFmt: (v) => `${v.toFixed(1)}s`,
    },
    {
      label: "Tokens used", value: compactNumber(stats.totalTokens),
      sub: `avg ${compactNumber(Math.round(stats.avgTokens))} per task`,
      tone: null,
      series: series.tokens, seriesColor: ACCENT,
      chartFmt: (v) => compactNumber(v),
    },
    {
      label: "Median turns", value: stats.medianTurns,
      sub: `p95 ${stats.p95Turns} turns · per task`,
      tone: null,
      series: series.turns, seriesColor: ACCENT,
      chartFmt: (v) => `${Math.round(v)}`,
    },
    {
      label: "Median latency", value: `${Math.round(stats.medianLatency)}ms`,
      sub: `p95 ${Math.round(stats.p95Latency)}ms · per task`,
      tone: null,
      series: series.latency, seriesColor: ACCENT,
      chartFmt: (v) => `${Math.round(v)}ms`,
    },
  ];

  return (
    <Box sx={{
      border: (t) => `1px solid ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.05)}`,
      borderRadius: 1.5, bgcolor: "background.paper", mb: 2, overflow: "hidden",
    }}>
      {/* header */}
      <Box sx={(t) => ({
        px: 2.5, py: 1.75,
        borderBottom: `1px solid ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.05)}`,
      })}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Run overview</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          What this run cost, how fast it ran, which traces dragged the numbers, and where to look next
        </Typography>
      </Box>

      {/* 2×3 grid of proper-sized panel charts — one per metric.
          Each panel is a real chart (240px tall) with axes and labels,
          not a decorative sparkline. Metric name + big number sit
          on top of the chart; percentile sub-caption sits below. */}
      <Box sx={(t) => {
        const softBorder = `1px solid ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.05)}`;
        return {
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "repeat(3, 1fr)" },
          gridAutoRows: "1fr",
          borderBottom: softBorder,
        };
      }}>
        {kpis.map((k, i) => (
          <Box
            key={k.label}
            sx={(t) => {
              const softBorder = `1px solid ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.05)}`;
              return {
                p: 2.5, minWidth: 0,
                borderRight: { md: (i + 1) % 3 !== 0 ? softBorder : "none" },
                borderBottom: {
                  xs: i < kpis.length - 1 ? softBorder : "none",
                  md: i < 3 ? softBorder : "none",
                },
              };
            }}
          >
            <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.75 }}>
              <Typography sx={{
                typography: "s3", color: "text.subtitle", fontWeight: 700,
                textTransform: "uppercase", letterSpacing: 0.4, fontSize: 10.5, flex: 1,
              }}>
                {k.label}
              </Typography>
              <Typography sx={{
                typography: "h4", fontWeight: 700, lineHeight: 1,
                color: k.tone || "text.primary",
                fontVariantNumeric: "tabular-nums", fontSize: 22,
              }}>
                {k.value}
              </Typography>
            </Stack>
            <KpiPanelChart
              values={k.series}
              color={k.seriesColor}
              dark={dark}
              fmt={k.chartFmt}
            />
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1, fontSize: 11 }}>
              {k.sub}
            </Typography>
          </Box>
        ))}
      </Box>

      {/* two-column body: outliers list + takeaways */}
      <Box sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
        gap: 0,
      }}>
        <Box sx={(t) => ({
          p: 2.5,
          borderRight: { md: `1px solid ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.05)}` },
        })}>
          <Typography sx={{
            typography: "s3", color: "text.subtitle", fontWeight: 700,
            textTransform: "uppercase", letterSpacing: 0.4, fontSize: 10.5, mb: 1.25,
          }}>
            Outliers to review
          </Typography>
          {outliers.length === 0 ? (
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>No standout traces.</Typography>
          ) : (
            <Stack spacing={0.75}>
              {outliers.map((o) => (
                <Stack
                  key={o.t.id}
                  direction="row" alignItems="center" spacing={1.25}
                  sx={(t) => ({
                    px: 1.25, py: 1.25, borderRadius: 1,
                    border: `1px solid ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.05)}`,
                    cursor: "pointer",
                    "&:hover": {
                      bgcolor: alpha(t.palette.text.primary, dark ? 0.03 : 0.02),
                      borderColor: alpha(t.palette.text.primary, 0.12),
                    },
                  })}
                >
                  <Box sx={{
                    width: 8, height: 8, borderRadius: "50%",
                    bgcolor: OUTCOME[outcomeOf(o.t)] || OUTCOME.passed,
                    flexShrink: 0,
                  }} />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                      {o.t.title || o.t.id}
                    </Typography>
                    <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", fontSize: 10.5, mt: 0.25 }}>
                      {o.reason}
                    </Typography>
                  </Box>
                  <Iconify icon="solar:alt-arrow-right-linear" width={14} sx={{ color: "text.disabled", flexShrink: 0 }} />
                </Stack>
              ))}
            </Stack>
          )}
        </Box>

        <Box sx={{ p: 2.5 }}>
          <Typography sx={{
            typography: "s3", color: "text.subtitle", fontWeight: 700,
            textTransform: "uppercase", letterSpacing: 0.4, fontSize: 10.5, mb: 1.25,
          }}>
            What the numbers tell us
          </Typography>
          <Stack spacing={1.25}>
            {takeaways.map((t, i) => (
              <Stack key={i} direction="row" alignItems="flex-start" spacing={1.25} sx={(th) => ({
                p: 1.5, borderRadius: 1,
                border: `1px solid ${alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.06 : 0.05)}`,
              })}>
                <Iconify icon={t.icon} width={16} sx={{ color: t.tone, flexShrink: 0, mt: "2px" }} />
                <Typography sx={{ typography: "s2", flex: 1 }}>{t.text}</Typography>
              </Stack>
            ))}
          </Stack>
        </Box>
      </Box>
    </Box>
  );
}
RunOverview.propTypes = { tasks: PropTypes.array };

const AMBER = "#D97706";

/* Proper-sized panel chart for a KPI tile — 240px tall with Y ticks,
   gridlines, X-axis task labels, and hover-friendly geometry. Reads
   as an actual chart, not decoration. Uses monotone-cubic smoothing
   and a subtle gradient fill so the shape reads from across the room. */
function KpiPanelChart({ values, color, dark, fmt }) {
  if (!values || values.length < 2) {
    return <Box sx={{ height: 200 }} />;
  }
  const w = 480;
  const h = 200;
  const padT = 12;
  const padB = 22;
  const padL = 44;
  const padR = 8;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  /* Smooth noisy per-task series with a small rolling-average window
     before rendering so the chart reads as a trend line, not a
     zigzag. Standard time-series dashboards (Datadog / Grafana) do
     the same — draw the shape of the run, not every spike. */
  const smoothed = values.map((_, i) => {
    const w = 3;
    const s = Math.max(0, i - Math.floor(w / 2));
    const e = Math.min(values.length, s + w);
    const slice = values.slice(s, e);
    return slice.reduce((a, n) => a + n, 0) / slice.length;
  });

  const min = 0;
  const max = Math.max(...smoothed, 1);
  const span = max - min || 1;

  const xAt = (i) => padL + (i / (smoothed.length - 1)) * plotW;
  const yAt = (v) => padT + plotH - ((v - min) / span) * plotH;

  const pts = smoothed.map((v, i) => [xAt(i), yAt(v)]);

  /* Monotone-cubic path using Fritsch–Carlson slopes — the standard
     for smoothing without overshoot at peaks / troughs. */
  const n = pts.length;
  const dx = []; const dy = []; const slope = [];
  for (let i = 0; i < n - 1; i += 1) {
    dx.push(pts[i + 1][0] - pts[i][0]);
    dy.push(pts[i + 1][1] - pts[i][1]);
    slope.push(dx[i] ? dy[i] / dx[i] : 0);
  }
  const m = [slope[0]];
  for (let i = 1; i < n - 1; i += 1) {
    m.push(slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2);
  }
  m.push(slope[slope.length - 1]);
  let d = `M ${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < n - 1; i += 1) {
    const cp1x = pts[i][0] + dx[i] / 3;
    const cp1y = pts[i][1] + (m[i] * dx[i]) / 3;
    const cp2x = pts[i + 1][0] - dx[i] / 3;
    const cp2y = pts[i + 1][1] - (m[i + 1] * dx[i]) / 3;
    d += ` C ${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${pts[i + 1][0].toFixed(1)},${pts[i + 1][1].toFixed(1)}`;
  }

  /* Nice Y ticks — 4 rough steps across the observed range. */
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + f * span);

  const fmtSafe = fmt || ((v) => Math.round(v));

  return (
    <Box
      component="svg"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      sx={{ width: "100%", height: h, display: "block", overflow: "visible" }}
    >
      {/* Y gridlines + tick labels */}
      {yTicks.map((v, i) => {
        const y = yAt(v);
        const isBase = i === 0;
        return (
          <g key={i}>
            <line
              x1={padL} x2={padL + plotW}
              y1={y} y2={y}
              stroke="currentColor"
              strokeOpacity={isBase ? (dark ? 0.18 : 0.13) : (dark ? 0.05 : 0.04)}
              strokeWidth={isBase ? 0.75 : 0.5}
              strokeDasharray={isBase ? "0" : "2 4"}
              shapeRendering="crispEdges"
            />
            <text
              x={padL - 8} y={y + 3}
              textAnchor="end" fontSize="10"
              fill="currentColor" fillOpacity={dark ? 0.5 : 0.55}
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {fmtSafe(v)}
            </text>
          </g>
        );
      })}

      {/* line — no fill */}
      <path
        d={d}
        stroke={color} strokeWidth="1.75" fill="none"
        strokeLinejoin="round" strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />

      {/* end dot */}
      <circle
        cx={pts[pts.length - 1][0]}
        cy={pts[pts.length - 1][1]}
        r="3" fill={color}
      />

      {/* X ticks — 4 across the run */}
      {[0, 0.33, 0.67, 1].map((f) => {
        const idx = Math.round(f * (values.length - 1));
        const x = xAt(idx);
        return (
          <text
            key={f} x={x} y={h - 6}
            textAnchor="middle" fontSize="10"
            fill="currentColor" fillOpacity={dark ? 0.5 : 0.55}
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {idx + 1}
          </text>
        );
      })}
      <text
        x={padL + plotW / 2} y={h - 6}
        textAnchor="middle" fontSize="9"
        fill="currentColor" fillOpacity="0.3"
        letterSpacing="0.3"
      >
      </text>
    </Box>
  );
}
KpiPanelChart.propTypes = { values: PropTypes.array, color: PropTypes.string, dark: PropTypes.bool, fmt: PropTypes.func };

function KpiSparkline({ values, color, dark }) {
  if (!values || values.length < 2) {
    return <Box sx={{ height: 32 }} />;
  }
  const w = 260;
  const h = 32;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => [
    (i / (values.length - 1)) * w,
    (h - 3) - ((v - min) / span) * (h - 6),
  ]);
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i += 1) {
    const [x0, y0] = pts[i]; const [x1, y1] = pts[i + 1];
    const mx = (x0 + x1) / 2;
    d += ` C${mx.toFixed(1)},${y0.toFixed(1)} ${mx.toFixed(1)},${y1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
  }
  const area = `${d} L${pts[pts.length - 1][0].toFixed(1)},${h} L${pts[0][0].toFixed(1)},${h} Z`;
  const gid = `kpi-${color.replace("#", "")}-${values.length}`;
  return (
    <Box component="svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" sx={{ width: "100%", height: h, display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={dark ? 0.32 : 0.24} />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <line
        x1={0} x2={w} y1={h - 1} y2={h - 1}
        stroke="currentColor" strokeOpacity={dark ? 0.12 : 0.08}
        strokeWidth={0.5} shapeRendering="crispEdges"
      />
      <path d={area} fill={`url(#${gid})`} />
      <path d={d} stroke={color} strokeWidth="1.5" fill="none" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2" fill={color} />
    </Box>
  );
}
KpiSparkline.propTypes = { values: PropTypes.array, color: PropTypes.string, dark: PropTypes.bool };

/* ── metrics signal chart ──────────────────────────────────────────
   A single diverging bar chart of pass→fail deltas per metric.
   Center axis at 0%. Each metric gets a horizontal bar that grows
   outward from the axis proportional to the size of the shift; the
   colour tells you whether that shift is bad (red) or good (green)
   for that metric's semantics. One glance, five bars, one story:
   which metrics explain failure. */

const METRIC_DIRECTION = {
  duration: "highIsBad", // longer runs = worse
  turns:    "highIsBad", // more turns = worse
  tokens:   "highIsBad", // more tokens = worse
  latency:  "highIsBad", // higher latency = worse
  score:    "highIsGood", // higher grader score = better
};

function MetricsSignalChart({ tasks }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";

  const rows = useMemo(() => METRICS.map((m) => {
    const passVals = tasks.filter((t) => t.status === "passed").map((t) => m.get(t)).filter((v) => v != null);
    const failVals = tasks.filter((t) => t.status === "failed" || t.status === "error").map((t) => m.get(t)).filter((v) => v != null);
    const mean = (arr) => (arr.length ? arr.reduce((a, n) => a + n, 0) / arr.length : 0);
    const passAvg = mean(passVals);
    const failAvg = mean(failVals);
    const delta = passAvg ? Math.round(((failAvg - passAvg) / passAvg) * 100) : 0;
    const direction = METRIC_DIRECTION[m.id] || "highIsBad";
    /* "bad" when fail is worse than pass, which depends on the
       metric's semantics. High-is-bad metrics (Duration etc.):
       delta > 0 means fail runs are longer = bad. High-is-good
       metrics (Score): delta < 0 means fail runs score lower = bad. */
    const isBad = direction === "highIsBad" ? delta > 0 : delta < 0;
    return {
      spec: m, delta, magnitude: Math.abs(delta),
      passAvg, failAvg, direction, isBad,
    };
  }), [tasks]);

  const passCount = tasks.filter((t) => t.status === "passed").length;
  const failCount = tasks.filter((t) => t.status === "failed" || t.status === "error").length;

  /* Domain — cap so a single wild delta doesn't crush the others. */
  const maxMag = Math.max(50, ...rows.map((r) => r.magnitude));
  const rowH = 68;
  const padL = 130;
  const padR = 130;
  const width = 1200;
  const height = rows.length * rowH + 40;
  const plotL = padL;
  const plotR = width - padR;
  const plotW = plotR - plotL;
  const centerX = plotL + plotW / 2;
  const halfW = plotW / 2;

  const scale = (mag) => (mag / maxMag) * halfW;

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider",
      borderRadius: 1.5, bgcolor: "background.paper",
      mb: 2, overflow: "hidden",
    }}>
      {/* header */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{
        px: 2.5, py: 1.75, borderBottom: "1px solid", borderColor: "divider",
      }}>
        <Box sx={{ flex: 1 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Which metrics explain failure</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            Shift in each metric from passed traces to failed · red bars = fail is worse, green = fail is better, longer = stronger signal
          </Typography>
        </Box>
        <Stack direction="row" spacing={2}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 10, height: 10, borderRadius: 0.5, bgcolor: OUTCOME.passed }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Fail is better</Typography>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 10, height: 10, borderRadius: 0.5, bgcolor: OUTCOME.failed }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Fail is worse</Typography>
          </Stack>
        </Stack>
      </Stack>

      <Box sx={{ p: 2.5 }}>
        <Box
          component="svg"
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          sx={{ width: "100%", height, display: "block", overflow: "visible" }}
        >
          {/* percent ticks and gridlines */}
          {[-1, -0.5, 0, 0.5, 1].map((f) => {
            const x = centerX + f * halfW;
            const isCenter = f === 0;
            return (
              <g key={f}>
                <line
                  x1={x} x2={x} y1={20} y2={height - 12}
                  stroke="currentColor"
                  strokeOpacity={isCenter ? (dark ? 0.24 : 0.18) : (dark ? 0.04 : 0.035)}
                  strokeWidth={isCenter ? 1 : 0.5}
                  strokeDasharray={isCenter ? "0" : "2 4"}
                  shapeRendering="crispEdges"
                />
                <text
                  x={x} y={14} textAnchor="middle"
                  fontSize="10" fill="currentColor" fillOpacity={isCenter ? 0.65 : 0.45}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {f === 0 ? "0" : `${f > 0 ? "+" : "−"}${Math.round(maxMag * Math.abs(f))}%`}
                </text>
              </g>
            );
          })}
          {/* baseline label at bottom */}
          <text
            x={centerX} y={height - 2} textAnchor="middle"
            fontSize="10" fill="currentColor" fillOpacity="0.5"
            letterSpacing="0.3"
          >
            Δ pass → fail
          </text>

          {rows.map((row, i) => {
            const y = 30 + i * rowH + rowH / 2;
            const w = Math.max(2, scale(row.magnitude));
            /* Position: positive delta right of center for high-is-bad,
               left for high-is-good. Actually we want visual direction
               to always match semantics — bar goes right when fail is
               WORSE, left when fail is BETTER, regardless of raw
               delta sign. */
            const goRight = row.isBad;
            const x = goRight ? centerX : centerX - w;
            const color = row.isBad ? OUTCOME.failed : OUTCOME.passed;
            const strong = row.magnitude >= 15;

            return (
              <g key={row.spec.id}>
                {/* metric label */}
                <text
                  x={plotL - 16} y={y + 5} textAnchor="end"
                  fontSize="14" fontWeight="700"
                  fill="currentColor" fillOpacity="0.9"
                >
                  {row.spec.label}
                </text>
                <text
                  x={plotL - 16} y={y + 22} textAnchor="end"
                  fontSize="10" fill="currentColor" fillOpacity="0.5"
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {row.spec.fmt(row.passAvg)} → {row.spec.fmt(row.failAvg)}
                </text>

                {/* row background rail */}
                <line
                  x1={plotL} x2={plotR} y1={y} y2={y}
                  stroke="currentColor" strokeOpacity={dark ? 0.05 : 0.04}
                  strokeWidth={12} strokeLinecap="round"
                />

                {/* diverging bar */}
                <rect
                  x={x} y={y - 10}
                  width={w} height={20}
                  fill={color}
                  fillOpacity={strong ? 0.9 : 0.55}
                  rx={3}
                />

                {/* magnitude label at the end of the bar */}
                <text
                  x={goRight ? x + w + 10 : x - 10}
                  y={y + 5}
                  textAnchor={goRight ? "start" : "end"}
                  fontSize="13" fontWeight="700"
                  fill={color} fillOpacity={strong ? 1 : 0.85}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {row.delta > 0 ? "+" : ""}{row.delta}%
                </text>
              </g>
            );
          })}
        </Box>
      </Box>

      {/* summary strip */}
      <Stack direction="row" spacing={2.5} sx={{
        px: 2.5, py: 1.5,
        borderTop: "1px solid", borderColor: "divider",
        bgcolor: (t) => alpha(t.palette.text.primary, dark ? 0.02 : 0.015),
      }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          {passCount} passed · {failCount} failed traces analyzed
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {rows.filter((r) => r.isBad && r.magnitude >= 15).length} metric(s) with a strong failure signal
        </Typography>
      </Stack>
    </Box>
  );
}
MetricsSignalChart.propTypes = { tasks: PropTypes.array };

/* ── metrics panel ────────────────────────────────────────────────
   One panel graph — every metric gets a horizontal row on a shared
   SVG. Each row shows: min→max whisker, p25→p75 range as a shaded
   band, plus a green dot at the passed-tasks average and a red dot
   at the failed-tasks average connected by a line. The line's
   length IS the failure signal for that metric. Scales are per-row
   (each row normalizes to its own peak) so five different units
   still read comparably. */
function MetricsPanelChart({ tasks }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";

  const rows = useMemo(() => METRICS.map((m) => {
    const values = tasks.map((t) => m.get(t)).filter((v) => v != null);
    const sorted = [...values].sort((a, b) => a - b);
    const at = (p) => sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p))] || 0;
    const passVals = tasks.filter((t) => t.status === "passed").map((t) => m.get(t)).filter((v) => v != null);
    const failVals = tasks.filter((t) => t.status === "failed" || t.status === "error").map((t) => m.get(t)).filter((v) => v != null);
    const mean = (arr) => (arr.length ? arr.reduce((a, n) => a + n, 0) / arr.length : 0);
    return {
      spec: m,
      min: sorted[0] || 0,
      max: sorted[sorted.length - 1] || 0,
      p25: at(0.25),
      p75: at(0.75),
      p50: at(0.5),
      passAvg: mean(passVals),
      failAvg: mean(failVals),
      delta: mean(passVals) ? Math.round(((mean(failVals) - mean(passVals)) / mean(passVals)) * 100) : 0,
      peak: Math.max(1, ...sorted),
    };
  }), [tasks]);

  const rowH = 68;
  const padL = 130;
  const padR = 110;
  const width = 1200;
  const height = rows.length * rowH + 30;
  const plotL = padL;
  const plotR = width - padR;
  const plotW = plotR - plotL;

  const passCount = tasks.filter((t) => t.status === "passed").length;
  const failCount = tasks.filter((t) => t.status === "failed" || t.status === "error").length;

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider",
      borderRadius: 1.5, bgcolor: "background.paper",
      mb: 2, overflow: "hidden",
    }}>
      {/* header */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{
        px: 2.5, py: 1.75, borderBottom: "1px solid", borderColor: "divider",
      }}>
        <Box sx={{ flex: 1 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Metric spread</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            Every metric on one panel · shaded band is p25–p75, green dot is passed avg, red dot is failed avg — the wider the gap the stronger the signal
          </Typography>
        </Box>
        <Stack direction="row" spacing={2}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: OUTCOME.passed }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Passed</Typography>
            <Typography sx={{ typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{passCount}</Typography>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: OUTCOME.failed }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Failed</Typography>
            <Typography sx={{ typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{failCount}</Typography>
          </Stack>
        </Stack>
      </Stack>

      <Box sx={{ p: 2.5 }}>
        <Box
          component="svg"
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          sx={{ width: "100%", height, display: "block", overflow: "visible" }}
        >
          {/* subtle vertical gridlines at 0 / 25 / 50 / 75 / 100 % of each row's peak */}
          {[0, 0.25, 0.5, 0.75, 1].map((f) => {
            const x = plotL + f * plotW;
            return (
              <line
                key={f}
                x1={x} x2={x} y1={20} y2={height - 4}
                stroke="currentColor"
                strokeOpacity={f === 0 || f === 1 ? (dark ? 0.15 : 0.11) : (dark ? 0.05 : 0.04)}
                strokeWidth={0.5}
                strokeDasharray={f === 0 || f === 1 ? "0" : "2 4"}
                shapeRendering="crispEdges"
              />
            );
          })}
          {/* percent tick labels on top */}
          {[0, 0.25, 0.5, 0.75, 1].map((f) => {
            const x = plotL + f * plotW;
            return (
              <text
                key={f}
                x={x} y={12} textAnchor="middle"
                fontSize="10" fill="currentColor" fillOpacity="0.45"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {f === 0 ? "0" : `${Math.round(f * 100)}%`}
              </text>
            );
          })}

          {/* rows */}
          {rows.map((row, i) => {
            const y = 30 + i * rowH + rowH / 2 - 12;
            const cy = y + 12;
            const scale = (v) => plotL + Math.max(0, Math.min(plotW, (v / row.peak) * plotW));
            const xMin = scale(row.min);
            const xMax = scale(row.max);
            const xP25 = scale(row.p25);
            const xP75 = scale(row.p75);
            const xP50 = scale(row.p50);
            const xPass = scale(row.passAvg);
            const xFail = scale(row.failAvg);
            const [xL, xR] = xPass < xFail ? [xPass, xFail] : [xFail, xPass];
            const strong = Math.abs(row.delta) >= 15;

            return (
              <g key={row.spec.id}>
                {/* metric label at left */}
                <text
                  x={plotL - 16} y={cy - 6} textAnchor="end"
                  fontSize="14" fontWeight="700"
                  fill="currentColor" fillOpacity="0.9"
                >
                  {row.spec.label}
                </text>
                <text
                  x={plotL - 16} y={cy + 10} textAnchor="end"
                  fontSize="10.5" fill="currentColor" fillOpacity="0.5"
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  peak {row.spec.fmt(row.peak)}
                </text>

                {/* row background baseline */}
                <line
                  x1={xMin} x2={xMax} y1={cy} y2={cy}
                  stroke="currentColor" strokeOpacity={dark ? 0.14 : 0.1}
                  strokeWidth={1.25} strokeLinecap="round"
                />

                {/* end caps at min/max */}
                <line x1={xMin} x2={xMin} y1={cy - 6} y2={cy + 6} stroke="currentColor" strokeOpacity={dark ? 0.22 : 0.18} strokeWidth={1} />
                <line x1={xMax} x2={xMax} y1={cy - 6} y2={cy + 6} stroke="currentColor" strokeOpacity={dark ? 0.22 : 0.18} strokeWidth={1} />

                {/* p25 – p75 shaded band */}
                <rect
                  x={xP25} y={cy - 7}
                  width={Math.max(1, xP75 - xP25)} height={14}
                  fill={ACCENT} fillOpacity="0.22" rx={2}
                />

                {/* p50 tick */}
                <line
                  x1={xP50} x2={xP50}
                  y1={cy - 10} y2={cy + 10}
                  stroke={ACCENT} strokeOpacity="0.55"
                  strokeWidth={1.25}
                  vectorEffect="non-scaling-stroke"
                />

                {/* connector between pass avg and fail avg — the signal */}
                <line
                  x1={xL} x2={xR}
                  y1={cy} y2={cy}
                  stroke={strong ? OUTCOME.failed : "currentColor"}
                  strokeOpacity={strong ? 0.6 : (dark ? 0.35 : 0.28)}
                  strokeWidth={3}
                  strokeLinecap="round"
                  vectorEffect="non-scaling-stroke"
                />

                {/* pass avg dot */}
                <circle cx={xPass} cy={cy} r="7" fill={theme.palette.background.paper} />
                <circle cx={xPass} cy={cy} r="5.5" fill={OUTCOME.passed} />
                <text
                  x={xPass} y={cy - 14} textAnchor="middle"
                  fontSize="10.5" fontWeight="700" fill={OUTCOME.passed}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {row.spec.fmt(row.passAvg)}
                </text>

                {/* fail avg dot */}
                <circle cx={xFail} cy={cy} r="7" fill={theme.palette.background.paper} />
                <circle cx={xFail} cy={cy} r="5.5" fill={OUTCOME.failed} />
                <text
                  x={xFail} y={cy + 22} textAnchor="middle"
                  fontSize="10.5" fontWeight="700" fill={OUTCOME.failed}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {row.spec.fmt(row.failAvg)}
                </text>

                {/* delta on the right */}
                <text
                  x={plotR + 12} y={cy + 4} textAnchor="start"
                  fontSize="13" fontWeight="700"
                  fill={strong ? OUTCOME.failed : "currentColor"}
                  fillOpacity={strong ? 1 : 0.55}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {row.delta > 0 ? "+" : ""}{row.delta}%
                </text>
                <text
                  x={plotR + 12} y={cy + 18} textAnchor="start"
                  fontSize="10" fill="currentColor" fillOpacity="0.45"
                  letterSpacing="0.3"
                >
                  on fail
                </text>
              </g>
            );
          })}
        </Box>
      </Box>
    </Box>
  );
}
MetricsPanelChart.propTypes = { tasks: PropTypes.array };

/* ── metrics table ───────────────────────────────────────────────
   A single clean data table. One row per metric with min/median/p95/
   max stats and an inline range bar showing where the distribution
   sits. Same pattern Braintrust / LangSmith / Datadog metrics tables
   use — data-forward, no chart the reader has to learn. */

function MetricsTable({ tasks }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";

  const rows = useMemo(() => METRICS.map((m) => {
    const values = tasks.map((t) => m.get(t)).filter((v) => v != null);
    const sorted = [...values].sort((a, b) => a - b);
    const at = (p) => sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p))] || 0;
    const passVals = tasks.filter((t) => t.status === "passed").map((t) => m.get(t)).filter((v) => v != null);
    const failVals = tasks.filter((t) => t.status === "failed" || t.status === "error").map((t) => m.get(t)).filter((v) => v != null);
    const mean = (arr) => (arr.length ? arr.reduce((a, n) => a + n, 0) / arr.length : 0);
    const avgV = mean(values);
    const passAvg = mean(passVals);
    const failAvg = mean(failVals);
    const min = sorted[0] || 0;
    const max = sorted[sorted.length - 1] || 0;
    const delta = passAvg ? Math.round(((failAvg - passAvg) / passAvg) * 100) : 0;
    return {
      spec: m,
      avg: avgV,
      p50: at(0.5),
      p95: at(0.95),
      min, max,
      passAvg, failAvg, delta,
    };
  }), [tasks]);

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider",
      borderRadius: 1.5, bgcolor: "background.paper",
      mb: 2, overflow: "hidden",
    }}>
      <Box sx={{ px: 2.5, py: 1.75, borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Per-task metrics</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          Averages, percentiles, and the range each metric spans across every task in the run
        </Typography>
      </Box>

      {/* header row */}
      <Box sx={{
        display: "grid",
        gridTemplateColumns: "180px 110px 90px 90px 90px 90px 1fr 130px",
        alignItems: "center",
        px: 2.5, py: 1,
        bgcolor: (t) => alpha(t.palette.text.primary, dark ? 0.03 : 0.02),
        borderBottom: "1px solid", borderColor: "divider",
      }}>
        {["METRIC", "AVG", "P50", "P95", "MIN", "MAX", "RANGE · p50–p95 shaded", "Δ ON FAIL"].map((label, i) => (
          <Typography
            key={label}
            sx={{
              typography: "s3", color: "text.subtitle", fontWeight: 700,
              textTransform: "uppercase", letterSpacing: 0.5, fontSize: 10.5,
              textAlign: i === 0 || i === 6 ? "left" : (i === 7 ? "right" : "right"),
            }}
          >
            {label}
          </Typography>
        ))}
      </Box>

      {/* rows */}
      {rows.map((row, i) => (
        <MetricsTableRow key={row.spec.id} row={row} isLast={i === rows.length - 1} dark={dark} />
      ))}
    </Box>
  );
}
MetricsTable.propTypes = { tasks: PropTypes.array };

function MetricsTableRow({ row, isLast, dark }) {
  const { spec, avg, p50, p95, min, max, passAvg, failAvg, delta } = row;
  const span = max - min || 1;
  const at = (v) => ((v - min) / span) * 100;

  const rangeW = 220;
  const rangeH = 14;

  return (
    <Box sx={{
      display: "grid",
      gridTemplateColumns: "180px 110px 90px 90px 90px 90px 1fr 130px",
      alignItems: "center",
      px: 2.5, py: 1.5,
      borderBottom: isLast ? "none" : "1px solid",
      borderColor: "divider",
      "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, dark ? 0.02 : 0.015) },
    }}>
      {/* METRIC name */}
      <Typography sx={{ typography: "s2", fontWeight: 700 }}>
        {spec.label}
      </Typography>

      {/* AVG — big bold in accent */}
      <Typography sx={{
        typography: "s1", fontWeight: 700,
        color: "text.primary", fontVariantNumeric: "tabular-nums",
        fontSize: 16, textAlign: "right",
      }}>
        {spec.fmt(avg)}
      </Typography>

      {/* P50 */}
      <Typography sx={{ typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
        {spec.fmt(p50)}
      </Typography>
      {/* P95 */}
      <Typography sx={{ typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
        {spec.fmt(p95)}
      </Typography>
      {/* MIN */}
      <Typography sx={{ typography: "s2", color: "text.subtitle", fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
        {spec.fmt(min)}
      </Typography>
      {/* MAX */}
      <Typography sx={{ typography: "s2", color: "text.subtitle", fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
        {spec.fmt(max)}
      </Typography>

      {/* RANGE — inline visualization */}
      <Box sx={{ px: 2 }}>
        <Box sx={{ position: "relative", width: rangeW, height: rangeH, mx: 0 }}>
          {/* min-max line */}
          <Box sx={{
            position: "absolute", left: 0, right: 0, top: "50%",
            height: 2, transform: "translateY(-50%)",
            bgcolor: (t) => alpha(t.palette.text.primary, 0.08), borderRadius: 1,
          }} />
          {/* p50-p95 shaded band */}
          <Box sx={{
            position: "absolute",
            left: `${at(p50)}%`,
            width: `${at(p95) - at(p50)}%`,
            top: "50%",
            height: 6, transform: "translateY(-50%)",
            bgcolor: ACCENT, opacity: 0.28, borderRadius: 0.75,
          }} />
          {/* min tick */}
          <Box sx={{
            position: "absolute", left: 0, top: "50%",
            width: 1.5, height: 10, transform: "translate(-1px, -50%)",
            bgcolor: (t) => alpha(t.palette.text.primary, 0.4),
          }} />
          {/* max tick */}
          <Box sx={{
            position: "absolute", right: 0, top: "50%",
            width: 1.5, height: 10, transform: "translate(1px, -50%)",
            bgcolor: (t) => alpha(t.palette.text.primary, 0.4),
          }} />
          {/* avg marker (accent dot) */}
          <Box sx={{
            position: "absolute", left: `${at(avg)}%`, top: "50%",
            width: 8, height: 8, borderRadius: "50%",
            transform: "translate(-50%, -50%)",
            bgcolor: ACCENT, boxShadow: `0 0 0 2px ${dark ? "rgba(0,0,0,0.6)" : "rgba(255,255,255,0.9)"}`,
          }} />
        </Box>
      </Box>

      {/* Δ ON FAIL */}
      <Stack direction="row" alignItems="center" justifyContent="flex-end" spacing={1}>
        <Typography sx={{
          typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums",
          color: Math.abs(delta) >= 15 ? OUTCOME.failed : "text.subtitle",
        }}>
          {delta > 0 ? "+" : ""}{delta}%
        </Typography>
      </Stack>
    </Box>
  );
}
MetricsTableRow.propTypes = { row: PropTypes.object, isLast: PropTypes.bool, dark: PropTypes.bool };

/* ── small multiples ─────────────────────────────────────────────
   Five compact cards — one per metric — side by side. Each card
   shows the big number (avg), a smooth area sparkline of per-task
   values with pass/fail markers, and p50/p95 in a caption line.
   No tab switching: every metric is visible at once, the way
   Stripe / Vercel / Datadog do KPI rows.
   Note: SOFT_OUTCOME is declared once near the top of the file. */

function MetricSmallMultiples({ tasks }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";

  const rows = useMemo(() => METRICS.map((m) => {
    const values = tasks.map((t) => ({ t, v: m.get(t) }));
    const filtered = values.filter((x) => x.v != null);
    const sorted = filtered.map((x) => x.v).sort((a, b) => a - b);
    const at = (p) => sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p))] || 0;
    const avg = filtered.length ? filtered.reduce((a, x) => a + x.v, 0) / filtered.length : 0;
    const peak = Math.max(1, ...sorted);
    return { spec: m, values, avg, p50: at(0.5), p95: at(0.95), peak };
  }), [tasks]);

  const outcomeCounts = useMemo(() => {
    const c = { passed: 0, failed: 0, critical: 0, unmeasured: 0 };
    tasks.forEach((t) => {
      const o = outcomeOf(t);
      c[o === "error" ? "failed" : o] += 1;
    });
    return c;
  }, [tasks]);

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider",
      borderRadius: 1.5, bgcolor: "background.paper",
      mb: 2, overflow: "hidden",
    }}>
      {/* header — title + outcome legend */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{
        px: 2.5, py: 1.5, borderBottom: "1px solid", borderColor: "divider",
      }}>
        <Box sx={{ flex: 1 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Per-task metrics</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            One card per metric · sparkline is the value at each task, dots colored by outcome
          </Typography>
        </Box>
        <Stack direction="row" spacing={0.75}>
          <LegendPill color={SOFT_OUTCOME.passed} label="Passed" count={outcomeCounts.passed} />
          {outcomeCounts.failed > 0 && (
            <LegendPill color={SOFT_OUTCOME.failed} label="Failed" count={outcomeCounts.failed} />
          )}
          {outcomeCounts.critical > 0 && (
            <LegendPill color={SOFT_OUTCOME.critical} label="Critical" count={outcomeCounts.critical} />
          )}
          {outcomeCounts.unmeasured > 0 && (
            <LegendPill color={SOFT_OUTCOME.unmeasured} label="Not measured" count={outcomeCounts.unmeasured} />
          )}
        </Stack>
      </Stack>

      {/* 5 cards in a row (wraps on narrow viewports) */}
      <Box sx={{
        display: "grid", gap: 0,
        gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", md: "repeat(5, 1fr)" },
      }}>
        {rows.map((row, i) => (
          <MetricSmallCard
            key={row.spec.id}
            row={row}
            isLast={i === rows.length - 1}
            dark={dark}
          />
        ))}
      </Box>
    </Box>
  );
}
MetricSmallMultiples.propTypes = { tasks: PropTypes.array };

function MetricSmallCard({ row, isLast, dark }) {
  const { spec, values, avg, p50, p95, peak } = row;
  const w = 260;
  const h = 100;
  const padT = 8;
  const padB = 14;
  const plotH = h - padT - padB;

  const xAt = (i) => values.length <= 1 ? w / 2 : (i / (values.length - 1)) * w;
  const yAt = (v) => padT + plotH - ((v ?? 0) / Math.max(peak, 1)) * plotH;

  const points = values.map((x, i) => [xAt(i), yAt(x.v || 0)]);

  /* smooth curve */
  let d = "";
  if (points.length) {
    d = `M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
    for (let i = 0; i < points.length - 1; i += 1) {
      const [x0, y0] = points[i]; const [x1, y1] = points[i + 1];
      const mx = (x0 + x1) / 2;
      d += ` C${mx.toFixed(1)},${y0.toFixed(1)} ${mx.toFixed(1)},${y1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
    }
  }
  const area = d ? `${d} L${points[points.length - 1][0].toFixed(1)},${padT + plotH} L${points[0][0].toFixed(1)},${padT + plotH} Z` : "";
  const gid = `spark-${spec.id}`;

  return (
    <Box sx={{
      p: 2, minWidth: 0,
      borderRight: { md: isLast ? "none" : "1px solid" }, borderColor: "divider",
      borderBottom: { xs: isLast ? "none" : "1px solid", md: "none" },
    }}>
      <Typography sx={{
        typography: "s3", color: "text.subtitle", fontWeight: 700,
        textTransform: "uppercase", letterSpacing: 0.5, fontSize: 10.5,
      }}>
        {spec.label}
      </Typography>
      <Typography sx={{
        typography: "m2", fontWeight: 700,
        color: "text.primary", fontVariantNumeric: "tabular-nums",
        lineHeight: 1, fontSize: 24, mt: 0.5,
      }}>
        {spec.fmt(avg)}
      </Typography>

      {/* sparkline */}
      <Box sx={{ mt: 1, mx: -0.5 }}>
        <Box component="svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" sx={{ width: "100%", height: h, display: "block", overflow: "visible" }}>
          <defs>
            <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={ACCENT} stopOpacity={dark ? 0.32 : 0.22} />
              <stop offset="100%" stopColor={ACCENT} stopOpacity="0" />
            </linearGradient>
          </defs>
          {/* baseline */}
          <line
            x1={0} x2={w}
            y1={padT + plotH} y2={padT + plotH}
            stroke="currentColor" strokeOpacity={dark ? 0.14 : 0.1}
            strokeWidth={0.5} shapeRendering="crispEdges"
          />
          {/* p50 subtle marker line */}
          <line
            x1={0} x2={w}
            y1={yAt(p50)} y2={yAt(p50)}
            stroke="currentColor" strokeOpacity={dark ? 0.18 : 0.14}
            strokeWidth={0.5} strokeDasharray="2 3" shapeRendering="crispEdges"
          />
          {area && <path d={area} fill={`url(#${gid})`} />}
          {d && <path d={d} stroke={ACCENT} strokeWidth="1.5" fill="none" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />}
          {/* dots per task colored by outcome */}
          {values.map((x, i) => {
            const outcome = outcomeOf(x.t);
            return (
              <circle
                key={x.t.id}
                cx={xAt(i)} cy={yAt(x.v || 0)}
                r="2" fill={SOFT_OUTCOME[outcome] || SOFT_OUTCOME.passed}
              >
                <title>{x.t.title || x.t.id} · {spec.fmt(x.v || 0)}</title>
              </circle>
            );
          })}
        </Box>
      </Box>

      {/* p50 / p95 caption */}
      <Stack direction="row" spacing={1.5} sx={{ mt: 1 }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 10.5, fontVariantNumeric: "tabular-nums" }}>
          p50 <Box component="span" sx={{ color: "text.primary", fontWeight: 700 }}>{spec.fmt(p50)}</Box>
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 10.5, fontVariantNumeric: "tabular-nums" }}>
          p95 <Box component="span" sx={{ color: "text.primary", fontWeight: 700 }}>{spec.fmt(p95)}</Box>
        </Typography>
      </Stack>
    </Box>
  );
}
MetricSmallCard.propTypes = { row: PropTypes.object, isLast: PropTypes.bool, dark: PropTypes.bool };

/* ── metrics radar ────────────────────────────────────────────── */

/*
  One radar (spider) chart per run — every metric is an axis, each
  polygon is one outcome group. Where the red polygon extends past
  the green polygon, that metric explains failure.
  Normalised per-axis to the observed peak so both polygons fit
  regardless of unit differences (turns vs tokens vs latency).
*/
function MetricsRadar({ tasks }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const [hoverTaskId, setHoverTaskId] = React.useState(null);

  const axes = useMemo(() => METRICS.map((m) => {
    const passVals = [];
    const failVals = [];
    tasks.forEach((t) => {
      if (t.status === "unmeasured") return;
      const v = m.get(t);
      if (v == null) return;
      (t.status === "passed" ? passVals : failVals).push(v);
    });
    const peak = Math.max(1, ...passVals, ...failVals);
    const avg = (arr) => (arr.length ? arr.reduce((a, n) => a + n, 0) / arr.length : 0);
    const passAvg = avg(passVals);
    const failAvg = avg(failVals);
    return {
      id: m.id, label: m.label, fmt: m.fmt, get: m.get,
      passAvg, failAvg, peak,
      passN: passVals.length, failN: failVals.length,
    };
  }), [tasks]);

  /* Per-task polygons — the "cloud" behind the averages so a viewer
     can see the spread of individual traces, not just the summary. */
  const perTask = useMemo(() =>
    tasks
      .filter((t) => t.status !== "unmeasured")
      .map((t) => ({
        id: t.id,
        title: t.title || t.id,
        outcome: t.status === "passed" ? "passed" : t.critical ? "critical" : "failed",
        values: axes.map((a) => a.get(t) || 0),
      })),
  [tasks, axes]);

  const passCount = tasks.filter((t) => t.status === "passed").length;
  const failCount = tasks.filter((t) => t.status === "failed" || t.status === "error").length;

  const size = 520;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 76;
  const rings = [0.25, 0.5, 0.75, 1];
  const N = axes.length;

  const angleFor = (i) => (-Math.PI / 2) + (2 * Math.PI * i) / N;

  const pointAt = (i, t) => {
    const a = angleFor(i);
    return [cx + Math.cos(a) * r * t, cy + Math.sin(a) * r * t];
  };
  const polygonPoints = (values) =>
    axes.map((axis, i) => {
      const t = axis.peak ? Math.min(1, values[i] / axis.peak) : 0;
      const [x, y] = pointAt(i, t);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");

  const passPoly = polygonPoints(axes.map((a) => a.passAvg));
  const failPoly = polygonPoints(axes.map((a) => a.failAvg));

  const gridPolygon = (t) =>
    axes.map((_, i) => {
      const [x, y] = pointAt(i, t);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider", borderRadius: 1.5,
      bgcolor: "background.paper", mb: 2, overflow: "hidden",
    }}>
      {/* header */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{
        px: 2.5, py: 1.5, borderBottom: "1px solid", borderColor: "divider",
      }}>
        <Box sx={{ flex: 1 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Metrics profile</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            Passed vs failed averages across every metric — the wider the red polygon reaches past green, the more that metric explains failure
          </Typography>
        </Box>
        <Stack direction="row" spacing={2}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: OUTCOME.passed }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Passed</Typography>
            <Typography sx={{ typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{passCount}</Typography>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: OUTCOME.failed }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Failed</Typography>
            <Typography sx={{ typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{failCount}</Typography>
          </Stack>
        </Stack>
      </Stack>

      {/* body — big radar on the left, per-axis stat table on the right */}
      <Box sx={{
        display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 320px" },
        gap: 0, alignItems: "center",
      }}>
        <Box sx={{ p: 3, display: "grid", placeItems: "center" }}>
          <Box component="svg" viewBox={`0 0 ${size} ${size}`} sx={{ width: "100%", maxWidth: size, height: "auto", display: "block", overflow: "visible" }}>
            {/* concentric grid rings */}
            {rings.map((t) => (
              <polygon
                key={t}
                points={gridPolygon(t)}
                fill="none"
                stroke="currentColor"
                strokeOpacity={dark ? 0.09 : 0.07}
                strokeWidth={0.75}
              />
            ))}
            {/* axis spokes */}
            {axes.map((_, i) => {
              const [x, y] = pointAt(i, 1);
              return (
                <line
                  key={i}
                  x1={cx} y1={cy} x2={x} y2={y}
                  stroke="currentColor" strokeOpacity={dark ? 0.09 : 0.07}
                  strokeWidth={0.75}
                />
              );
            })}

            {/* Per-task ghost polygons — every measured task drawn as
                a very faint outline behind the averages. Cluster of
                thin polygons = the "spread" of individual traces. */}
            {perTask.map((task) => {
              const pts = axes.map((axis, i) => {
                const t = axis.peak ? Math.min(1, task.values[i] / axis.peak) : 0;
                const [x, y] = pointAt(i, t);
                return `${x.toFixed(1)},${y.toFixed(1)}`;
              }).join(" ");
              const isHover = hoverTaskId === task.id;
              const dim = hoverTaskId && !isHover;
              return (
                <polygon
                  key={task.id}
                  points={pts}
                  fill="none"
                  stroke={OUTCOME[task.outcome]}
                  strokeOpacity={isHover ? 0.95 : (dim ? 0.02 : 0.09)}
                  strokeWidth={isHover ? 2 : 0.75}
                  strokeLinejoin="round"
                  style={{ transition: "stroke-opacity .12s ease, stroke-width .12s ease" }}
                />
              );
            })}

            {/* passed polygon */}
            <polygon
              points={passPoly}
              fill={OUTCOME.passed}
              fillOpacity={hoverTaskId ? "0.06" : "0.15"}
              stroke={OUTCOME.passed}
              strokeWidth="1.75"
              strokeLinejoin="round"
            />
            {/* failed polygon */}
            <polygon
              points={failPoly}
              fill={OUTCOME.failed}
              fillOpacity={hoverTaskId ? "0.06" : "0.18"}
              stroke={OUTCOME.failed}
              strokeWidth="1.75"
              strokeLinejoin="round"
            />

            {/* dot at each polygon vertex */}
            {axes.map((axis, i) => {
              const tPass = axis.peak ? Math.min(1, axis.passAvg / axis.peak) : 0;
              const tFail = axis.peak ? Math.min(1, axis.failAvg / axis.peak) : 0;
              const [pxp, pyp] = pointAt(i, tPass);
              const [pxf, pyf] = pointAt(i, tFail);
              return (
                <g key={axis.id}>
                  <circle cx={pxp} cy={pyp} r="3.5" fill={OUTCOME.passed} />
                  <circle cx={pxf} cy={pyf} r="3.5" fill={OUTCOME.failed} />
                </g>
              );
            })}

            {/* axis labels */}
            {axes.map((axis, i) => {
              const [x, y] = pointAt(i, 1.18);
              const a = angleFor(i);
              const align = Math.abs(Math.cos(a)) < 0.3 ? "middle" : Math.cos(a) > 0 ? "start" : "end";
              return (
                <g key={`lbl-${axis.id}`}>
                  <text
                    x={x} y={y - 4} textAnchor={align}
                    fontSize="13" fontWeight="700"
                    fill="currentColor" fillOpacity="0.85"
                    letterSpacing="0.2"
                  >
                    {axis.label}
                  </text>
                  <text
                    x={x} y={y + 12} textAnchor={align}
                    fontSize="11" fill="currentColor" fillOpacity="0.55"
                    style={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    peak {axis.fmt(axis.peak)}
                  </text>
                </g>
              );
            })}
          </Box>
        </Box>

        {/* Right pane: aggregate stats on top, scrollable trace list
            below. Hovering a trace lights up its polygon on the radar
            so the "context of a trace" is one glance away. */}
        <Box sx={{
          borderLeft: { md: "1px solid" }, borderTop: { xs: "1px solid", md: "none" },
          borderColor: "divider",
          display: "flex", flexDirection: "column",
          maxHeight: 560,
        }}>
          {/* aggregate stats */}
          <Box sx={{ p: 2 }}>
            <Typography sx={{
              typography: "s3", color: "text.subtitle", fontWeight: 700,
              textTransform: "uppercase", letterSpacing: 0.4, fontSize: 10.5, mb: 1,
            }}>
              Averages by metric
            </Typography>
            {axes.map((axis) => {
              const delta = axis.passAvg ? Math.round(((axis.failAvg - axis.passAvg) / axis.passAvg) * 100) : 0;
              const strong = Math.abs(delta) >= 15;
              return (
                <Stack key={axis.id} direction="row" alignItems="baseline" spacing={1} sx={{ py: 0.5 }}>
                  <Typography sx={{ typography: "s3", flex: 1, fontSize: 11.5 }}>
                    {axis.label}
                  </Typography>
                  <Typography sx={{
                    typography: "s3", fontWeight: 700, color: OUTCOME.passed,
                    fontVariantNumeric: "tabular-nums", width: 50, textAlign: "right", fontSize: 11.5,
                  }}>
                    {axis.fmt(axis.passAvg)}
                  </Typography>
                  <Typography sx={{
                    typography: "s3", fontWeight: 700, color: OUTCOME.failed,
                    fontVariantNumeric: "tabular-nums", width: 50, textAlign: "right", fontSize: 11.5,
                  }}>
                    {axis.fmt(axis.failAvg)}
                  </Typography>
                  <Typography sx={{
                    typography: "s3", fontSize: 10.5, width: 44, textAlign: "right",
                    color: strong ? OUTCOME.failed : "text.subtitle",
                    fontVariantNumeric: "tabular-nums",
                  }}>
                    {delta > 0 ? "+" : ""}{delta}%
                  </Typography>
                </Stack>
              );
            })}
          </Box>

          {/* per-trace list — hover to highlight on the radar */}
          <Box sx={{ px: 2, pb: 1 }}>
            <Typography sx={{
              typography: "s3", color: "text.subtitle", fontWeight: 700,
              textTransform: "uppercase", letterSpacing: 0.4, fontSize: 10.5, mb: 1,
              display: "flex", justifyContent: "space-between",
            }}>
              <span>Individual traces</span>
              <Box component="span" sx={{ color: "text.subtitle", fontWeight: 500, textTransform: "none", letterSpacing: 0 }}>
                hover to trace
              </Box>
            </Typography>
          </Box>
          <Box sx={{
            overflowY: "auto", flex: 1,
            borderTop: "1px solid", borderColor: "divider",
          }}>
            {perTask.map((task) => {
              const active = hoverTaskId === task.id;
              return (
                <Stack
                  key={task.id}
                  direction="row" alignItems="center" spacing={1}
                  onMouseEnter={() => setHoverTaskId(task.id)}
                  onMouseLeave={() => setHoverTaskId(null)}
                  sx={{
                    px: 2, py: 0.75,
                    cursor: "pointer",
                    bgcolor: active ? (t) => alpha(t.palette.text.primary, dark ? 0.06 : 0.04) : "transparent",
                    borderBottom: "1px solid", borderColor: "divider",
                    "&:last-child": { borderBottom: "none" },
                    transition: "background-color .08s ease",
                  }}
                >
                  <Box sx={{
                    width: 6, height: 6, borderRadius: "50%",
                    bgcolor: OUTCOME[task.outcome], flexShrink: 0,
                  }} />
                  <Typography noWrap sx={{ typography: "s3", flex: 1, minWidth: 0, fontSize: 11.5 }}>
                    {task.title}
                  </Typography>
                  <Typography sx={{
                    typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums",
                    fontSize: 10.5,
                  }}>
                    {axes[0].fmt(task.values[0])}
                  </Typography>
                </Stack>
              );
            })}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}
MetricsRadar.propTypes = { tasks: PropTypes.array };

/* ── metric tabs (Y-axis switcher) ────────────────────────────── */

function MetricTabs({ metrics, value, onChange }) {
  return (
    <Stack direction="row" spacing={0}>
      {metrics.map((m) => {
        const on = m.id === value;
        return (
          <ButtonBase
            key={m.id}
            onClick={() => onChange(m.id)}
            sx={{
              px: 1.5, py: 0.5, borderRadius: 0.75,
              bgcolor: on
                ? (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04)
                : "transparent",
              transition: "background-color .12s ease",
              "&:hover": {
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.025),
              },
            }}
          >
            <Typography sx={{
              typography: "s2", fontWeight: on ? 700 : 500,
              color: on ? "text.primary" : "text.subtitle",
              fontSize: 12.5,
            }}>
              {m.label}
            </Typography>
          </ButtonBase>
        );
      })}
    </Stack>
  );
}
MetricTabs.propTypes = {
  metrics: PropTypes.array, value: PropTypes.string, onChange: PropTypes.func,
};

/* ── the chart ────────────────────────────────────────────────── */

function TaskLineChart({
  tasks, metric, peak, median, p95,
  taskFilterActive, outcomeFilterActive, onTaskClick,
}) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const gradId = React.useId();

  const width = 1200;
  const height = 180;
  const padTop = 20;
  const padBottom = 20;
  const padLeft = 52;
  const padRight = 24;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const yAt = (v) => padTop + plotH - (v / Math.max(peak, 1)) * plotH;
  const xAt = (i) => {
    if (tasks.length <= 1) return padLeft + plotW / 2;
    return padLeft + (i / (tasks.length - 1)) * plotW;
  };

  /*
    Points for a smooth monotone cubic curve — nicer read than the
    zig-zag straight-line path. Uses the mid-point method: control
    points sit halfway between adjacent samples in X, at the sample's
    Y. Prevents overshoot at peaks/troughs unlike Catmull-Rom.
  */
  const points = useMemo(
    () => tasks.map((t, i) => ({ x: xAt(i), y: yAt(metric.get(t) || 0) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tasks, metric, peak],
  );

  const linePath = useMemo(() => {
    if (points.length === 0) return "";
    if (points.length === 1) return `M${points[0].x},${points[0].y}`;
    let d = `M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`;
    for (let i = 0; i < points.length - 1; i += 1) {
      const p0 = points[i];
      const p1 = points[i + 1];
      const mx = (p0.x + p1.x) / 2;
      d += ` C${mx.toFixed(1)},${p0.y.toFixed(1)} ${mx.toFixed(1)},${p1.y.toFixed(1)} ${p1.x.toFixed(1)},${p1.y.toFixed(1)}`;
    }
    return d;
  }, [points]);

  const areaPath = useMemo(() => {
    if (!linePath || points.length === 0) return "";
    const lastX = points[points.length - 1].x;
    const firstX = points[0].x;
    const bottomY = padTop + plotH;
    return `${linePath} L${lastX.toFixed(1)},${bottomY} L${firstX.toFixed(1)},${bottomY} Z`;
  }, [linePath, points, plotH]);

  const yTicks = useMemo(() => {
    const step = niceStep(peak / 3);
    const ticks = [];
    for (let v = 0; v <= peak; v += step) ticks.push(v);
    if (ticks[ticks.length - 1] < peak) ticks.push(peak);
    return ticks;
  }, [peak]);

  const anyOutcomeFilter = !!outcomeFilterActive;

  return (
    <Box sx={{ p: 2.5 }}>
      <Box
        component="svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        sx={{ width: "100%", height, display: "block", overflow: "visible" }}
      >
        <defs>
          {/* layered area fill — deeper at the line, softer to the baseline */}
          <linearGradient id={`fill-${gradId}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={ACCENT} stopOpacity={dark ? 0.36 : 0.24} />
            <stop offset="45%" stopColor={ACCENT} stopOpacity={dark ? 0.16 : 0.1} />
            <stop offset="100%" stopColor={ACCENT} stopOpacity="0" />
          </linearGradient>
          {/* soft glow for hovered / active dots */}
          <radialGradient id={`glow-${gradId}`}>
            <stop offset="0%" stopColor={ACCENT} stopOpacity="0.5" />
            <stop offset="60%" stopColor={ACCENT} stopOpacity="0.12" />
            <stop offset="100%" stopColor={ACCENT} stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Y grid + labels (baseline is stronger; midlines subtle) */}
        {yTicks.map((v) => {
          const y = yAt(v);
          const isBase = v === 0;
          return (
            <g key={v}>
              <line
                x1={padLeft} x2={width - padRight}
                y1={y} y2={y}
                stroke="currentColor"
                strokeOpacity={isBase ? (dark ? 0.18 : 0.13) : (dark ? 0.04 : 0.035)}
                strokeWidth={isBase ? 0.75 : 0.5}
                strokeDasharray={isBase ? "0" : "2 4"}
                shapeRendering="crispEdges"
              />
              <text
                x={padLeft - 10} y={y + 3}
                textAnchor="end"
                fontSize="10.5"
                fill="currentColor"
                fillOpacity={dark ? 0.45 : 0.5}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {metric.fmt(v)}
              </text>
            </g>
          );
        })}

        {/* p50 / p95 guides — thin dashed, floating pill label on the right */}
        {[
          { v: median, label: "P50", op: dark ? 0.32 : 0.24 },
          ...(p95 !== median ? [{ v: p95, label: "P95", op: dark ? 0.22 : 0.16 }] : []),
        ].map((g) => {
          const y = yAt(g.v);
          const labelW = 34;
          const labelH = 14;
          return (
            <g key={g.label}>
              <line
                x1={padLeft} x2={width - padRight - labelW - 4}
                y1={y} y2={y}
                stroke="currentColor"
                strokeOpacity={g.op}
                strokeWidth={0.75}
                strokeDasharray="2 4"
              />
              <rect
                x={width - padRight - labelW} y={y - labelH / 2}
                width={labelW} height={labelH}
                rx={3}
                fill="currentColor"
                fillOpacity={dark ? 0.08 : 0.05}
                stroke="currentColor"
                strokeOpacity={g.op}
                strokeWidth={0.5}
              />
              <text
                x={width - padRight - labelW / 2} y={y + 3}
                textAnchor="middle"
                fontSize="9.5"
                fontWeight="700"
                letterSpacing="0.5"
                fill="currentColor"
                fillOpacity={dark ? 0.6 : 0.5}
              >
                {g.label}
              </text>
            </g>
          );
        })}

        {/* area + line */}
        {areaPath && <path d={areaPath} fill={`url(#fill-${gradId})`} />}
        {linePath && (
          <path
            d={linePath}
            fill="none"
            stroke={ACCENT}
            strokeWidth="1.75"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}

        {/* task points — outer soft halo + white bg + colored core */}
        {tasks.map((t, i) => {
          const outcome = outcomeOf(t);
          const isTaskActive = taskFilterActive === t.id;
          const isOutcomeDim = anyOutcomeFilter && outcomeFilterActive !== outcome;
          const dim = (taskFilterActive && !isTaskActive) || isOutcomeDim;
          const cx = points[i]?.x || 0;
          const cy = points[i]?.y || 0;
          const color = OUTCOME[outcome] || ACCENT;
          const r = isTaskActive ? 5.5 : 3.5;
          return (
            <g key={t.id} opacity={dim ? 0.25 : 1} style={{ transition: "opacity .15s ease" }}>
              {isTaskActive && (
                <circle cx={cx} cy={cy} r={14} fill={`url(#glow-${gradId})`} />
              )}
              {/* white/dark backing so dots read on top of the area fill */}
              <circle cx={cx} cy={cy} r={r + 2} fill={dark ? "#0a0a0a" : "#fff"} />
              {/* subtle color halo */}
              <circle cx={cx} cy={cy} r={r + 1} fill={color} opacity={0.18} />
              {/* solid dot */}
              <circle cx={cx} cy={cy} r={r} fill={color} />
              {isTaskActive && (
                <circle cx={cx} cy={cy} r={r + 3.5} fill="none" stroke={ACCENT} strokeWidth={1.25} />
              )}
            </g>
          );
        })}
      </Box>

      {/* invisible click overlay, one hit-target per task */}
      <Box sx={{ position: "relative", height: 0 }}>
        <Box sx={{
          position: "absolute",
          top: -height, left: 0, right: 0, height,
          display: "grid",
          gridTemplateColumns: `${padLeft}px 1fr ${padRight}px`,
        }}>
          <Box />
          <Box sx={{ display: "flex", alignItems: "stretch" }}>
            {tasks.map((t) => (
              <Tooltip
                key={t.id}
                arrow
                placement="top"
                title={
                  <Box>
                    <Typography sx={{ typography: "s3", fontWeight: 700 }}>{t.title || t.id}</Typography>
                    <Typography sx={{ typography: "s3", color: "rgba(255,255,255,0.7)" }}>
                      {t.status}{t.critical ? " · critical" : ""}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "rgba(255,255,255,0.7)" }}>
                      {metric.label}: {metric.fmt(metric.get(t) || 0)}
                    </Typography>
                    {t.useCase && (
                      <Typography sx={{ typography: "s3", color: "rgba(255,255,255,0.55)" }}>
                        {t.useCase}
                      </Typography>
                    )}
                  </Box>
                }
              >
                <Box
                  onClick={() => onTaskClick(t)}
                  sx={{
                    flex: 1, cursor: "pointer",
                    "&:hover": {
                      bgcolor: (theme2) => alpha(theme2.palette.text.primary, theme2.palette.mode === "dark" ? 0.03 : 0.02),
                    },
                  }}
                />
              </Tooltip>
            ))}
          </Box>
          <Box />
        </Box>
      </Box>

      <Stack direction="row" alignItems="center" sx={{ mt: 1.25 }}>
        <Typography sx={{
          typography: "s3", color: "text.disabled", fontSize: 10.5,
          letterSpacing: 0.3, textTransform: "uppercase", fontWeight: 700,
        }}>
          Task 1
        </Typography>
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.disabled", fontSize: 10.5 }}>
          Hover a point for details · click to filter the traces below
        </Typography>
        <Box flex={1} />
        <Typography sx={{
          typography: "s3", color: "text.disabled", fontSize: 10.5,
          letterSpacing: 0.3, textTransform: "uppercase", fontWeight: 700,
        }}>
          Task {tasks.length}
        </Typography>
      </Stack>
    </Box>
  );
}
TaskLineChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object,
  peak: PropTypes.number, median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string,
  onTaskClick: PropTypes.func,
};

/* ── header widgets ───────────────────────────────────────────── */

function Stat({ label, value, bold }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.625}>
      <Typography sx={{
        typography: "s3", color: "text.disabled", fontSize: 10,
        letterSpacing: 0.4, textTransform: "uppercase", fontWeight: 700,
      }}>
        {label}
      </Typography>
      <Typography sx={{
        typography: bold ? "s2" : "s3",
        color: "text.primary", fontWeight: bold ? 700 : 600,
        fontVariantNumeric: "tabular-nums",
        fontSize: bold ? 13 : 12,
      }}>
        {value}
      </Typography>
    </Stack>
  );
}
Stat.propTypes = { label: PropTypes.string, value: PropTypes.node, bold: PropTypes.bool };

function LegendPill({ color, label, count, active, onClick }) {
  return (
    <ButtonBase
      onClick={onClick}
      sx={{
        display: "inline-flex", alignItems: "center", gap: 0.625,
        px: 1, py: 0.5, borderRadius: 0.875,
        border: "1px solid",
        borderColor: active ? alpha(color, 0.5) : "divider",
        bgcolor: active ? alpha(color, 0.08) : "transparent",
        "&:hover": {
          bgcolor: alpha(color, 0.05),
          borderColor: alpha(color, 0.35),
        },
        transition: "background-color .12s ease, border-color .12s ease",
      }}
    >
      <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: color }} />
      <Typography sx={{
        typography: "s3", color: "text.primary", fontWeight: 600, fontSize: 11,
      }}>
        {label}
      </Typography>
      <Typography sx={{
        typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", fontSize: 11,
      }}>
        {count}
      </Typography>
    </ButtonBase>
  );
}
LegendPill.propTypes = {
  color: PropTypes.string, label: PropTypes.string, count: PropTypes.number,
  active: PropTypes.bool, onClick: PropTypes.func,
};

/* ── helpers ──────────────────────────────────────────────────── */

function matchOutcome(task, outcomeId) {
  const o = outcomeOf(task);
  if (outcomeId === "failed") return o === "failed" || o === "error";
  return o === outcomeId;
}

function niceStep(raw) {
  if (raw <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const scaled = raw / pow;
  let stepScaled = 1;
  if (scaled > 5) stepScaled = 10;
  else if (scaled > 2) stepScaled = 5;
  else if (scaled > 1) stepScaled = 2;
  return stepScaled * pow;
}

function compactNumber(n) {
  const v = Math.round(n);
  if (v >= 10000) return `${(v / 1000).toFixed(1)}k`;
  if (v >= 1000) return v.toLocaleString();
  return String(v);
}

/*
  Routes the current chartType to its body component. Kept as its own
  component so React unmounts the previous chart cleanly and mounts
  the new one — instead of an inline IIFE that some hot-reload paths
  can misidentify. Also gives us one place to add a fallback if a
  chart throws.
*/
function ChartBodyRouter({ chartType, ...props }) {
  switch (chartType) {
    case "heatmap": return <TaskHeatmapChart {...props} />;
    case "step":    return <TaskStepChart {...props} />;
    case "column":  return <TaskColumnChart {...props} />;
    case "sorted":  return <TaskSortedChart {...props} />;
    case "band":    return <TaskPercentileBandChart {...props} />;
    case "candle":  return <TaskCandleChart {...props} />;
    case "line":
    default:        return <TaskLineChart {...props} />;
  }
}
ChartBodyRouter.propTypes = { chartType: PropTypes.string };

/* ── chart type tabs ─────────────────────────────────────────────── */

function ChartTypeTabs({ value, onChange }) {
  return (
    <>
      {CHART_TYPES.map((c) => {
        const on = c.id === value;
        return (
          <Tooltip key={c.id} arrow title={c.label} placement="top">
            <ButtonBase
              onClick={() => onChange(c.id)}
              sx={{
                width: 30, height: 30, borderRadius: 0.75,
                bgcolor: on
                  ? (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06)
                  : "transparent",
                color: on ? "text.primary" : "text.subtitle",
                transition: "background-color .12s ease, color .12s ease",
                "&:hover": {
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.025),
                  color: "text.primary",
                },
              }}
            >
              <Iconify icon={c.icon} width={16} />
            </ButtonBase>
          </Tooltip>
        );
      })}
    </>
  );
}
ChartTypeTabs.propTypes = { value: PropTypes.string, onChange: PropTypes.func };

/* ── shared chart geometry ───────────────────────────────────────── */

const CHART_WIDTH = 1200;
const CHART_HEIGHT = 180;
const CHART_PAD_T = 20;
const CHART_PAD_B = 20;
const CHART_PAD_L = 52;
const CHART_PAD_R = 24;
const CHART_PLOT_W = CHART_WIDTH - CHART_PAD_L - CHART_PAD_R;
const CHART_PLOT_H = CHART_HEIGHT - CHART_PAD_T - CHART_PAD_B;

function useYTicks(peak) {
  return useMemo(() => {
    const step = niceStep(peak / 3);
    const ticks = [];
    for (let v = 0; v <= peak; v += step) ticks.push(v);
    if (ticks[ticks.length - 1] < peak) ticks.push(peak);
    return ticks;
  }, [peak]);
}

function ChartAxes({ metric, peak, median, p95 }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const yTicks = useYTicks(peak);
  return (
    <>
      {yTicks.map((v) => {
        const y = yAt(v);
        const isBase = v === 0;
        return (
          <g key={v}>
            <line
              x1={CHART_PAD_L} x2={CHART_WIDTH - CHART_PAD_R}
              y1={y} y2={y}
              stroke="currentColor"
              strokeOpacity={isBase ? (dark ? 0.18 : 0.13) : (dark ? 0.04 : 0.035)}
              strokeWidth={isBase ? 0.75 : 0.5}
              strokeDasharray={isBase ? "0" : "2 4"}
              shapeRendering="crispEdges"
            />
            <text
              x={CHART_PAD_L - 10} y={y + 3}
              textAnchor="end" fontSize="10.5" fill="currentColor"
              fillOpacity={dark ? 0.45 : 0.5}
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {metric.fmt(v)}
            </text>
          </g>
        );
      })}
      {[
        { v: median, label: "P50", op: dark ? 0.32 : 0.24 },
        ...(p95 !== median ? [{ v: p95, label: "P95", op: dark ? 0.22 : 0.16 }] : []),
      ].map((g) => {
        const y = yAt(g.v);
        const labelW = 34; const labelH = 14;
        return (
          <g key={g.label}>
            <line
              x1={CHART_PAD_L} x2={CHART_WIDTH - CHART_PAD_R - labelW - 4}
              y1={y} y2={y}
              stroke="currentColor" strokeOpacity={g.op}
              strokeWidth={0.75} strokeDasharray="2 4"
            />
            <rect
              x={CHART_WIDTH - CHART_PAD_R - labelW} y={y - labelH / 2}
              width={labelW} height={labelH} rx={3}
              fill="currentColor" fillOpacity={dark ? 0.08 : 0.05}
              stroke="currentColor" strokeOpacity={g.op} strokeWidth={0.5}
            />
            <text
              x={CHART_WIDTH - CHART_PAD_R - labelW / 2} y={y + 3}
              textAnchor="middle" fontSize="9.5" fontWeight="700" letterSpacing="0.5"
              fill="currentColor" fillOpacity={dark ? 0.6 : 0.5}
            >
              {g.label}
            </text>
          </g>
        );
      })}
    </>
  );
}
ChartAxes.propTypes = { metric: PropTypes.object, peak: PropTypes.number, median: PropTypes.number, p95: PropTypes.number };

/* ── alternate chart bodies ──────────────────────────────────────── */

/*
  Area chart — the line view with a gradient area fill underneath.
  Reads as "the metric has depth" instead of a bare trajectory.
*/
function TaskAreaChart({ tasks, metric, peak, median, p95, taskFilterActive, outcomeFilterActive, onTaskClick }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const gradId = React.useId();
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const xAt = (i) => tasks.length <= 1 ? CHART_PAD_L + CHART_PLOT_W / 2
    : CHART_PAD_L + (i / (tasks.length - 1)) * CHART_PLOT_W;

  const points = tasks.map((t, i) => ({ x: xAt(i), y: yAt(metric.get(t) || 0) }));
  let d = points[0] ? `M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}` : "";
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i]; const p1 = points[i + 1];
    const mx = (p0.x + p1.x) / 2;
    d += ` C${mx.toFixed(1)},${p0.y.toFixed(1)} ${mx.toFixed(1)},${p1.y.toFixed(1)} ${p1.x.toFixed(1)},${p1.y.toFixed(1)}`;
  }
  const baseY = CHART_PAD_T + CHART_PLOT_H;
  const area = d ? `${d} L${points[points.length - 1].x.toFixed(1)},${baseY} L${points[0].x.toFixed(1)},${baseY} Z` : "";
  const anyOutcomeFilter = !!outcomeFilterActive;

  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        <defs>
          <linearGradient id={`area-${gradId}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={ACCENT} stopOpacity={dark ? 0.5 : 0.32} />
            <stop offset="50%" stopColor={ACCENT} stopOpacity={dark ? 0.2 : 0.12} />
            <stop offset="100%" stopColor={ACCENT} stopOpacity="0" />
          </linearGradient>
        </defs>
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {area && <path d={area} fill={`url(#area-${gradId})`} />}
        {d && <path d={d} stroke={ACCENT} strokeWidth="2" fill="none" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />}
        {tasks.map((t, i) => {
          const outcome = outcomeOf(t);
          const color = OUTCOME[outcome] || ACCENT;
          const isActive = taskFilterActive === t.id;
          const dim = (taskFilterActive && !isActive) || (anyOutcomeFilter && outcomeFilterActive !== outcome);
          return (
            <g key={t.id} opacity={dim ? 0.25 : 1} onClick={() => onTaskClick(t)} style={{ cursor: "pointer" }}>
              <circle cx={points[i].x} cy={points[i].y} r={isActive ? 5.5 : 3.5} fill={color}>
                <title>{t.title || t.id} · {metric.fmt(metric.get(t) || 0)} · {outcome}</title>
              </circle>
            </g>
          );
        })}
      </Box>
    </Box>
  );
}
TaskAreaChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Lollipop chart — a thin vertical stick from the baseline to each
  task's value, capped with a colored dot. Cleaner than a bar chart
  because the ink-to-data ratio is lower, and the dots keep the
  outcome color legible without a full-height fill.
*/
function TaskLollipopChart({ tasks, metric, peak, median, p95, taskFilterActive, outcomeFilterActive, onTaskClick }) {
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const xAt = (i) => tasks.length <= 1 ? CHART_PAD_L + CHART_PLOT_W / 2
    : CHART_PAD_L + (i / (tasks.length - 1)) * CHART_PLOT_W;
  const anyOutcomeFilter = !!outcomeFilterActive;
  const baseY = CHART_PAD_T + CHART_PLOT_H;
  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {tasks.map((t, i) => {
          const outcome = outcomeOf(t);
          const color = OUTCOME[outcome] || ACCENT;
          const isActive = taskFilterActive === t.id;
          const dim = (taskFilterActive && !isActive) || (anyOutcomeFilter && outcomeFilterActive !== outcome);
          const cx = xAt(i);
          const cy = yAt(metric.get(t) || 0);
          return (
            <g key={t.id} opacity={dim ? 0.25 : 1} onClick={() => onTaskClick(t)} style={{ cursor: "pointer" }}>
              <line x1={cx} x2={cx} y1={baseY} y2={cy} stroke={color} strokeOpacity="0.55" strokeWidth={1.25} vectorEffect="non-scaling-stroke" />
              <circle cx={cx} cy={cy} r={isActive ? 5 : 3.5} fill={color}>
                <title>{t.title || t.id} · {metric.fmt(metric.get(t) || 0)} · {outcome}</title>
              </circle>
            </g>
          );
        })}
      </Box>
    </Box>
  );
}
TaskLollipopChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Swarm chart — three horizontal bands (Passed / Failed / Critical),
  each holding dots for its group. X position = task index; Y jitter
  inside the band is deterministic from task id. Shows how failures
  cluster in the sequence without stacking dots on top of each other.
*/
function TaskSwarmChart({ tasks, metric, peak, taskFilterActive, outcomeFilterActive, onTaskClick }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const xAt = (i) => tasks.length <= 1 ? CHART_PAD_L + CHART_PLOT_W / 2
    : CHART_PAD_L + (i / (tasks.length - 1)) * CHART_PLOT_W;

  const groups = [
    { id: "passed",   label: "PASSED",   color: OUTCOME.passed,   cy: CHART_PAD_T + CHART_PLOT_H * 0.20 },
    { id: "failed",   label: "FAILED",   color: OUTCOME.failed,   cy: CHART_PAD_T + CHART_PLOT_H * 0.55 },
    { id: "critical", label: "CRITICAL", color: OUTCOME.critical, cy: CHART_PAD_T + CHART_PLOT_H * 0.85 },
  ];
  const jitter = CHART_PLOT_H * 0.11;
  const anyOutcomeFilter = !!outcomeFilterActive;

  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        {/* band guides + labels */}
        {groups.map((g) => (
          <g key={g.id}>
            <line
              x1={CHART_PAD_L} x2={CHART_WIDTH - CHART_PAD_R}
              y1={g.cy} y2={g.cy}
              stroke="currentColor" strokeOpacity={dark ? 0.06 : 0.05}
              strokeWidth={0.5} strokeDasharray="3 6" shapeRendering="crispEdges"
            />
            <text
              x={CHART_PAD_L - 8} y={g.cy + 3} textAnchor="end"
              fontSize="10" fontWeight="700" letterSpacing="0.5"
              fill={g.color} fillOpacity="0.9"
            >
              {g.label}
            </text>
          </g>
        ))}

        {/* dots */}
        {tasks.map((t, i) => {
          const outcome = outcomeOf(t) === "error" ? "failed" : outcomeOf(t);
          const g = groups.find((x) => x.id === outcome) || groups[1];
          const isActive = taskFilterActive === t.id;
          const dim = (taskFilterActive && !isActive) || (anyOutcomeFilter && outcomeFilterActive !== outcome && !(outcomeFilterActive === "critical" && outcome === "critical"));
          const cx = xAt(i);
          const cy = g.cy + ((hash(t.id) % 1000) / 1000 - 0.5) * jitter;
          return (
            <g key={t.id} opacity={dim ? 0.2 : 1} onClick={() => onTaskClick(t)} style={{ cursor: "pointer" }}>
              <circle cx={cx} cy={cy} r={isActive ? 5 : 3.5} fill={g.color}>
                <title>{t.title || t.id} · {metric.fmt(metric.get(t) || 0)} · {outcome}</title>
              </circle>
            </g>
          );
        })}

        {/* X-axis task-index ticks */}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const idx = Math.round(f * (tasks.length - 1));
          const x = CHART_PAD_L + f * CHART_PLOT_W;
          return (
            <text
              key={f} x={x} y={CHART_HEIGHT - 4} textAnchor="middle"
              fontSize="10" fill="currentColor" fillOpacity="0.5"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              Task {idx + 1}
            </text>
          );
        })}
      </Box>
    </Box>
  );
}
TaskSwarmChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Heatmap — X divided into task-index buckets, Y divided into metric
  value buckets. Cell color intensity = number of tasks landing in
  that (task-bucket, value-bucket) cell. Reveals density patterns
  invisible to a line: e.g. "high tokens are all clustered in the
  first third of the run".
*/
function TaskHeatmapChart({ tasks, metric, peak }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const xBins = Math.min(20, Math.max(6, Math.ceil(tasks.length / 3)));
  const yBins = 8;
  const grid = Array.from({ length: yBins }, () => new Array(xBins).fill(0));
  const yStep = (peak || 1) / yBins;
  tasks.forEach((t, i) => {
    const v = metric.get(t) || 0;
    const xi = Math.min(xBins - 1, Math.max(0, Math.floor((i / Math.max(tasks.length, 1)) * xBins)));
    const yi = Math.min(yBins - 1, Math.max(0, Math.floor(v / yStep)));
    grid[yi][xi] += 1;
  });
  const maxN = Math.max(1, ...grid.flat());

  const cellW = CHART_PLOT_W / xBins;
  const cellH = CHART_PLOT_H / yBins;

  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        {/* Y axis: value labels at bottom / mid / top */}
        {[0, yBins / 2, yBins].map((row) => {
          const v = row * yStep;
          const y = CHART_PAD_T + (yBins - row) * cellH;
          return (
            <text
              key={row} x={CHART_PAD_L - 10} y={y + 3} textAnchor="end"
              fontSize="10.5" fill="currentColor" fillOpacity={dark ? 0.45 : 0.5}
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {metric.fmt(v)}
            </text>
          );
        })}
        {/* cells — top row first (highest value) */}
        {grid.map((row, ri) => row.map((n, ci) => {
          const x = CHART_PAD_L + ci * cellW;
          const y = CHART_PAD_T + (yBins - 1 - ri) * cellH;
          const t = maxN ? n / maxN : 0;
          const fill = n === 0
            ? (dark ? alpha("#fff", 0.02) : alpha("#000", 0.02))
            : alpha(ACCENT, 0.15 + t * 0.7);
          return (
            <rect
              key={`${ri}-${ci}`}
              x={x + 1} y={y + 1}
              width={cellW - 2} height={cellH - 2}
              fill={fill} rx={2}
            >
              <title>{n} task{n === 1 ? "" : "s"} · value {metric.fmt(ri * yStep)}–{metric.fmt((ri + 1) * yStep)}</title>
            </rect>
          );
        }))}
        {/* X-axis task-index ticks */}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const idx = Math.round(f * (tasks.length - 1));
          const x = CHART_PAD_L + f * CHART_PLOT_W;
          return (
            <text
              key={f} x={x} y={CHART_HEIGHT - 4} textAnchor="middle"
              fontSize="10" fill="currentColor" fillOpacity="0.5"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              Task {idx + 1}
            </text>
          );
        })}
      </Box>
    </Box>
  );
}
TaskHeatmapChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Rolling-average chart — smooth line through a moving window
  average of the metric. Removes noise so a trend across a long run
  reads clearly. Failure markers underneath keep the outcome context.
*/
function TaskRollingAvgChart({ tasks, metric, peak, median, p95 }) {
  const window = Math.max(3, Math.round(tasks.length / 10));
  const raw = tasks.map((t) => metric.get(t) || 0);
  const smooth = raw.map((_, i) => {
    const s = Math.max(0, i - Math.floor(window / 2));
    const e = Math.min(raw.length, s + window);
    const slice = raw.slice(s, e);
    return slice.reduce((a, n) => a + n, 0) / slice.length;
  });
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const xAt = (i) => tasks.length <= 1 ? CHART_PAD_L + CHART_PLOT_W / 2
    : CHART_PAD_L + (i / (tasks.length - 1)) * CHART_PLOT_W;

  let d = "";
  smooth.forEach((v, i) => {
    const x = xAt(i);
    const y = yAt(v);
    if (i === 0) d = `M${x.toFixed(1)},${y.toFixed(1)}`;
    else {
      const prevX = xAt(i - 1);
      const mx = (prevX + x) / 2;
      const prevY = yAt(smooth[i - 1]);
      d += ` C${mx.toFixed(1)},${prevY.toFixed(1)} ${mx.toFixed(1)},${y.toFixed(1)} ${x.toFixed(1)},${y.toFixed(1)}`;
    }
  });

  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {/* faint raw dots underneath */}
        {tasks.map((t, i) => {
          const v = metric.get(t) || 0;
          const outcome = outcomeOf(t);
          return (
            <circle
              key={t.id}
              cx={xAt(i)} cy={yAt(v)}
              r="1.75" fill={OUTCOME[outcome] || ACCENT} fillOpacity="0.35"
            >
              <title>{t.title || t.id} · {metric.fmt(v)}</title>
            </circle>
          );
        })}
        {/* smoothed line */}
        {d && <path d={d} stroke={ACCENT} strokeWidth="2.5" fill="none" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />}
      </Box>
    </Box>
  );
}
/*
  Step chart — same axes as the line view, but with angular
  staircase transitions between adjacent samples. Reads well for
  metrics that are essentially discrete state changes (e.g. call
  latency at each step) or when you want to emphasize each
  individual sample instead of implied continuity.
*/
function TaskStepChart({ tasks, metric, peak, median, p95, taskFilterActive, outcomeFilterActive, onTaskClick }) {
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const xAt = (i) => tasks.length <= 1 ? CHART_PAD_L + CHART_PLOT_W / 2
    : CHART_PAD_L + (i / (tasks.length - 1)) * CHART_PLOT_W;

  let d = "";
  tasks.forEach((t, i) => {
    const x = xAt(i); const y = yAt(metric.get(t) || 0);
    if (i === 0) { d = `M${x.toFixed(1)},${y.toFixed(1)}`; return; }
    const prevY = yAt(metric.get(tasks[i - 1]) || 0);
    d += ` L${x.toFixed(1)},${prevY.toFixed(1)} L${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const anyOutcomeFilter = !!outcomeFilterActive;

  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {d && <path d={d} stroke={ACCENT} strokeWidth="1.75" fill="none" strokeLinejoin="miter" strokeLinecap="butt" vectorEffect="non-scaling-stroke" />}
        {tasks.map((t, i) => {
          const outcome = outcomeOf(t);
          const color = OUTCOME[outcome] || ACCENT;
          const isActive = taskFilterActive === t.id;
          const dim = (taskFilterActive && !isActive) || (anyOutcomeFilter && outcomeFilterActive !== outcome);
          return (
            <g key={t.id} opacity={dim ? 0.25 : 1} onClick={() => onTaskClick(t)} style={{ cursor: "pointer" }}>
              <circle cx={xAt(i)} cy={yAt(metric.get(t) || 0)} r={isActive ? 5 : 3} fill={color}>
                <title>{t.title || t.id} · {metric.fmt(metric.get(t) || 0)} · {outcome}</title>
              </circle>
            </g>
          );
        })}
      </Box>
    </Box>
  );
}
TaskStepChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Column chart — thin, tightly-packed vertical columns per task,
  colored by outcome. Reads like an audio waveform. Fills the plot
  wall-to-wall so patterns and streaks pop even with 50+ tasks.
*/
function TaskColumnChart({ tasks, metric, peak, median, p95, taskFilterActive, outcomeFilterActive, onTaskClick }) {
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const slot = CHART_PLOT_W / Math.max(tasks.length, 1);
  const colW = Math.max(1.5, slot - 1);
  const anyOutcomeFilter = !!outcomeFilterActive;
  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {tasks.map((t, i) => {
          const v = metric.get(t) || 0;
          const outcome = outcomeOf(t);
          const color = OUTCOME[outcome] || ACCENT;
          const isActive = taskFilterActive === t.id;
          const dim = (taskFilterActive && !isActive) || (anyOutcomeFilter && outcomeFilterActive !== outcome);
          const x = CHART_PAD_L + i * slot + (slot - colW) / 2;
          const y = yAt(v);
          const h = CHART_PAD_T + CHART_PLOT_H - y;
          return (
            <rect
              key={t.id}
              x={x} y={y} width={colW} height={Math.max(1, h)}
              fill={color} opacity={dim ? 0.25 : 0.92} rx={0.5}
              onClick={() => onTaskClick(t)}
              style={{ cursor: "pointer", transition: "opacity .12s ease" }}
            >
              <title>{t.title || t.id} · {metric.fmt(v)} · {outcome}</title>
            </rect>
          );
        })}
      </Box>
    </Box>
  );
}
TaskColumnChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Sorted chart — X = tasks sorted ascending by their metric value,
  Y = the value itself. The curve's shape reveals the distribution
  without a histogram: a straight ramp = uniform spread, a shallow
  climb with a cliff = a few outliers, a flat plateau = one dominant
  cluster. Dot colors keep the outcome context.
*/
function TaskSortedChart({ tasks, metric, peak, median, p95, taskFilterActive, outcomeFilterActive, onTaskClick }) {
  const sorted = tasks.map((t, i) => ({ t, i, v: metric.get(t) || 0 })).sort((a, b) => a.v - b.v);
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const xAt = (i) => sorted.length <= 1 ? CHART_PAD_L + CHART_PLOT_W / 2
    : CHART_PAD_L + (i / (sorted.length - 1)) * CHART_PLOT_W;

  let d = "";
  sorted.forEach((s, i) => {
    const x = xAt(i); const y = yAt(s.v);
    d += (i === 0 ? "M" : " L") + x.toFixed(1) + "," + y.toFixed(1);
  });
  const anyOutcomeFilter = !!outcomeFilterActive;

  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {d && <path d={d} stroke={ACCENT} strokeWidth="1.5" fill="none" strokeOpacity="0.6" vectorEffect="non-scaling-stroke" />}
        {sorted.map((s, i) => {
          const outcome = outcomeOf(s.t);
          const color = OUTCOME[outcome] || ACCENT;
          const isActive = taskFilterActive === s.t.id;
          const dim = (taskFilterActive && !isActive) || (anyOutcomeFilter && outcomeFilterActive !== outcome);
          return (
            <g key={s.t.id} opacity={dim ? 0.25 : 1} onClick={() => onTaskClick(s.t)} style={{ cursor: "pointer" }}>
              <circle cx={xAt(i)} cy={yAt(s.v)} r={isActive ? 5 : 3.25} fill={color}>
                <title>{s.t.title || s.t.id} · {metric.fmt(s.v)} · {outcome} · rank {i + 1}</title>
              </circle>
            </g>
          );
        })}
        {/* footer note — X is rank, not task index */}
        <text x={CHART_WIDTH - CHART_PAD_R} y={CHART_HEIGHT - 4} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity="0.4">
          sorted ascending
        </text>
      </Box>
    </Box>
  );
}
TaskSortedChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Percentile band chart — for each task position, compute a rolling
  window's p25 / p50 / p75; render as a filled band with the median
  line down the middle. Reveals volatility (a wide band) vs stability
  (a tight band) across the run's sequence.
*/
function TaskPercentileBandChart({ tasks, metric, peak, median, p95 }) {
  const w = Math.max(5, Math.round(tasks.length / 10));
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const xAt = (i) => tasks.length <= 1 ? CHART_PAD_L + CHART_PLOT_W / 2
    : CHART_PAD_L + (i / (tasks.length - 1)) * CHART_PLOT_W;

  const rows = tasks.map((_, i) => {
    const s = Math.max(0, i - Math.floor(w / 2));
    const e = Math.min(tasks.length, s + w);
    const slice = tasks.slice(s, e).map((t) => metric.get(t) || 0).sort((a, b) => a - b);
    const at = (p) => slice[Math.min(slice.length - 1, Math.floor(p * slice.length))] || 0;
    return { p25: at(0.25), p50: at(0.5), p75: at(0.75) };
  });

  const upperPts = rows.map((r, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)},${yAt(r.p75).toFixed(1)}`).join(" ");
  const lowerRev = [...rows].reverse().map((r, i) => {
    const idx = rows.length - 1 - i;
    return `L ${xAt(idx).toFixed(1)},${yAt(r.p25).toFixed(1)}`;
  }).join(" ");
  const bandD = rows.length ? `${upperPts} ${lowerRev} Z` : "";
  const medianD = rows.map((r, i) => (i === 0 ? "M" : "L") + " " + xAt(i).toFixed(1) + "," + yAt(r.p50).toFixed(1)).join(" ");

  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {bandD && <path d={bandD} fill={ACCENT} fillOpacity="0.15" />}
        {medianD && <path d={medianD} stroke={ACCENT} strokeWidth="1.75" fill="none" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
        <text x={CHART_WIDTH - CHART_PAD_R} y={CHART_HEIGHT - 4} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity="0.4">
          rolling p25 – p75 band · {w}-task window
        </text>
      </Box>
    </Box>
  );
}
TaskPercentileBandChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Candles — bucket tasks into windows and render each as an OHLC-
  style candle (low/high wick + open/close body). Useful for reading
  volatility, direction and range together at each phase of the run.
  Body is colored green when close >= open, red when close < open.
*/
function TaskCandleChart({ tasks, metric, peak, median, p95 }) {
  const nBuckets = Math.min(24, Math.max(6, Math.round(tasks.length / 4)));
  const bucketSize = Math.max(1, Math.ceil(tasks.length / nBuckets));
  const buckets = [];
  for (let i = 0; i < tasks.length; i += bucketSize) {
    const slice = tasks.slice(i, i + bucketSize).map((t) => metric.get(t) || 0);
    if (!slice.length) continue;
    buckets.push({
      open: slice[0],
      close: slice[slice.length - 1],
      high: Math.max(...slice),
      low: Math.min(...slice),
    });
  }

  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const slot = CHART_PLOT_W / Math.max(buckets.length, 1);
  const bodyW = Math.max(3, slot * 0.55);

  return (
    <Box sx={{ p: 2.5 }}>
      <Box component="svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}>
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {buckets.map((b, i) => {
          const up = b.close >= b.open;
          const color = up ? OUTCOME.passed : OUTCOME.failed;
          const cx = CHART_PAD_L + i * slot + slot / 2;
          const bodyTop = yAt(Math.max(b.open, b.close));
          const bodyBot = yAt(Math.min(b.open, b.close));
          const bodyH = Math.max(1.5, bodyBot - bodyTop);
          return (
            <g key={i}>
              {/* wick */}
              <line x1={cx} x2={cx} y1={yAt(b.high)} y2={yAt(b.low)} stroke={color} strokeOpacity="0.8" strokeWidth="1" vectorEffect="non-scaling-stroke" />
              {/* body */}
              <rect x={cx - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={up ? "none" : color} stroke={color} strokeWidth="1" vectorEffect="non-scaling-stroke" rx={0.5}>
                <title>tasks {i * bucketSize + 1}–{Math.min((i + 1) * bucketSize, tasks.length)} · open {metric.fmt(b.open)} · close {metric.fmt(b.close)} · high {metric.fmt(b.high)} · low {metric.fmt(b.low)}</title>
              </rect>
            </g>
          );
        })}
        <text x={CHART_WIDTH - CHART_PAD_R} y={CHART_HEIGHT - 4} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity="0.4">
          {bucketSize}-task buckets · open→close body, high/low wick
        </text>
      </Box>
    </Box>
  );
}
TaskCandleChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

TaskRollingAvgChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

function TaskBarChart_deprecated({ tasks, metric, peak, median, p95, taskFilterActive, outcomeFilterActive, onTaskClick }) {
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const slot = CHART_PLOT_W / Math.max(tasks.length, 1);
  const barW = Math.max(2, slot * 0.72);
  const anyOutcomeFilter = !!outcomeFilterActive;
  return (
    <Box sx={{ p: 2.5 }}>
      <Box
        component="svg"
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}
      >
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {tasks.map((t, i) => {
          const v = metric.get(t) || 0;
          const outcome = outcomeOf(t);
          const color = OUTCOME[outcome] || ACCENT;
          const isTaskActive = taskFilterActive === t.id;
          const isOutcomeDim = anyOutcomeFilter && outcomeFilterActive !== outcome;
          const dim = (taskFilterActive && !isTaskActive) || isOutcomeDim;
          const x = CHART_PAD_L + i * slot + (slot - barW) / 2;
          const y = yAt(v);
          const h = CHART_PAD_T + CHART_PLOT_H - y;
          return (
            <g key={t.id} opacity={dim ? 0.28 : 1} style={{ transition: "opacity .15s ease", cursor: "pointer" }} onClick={() => onTaskClick(t)}>
              <rect x={x} y={y} width={barW} height={Math.max(1, h)} fill={color} rx={1.5}>
                <title>{t.title || t.id} · {metric.fmt(v)} · {outcome}</title>
              </rect>
            </g>
          );
        })}
      </Box>
    </Box>
  );
}
TaskBarChart_deprecated.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Scatter chart — dots only, no connecting line. Same grid and dot
  behaviour as the line chart, minus the trajectory. Useful when
  ordering by task index shouldn't imply continuity.
*/
function TaskScatterChart({ tasks, metric, peak, median, p95, taskFilterActive, outcomeFilterActive, onTaskClick }) {
  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / Math.max(peak, 1)) * CHART_PLOT_H;
  const xAt = (i) => {
    if (tasks.length <= 1) return CHART_PAD_L + CHART_PLOT_W / 2;
    return CHART_PAD_L + (i / (tasks.length - 1)) * CHART_PLOT_W;
  };
  const anyOutcomeFilter = !!outcomeFilterActive;
  return (
    <Box sx={{ p: 2.5 }}>
      <Box
        component="svg"
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}
      >
        <ChartAxes metric={metric} peak={peak} median={median} p95={p95} />
        {tasks.map((t, i) => {
          const v = metric.get(t) || 0;
          const outcome = outcomeOf(t);
          const color = OUTCOME[outcome] || ACCENT;
          const isTaskActive = taskFilterActive === t.id;
          const isOutcomeDim = anyOutcomeFilter && outcomeFilterActive !== outcome;
          const dim = (taskFilterActive && !isTaskActive) || isOutcomeDim;
          const cx = xAt(i);
          const cy = yAt(v);
          const r = isTaskActive ? 5.5 : 3.75;
          return (
            <g key={t.id} opacity={dim ? 0.25 : 1} style={{ transition: "opacity .15s ease", cursor: "pointer" }} onClick={() => onTaskClick(t)}>
              <circle cx={cx} cy={cy} r={r + 3} fill={color} fillOpacity="0.14" />
              <circle cx={cx} cy={cy} r={r} fill={color}>
                <title>{t.title || t.id} · {metric.fmt(v)} · {outcome}</title>
              </circle>
            </g>
          );
        })}
      </Box>
    </Box>
  );
}
TaskScatterChart.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
  median: PropTypes.number, p95: PropTypes.number,
  taskFilterActive: PropTypes.string, outcomeFilterActive: PropTypes.string, onTaskClick: PropTypes.func,
};

/*
  Histogram — count of tasks per value bin, stacked by outcome so
  the color composition of each column reveals whether high or low
  values of the metric skew towards failure. X-axis is now the
  metric's value range, not task index.
*/
function MetricHistogram({ tasks, metric, peak }) {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  const bins = 20;
  const values = tasks.map((t) => ({ v: metric.get(t) || 0, outcome: outcomeOf(t) }));
  const max = Math.max(...values.map((x) => x.v), peak, 1);
  const min = 0;
  const step = (max - min) / bins;
  const grid = Array.from({ length: bins }, () => ({ passed: 0, failed: 0, critical: 0, unmeasured: 0, error: 0 }));
  values.forEach(({ v, outcome }) => {
    const idx = Math.min(bins - 1, Math.max(0, Math.floor((v - min) / step)));
    grid[idx][outcome === "error" ? "failed" : outcome] += 1;
  });
  const peakN = Math.max(1, ...grid.map((g) => g.passed + g.failed + g.critical + g.unmeasured));

  const yAt = (v) => CHART_PAD_T + CHART_PLOT_H - (v / peakN) * CHART_PLOT_H;
  const slot = CHART_PLOT_W / bins;
  const barW = slot * 0.85;

  const yTickN = 4;
  const yTicks = Array.from({ length: yTickN + 1 }).map((_, i) => Math.round((i / yTickN) * peakN));

  return (
    <Box sx={{ p: 2.5 }}>
      <Box
        component="svg"
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        sx={{ width: "100%", height: CHART_HEIGHT, display: "block", overflow: "visible" }}
      >
        {/* Y grid + labels (count axis) */}
        {yTicks.map((v) => {
          const y = yAt(v);
          const isBase = v === 0;
          return (
            <g key={v}>
              <line
                x1={CHART_PAD_L} x2={CHART_WIDTH - CHART_PAD_R}
                y1={y} y2={y}
                stroke="currentColor"
                strokeOpacity={isBase ? (dark ? 0.18 : 0.13) : (dark ? 0.04 : 0.035)}
                strokeWidth={isBase ? 0.75 : 0.5}
                strokeDasharray={isBase ? "0" : "2 4"}
                shapeRendering="crispEdges"
              />
              <text
                x={CHART_PAD_L - 10} y={y + 3}
                textAnchor="end" fontSize="10.5" fill="currentColor"
                fillOpacity={dark ? 0.45 : 0.5}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {v}
              </text>
            </g>
          );
        })}

        {/* stacked bars: passed (bottom, green) → failed → critical (top, deep red) */}
        {grid.map((cell, i) => {
          const x = CHART_PAD_L + i * slot + (slot - barW) / 2;
          let acc = 0;
          const order = ["passed", "failed", "critical", "unmeasured"];
          return (
            <g key={i}>
              {order.map((key) => {
                const n = cell[key];
                if (!n) return null;
                const h = (n / peakN) * CHART_PLOT_H;
                const y = CHART_PAD_T + CHART_PLOT_H - acc - h;
                acc += h;
                const binLo = min + i * step;
                const binHi = min + (i + 1) * step;
                return (
                  <rect
                    key={key}
                    x={x} y={y} width={barW} height={h}
                    fill={OUTCOME[key]}
                    opacity={0.9}
                    rx={1}
                  >
                    <title>{n} × {key} · {metric.fmt(binLo)}–{metric.fmt(binHi)}</title>
                  </rect>
                );
              })}
            </g>
          );
        })}

        {/* X-axis metric-value ticks: min · mid · max */}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const x = CHART_PAD_L + f * CHART_PLOT_W;
          const v = min + f * (max - min);
          return (
            <text
              key={f} x={x} y={CHART_HEIGHT - 6} textAnchor="middle"
              fontSize="10" fill="currentColor" fillOpacity="0.5"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {metric.fmt(v)}
            </text>
          );
        })}
      </Box>
    </Box>
  );
}
MetricHistogram.propTypes = {
  tasks: PropTypes.array, metric: PropTypes.object, peak: PropTypes.number,
};
