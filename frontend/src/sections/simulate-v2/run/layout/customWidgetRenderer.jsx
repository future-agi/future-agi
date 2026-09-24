import PropTypes from "prop-types";
import { useContext, useMemo } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import ReactApexChart from "../../components/SafeApexChart";
import { attribute, DOMAINS } from "../../_mock/failures";
import { DrilldownContext } from "../drilldownContext";
import { deriveGoalOutcome, GOAL_OUTCOME_LABELS } from "../goalOutcome";
import { deriveToolCalls } from "../toolCalls.js";
import { readPersonaDimensions } from "../personaDimensions.js";
import { OUTCOME_LABELS, attributionOf, endReasonOf, outcomeOf, passRateOf, sentimentOf } from "../taskOutcome";

/**
 * The unified widget renderer.
 *
 * A config drives everything:
 *   {
 *     id, kind: "custom" | "builtin", title, info?,
 *     source: "tasks" | "evaluations" | "graders" | "voice_pipeline" | "voice_segments" | "attribution_layers" | "run_positions",
 *     chart: "bignumber" | "donut" | "bar" | "line" | "table" | "heatmap" | "stacked_bar" | "percentile_tiles" | "slo_table" | "radar",
 *     metric: "count" | "pass_rate" | "avg_latency" | "avg_cost" | "avg_turns" | "avg_tokens" | "sum_cost" | "p50" | "p90" | "p99" | "max",
 *     groupBy?, subGroupBy?, limit?, filters?,
 *     colorMap?: { [bucketKey]: "#hex" },        // per-slice colors (attribution)
 *     thresholds?: { warn: number, bad: number, direction: "up" | "down" }, // SLO tables
 *     drilldown?: boolean,                       // click a slice → task drawer
 *   }
 */

const NEUTRAL_COLORS = ["#7857FC", "#0EA5E9", "#16A34A", "#F59E0B", "#DB2777", "#94A3B8", "#DC2626", "#8B5CF6"];

/** ── data source resolvers ────────────────────────────────────── */

function accessorRows({ source, tasks, evals, graderResults, voice }) {
  if (source === "tasks")             return tasks || [];
  if (source === "evaluations")       return graderResults || [];
  if (source === "graders")           return evals || [];
  if (source === "voice_pipeline")    return voicePipelineRows(tasks, voice);
  if (source === "voice_segments")    return voiceSegmentRows(tasks, voice);
  if (source === "attribution_layers") return attributionLayerRows(tasks);
  if (source === "run_positions")     return runPositionRows(tasks);
  if (source === "tool_calls")        return deriveToolCalls(tasks);
  return [];
}

/* Pre-shaped rows for the non-task sources so groupBy + metric logic
   stays uniform. Each row carries the fields group_key + metric
   accessors expect. */
function voicePipelineRows(tasks) {
  const stages = ["LLM", "TTS", "STT", "Transport"];
  const out = [];
  (tasks || []).forEach((t) => {
    const cost = t.cost || 0;
    stages.forEach((stage) => {
      out.push({
        __derived: true,
        pipelineStage: stage,
        cost: cost * pipelineShare(stage),
        durationMs: t.durationMs || 0,
      });
    });
  });
  return out;
}
function pipelineShare(stage) {
  switch (stage) {
    case "LLM":       return 0.55;
    case "TTS":       return 0.20;
    case "STT":       return 0.15;
    case "Transport": return 0.10;
    default:          return 0;
  }
}

function voiceSegmentRows(tasks) {
  /* Each row = one voice segment latency observation. The renderer's
     p50/p90/p99 metrics compute over these. Threshold: TTFW 2500ms,
     LLM 4000ms, TTS 1200ms, STT 900ms. */
  const segments = [
    { key: "TTFW", threshold: 2500, share: 0.20 },
    { key: "LLM",  threshold: 4000, share: 0.55 },
    { key: "TTS",  threshold: 1200, share: 0.15 },
    { key: "STT",  threshold: 900,  share: 0.10 },
  ];
  const out = [];
  (tasks || []).forEach((t) => {
    const total = t.durationMs || 0;
    segments.forEach((s) => {
      out.push({
        __derived: true,
        voiceSegment: s.key,
        threshold: s.threshold,
        durationMs: Math.round(total * s.share),
      });
    });
  });
  return out;
}

function attributionLayerRows(tasks) {
  const out = [];
  (tasks || []).forEach((t) => {
    if (t.status === "passed") return;
    const d = attribute(t);
    if (!d) return;
    out.push({ __derived: true, attributionLayer: d.id, attributionColor: DOMAINS[d.id]?.color, task: t });
  });
  return out;
}

