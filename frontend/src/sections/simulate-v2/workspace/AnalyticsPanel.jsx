import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import ReactApexChart from "../components/SafeApexChart";
import { EmptyState } from "../components/primitives";
import { runStatsOf } from "../_mock/runCompare";
import { runEnvVersion } from "../_mock/toolFit";
import { causeOf, outcomeOfCause, VERDICT_CAUSES } from "../_mock/decoupled";
import { sourceLinkOf } from "../_mock/scenarioProvenance";
import { envLabelOf } from "../_mock/envVersionKinds";
import { runSummaries } from "../_mock/comparison";
import { groupsOf } from "../_mock/scenarioOrg";
import { goalLabel } from "../_mock/scenarios";

/**
 * Trends across this environment's runs (roadmap: Runs → "Environment
 * Analytics"), drawn the way the run page's analytics are: a metric strip,
 * the pass rate over runs and by agent version, the latest run by goal,
 * outcome and scenario source, and every scenario across runs. When the runs
 * span environment versions the test itself changed between them — that is
 * marked on the trend, not drawn as one continuous measurement.
 */

/* Chart colours — the run page's accent, and pass/fail as status. */
/* eslint-disable no-restricted-syntax -- ds: data colours */
const ACCENT = "#7857FC";
const PASS = "#34D399";
const FAIL = "#F87171";
const OUTCOME_COLORS = {
  passed: "#34D399",
  failed: "#F87171",
  inconclusive: "#94A3B8",
  "test-invalid": "#FBBF24",
  error: "#A78BFA",
};
/* eslint-enable no-restricted-syntax */

/* A bar chart shows this many rows; the rest are named by count. */
const TOP_ROWS = 10;
/* The flip grid: the last runs it spans, and the rows it draws. */
const GRID_RUNS = 20;
const GRID_ROWS = 12;
const pctOf = (n, d) => (d ? Math.round((n / d) * 100) : null);
const pctLabel = (n, d) => (d ? `${pctOf(n, d)}%` : "—");
const tasksOf = (run) =>
  run?.tasks?.length ? run.tasks : run?.trialTasks || [];
const idOf = (t) => t.scenarioId || t.id;
const passedTask = (t) => t.status === "passed";