function runPositionRows(tasks) {
  const total = (tasks || []).length;
  const bucketSize = Math.max(1, Math.ceil(total / 12));
  const out = [];
  (tasks || []).forEach((t, i) => {
    const bucket = Math.floor(i / bucketSize);
    out.push({
      __derived: true,
      runPositionBucket: `T${bucket * bucketSize + 1}–${Math.min(total, (bucket + 1) * bucketSize)}`,
      runPositionOrder: bucket,
      status: t.status,
      passed: t.status === "passed",
      durationMs: t.durationMs || 0,
      cost: t.cost || 0,
      steps: t.steps || [],
      tokens: t.tokens,
    });
  });
  return out;
}

export function groupKey(row, groupBy) {
  if (!row) return "—";
  switch (groupBy) {
    case "persona":              return row.persona?.name || row.persona?.slug || "—";
    case "use_case":             return row.useCase || row.task || "—";
    /* Task rows use the shared outcome definition (passed / flaky / failed /
       not measured); derived rows (grader results) carry their own status. */
    case "status":               return row.__derived || !row.steps ? (row.status || "—") : OUTCOME_LABELS[outcomeOf(row)];
    case "outcome_binary": {
      if (row.__derived || !row.steps) return row.status === "passed" ? "Successful" : "Unsuccessful";
      const o = outcomeOf(row);
      return o === "unmeasured" ? "Not measured" : o === "passed" ? "Successful" : "Unsuccessful";
    }
    case "goal_outcome":         return GOAL_OUTCOME_LABELS[deriveGoalOutcome(row)] || "N/A";
    case "sentiment":            return sentimentOf(row) || "—";
    case "disconnection":        return endReasonOf(row);
    case "attribution":          return attributionOf(row)?.label || "—";
    case "attribution_layer":    return row.attributionLayer || "—";
    case "pipeline_stage":       return row.pipelineStage || "—";
    case "voice_segment":        return row.voiceSegment || "—";
    case "run_position_bucket":  return row.runPositionBucket || "—";
    case "tool_name":            return row.toolName || "—";
    case "tool_status":          return row.toolStatus || "—";
    case "turns":                return String(row.steps?.length || 0);
    case "turn_bucket":          return turnBucket(row.steps?.length || 0);
    case "eval":                 return row.evalName || row.name || "—";
    default: {
      /* Custom persona dimensions — group key = whatever value the
         persona has for that dimension. Enables slicing by "patience
         level" and friends per Monika's ask. */
      if (groupBy?.startsWith("persona_dim:")) {
        const dim = groupBy.slice("persona_dim:".length);
        return String(row.persona?.dims?.[dim] || row.persona?.[dim] || "—");
      }
      return "all";
    }
  }
}

function turnBucket(n) {
  if (n <= 3) return "1–3";
  if (n <= 6) return "4–6";
  if (n <= 10) return "7–10";
  return "11+";
}

/** ── filter application ─────────────────────────────────────── */

function passesFilters(row, filters) {
  if (!filters?.length) return true;
  return filters.every((f) => {
    const val = groupKey(row, f.field);
    const values = (f.values || []).map((v) => String(v));
    const contains = values.includes(String(val));
    switch (f.op) {
      case "is":     return values.length ? contains : true;
      case "is_not": return values.length ? !contains : true;
      case "in":     return values.length ? contains : true;
      default:       return true;
    }
  });
}

/** ── metric reducers ─────────────────────────────────────────── */

function metricLabel(m) {
  return ({
    count:              "Count",
    pass_rate:          "Pass rate",
    goal_outcome_rate:  "Goal outcome rate",
    tool_success_rate:  "Tool success rate",
    tool_fail_rate:     "Tool failure rate",
    avg_latency:        "Avg latency (ms)",
    avg_cost:           "Avg cost ($)",
    avg_turns:          "Avg turns",
    avg_tokens:         "Avg tokens",
    sum_cost:           "Total cost ($)",
    p50:                "p50",
    p90:                "p90",
    p99:                "p99",
    max:                "Max",
  })[m] || m;
}

function metricFormatter(m) {
  switch (m) {
    case "pass_rate":
    case "goal_outcome_rate":
    case "tool_success_rate":
    case "tool_fail_rate": return (v) => `${Math.round(v)}%`;
    case "avg_latency":
    case "p50":
    case "p90":
    case "p99":
    case "max":         return (v) => `${Math.round(v)}ms`;
    case "avg_cost":    return (v) => `$${Number(v).toFixed(3)}`;
    case "sum_cost":    return (v) => `$${Number(v).toFixed(2)}`;
    case "avg_turns":   return (v) => Number(v).toFixed(1);
    case "avg_tokens":  return (v) => Math.round(v).toLocaleString();
    default:            return (v) => Math.round(v).toLocaleString();
  }
}

function reduce(rows, metric) {
  if (!rows.length) return 0;
  const nums = (extract) => rows.map(extract).filter((v) => Number.isFinite(v) && v > 0);
  const pct = (arr, p) => {
    if (!arr.length) return 0;
    const sorted = [...arr].sort((a, b) => a - b);
    const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
    return sorted[idx];
  };
  switch (metric) {
    case "count":      return rows.length;
    case "pass_rate": {
      /* Task rows: the runs-table rate — mean passShare over measured
         tasks. Grader rows: share of checks that passed. */
      if (rows[0]?.steps) return passRateOf(rows) ?? 0;
      const total = rows.length;
      const passed = rows.filter((r) => r.status === "passed" || r.passed === true).length;
      return total ? (passed / total) * 100 : 0;
    }
    case "goal_outcome_rate": {
      /* Share of rows whose goal outcome is "achieved" — the
         business-level answer above pass/fail. */
      const total = rows.length;
      if (!total) return 0;
      const achieved = rows.filter((r) => deriveGoalOutcome(r) === "achieved").length;
      return (achieved / total) * 100;
    }
    /* Over calls the agent actually made — a required tool it never
       called is the agent's miss, not a tool success or failure. */
    case "tool_success_rate": {
      const made = rows.filter((r) => r.toolStatus !== "not_called");
      const ok = made.filter((r) => r.toolStatus === "success").length;
      return made.length ? (ok / made.length) * 100 : 0;
    }
    case "tool_fail_rate": {
      const made = rows.filter((r) => r.toolStatus !== "not_called");
      const bad = made.filter((r) => r.toolStatus === "failed" || r.toolStatus === "error" || r.toolStatus === "timeout").length;
      return made.length ? (bad / made.length) * 100 : 0;
    }
    case "avg_latency": {
      const vals = nums((r) => r.durationMs || r.latencyMs || 0);
      return vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : 0;
    }
    case "avg_cost": {
      const vals = nums((r) => r.cost || 0);
      return vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : 0;
    }
    case "sum_cost":   return rows.reduce((a, r) => a + (r.cost || 0), 0);
    case "avg_turns": {
      const vals = nums((r) => r.steps?.length || 0);
      return vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : 0;
    }
    case "avg_tokens": {
      const vals = nums((r) => r.tokens || 0);
      return vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : 0;
    }
    case "p50": return pct(nums((r) => r.durationMs || r.latencyMs || 0), 50);
    case "p90": return pct(nums((r) => r.durationMs || r.latencyMs || 0), 90);
    case "p99": return pct(nums((r) => r.durationMs || r.latencyMs || 0), 99);
    case "max": return Math.max(0, ...nums((r) => r.durationMs || r.latencyMs || 0));
    default: return 0;
  }
}

/* Rows contributing to a bucket key — used for drill-down so a click
   on "Successful" in a donut opens the drawer with just the passing
   rows. */
function rowsForKey(rows, groupBy, key) {
  return rows.filter((r) => String(groupKey(r, groupBy)) === String(key));
}

/** ── main renderer ───────────────────────────────────────────── */