export default function AnalyticsPanel({ env, envState }) {
  const theme = useTheme();
  const runs = useMemo(
    () =>
      /* The same run records the Runs tab reads, with their outcomes. */
      runSummaries(env, envState)
        .filter((r) => r.finishedAt && !r.synthetic && r.kind !== "trial")
        .sort((a, b) => Date.parse(a.finishedAt) - Date.parse(b.finishedAt)),
    [env, envState],
  );
  const latest = runs[runs.length - 1] || null;
  const previous = runs[runs.length - 2] || null;

  /* Each run as a point: its rate, who ran, on which world, and whether the
     graded population moved since the run before it. */
  const trend = useMemo(
    () =>
      runs.map((r, i) => {
        const s = runStatsOf(r);
        const prev = i ? runStatsOf(runs[i - 1]).measured : null;
        const envV = runEnvVersion(env, envState, r);
        const prevEnv = i ? runEnvVersion(env, envState, runs[i - 1]) : envV;
        return {
          label: `Run ${r.ordinal || i + 1}`,
          /* Run numbers restart per environment version, so the axis counts
             runs oldest first; the tooltip names the run. */
          n: i + 1,
          rate: s.scenarioRate ?? 0,
          passed: s.scenarioPassed,
          measured: s.measured,
          agent: r.agentVersion || "v1",
          envV,
          envChanged: i > 0 && envV && prevEnv && envV !== prevEnv,
          popChanged: prev != null && prev !== s.measured,
          prevMeasured: prev,
          channel:
            { voice: "Voice", multi: "Voice + text" }[
              r.surface || env?.surface
            ] || "Text",
          model: r.simulator?.label || "—",
        };
      }),
    [runs, env, envState],
  );

  const byVersion = useMemo(() => {
    const m = new Map();
    runs.forEach((r) => {
      const v = r.agentVersion || "v1";
      const row = m.get(v) || { version: v, runs: 0, latest: null };
      row.runs += 1;
      row.latest = { run: r, stats: runStatsOf(r) };
      m.set(v, row);
    });
    return [...m.values()];
  }, [runs]);

  const envVersions = useMemo(
    () => [...new Set(trend.map((t) => t.envV).filter(Boolean))],
    [trend],
  );

  const scenarios = useMemo(
    () => new Map((envState?.scenarios || []).map((s) => [s.id, s])),
    [envState?.scenarios],
  );
  const bySource = useMemo(() => {
    const m = new Map();
    tasksOf(latest).forEach((t) => {
      const sc = scenarios.get(idOf(t));
      const label = sc
        ? sourceLinkOf(sc, { env, envState }).label
        : "Not in the suite now";
      const row = m.get(label) || { label, total: 0, passed: 0 };
      row.total += 1;
      if (passedTask(t)) row.passed += 1;
      m.set(label, row);
    });
    return [...m.values()].sort((a, b) => b.total - a.total);
  }, [latest, scenarios, env, envState]);

  /* How each scenario of the latest run ended, by cause. */
  const byOutcome = useMemo(() => {
    const m = new Map();
    tasksOf(latest).forEach((t) => {
      const cause = passedTask(t) ? "pass" : causeOf(t);
      const row = m.get(cause) || {
        cause,
        label: VERDICT_CAUSES[cause]?.label || cause,
        outcome: outcomeOfCause(cause),
        n: 0,
      };
      row.n += 1;
      m.set(cause, row);
    });
    return [...m.values()].sort((a, b) => b.n - a.n);
  }, [latest]);

  /* The latest run by scenario group — or by goal when there are no groups. */
  const byGroup = useMemo(() => {
    const groups = groupsOf(envState);
    const m = new Map();
    tasksOf(latest).forEach((t) => {
      const id = idOf(t);
      const labels = groups.length
        ? groups.filter((g) => g.ids.includes(id)).map((g) => g.name)
        : [goalLabel(t.useCase) || "Other"];
      (labels.length ? labels : ["No group"]).forEach((label) => {
        const row = m.get(label) || { label, total: 0, passed: 0 };
        row.total += 1;
        if (passedTask(t)) row.passed += 1;
        m.set(label, row);
      });
    });
    return {
      by: groups.length ? "group" : "goal",
      rows: [...m.values()].sort(
        (a, b) => a.passed / a.total - b.passed / b.total || b.total - a.total,
      ),
    };
  }, [latest, envState]);

  /* Every scenario of the latest run across all runs: which ones flip. */
  /* The latest run's scenarios over the last runs: the ones whose result
     changed, most changes first — the rest are counted, not drawn. */
  const grid = useMemo(() => {
    const window = runs.slice(-GRID_RUNS);
    const title = (id) => {
      const sc = scenarios.get(id);
      const t = tasksOf(latest).find((x) => idOf(x) === id);
      return sc?.title || t?.title || t?.name || id;
    };
    const all = tasksOf(latest)
      .map(idOf)
      .map((id) => {
        const cells = window.map((r) => {
          const t = tasksOf(r).find((x) => idOf(x) === id);
          return !t
            ? "absent"
            : passedTask(t)
              ? "passed"
              : t.status === "flaky"
                ? "flaky"
                : "failed";
        });
        const seen = cells.filter((c) => c !== "absent");
        const flips = seen.filter((c, k) => k && c !== seen[k - 1]).length;
        return { id, title: title(id), cells, flips, seen };
      });
    const flipping = all
      .filter((r) => r.flips > 0)
      .sort((a, b) => b.flips - a.flips);
    return {
      /* Counted oldest first, the same numbers as the trend's axis. */
      runs: window.map((_, k) => `#${runs.length - window.length + k + 1}`),
      rows: flipping.slice(0, GRID_ROWS),
      flipping: flipping.length,
      total: all.length,
      alwaysPassed: all.filter(
        (r) => !r.flips && r.seen.every((c) => c === "passed"),
      ).length,
      alwaysFailed: all.filter(
        (r) => !r.flips && r.seen.length && r.seen.every((c) => c !== "passed"),
      ).length,
    };
  }, [latest, runs, scenarios]);

  if (!runs.length)
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:chart-2-linear"
          title="No runs to chart yet"
          body="Analytics fills in after the first finished run of this environment."
        />
      </Box>
    );

  const latestStats = runStatsOf(latest);
  const prevStats = previous ? runStatsOf(previous) : null;
  const total = tasksOf(latest).length;
  const agentFailed = byOutcome
    .filter((o) => o.outcome === "failed")
    .reduce((a, o) => a + o.n, 0);
  const unmeasured = byOutcome
    .filter((o) => !["passed", "failed"].includes(o.outcome))
    .reduce((a, o) => a + o.n, 0);
  const prevAgentFailed = previous
    ? tasksOf(previous).filter(
        (t) => !passedTask(t) && outcomeOfCause(causeOf(t)) === "failed",
      ).length
    : null;
  const latestTrend = trend[trend.length - 1];

  const axisLabels = {
    style: { colors: theme.palette.text.tertiary, fontSize: "11px" },
  };
  const baseChart = {
    toolbar: { show: false },
    animations: { enabled: false },
    background: "transparent",
    fontFamily: theme.typography.fontFamily,
    zoom: { enabled: false },
  };
  const grid0 = {
    borderColor: theme.palette.divider,
    strokeDashArray: 3,
    padding: { left: 8, right: 8 },
  };

  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      {envVersions.length > 1 && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {`These runs span ${envVersions.length} environment versions (${envVersions.map((v) => envLabelOf(env, envState, v)).join(", ")}). The test changed between them — the dashed lines on the trend — so compare runs on the scenarios they share.`}
        </Typography>
      )}

      {/* The page in numbers; colour only on the change. */}
      <MetricStrip
        items={[
          {
            label: "Pass rate · latest run",
            value: pctLabel(latestStats.scenarioPassed, latestStats.measured),
            sub: `${latestStats.scenarioPassed} of ${latestStats.measured} scenarios`,
            delta:
              prevStats && prevStats.measured
                ? (latestStats.scenarioRate ?? 0) -
                  (prevStats.scenarioRate ?? 0)
                : null,
            unit: " pts",
          },
          {
            label: "Runs",
            value: runs.length,
            sub: `${byVersion.length} agent · ${envVersions.length || 1} environment version${byVersion.length + (envVersions.length || 1) === 2 ? "" : "s"}`,
          },
          {
            label: "Scenarios graded",
            value: latestStats.measured,
            sub: latestTrend?.popChanged
              ? `was ${latestTrend.prevMeasured} in the run before`
              : "same as the run before",
          },
          {
            label: "Failed · agent",
            value: agentFailed,
            sub: "the agent had the capability and failed",
            delta:
              prevAgentFailed != null ? agentFailed - prevAgentFailed : null,
            lowerIsBetter: true,
          },
          {
            label: "Not measured",
            value: unmeasured,
            sub: "inconclusive, test invalid or error",
          },
        ]}
      />

      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: { xs: "1fr", lg: "2fr 1fr" },
        }}
      >
        <ChartPanel
          title="Pass rate over time"
          subtitle={
            trend.some((t) => t.popChanged)
              ? "Every finished run, oldest first. The graded population changed between some runs — hover a point for its count."
              : "Every finished run, oldest first, with the agent and simulator it ran on."
          }
        >
          <ReactApexChart
            type="area"
            height={240}
            series={[{ name: "Scenario pass", data: trend.map((t) => t.rate) }]}
            options={{
              chart: baseChart,
              theme: { mode: theme.palette.mode },
              colors: [ACCENT],
              stroke: { curve: "straight", width: 2 },
              fill: {
                type: "gradient",
                gradient: { opacityFrom: 0.28, opacityTo: 0.02 },
              },
              markers: {
                size: 5,
                strokeWidth: 2,
                strokeColors: theme.palette.background.paper,
                hover: { size: 6 },
              },
              dataLabels: { enabled: false },
              grid: grid0,
              xaxis: {
                categories: trend.map((t) => String(t.n)),
                title: {
                  text: "Runs, oldest first",
                  style: {
                    color: theme.palette.text.tertiary,
                    fontSize: "11px",
                    fontWeight: 400,
                  },
                },
                labels: axisLabels,
                axisBorder: { show: false },
                axisTicks: { show: false },
              },
              yaxis: {
                min: 0,
                max: 100,
                tickAmount: 4,
                labels: {
                  ...axisLabels,
                  formatter: (v) => `${Math.round(v)}%`,
                },
              },
              annotations: {
                xaxis: trend
                  .filter((t) => t.envChanged)
                  .map((t, k) => ({
                    x: String(t.n),
                    borderColor: theme.palette.text.disabled,
                    strokeDashArray: 4,
                    label: {
                      text: `test ${envLabelOf(env, envState, t.envV)}`,
                      orientation: "horizontal",
                      /* Changes close together stack instead of overlapping. */
                      offsetY: (k % 2) * 18,
                      style: {
                        background: theme.palette.background.paper,
                        color: theme.palette.text.secondary,
                        fontSize: "11px",
                      },
                    },
                  })),
              },
              tooltip: {
                custom: ({ dataPointIndex }) => {
                  const t = trend[dataPointIndex];
                  return `<div style="padding:8px 10px;font-size:12px;line-height:1.5">
                    <b>${t.label}${t.envV ? ` · environment ${envLabelOf(env, envState, t.envV)}` : ""} · ${t.rate}%</b><br/>
                    ${t.passed} of ${t.measured} scenarios${t.popChanged ? ` (was ${t.prevMeasured})` : ""}<br/>
                    Agent ${t.agent} · ${t.channel} · ${t.model}
                  </div>`;
                },
              },
            }}
          />
        </ChartPanel>

        <ChartPanel
          title="Pass rate by agent version"
          subtitle="Each version's latest finished run."
        >
          <ReactApexChart
            type="bar"
            height={240}
            series={[
              {
                name: "Scenario pass",
                data: byVersion.map(
                  (r) =>
                    pctOf(
                      r.latest.stats.scenarioPassed,
                      r.latest.stats.measured,
                    ) ?? 0,
                ),
              },
            ]}
            options={{
              chart: baseChart,
              theme: { mode: theme.palette.mode },
              colors: [ACCENT],
              plotOptions: {
                bar: {
                  columnWidth:
                    byVersion.length === 1
                      ? "22%"
                      : byVersion.length < 3
                        ? "36%"
                        : "56%",
                  borderRadius: 4,
                  dataLabels: { position: "top" },
                },
              },
              dataLabels: {
                enabled: true,
                offsetY: -18,
                formatter: (v) => `${v}%`,
                style: {
                  fontSize: "12px",
                  colors: [theme.palette.text.primary],
                },
              },
              grid: grid0,
              xaxis: {
                categories: byVersion.map((r) => r.version),
                labels: axisLabels,
                axisBorder: { show: false },
                axisTicks: { show: false },
              },
              yaxis: {
                min: 0,
                max: 100,
                tickAmount: 4,
                labels: {
                  ...axisLabels,
                  formatter: (v) => `${Math.round(v)}%`,
                },
              },
              tooltip: {
                y: {
                  formatter: (v, { dataPointIndex }) => {
                    const r = byVersion[dataPointIndex];
                    return `${v}% · ${r.latest.stats.scenarioPassed} of ${r.latest.stats.measured} · ${r.runs} run${r.runs === 1 ? "" : "s"}`;
                  },
                },
              },
            }}
          />
        </ChartPanel>
      </Box>

      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: { xs: "1fr", lg: "2fr 1fr" },
        }}
      >
        <ChartPanel
          title={`Latest run by ${byGroup.by}`}
          subtitle={`Run ${latest.ordinal || ""} · ${byGroup.rows.length > TOP_ROWS ? `the weakest ${TOP_ROWS} of ${byGroup.rows.length}` : "weakest first"}. ${byGroup.by === "group" ? "A scenario in several groups counts in each." : "The goal each scenario tests."}`}
        >
          <StackedBars
            rows={byGroup.rows.slice(0, TOP_ROWS).map((r) => ({
              label: r.label,
              passed: r.passed,
              failed: r.total - r.passed,
            }))}
          />
        </ChartPanel>

        <ChartPanel
          title="Latest run by outcome"
          subtitle="Failures the agent caused, apart from tests that couldn't measure it."
        >
          <Box sx={{ px: 1.5, pb: 1.5 }}>
            <ReactApexChart
              type="donut"
              height={200}
              series={byOutcome.map((o) => o.n)}
              options={{
                chart: baseChart,
                theme: { mode: theme.palette.mode },
                labels: byOutcome.map((o) => o.label),
                colors: byOutcome.map(
                  (o) => OUTCOME_COLORS[o.outcome] || ACCENT,
                ),
                legend: { show: false },
                dataLabels: { enabled: false },
                stroke: { width: 2, colors: [theme.palette.background.paper] },
                states: {
                  hover: { filter: { type: "none" } },
                  active: { filter: { type: "none" } },
                },
                plotOptions: {
                  pie: {
                    expandOnClick: false,
                    donut: {
                      size: "70%",
                      labels: {
                        show: true,
                        name: {
                          fontSize: "12px",
                          color: theme.palette.text.tertiary,
                        },
                        value: {
                          fontSize: "18px",
                          fontWeight: 600,
                          color: theme.palette.text.primary,
                          formatter: (v) => `${pctOf(Number(v), total)}%`,
                        },
                        total: {
                          show: true,
                          label: "Passed",
                          fontSize: "12px",
                          color: theme.palette.text.tertiary,
                          formatter: () =>
                            pctLabel(
                              byOutcome.find((o) => o.cause === "pass")?.n || 0,
                              total,
                            ),
                        },
                      },
                    },
                  },
                },
                tooltip: {
                  y: {
                    formatter: (v) =>
                      `${v} scenario${v === 1 ? "" : "s"} · ${pctOf(v, total)}%`,
                  },
                },
              }}
            />
            <Stack spacing={0.5} sx={{ mt: 1 }}>
              {byOutcome.map((o) => (
                <Stack
                  key={o.cause}
                  direction="row"
                  spacing={1}
                  alignItems="center"
                >
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: "2px",
                      bgcolor: OUTCOME_COLORS[o.outcome] || ACCENT,
                      flexShrink: 0,
                    }}
                  />
                  <Tooltip
                    title={VERDICT_CAUSES[o.cause]?.note || ""}
                    placement="left"
                  >
                    <Typography
                      noWrap
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        flex: 1,
                      }}
                    >
                      {o.label}
                    </Typography>
                  </Tooltip>
                  <Typography
                    sx={{
                      typography: "s3",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {`${o.n} · ${pctLabel(o.n, total)}`}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </Box>
        </ChartPanel>
      </Box>

      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: { xs: "1fr", lg: "1fr 2fr" },
        }}
      >
        <ChartPanel
          title="Latest run by scenario source"
          subtitle="Where each scenario came from, and how many passed."
        >
          <StackedBars
            rows={bySource.map((r) => ({
              label: r.label,
              passed: r.passed,
              failed: r.total - r.passed,
            }))}
          />
        </ChartPanel>

        <ChartPanel
          title="Scenarios that flip between runs"
          subtitle={`${grid.flipping} of ${grid.total} changed result in the last ${grid.runs.length} runs${grid.flipping > GRID_ROWS ? `; the ${GRID_ROWS} that change most` : ""}. ${grid.alwaysPassed} always passed, ${grid.alwaysFailed} always failed.`}
        >
          <ScenarioGrid rows={grid.rows} runs={grid.runs} />
        </ChartPanel>
      </Box>
    </Stack>
  );
}