export function CustomWidgetBody({ config, ctx }) {
  const theme = useTheme();
  const drill = useContext(DrilldownContext);
  /* eslint-disable-next-line no-console */
  if (typeof window !== "undefined") console.log("[CustomWidgetBody]", config?.id, config?.source, "tasks:", (ctx?.tasks || []).length);
  const rawRows = accessorRows({ source: config.source, ...ctx });
  const rows = useMemo(
    () => rawRows.filter((r) => passesFilters(r, config.filters)),
    [rawRows, config.filters],
  );

  const isMatrix = config.chart === "heatmap" || config.chart === "stacked_bar";

  const grouped = useMemo(() => {
    if (config.chart === "bignumber" || !config.groupBy) return null;
    if (isMatrix && config.subGroupBy) return null;
    const bucketMap = new Map();
    rows.forEach((r) => {
      const key = String(groupKey(r, config.groupBy) || "—");
      if (!bucketMap.has(key)) bucketMap.set(key, []);
      bucketMap.get(key).push(r);
    });
    const buckets = [...bucketMap.entries()].map(([key, list]) => ({
      key, value: reduce(list, config.metric), count: list.length, rows: list,
    }));
    buckets.sort((a, b) => b.value - a.value);
    return config.limit ? buckets.slice(0, config.limit) : buckets;
  }, [rows, config, isMatrix]);

  const matrixGrouped = useMemo(() => {
    if (!isMatrix || !config.groupBy || !config.subGroupBy) return null;
    const rowMap = new Map();
    const colSet = new Set();
    rows.forEach((r) => {
      const rk = String(groupKey(r, config.groupBy) || "—");
      const ck = String(groupKey(r, config.subGroupBy) || "—");
      colSet.add(ck);
      if (!rowMap.has(rk)) rowMap.set(rk, new Map());
      const inner = rowMap.get(rk);
      if (!inner.has(ck)) inner.set(ck, []);
      inner.get(ck).push(r);
    });
    const rowKeys = [...rowMap.keys()];
    const colKeys = [...colSet];
    rowKeys.sort((a, b) => {
      const A = [...(rowMap.get(a) || new Map()).values()].reduce((acc, list) => acc + reduce(list, config.metric), 0);
      const B = [...(rowMap.get(b) || new Map()).values()].reduce((acc, list) => acc + reduce(list, config.metric), 0);
      return B - A;
    });
    const limitedRows = config.limit ? rowKeys.slice(0, config.limit) : rowKeys;
    return { rowKeys: limitedRows, colKeys, rowMap };
  }, [rows, config, isMatrix]);

  const fmt = metricFormatter(config.metric);

  /* Drill-down click — filter the underlying tasks by the bucket
     key and open the shell's task drawer. Only wired when the
     config opts in via drilldown: true AND the source is tasks or
     evaluations (drilling into voice_pipeline etc. doesn't map
     back cleanly to a task list). */
  const onBucketClick = (bucket) => {
    if (!drill || !config.drilldown) return;
    const source = config.source;
    if (source !== "tasks" && source !== "evaluations") return;
    const tasks = source === "tasks"
      ? bucket.rows
      : uniqueTasksFromResults(bucket.rows, ctx.tasks || []);
    drill({
      title: `${config.title || "Widget"} — ${bucket.key}`,
      subtitle: `${tasks.length} tasks`,
      tasks,
    });
  };

  if (!rows.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ fontSize: 12, color: "text.primary" }}>
          {rawRows.length
            ? "Filters excluded every row."
            : `No data for source "${config.source}" yet — ${rawRows.length} raw rows returned.`}
        </Typography>
      </Box>
    );
  }

  /* ── big number ── */
  if (config.chart === "bignumber") {
    const value = reduce(rows, config.metric);
    return (
      <Box sx={{ px: 3, py: 3 }}>
        <Typography sx={{ fontSize: 32, fontWeight: 700, letterSpacing: -0.5, color: "text.primary", fontVariantNumeric: "tabular-nums" }}>
          {fmt(value)}
        </Typography>
        <Typography sx={{ fontSize: 12, color: "text.subtitle", mt: 0.5 }}>
          {metricLabel(config.metric)} · {rows.length} {config.source}
        </Typography>
      </Box>
    );
  }

  /* ── percentile tiles — big-number grid, one tile per bucket ── */
  if (config.chart === "percentile_tiles") {
    /* Prefer the source-native tile shape when groupBy is a
       distribution-shaped dimension (metric ∈ p50..max), otherwise
       fall back to a single hero number per bucket. */
    const percentileMetrics = ["p50", "p90", "p99", "max"];
    const showAllPercentiles = percentileMetrics.includes(config.metric);
    return (
      <Box sx={{ px: 2, py: 2, display: "grid", gap: 1.25, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(auto-fit, minmax(140px, 1fr))" } }}>
        {(grouped || [{ key: metricLabel(config.metric), rows }]).map((b) => (
          <Box key={b.key} sx={{
            p: 1.5, border: "1px solid", borderColor: "divider", borderRadius: 1.25,
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.02 : 0.015),
          }}>
            <Typography noWrap sx={{ fontSize: 11, color: "text.subtitle", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4 }}>
              {b.key}
            </Typography>
            {showAllPercentiles ? (
              <Stack spacing={0.5} sx={{ mt: 0.75 }}>
                {["p50", "p90", "p99"].map((m) => (
                  <Stack key={m} direction="row" justifyContent="space-between" alignItems="baseline">
                    <Typography sx={{ fontSize: 10.5, color: "text.subtitle", textTransform: "uppercase" }}>{m}</Typography>
                    <Typography sx={{ fontSize: 13, fontWeight: 700, fontVariantNumeric: "tabular-nums", color: "text.primary" }}>
                      {metricFormatter(m)(reduce(b.rows, m))}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            ) : (
              <Typography sx={{ fontSize: 20, fontWeight: 700, mt: 0.75, letterSpacing: -0.3, fontVariantNumeric: "tabular-nums" }}>
                {fmt(b.value)}
              </Typography>
            )}
          </Box>
        ))}
      </Box>
    );
  }

  /* ── SLO table — each row shows the segment, p50/p90/p99, color-coded ── */
  if (config.chart === "slo_table") {
    if (!grouped?.length) {
      return (
        <Box sx={{ p: 3, textAlign: "center" }}>
          <Typography sx={{ fontSize: 12, color: "text.subtitle" }}>Pick a Group by that names each row (e.g. Voice segment).</Typography>
        </Box>
      );
    }
    return (
      <Box sx={{ px: 2.5, py: 1.5 }}>
        <Box sx={{
          display: "grid", gridTemplateColumns: "minmax(120px, 1.4fr) repeat(3, minmax(64px, 1fr)) minmax(80px, 1fr)",
          columnGap: 2, rowGap: 0.75, alignItems: "center",
        }}>
          {["Segment", "p50", "p90", "p99", "SLO"].map((h, i) => (
            <Typography key={h} sx={{
              fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase", color: "text.subtitle",
              textAlign: i === 0 ? "left" : "right",
            }}>{h}</Typography>
          ))}
          {grouped.map((b) => {
            const p50 = reduce(b.rows, "p50");
            const p90 = reduce(b.rows, "p90");
            const p99 = reduce(b.rows, "p99");
            const slo = b.rows[0]?.threshold;
            const violates = slo && p90 > slo;
            const warn = slo && p90 > slo * 0.7;
            const p90Color = violates ? "#DC2626" : warn ? "#F59E0B" : "text.primary";
            return (
              <Box key={b.key} sx={{ display: "contents" }}>
                <Typography noWrap sx={{ fontSize: 13, fontWeight: 600 }}>{b.key}</Typography>
                <Typography sx={{ fontSize: 12.5, textAlign: "right", fontVariantNumeric: "tabular-nums", color: "text.subtitle" }}>{Math.round(p50)}ms</Typography>
                <Typography sx={{ fontSize: 13, textAlign: "right", fontVariantNumeric: "tabular-nums", fontWeight: 700, color: p90Color }}>{Math.round(p90)}ms</Typography>
                <Typography sx={{ fontSize: 12.5, textAlign: "right", fontVariantNumeric: "tabular-nums", color: "text.subtitle" }}>{Math.round(p99)}ms</Typography>
                <Typography sx={{ fontSize: 11.5, textAlign: "right", color: "text.subtitle" }}>{slo ? `≤ ${slo}ms` : "—"}</Typography>
              </Box>
            );
          })}
        </Box>
      </Box>
    );
  }

  /* ── radar ── */
  if (config.chart === "radar") {
    if (!grouped?.length) {
      return <EmptyMsg text="Pick a Group by to render a radar." />;
    }
    const categories = grouped.map((b) => b.key);
    const series = [{ name: metricLabel(config.metric), data: grouped.map((b) => b.value) }];
    return (
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="radar" height={280}
          series={series}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent" },
            theme: { mode: theme.palette.mode },
            colors: [NEUTRAL_COLORS[0]],
            xaxis: { categories, labels: { style: { fontSize: "10.5px", colors: Array(categories.length).fill(theme.palette.text.secondary) } } },
            yaxis: { show: false },
            stroke: { width: 2 },
            fill: { opacity: 0.25 },
            markers: { size: 3 },
            tooltip: { y: { formatter: (v) => fmt(v) } },
          }}
        />
      </Box>
    );
  }

  /* ── table ── */
  if (config.chart === "table") {
    return (
      <Box sx={{ px: 2, py: 1.5 }}>
        <Box sx={{
          display: "grid", gridTemplateColumns: "1fr auto auto", columnGap: 2, rowGap: 0.5,
          fontSize: 12,
        }}>
          <Typography sx={{ fontSize: 10.5, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 700 }}>{config.groupBy || "group"}</Typography>
          <Typography sx={{ fontSize: 10.5, color: "text.subtitle", textAlign: "right", textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 700 }}>{metricLabel(config.metric)}</Typography>
          <Typography sx={{ fontSize: 10.5, color: "text.subtitle", textAlign: "right", textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 700 }}>N</Typography>
          {grouped.map((b) => (
            <Box key={b.key} sx={{ display: "contents", cursor: config.drilldown ? "pointer" : "default" }} onClick={() => onBucketClick(b)}>
              <Typography noWrap sx={{ fontSize: 12.5, color: "text.primary" }}>{b.key}</Typography>
              <Typography sx={{ fontSize: 12.5, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{fmt(b.value)}</Typography>
              <Typography sx={{ fontSize: 12.5, textAlign: "right", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>{b.count}</Typography>
            </Box>
          ))}
        </Box>
      </Box>
    );
  }

  /* ── heatmap ── */
  if (config.chart === "heatmap") {
    if (!matrixGrouped) return <EmptyMsg text="Pick a Group by and a Split by to render a heatmap." />;
    const { rowKeys, colKeys, rowMap } = matrixGrouped;
    const series = rowKeys.map((rk) => ({
      name: rk,
      data: colKeys.map((ck) => {
        const list = rowMap.get(rk)?.get(ck) || [];
        return { x: ck, y: list.length ? reduce(list, config.metric) : 0 };
      }),
    }));
    return (
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="heatmap" height={Math.max(220, rowKeys.length * 34 + 60)}
          series={series}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent" },
            theme: { mode: theme.palette.mode },
            dataLabels: { enabled: true, style: { fontSize: "10.5px", colors: [theme.palette.text.primary] } },
            plotOptions: { heatmap: { radius: 3, useFillColorAsStroke: false, colorScale: { ranges: metricHeatmapRanges(config.metric) } } },
            xaxis: { labels: { style: { fontSize: "10.5px", colors: theme.palette.text.secondary }, rotate: -30, trim: false } },
            yaxis: { labels: { style: { fontSize: "10.5px", colors: theme.palette.text.secondary } } },
            grid: { padding: { bottom: 20 } },
            tooltip: { y: { formatter: (v) => fmt(v) } },
          }}
        />
      </Box>
    );
  }

  /* ── stacked_bar ── */
  if (config.chart === "stacked_bar") {
    if (!matrixGrouped) return <EmptyMsg text="Pick a Group by and a Split by to render a stacked bar." />;
    const { rowKeys, colKeys, rowMap } = matrixGrouped;
    const series = colKeys.map((ck, i) => ({
      name: ck,
      data: rowKeys.map((rk) => {
        const list = rowMap.get(rk)?.get(ck) || [];
        return list.length ? reduce(list, config.metric) : 0;
      }),
      color: NEUTRAL_COLORS[i % NEUTRAL_COLORS.length],
    }));
    return (
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="bar" height={Math.max(240, rowKeys.length * 34 + 60)}
          series={series}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", stacked: true },
            theme: { mode: theme.palette.mode },
            plotOptions: { bar: { horizontal: false, columnWidth: "58%", borderRadius: 2 } },
            dataLabels: { enabled: false },
            xaxis: { categories: rowKeys, labels: { style: { fontSize: "10.5px", colors: theme.palette.text.secondary }, rotate: -30 } },
            yaxis: { labels: { style: { fontSize: "10px", colors: theme.palette.text.secondary }, formatter: fmt } },
            grid: { borderColor: alpha(theme.palette.text.primary, 0.08), strokeDashArray: 4, padding: { bottom: 20 } },
            legend: { position: "bottom", fontSize: "11px" },
            tooltip: { y: { formatter: (v) => fmt(v) } },
          }}
        />
      </Box>
    );
  }

  /* ── donut / bar / line ── */
  const labels = grouped.map((b) => b.key);
  const series = grouped.map((b) => b.value);
  const colors = grouped.map((b, i) => (
    config.colorMap && config.colorMap[b.key]
      ? config.colorMap[b.key]
      : (b.rows[0]?.attributionColor || NEUTRAL_COLORS[i % NEUTRAL_COLORS.length])
  ));

  if (config.chart === "donut") {
    return (
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="donut" height={240}
          series={series}
          options={{
            chart: {
              animations: { enabled: false }, background: "transparent",
              ...(config.drilldown && {
                events: {
                  dataPointSelection: (_e, _ctx, cfg) => {
                    const b = grouped[cfg.dataPointIndex];
                    if (b) onBucketClick(b);
                  },
                },
              }),
            },
            theme: { mode: theme.palette.mode },
            labels, colors,
            legend: { position: "bottom", fontSize: "11px" },
            dataLabels: { enabled: false },
            stroke: { width: 2, colors: [theme.palette.background.paper] },
            plotOptions: { pie: { expandOnClick: false, donut: { size: "60%" } } },
            states: { hover: { filter: { type: "none" } }, active: { filter: { type: "none" } } },
            tooltip: {
              intersect: false, followCursor: false, fixed: { enabled: false },
              y: { formatter: (v) => `${fmt(v)}${config.drilldown ? " · click to drill down" : ""}` },
            },
          }}
        />
      </Box>
    );
  }

  if (config.chart === "line") {
    return (
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="line" height={240}
          series={[{ name: metricLabel(config.metric), data: series }]}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent" },
            theme: { mode: theme.palette.mode },
            colors: [NEUTRAL_COLORS[0]],
            stroke: { curve: "smooth", width: 2 },
            xaxis: { categories: labels, axisBorder: { show: false }, axisTicks: { show: false }, labels: { style: { fontSize: "10px", colors: theme.palette.text.secondary }, rotate: -30 } },
            yaxis: { labels: { style: { fontSize: "10px", colors: theme.palette.text.secondary }, formatter: fmt } },
            grid: { borderColor: alpha(theme.palette.text.primary, 0.08), strokeDashArray: 4 },
            markers: { size: 4 },
            tooltip: { y: { formatter: (v) => fmt(v) } },
          }}
        />
      </Box>
    );
  }

  // default: bar
  return (
    <Box sx={{ px: 1.5, py: 1.5 }}>
      <ReactApexChart
        type="bar" height={280}
        series={[{ name: metricLabel(config.metric), data: series }]}
        options={{
          chart: {
            toolbar: { show: false }, animations: { enabled: false }, background: "transparent",
            ...(config.drilldown && {
              events: {
                dataPointSelection: (_e, _ctx, cfg) => {
                  const b = grouped[cfg.dataPointIndex];
                  if (b) onBucketClick(b);
                },
              },
            }),
          },
          theme: { mode: theme.palette.mode },
          colors: [NEUTRAL_COLORS[0]],
          plotOptions: { bar: { columnWidth: "58%", borderRadius: 4, borderRadiusApplication: "end", distributed: !!config.colorMap } },
          dataLabels: { enabled: true, formatter: fmt, style: { fontSize: "10px", fontWeight: 700, colors: [theme.palette.text.primary] }, offsetY: -16 },
          xaxis: {
            categories: labels,
            axisBorder: { show: false }, axisTicks: { show: false },
            labels: { style: { fontSize: "10.5px", colors: theme.palette.text.secondary, fontWeight: 600 }, rotate: -30, trim: false, hideOverlappingLabels: false },
          },
          yaxis: { labels: { style: { fontSize: "10px", colors: theme.palette.text.secondary }, formatter: fmt } },
          grid: { borderColor: alpha(theme.palette.text.primary, 0.08), strokeDashArray: 4, padding: { bottom: 30 } },
          tooltip: { y: { formatter: (v) => fmt(v) } },
          ...(config.colorMap ? { fill: { colors: colors } } : {}),
        }}
      />
    </Box>
  );
}
CustomWidgetBody.propTypes = { config: PropTypes.object.isRequired, ctx: PropTypes.object.isRequired };

function EmptyMsg({ text }) {
  return (
    <Box sx={{ p: 3, textAlign: "center" }}>
      <Typography sx={{ fontSize: 12, color: "text.subtitle" }}>{text}</Typography>
    </Box>
  );
}
EmptyMsg.propTypes = { text: PropTypes.string };

function uniqueTasksFromResults(rows, allTasks) {
  const ids = new Set(rows.map((r) => r.taskId));
  return (allTasks || []).filter((t) => ids.has(t.id));
}

function metricHeatmapRanges(metric) {
  if (metric === "pass_rate") {
    return [
      { from: 0,   to: 40,  color: "#DC2626", name: "0–40%" },
      { from: 41,  to: 70,  color: "#F59E0B", name: "41–70%" },
      { from: 71,  to: 100, color: "#16A34A", name: "71–100%" },
    ];
  }
  return [{ from: 0, to: Infinity, color: "#7857FC", name: metricLabel(metric) }];
}

/* Options exposed to the editor UI — kept next to the renderer so
   the two stay in sync when a new source/metric/chart is added. */
export const DATA_SOURCES = [
  { id: "tasks",              label: "Tasks",             hint: "Every simulated task in this run" },
  { id: "evaluations",        label: "Evaluator results", hint: "Each grader's per-task pass/fail" },
  { id: "graders",            label: "Graders",           hint: "One row per evaluator" },
  { id: "tool_calls",         label: "Tool calls",        hint: "One row per tool invocation — routes infra failures separately" },
  { id: "voice_pipeline",     label: "Voice pipeline",    hint: "Per-stage cost / latency (LLM · TTS · STT · Transport)" },
  { id: "voice_segments",     label: "Voice segments",    hint: "Per-segment latency observations for SLO tables" },
  { id: "attribution_layers", label: "Attribution layers", hint: "Failures grouped by responsible layer" },
  { id: "run_positions",      label: "Task position",     hint: "Tasks bucketed by their order in the run (for trends)" },
];