AnalyticsPanel.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
};

/* A run-page panel: a bordered surface, its title and one line under it. */
function ChartPanel({ title, subtitle, children }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      <Box sx={{ px: 2.5, pt: 2, pb: 1 }}>
        <Typography sx={{ typography: "s1", fontWeight: 600, fontSize: 13 }}>
          {title}
        </Typography>
        {subtitle && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              mt: 0.5,
              lineHeight: 1.45,
            }}
          >
            {subtitle}
          </Typography>
        )}
      </Box>
      {children}
    </Box>
  );
}
ChartPanel.propTypes = {
  title: PropTypes.node,
  subtitle: PropTypes.node,
  children: PropTypes.node,
};

/* One bordered strip of numbers, divided by hairlines; colour on the change. */
function MetricStrip({ items }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: {
          xs: "repeat(2, 1fr)",
          md: `repeat(${items.length}, 1fr)`,
        },
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {items.map((m, i) => {
        const has = typeof m.delta === "number";
        const better = m.lowerIsBetter ? m.delta < 0 : m.delta > 0;
        const color =
          !has || m.delta === 0
            ? "text.tertiary"
            : better
              ? "status.success.fg"
              : "status.danger.fg";
        return (
          <Box
            key={m.label}
            sx={{
              px: 2,
              py: 1.5,
              borderLeft: i ? "1px solid" : "none",
              borderColor: "divider",
              minWidth: 0,
            }}
          >
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", fontWeight: 500 }}
            >
              {m.label}
            </Typography>
            <Stack
              direction="row"
              alignItems="baseline"
              spacing={1}
              sx={{ mt: 0.5 }}
            >
              <Typography
                sx={{
                  fontSize: 24,
                  fontWeight: 600,
                  lineHeight: 1.15,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {m.value}
              </Typography>
              {has && (
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {m.delta === 0
                    ? "±0"
                    : `${m.delta > 0 ? "+" : "−"}${Math.abs(m.delta)}${m.unit || ""}`}
                </Typography>
              )}
            </Stack>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
              title={m.sub}
            >
              {m.sub}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}
MetricStrip.propTypes = { items: PropTypes.array.isRequired };

/* Passed and failed side by side per row — the run page's horizontal bars. */
function StackedBars({ rows }) {
  const theme = useTheme();
  if (!rows.length)
    return (
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", px: 2.5, pb: 2 }}
      >
        No scenarios in the latest run.
      </Typography>
    );
  return (
    <Box sx={{ px: 1, pb: 1 }}>
      <ReactApexChart
        type="bar"
        height={Math.max(150, rows.length * 38 + 50)}
        series={[
          { name: "Passed", data: rows.map((r) => r.passed) },
          { name: "Failed", data: rows.map((r) => r.failed) },
        ]}
        options={{
          chart: {
            stacked: true,
            toolbar: { show: false },
            animations: { enabled: false },
            background: "transparent",
            fontFamily: theme.typography.fontFamily,
          },
          theme: { mode: theme.palette.mode },
          colors: [PASS, FAIL],
          plotOptions: {
            bar: { horizontal: true, barHeight: "56%", borderRadius: 3 },
          },
          dataLabels: { enabled: false },
          grid: {
            borderColor: theme.palette.divider,
            strokeDashArray: 3,
            xaxis: { lines: { show: true } },
            yaxis: { lines: { show: false } },
          },
          legend: {
            position: "top",
            horizontalAlign: "left",
            fontSize: "12px",
            labels: { colors: theme.palette.text.secondary },
            markers: { size: 5 },
          },
          xaxis: {
            categories: rows.map((r) =>
              r.label.length > 46 ? `${r.label.slice(0, 44)}…` : r.label,
            ),
            /* Whole scenarios only: no 0.5 of a scenario on the axis. */
            min: 0,
            max: Math.max(1, ...rows.map((r) => r.passed + r.failed)),
            tickAmount: Math.min(
              5,
              Math.max(1, ...rows.map((r) => r.passed + r.failed)),
            ),
            labels: {
              formatter: (v) => String(Math.round(v)),
              style: { colors: theme.palette.text.tertiary, fontSize: "11px" },
            },
          },
          yaxis: {
            labels: {
              maxWidth: 260,
              style: { colors: theme.palette.text.secondary, fontSize: "12px" },
            },
          },
          tooltip: {
            shared: true,
            intersect: false,
            y: { formatter: (v) => `${v} scenario${v === 1 ? "" : "s"}` },
          },
        }}
      />
    </Box>
  );
}
StackedBars.propTypes = { rows: PropTypes.array.isRequired };

const CELL = {
  passed: { bg: PASS, label: "Passed" },
  failed: { bg: FAIL, label: "Failed" },
  flaky: { bg: "#FBBF24", label: "Flaky" }, // eslint-disable-line no-restricted-syntax -- ds: data colour
  absent: { bg: null, label: "Not in this run" },
};

/* Scenario rows by run columns — one square per result. */
function ScenarioGrid({ rows, runs }) {
  return (
    <Box sx={{ px: 2.5, pb: 2, overflowX: "auto" }}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: `minmax(160px, 220px) repeat(${runs.length}, minmax(10px, 44px))`,
          columnGap: 0.75,
          rowGap: 0.5,
          alignItems: "center",
        }}
      >
        <Box />
        {runs.map((r, k) => (
          <Typography
            key={`${r}-${k}`}
            noWrap
            sx={{
              typography: "s3",
              fontSize: 10,
              color: "text.tertiary",
              textAlign: "center",
            }}
          >
            {/* Every other run named when there are many. */}
            {runs.length > 12 && k % 2 ? "" : r}
          </Typography>
        ))}
        {rows.map((row) => [
          <Typography
            key={`${row.id}-t`}
            noWrap
            title={row.title}
            sx={{ typography: "s3", color: "text.secondary" }}
          >
            {row.title}
          </Typography>,
          ...row.cells.map((c, i) => (
            <Tooltip
              key={`${row.id}-${i}`}
              title={`Run ${runs[i]} (oldest first) · ${CELL[c].label}`}
              arrow
            >
              <Box
                sx={{
                  height: 18,
                  borderRadius: "4px",
                  bgcolor: CELL[c].bg || "transparent",
                  border: CELL[c].bg ? "none" : "1px dashed",
                  borderColor: "divider",
                  opacity: CELL[c].bg ? 0.85 : 1,
                }}
              />
            </Tooltip>
          )),
        ])}
      </Box>
      <Stack direction="row" spacing={2} sx={{ mt: 1.25 }}>
        {["passed", "failed", "flaky", "absent"].map((k) => (
          <Stack key={k} direction="row" spacing={0.75} alignItems="center">
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: "3px",
                bgcolor: CELL[k].bg || "transparent",
                border: CELL[k].bg ? "none" : "1px dashed",
                borderColor: "divider",
              }}
            />
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {CELL[k].label}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}
ScenarioGrid.propTypes = {
  rows: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
};