export const METRICS = [
  { id: "count",              label: "Count",             forSource: ["tasks", "evaluations", "graders", "tool_calls", "voice_pipeline", "voice_segments", "attribution_layers", "run_positions"] },
  { id: "goal_outcome_rate",  label: "Goal outcome rate (%)", forSource: ["tasks", "run_positions"] },
  { id: "pass_rate",          label: "Pass rate (%)",     forSource: ["tasks", "evaluations", "run_positions"] },
  { id: "tool_success_rate",  label: "Tool success rate (%)", forSource: ["tool_calls"] },
  { id: "tool_fail_rate",     label: "Tool failure rate (%)", forSource: ["tool_calls"] },
  { id: "avg_latency",        label: "Avg latency",       forSource: ["tasks", "tool_calls", "voice_pipeline", "voice_segments", "run_positions"] },
  { id: "p50",                label: "p50 latency",       forSource: ["tasks", "voice_pipeline", "voice_segments", "run_positions"] },
  { id: "p90",                label: "p90 latency",       forSource: ["tasks", "voice_pipeline", "voice_segments", "run_positions"] },
  { id: "p99",                label: "p99 latency",       forSource: ["tasks", "voice_pipeline", "voice_segments", "run_positions"] },
  { id: "max",                label: "Max latency",       forSource: ["tasks", "voice_pipeline", "voice_segments", "run_positions"] },
  { id: "avg_cost",           label: "Avg cost",          forSource: ["tasks", "voice_pipeline", "run_positions"] },
  { id: "sum_cost",           label: "Total cost",        forSource: ["tasks", "voice_pipeline", "run_positions"] },
  { id: "avg_turns",          label: "Avg turns",         forSource: ["tasks", "run_positions"] },
  { id: "avg_tokens",         label: "Avg tokens",        forSource: ["tasks", "run_positions"] },
];

export const GROUP_BYS = [
  { id: "persona",              label: "Persona",              forSource: ["tasks", "evaluations"] },
  { id: "use_case",             label: "Use case",             forSource: ["tasks", "evaluations", "tool_calls"] },
  { id: "status",               label: "Outcome status",       forSource: ["tasks", "evaluations", "run_positions"] },
  { id: "outcome_binary",       label: "Successful vs unsuccessful", forSource: ["tasks", "evaluations", "run_positions"] },
  { id: "goal_outcome",         label: "Goal outcome",         forSource: ["tasks", "run_positions"] },
  { id: "sentiment",            label: "Caller sentiment",     forSource: ["tasks"] },
  { id: "disconnection",        label: "Disconnection reason", forSource: ["tasks"] },
  { id: "attribution",          label: "Failure attribution",  forSource: ["tasks"] },
  { id: "attribution_layer",    label: "Attribution layer",    forSource: ["attribution_layers"] },
  { id: "tool_name",            label: "Tool name",            forSource: ["tool_calls"] },
  { id: "tool_status",          label: "Tool status",          forSource: ["tool_calls"] },
  { id: "pipeline_stage",       label: "Pipeline stage",       forSource: ["voice_pipeline"] },
  { id: "voice_segment",        label: "Voice segment",        forSource: ["voice_segments"] },
  { id: "run_position_bucket",  label: "Task position bucket", forSource: ["run_positions"] },
  { id: "turns",                label: "Exact turn count",     forSource: ["tasks", "evaluations"] },
  { id: "turn_bucket",          label: "Turn count bucket",    forSource: ["tasks", "evaluations"] },
  { id: "eval",                 label: "Evaluator",            forSource: ["evaluations", "graders"] },
];

/**
 * Discover the user-defined persona dimensions attached to this run's
 * tasks and expose them as first-class group-by options. Called by
 * the editor at open time. Each dim becomes id `persona_dim:<name>`.
 */
export function personaDimensionGroupBys(ctx) {
  const dims = readPersonaDimensions(ctx?.tasks || []);
  return dims.map((name) => ({
    id: `persona_dim:${name}`,
    label: `Persona: ${name}`,
    forSource: ["tasks"],
  }));
}

export const CHART_TYPES = [
  { id: "bignumber",        label: "Big number",          hint: "Single hero value" },
  { id: "donut",            label: "Donut",               hint: "Share of a whole" },
  { id: "bar",              label: "Bar",                 hint: "Rank groups by metric" },
  { id: "stacked_bar",      label: "Stacked bar",         hint: "Split each bar by a second dimension" },
  { id: "line",             label: "Line",                hint: "Trend across groups" },
  { id: "heatmap",          label: "Heatmap",             hint: "Rows × columns matrix, colored by metric" },
  { id: "table",            label: "Table",               hint: "Sortable rows + metric" },
  { id: "radar",            label: "Radar",               hint: "Shape by dimension" },
  { id: "percentile_tiles", label: "Percentile tiles",    hint: "p50 / p90 / p99 per group" },
  { id: "slo_table",        label: "SLO table",           hint: "Segment table with thresholds" },
];

export const FILTER_FIELDS = GROUP_BYS.filter((g) => !g.forSource || g.forSource.includes("tasks"));

export const FILTER_OPS = [
  { id: "is",     label: "is" },
  { id: "is_not", label: "is not" },
  { id: "in",     label: "in" },
];

export function distinctValues(field, ctx) {
  const rows = accessorRows({ source: "tasks", ...ctx });
  const set = new Set();
  rows.forEach((r) => set.add(String(groupKey(r, field) || "—")));
  return [...set].sort();
}
