import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  LinearProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useRunAnalytics } from "src/api/simulate-environments/runAnalytics";
import { useRunDetail } from "src/api/simulate-environments/runDetail";
import { paths } from "src/routes/paths";
import EmptyState from "../../../components/EmptyState";
import SoonChip from "../../../components/SoonChip";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import {
  Bars,
  COLORS,
  Donut,
  format,
  NoMeasurement,
  number,
  TrendLine,
  Widget,
} from "./analytics/DashboardCharts";
import { printDashboard } from "./analytics/DashboardControls";
import DashboardHistogram from "./analytics/DashboardHistogram";
import { CHART_GUIDE } from "./analytics/chartGuide";
import { csvFilename, downloadCsv } from "./analytics/csv";
import HiddenWidgetsPopover from "./analytics/HiddenWidgetsPopover";
import LayoutBody from "./analytics/LayoutBody";
import RegressionBanner from "./analytics/RegressionBanner";
import ViewTabBar from "./analytics/ViewTabBar";
import { defaultSectionOf, panelTitle } from "./analytics/panelRegistry";
import { DEFAULT_VIEW_NAME, useRunLayout } from "./analytics/useRunLayout";
import { widgetRows } from "./analytics/widgetRows";
import RunQueryWidget from "./widgets/RunQueryWidget";

// The voice-pipeline p90 a caller starts to hear as silence. Over it, the
// cell turns red.
const VOICE_P90_LIMITS_MS = {
  model: 2500,
  voice: 800,
  transcriber: 500,
};
const BREAKDOWNS = [
  "goal_outcome",
  "disconnection",
  "provider_success",
  "sentiment",
];
const VERDICTS = {
  passed: "Passed all evaluated trials",
  failed: "Failed all evaluated trials",
  flaky: "Flipped between trials",
  not_evaluated: "Not evaluated",
};
const OUTCOMES = [
  { key: "queued", label: "Queued", color: COLORS[5] },
  { key: "in_progress", label: "In progress", color: COLORS[4] },
  { key: "passed", label: "Passed", color: COLORS[0] },
  { key: "failed", label: "Failed", color: COLORS[2] },
  { key: "error", label: "Error", color: COLORS[3] },
  { key: "inconclusive", label: "Inconclusive", color: COLORS[5] },
];
const DISTRIBUTIONS = {
  latency_ms: ["Agent response time", "ms"],
  duration_seconds: ["Call duration", "seconds"],
  tokens: ["Tokens per call", "number"],
  cost_cents: ["Cost per call", "cents"],
  turns: ["Turns per call", "number"],
};
const tableSx = {
  "& th": { fontSize: 10, textTransform: "uppercase", color: "text.secondary" },
  "& td": { fontSize: 12 },
  "& tr:last-child td": { borderBottom: 0 },
};

// Durations arrive in milliseconds; whole calls read in seconds.
const seconds = (ms) => (ms == null ? "-" : format(ms / 1000, "seconds"));

export default function RunAnalytics({
  executionId,
  envId,
  testId,
  onOpenCall,
  onOpenCalls,
}) {
  const params = useParams();
  return (
    <AnalyticsDashboard
      key={executionId}
      executionId={executionId}
      envId={envId ?? params.envId}
      testId={testId ?? params.testId}
      onOpenCall={onOpenCall}
      onOpenCalls={onOpenCalls}
    />
  );
}
RunAnalytics.propTypes = {
  executionId: PropTypes.string.isRequired,
  // Layouts are kept per environment; defaults to the route's envId.
  envId: PropTypes.string,
  // Builds the widget editor link; defaults to the route's testId.
  testId: PropTypes.string,
  onOpenCall: PropTypes.func,
  onOpenCalls: PropTypes.func,
};

function AnalyticsDashboard({
  executionId,
  envId,
  testId,
  onOpenCall,
  onOpenCalls,
}) {
  const { data, isPending, isError, refetch } = useRunAnalytics(executionId);
  if (isPending)
    return (
      <Stack alignItems="center" sx={{ py: 8 }}>
        <CircularProgress size={26} />
      </Stack>
    );
  if (isError)
    return (
      <Stack alignItems="center">
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Analytics could not be loaded"
        />
        <Button onClick={() => refetch()}>Retry</Button>
      </Stack>
    );
  if (!data?.summary?.total)
    return (
      <EmptyState icon="solar:chart-2-linear" title="No calls to analyze" />
    );
  if (!data.dashboard)
    return (
      <EmptyState
        icon="solar:chart-2-linear"
        title="Dashboard data is unavailable"
      />
    );
  return (
    <Dashboard
      data={data}
      envId={envId}
      testId={testId}
      executionId={executionId}
      onOpenCall={onOpenCall}
      onOpenCalls={onOpenCalls}
    />
  );
}
AnalyticsDashboard.propTypes = RunAnalytics.propTypes;

function Dashboard({
  data,
  envId,
  testId,
  executionId,
  onOpenCall,
  onOpenCalls,
}) {
  const navigate = useNavigate();
  const printable = useRef(null);
  const [showDetails, setShowDetails] = useState(false);
  const [hiddenAnchor, setHiddenAnchor] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const dashboard = data.dashboard;
  const metricsByKey = Object.fromEntries(
    (dashboard.metrics || []).map((metric) => [metric.key, metric]),
  );
  // The backend names the run's noun in its labels ("Total chats"); a run
  // with no voice call is a chat run.
  const isChat = /\bchats?\b/i.test(
    metricsByKey.total?.label || metricsByKey.pass_rate?.label || "",
  );
  const noun = isChat ? "chat" : "call";
  const Noun = isChat ? "Chat" : "Call";
  const surface = isChat ? "chat" : "voice";

  const layout = useRunLayout({
    envId,
    surface,
    initialView: searchParams.get("view"),
  });
  // `?view=` names the open saved view, so the link opens it.
  useEffect(() => {
    const wanted =
      layout.activeView === DEFAULT_VIEW_NAME ? null : layout.activeView;
    if ((searchParams.get("view") || null) === wanted) return;
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (wanted) next.set("view", wanted);
        else next.delete("view");
        return next;
      },
      { replace: true },
    );
  }, [layout.activeView, searchParams, setSearchParams]);

  const comparison = dashboard.comparison;
  const previous = useRunDetail(
    undefined,
    comparison?.available ? comparison.previous_execution_id : undefined,
  );
  const previousOrdinal = previous?.identity?.ordinal;
  // Shares the run header's read; names the run on a saved widget's axis.
  const currentOrdinal = useRunDetail(undefined, executionId)?.identity
    ?.ordinal;

  const latencyLabel = metricsByKey.agent_latency?.label || "Agent latency";
  // Call length kept in the payload for earlier builds of this page.
  const distributionRows = (dashboard.distributions || []).filter(
    (row) => row.key !== "end_to_end_ms",
  );
  const latencyAt = (percentile) =>
    dashboard.agent_latency_percentiles?.find(
      (row) => row.percentile === percentile,
    )?.value;
  const durationAt = (percentile) =>
    dashboard.latency_percentiles?.find((row) => row.percentile === percentile)
      ?.value;
  const unavailable = (key) =>
    dashboard.unavailable_features?.find((item) => item.key === key)?.reason;
  const open = onOpenCall
    ? (task) =>
        onOpenCall({
          id: task.id,
          scenario: task.label,
          simulationCallType: task.modality,
          provider: task.provider,
        })
    : undefined;
  const { summary } = data;
  const reliability = data.reliability ?? {};
  const interval = reliability?.pass_rate_interval;
  const evalSummary = dashboard.evaluation_summary;
  const bucketed = dashboard.series_mode === "time_buckets";
  const subtitles = {
    goal_outcome: `${summary.measured} evaluated of ${summary.total} · errored and not-evaluated ${noun}s never count against the agent`,
    disconnection:
      "Every provider's end reason mapped to one list; unrecognised reasons stay visible",
    provider_success:
      "The provider's own judgement as reported; it never replaces your evals",
    sentiment:
      "As reported by the provider; the platform does not compute sentiment",
    reliability: `${reliability.scenarios ?? 0} scenarios × ${reliability.trials ?? 0} trial${reliability.trials === 1 ? "" : "s"}${interval ? ` · pass^1 95% range ${format(interval.low, "percent")}–${format(interval.high, "percent")}` : ""}`,
    evaluations: `${evalSummary.graders} evals · ${evalSummary.passed} of ${evalSummary.measured} evaluated ${noun}s passed every eval (${format(evalSummary.pass_rate, "percent")})${evalSummary.errored_checks ? ` · ${evalSummary.errored_checks} checks could not run` : ""}`,
    csat: "Scorer CSAT on a 0–10 scale; a provider success flag is never mixed in",
    response_time:
      "Each call's average wait before the agent replies; one long pause inside a call is not visible here",
    voice_slos: "p50 / p90 / p99 of recorded per-call pipeline timings (ms)",
    pipeline_cost: bucketed
      ? "LLM / TTS / STT / Storage · up to 100 time buckets; costs summed per bucket"
      : `Recorded LLM / TTS / STT / Storage cost per ${noun}`,
    call_duration: bucketed
      ? `${Noun} duration · average per time bucket`
      : `Wall-clock length of each ${noun}`,
    duration_percentiles: `p50 ${seconds(durationAt(50))} · p90 ${seconds(durationAt(90))} · p99 ${seconds(durationAt(99))} of measured durations`,
    task_latency: bucketed
      ? `${latencyLabel} · average per time bucket`
      : `${latencyLabel} per call`,
    percentiles: `p50 ${format(latencyAt(50), "ms")} · p90 ${format(latencyAt(90), "ms")} · p99 ${format(latencyAt(99), "ms")} of per-call averages`,
    distribution: "p50 · p90 · p99 · max for every measured metric",
    attribution: "Which layer to blame first",
    risk: `Weakest ${dashboard.use_case_risk.length} of ${dashboard.goal_count} scenarios · fewer than 3 evaluated ${noun}s ranked last`,
    tools_volume: `${dashboard.tools.total_invocations} recorded invocations · ${dashboard.tools.total_tools} tools · top 20`,
    tools_failure:
      "Failures among invocations with a recorded verdict · threshold at 40%",
    slowest: `Top 8 by wall-clock duration · select a ${noun} to inspect it`,
    expensive: `Top 8 by recorded cost · select a ${noun} to inspect it`,
  };
  const drillSoon = (
    <SoonChip label="Drill-down" what="Drill-down" size="tiny" />
  );
  const headerActions = {
    disconnection: drillSoon,
    provider_success: drillSoon,
    sentiment: drillSoon,
    attribution: (
      <SoonChip label="Attribution" what="Failure attribution" size="tiny" />
    ),
    tools_failure: (
      <SoonChip
        label="Failure split"
        what="The split between a tool failing and the agent never calling it"
        size="tiny"
      />
    ),
  };
  const breakdown = (key) =>
    dashboard.breakdowns.find((item) => item.key === key);
  // Provider-only charts render only when some call reported the value; the
  // details toggle only reveals the reliability section.
  const available = (id) => {
    if (["provider_success", "sentiment"].includes(id))
      return Boolean(breakdown(id));
    if (id === "reliability") return showDetails;
    return true;
  };

  const passRate = metricsByKey.pass_rate;
  const dropOff = metricsByKey.drop_off;
  const costPerPass = metricsByKey.cost_per_pass;
  const csatMetric = metricsByKey.csat;
  const health = dashboard.run_health;
  const metricTile = (key, label, coverage, unit) => {
    const metric = metricsByKey[key];
    if (!metric) return null;
    return {
      key,
      label,
      value: format(metric.value, unit ?? metric.unit),
      reported: metric.value != null,
      coverage: coverage(metric),
      note: metric.note,
    };
  };
  const measuredOf = (metric) =>
    `${metric.measured ?? 0} / ${metric.total ?? 0} measured`;
  const durationP90 = metricsByKey.duration_p90;
  // Voice-only metrics are absent from chat runs, so their tiles drop out.
  const callTiles = [
    metricTile("total", `Total ${Noun}s`, () => "this run"),
    health && {
      key: "connected",
      label: isChat ? "Completed" : "Connected",
      value: format(health.connected),
      reported: health.connected != null,
      coverage: `of ${health.attempted ?? 0}`,
      note: isChat
        ? "Chats that exchanged at least one message"
        : "Calls that exchanged at least one message",
    },
    metricTile(
      "connected_rate",
      isChat ? "Completion (%)" : "Calls Connected (%)",
      (metric) => `${metric.measured ?? 0} / ${metric.total ?? 0} ${noun}s`,
    ),
    metricTile("agent_latency", "Agent Latency", measuredOf),
    metricTile("wpm", "Agent WPM", () => "words/min"),
    metricTile("stop", "Agent Stop Latency", measuredOf),
    // API sends the agent's share as a percent; show it as agent/customer.
    metricTile("talk", "Talk Ratio", () => "agent/customer", "ratio"),
    metricTile(
      "duration",
      "Avg duration",
      (metric) => `${metric.measured ?? 0} timed ${noun}s`,
    ),
    durationP90 && {
      key: "duration_p90",
      label: "Duration p90",
      value: seconds(durationP90.value),
      reported: durationP90.value != null,
      coverage: `${durationP90.measured ?? 0} timed ${noun}s`,
      note: durationP90.note,
    },
    metricTile(
      "turns",
      "Avg turns",
      (metric) => `${metric.measured ?? 0} ${noun}s`,
    ),
    metricTile(
      "total_cost",
      "Total cost",
      (metric) => `${metric.measured ?? 0} / ${metric.total ?? 0} reported`,
    ),
  ].filter(Boolean);
  const csat = dashboard.csat || {};
  const headlines = [
    {
      key: "pass_rate",
      label: passRate?.label || `${Noun}s passed`,
      value: format(passRate?.value, "percent"),
      reported: passRate?.value != null,
      coverage: `${passRate?.measured ?? 0} / ${passRate?.total ?? 0} evaluated`,
      note: passRate?.note,
    },
    {
      key: "drop_off",
      label: "Drop-off",
      value: format(dropOff?.value, "percent"),
      reported: dropOff?.value != null,
      coverage: `${dropOff?.measured ?? 0} / ${dropOff?.total ?? 0} assessed`,
      note: dropOff?.note,
    },
    {
      key: "response_p95",
      label: "Response time p95",
      value: format(dashboard.agent_response_time.p95, "ms"),
      reported: dashboard.agent_response_time.p95 != null,
      coverage: `${dashboard.agent_response_time.measured} / ${dashboard.agent_response_time.total} measured`,
      note: `Slow 1-in-20 per-${noun} average response time`,
    },
    {
      key: "cost_per_pass",
      label: "Cost / pass",
      value: format(costPerPass?.value, "cents"),
      reported: costPerPass?.value != null,
      coverage: `${costPerPass?.measured ?? 0} / ${costPerPass?.total ?? 0} reported`,
      note: costPerPass?.note,
    },
    // R22: Avg CSAT replaces the satisfied share, which moves to its hover.
    {
      key: "csat",
      label: "Avg CSAT Score",
      value: format(csatMetric?.value),
      reported: csatMetric?.value != null,
      coverage: `${csatMetric?.measured ?? csat.measured ?? 0} scored`,
      note:
        csat.satisfied_percent == null
          ? "No 0–10 CSAT scores were recorded"
          : `${csat.satisfied} of ${csat.measured} scored at 8 or above (${format(csat.satisfied_percent, "percent")})`,
    },
    {
      key: "change",
      label: "Change vs last run",
      value: comparison?.available
        ? `+${comparison.newly_passing.length} / −${comparison.newly_failing.length}`
        : "-",
      reported: Boolean(comparison?.available),
      coverage: comparison?.available
        ? `${comparison.shared_scenarios} shared scenarios`
        : "No previous comparable run",
      note: "Newly passing / newly failing, on scenarios evaluated in both runs",
    },
    ...callTiles,
  ];
  const healthIssues = [];
  const notConnected = (health?.attempted ?? 0) - (health?.connected ?? 0);
  if (notConnected)
    healthIssues.push(
      `${notConnected} ${noun}${notConnected === 1 ? "" : "s"} did not connect`,
    );
  if (health?.errored)
    healthIssues.push(
      `${health.errored} ${noun}${health.errored === 1 ? "" : "s"} failed to run`,
    );
  if (health?.not_evaluated)
    healthIssues.push(
      `${health.not_evaluated} ${noun}${health.not_evaluated === 1 ? " was" : "s were"} not evaluated`,
    );
  if (health?.eval_errors)
    healthIssues.push(
      `${health.eval_errors} eval check${health.eval_errors === 1 ? "" : "s"} could not run`,
    );
  const findings = [];
  if ((dropOff?.value ?? 0) > 5)
    findings.push(
      `Drop-off is ${format(dropOff.value, "percent")} (target ≤5%).`,
    );
  if (reliability.flaky)
    findings.push(
      `${reliability.flaky} scenario${reliability.flaky === 1 ? "" : "s"} flipped across trials.`,
    );
  if ((dashboard.agent_response_time.at_or_above_target_percent ?? 0) > 5)
    findings.push(
      `${format(dashboard.agent_response_time.at_or_above_target_percent, "percent")} of measured ${noun}s exceeded the response-time target.`,
    );
  const failingTools = dashboard.tools.failures.filter(
    (tool) => (tool.failure_rate ?? 0) > 5,
  );
  if (failingTools.length)
    findings.push(
      `${failingTools.length} tool${failingTools.length === 1 ? "" : "s"} exceeded 5% errors.`,
    );

  const renderWidget = (id) => {
    if (BREAKDOWNS.includes(id))
      return (
        <Donut
          data={breakdown(id)}
          onOpen={id === "goal_outcome" ? onOpenCalls : undefined}
        />
      );
    if (id === "reliability")
      return <Reliability data={reliability} onOpenCalls={onOpenCalls} />;
    if (id === "csat" || id === "response_time")
      return (
        <DashboardHistogram
          kind={id}
          data={id === "csat" ? dashboard.csat : dashboard.agent_response_time}
        />
      );
    if (id === "evaluations")
      return data.evaluations.length ? (
        <Box sx={{ overflowX: "auto" }}>
          <Table size="small" sx={tableSx}>
            <TableHead>
              <TableRow>
                <TableCell>Eval</TableCell>
                <TableCell sx={{ minWidth: 180 }}>Pass rate</TableCell>
                <TableCell align="right">%</TableCell>
                <TableCell align="right">Passed / evaluated</TableCell>
                <TableCell align="right">Could not run</TableCell>
                <TableCell align="right">Not applicable</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.evaluations.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>{row.name}</TableCell>
                  <TableCell>
                    {row.pass_rate == null ? (
                      "-"
                    ) : (
                      <LinearProgress
                        variant="determinate"
                        value={row.pass_rate}
                        sx={{
                          height: 6,
                          borderRadius: 1,
                          bgcolor: "action.hover",
                          "& .MuiLinearProgress-bar": {
                            bgcolor: row.pass_rate < 50 ? COLORS[2] : COLORS[1],
                          },
                        }}
                      />
                    )}
                  </TableCell>
                  <TableCell align="right">
                    {format(row.pass_rate, "percent")}
                  </TableCell>
                  <TableCell align="right">
                    {row.passed} / {row.measured}
                  </TableCell>
                  <TableCell align="right">{row.errored}</TableCell>
                  <TableCell align="right">{row.missing}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      ) : (
        <NoMeasurement text="No evaluations were recorded" />
      );
    if (id === "voice_slos")
      return (
        <>
          <Table size="small" sx={tableSx}>
            <TableHead>
              <TableRow>
                <TableCell>Segment</TableCell>
                <TableCell align="right">p50</TableCell>
                <TableCell align="right">p90</TableCell>
                <TableCell align="right">p99</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {dashboard.voice_slos.map((row) => (
                <TableRow key={row.key}>
                  <TableCell>
                    <CustomTooltip
                      show
                      size="small"
                      title={`${row.measured} measured calls`}
                    >
                      <span>{row.label}</span>
                    </CustomTooltip>
                  </TableCell>
                  {["p50", "p90", "p99"].map((p) => {
                    const limit = VOICE_P90_LIMITS_MS[row.key];
                    const over = p === "p90" && limit != null && row[p] > limit;
                    return (
                      <TableCell key={p} align="right">
                        {over ? (
                          <Box
                            component="span"
                            aria-label={`${format(row[p], "ms")}, over the ${limit} ms target`}
                            sx={{ color: "error.main", fontWeight: 600 }}
                          >
                            {format(row[p], "ms")}
                          </Box>
                        ) : (
                          format(row[p], "ms")
                        )}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Typography sx={{ p: 2, fontSize: 11, color: "text.secondary" }}>
            ASR word-error rate: not recorded · User interruptions:{" "}
            {number(dashboard.interruptions.average)} / measured call ·{" "}
            {number(dashboard.interruptions.total, 0)} total
          </Typography>
        </>
      );
    if (id === "pipeline_cost")
      return (
        <>
          <Bars
            rows={dashboard.series}
            xKey="label"
            unit="cents"
            series={[
              { key: "llm_cents", label: "LLM" },
              { key: "tts_cents", label: "TTS", color: COLORS[4] },
              { key: "stt_cents", label: "STT", color: COLORS[3] },
              { key: "storage_cents", label: "Storage", color: COLORS[5] },
            ]}
          />
          <Stack
            direction="row"
            gap={1.5}
            flexWrap="wrap"
            sx={{ px: 2, pb: 2 }}
          >
            {dashboard.pipeline_cost?.map((component) => (
              <Typography
                key={component.key}
                sx={{ fontSize: 10, color: "text.secondary" }}
              >
                {component.label} {format(component.total_cents, "cents")} (
                {format(component.share, "percent")})
              </Typography>
            ))}
          </Stack>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 2, pb: 1.5 }}
          >
            <SoonChip
              label="Transport cost"
              what="Transport cost"
              size="tiny"
            />
            <Typography sx={{ fontSize: 10, color: "text.secondary" }}>
              Storage is shown separately.
            </Typography>
          </Stack>
        </>
      );
    if (id === "call_duration")
      return (
        <TrendLine
          rows={dashboard.series}
          xKey="label"
          valueKey="duration_ms"
          valueLabel={`${Noun} duration`}
          formatValue={seconds}
          noun={Noun}
          bucketed={bucketed}
        />
      );
    if (id === "duration_percentiles")
      return (
        <TrendLine
          rows={dashboard.latency_percentiles}
          xKey="percentile"
          valueKey="value"
          valueLabel={`${Noun} duration`}
          formatValue={seconds}
          percentile
        />
      );
    if (id === "task_latency")
      return (
        <TrendLine
          rows={dashboard.series}
          xKey="label"
          valueKey="latency_ms"
          valueLabel={latencyLabel}
          noun={Noun}
          bucketed={bucketed}
        />
      );
    if (id === "percentiles")
      return (
        <TrendLine
          rows={dashboard.agent_latency_percentiles}
          xKey="percentile"
          valueKey="value"
          valueLabel={latencyLabel}
          percentile
        />
      );
    if (id === "distribution")
      return (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))",
            gap: 2,
            px: 2,
            pb: 2,
          }}
        >
          {distributionRows.map((row) => {
            const [mapped, unit] = DISTRIBUTIONS[row.key] || [
              row.key,
              "number",
            ];
            const label = row.key === "latency_ms" ? latencyLabel : mapped;
            return (
              <Box key={row.key}>
                <Typography
                  sx={{
                    fontSize: 10,
                    color: "text.secondary",
                    textTransform: "uppercase",
                  }}
                >
                  {label}
                </Typography>
                <Typography
                  sx={{
                    fontSize: 21,
                    my: 0.5,
                    fontWeight: row.p90 == null ? 400 : 600,
                    color: row.p90 == null ? "text.disabled" : "text.primary",
                  }}
                >
                  {format(row.p90, unit)}{" "}
                  <Typography
                    component="span"
                    sx={{ fontSize: 10, color: "text.secondary" }}
                  >
                    p90
                  </Typography>
                </Typography>
                {["p50", "p99", "max"].map((p) => (
                  <Stack key={p} direction="row" justifyContent="space-between">
                    <Typography
                      sx={{
                        fontSize: 10,
                        color: "text.secondary",
                        textTransform: "uppercase",
                      }}
                    >
                      {p}
                    </Typography>
                    <Typography sx={{ fontSize: 11 }}>
                      {format(row[p], unit)}
                    </Typography>
                  </Stack>
                ))}
                <Typography
                  sx={{ mt: 0.5, fontSize: 10, color: "text.secondary" }}
                >
                  {row.measured} measured
                </Typography>
              </Box>
            );
          })}
        </Box>
      );
    if (id === "attribution")
      return (
        <NoMeasurement
          text={
            unavailable("failure_attribution") ||
            "Failure attribution isn't in the API yet"
          }
        />
      );
    if (id === "risk")
      return (
        <Bars
          rows={dashboard.use_case_risk}
          xKey="scenario"
          series={OUTCOMES}
          horizontal
          height={330}
          legend
          labels
        />
      );
    if (id === "tools_volume")
      return (
        <Bars
          rows={dashboard.tools.volume}
          xKey="name"
          height={300}
          labels
          multicolor
          angled
          series={[
            { key: "invocations", label: "Invocations", color: COLORS[4] },
          ]}
        />
      );
    if (id === "tools_failure")
      return (
        <Bars
          rows={dashboard.tools.failures}
          xKey="name"
          horizontal
          height={Math.max(245, dashboard.tools.failures.length * 25)}
          unit="percent"
          threshold={40}
          labels
          labelKey="failure_label"
          multicolor
          series={[
            { key: "failure_rate", label: "Failure rate", color: COLORS[2] },
          ]}
        />
      );
    if (id === "slowest" || id === "expensive")
      return (
        <Bars
          rows={
            id === "slowest"
              ? dashboard.slowest_tasks
              : dashboard.most_expensive_tasks
          }
          xKey="axis_label"
          angled
          labels
          unit={id === "slowest" ? "seconds" : "cents"}
          series={[
            {
              key: "value",
              label: id === "slowest" ? "Duration" : "Cost",
              color: id === "slowest" ? COLORS[0] : "#c02f80",
            },
          ]}
          onOpen={open}
        />
      );
    return null;
  };

  const customById = useMemo(
    () => new Map(layout.customWidgets.map((widget) => [widget.id, widget])),
    [layout.customWidgets],
  );
  const sourceOf = (id) => customById.get(id)?.of || id;
  const titleOf = (id) => customById.get(id)?.title || panelTitle(id, noun);
  const { sectionOverrides } = layout;
  const sectionOf = useCallback(
    (id) =>
      sectionOverrides[id] || defaultSectionOf(id, customById.get(id), surface),
    [sectionOverrides, customById, surface],
  );
  const rowsOf = (id) => widgetRows(sourceOf(id), { data, noun });
  const visibleIds = layout.visibleIds.filter((id) => available(sourceOf(id)));
  const hidden = layout.hiddenIds
    .filter((id) => available(sourceOf(id)))
    .map((id) => ({
      id,
      title: titleOf(id),
      section: sectionOf(id),
      custom: customById.has(id),
    }));
  // The widget editor page, back into the view it was opened from.
  const openEditor = (id) => {
    const view =
      layout.activeView === DEFAULT_VIEW_NAME
        ? ""
        : `?view=${encodeURIComponent(layout.activeView)}`;
    navigate(
      `${paths.dashboard.simulate.environments.widgetEditor(
        envId,
        testId,
        executionId,
        id,
      )}${view}`,
    );
  };
  const actionsFor = (id, columns) => {
    const custom = customById.has(id);
    return {
      onEdit: () => openEditor(id),
      onDuplicate: () => layout.duplicate(id, titleOf(id)),
      onResetOverride: () => layout.resetOverride(id),
      onSetSpan: (span) => layout.setSpan(id, Math.min(span, columns)),
      canExport: rowsOf(id).length > 0,
      onExport: () => downloadCsv(csvFilename(titleOf(id)), rowsOf(id)),
      // A built-in goes to Hidden; a copy is removed for good.
      onDelete: custom ? () => layout.removeCustom(id) : () => layout.hide(id),
    };
  };
  const frame = (id) => {
    const saved = customById.get(id);
    if (saved?.kind === "query")
      return (
        <Widget id={id} title={titleOf(id)} subtitle={saved.description}>
          <RunQueryWidget
            widget={saved}
            executionId={executionId}
            runLabel={
              currentOrdinal != null ? `Run ${currentOrdinal}` : undefined
            }
          />
        </Widget>
      );
    const source = sourceOf(id);
    return (
      <Widget
        id={id}
        title={titleOf(id)}
        subtitle={subtitles[source]}
        help={CHART_GUIDE[source]}
        action={headerActions[source]}
      >
        {renderWidget(source)}
      </Widget>
    );
  };

  return (
    <Stack spacing={2}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        className="analytics-no-print"
      >
        <ViewTabBar
          activeView={layout.activeView}
          viewNames={layout.viewNames}
          defaultName={DEFAULT_VIEW_NAME}
          onSwitch={layout.switchView}
          onRename={layout.renameView}
          onDuplicate={layout.duplicateView}
          onDelete={layout.deleteView}
          onReorder={layout.reorderViews}
          adornment={<UiOnlyFlag feature="analyticsViews" />}
        />
        <Button
          size="small"
          variant="text"
          onClick={() => setShowDetails((current) => !current)}
          sx={{ flexShrink: 0 }}
        >
          {showDetails ? "Hide detailed analytics" : "Show detailed analytics"}
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          onClick={() => openEditor("new")}
          sx={{ flexShrink: 0, borderColor: "divider" }}
        >
          Add widget
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          startIcon={<Iconify icon="solar:eye-closed-linear" width={16} />}
          onClick={(event) => setHiddenAnchor(event.currentTarget)}
          sx={{ flexShrink: 0, borderColor: "divider" }}
        >
          {hidden.length ? `Hidden (${hidden.length})` : "Hidden"}
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          startIcon={<Iconify icon="solar:printer-linear" width={16} />}
          onClick={() =>
            printDashboard(
              printable.current,
              `${data.execution?.name || "Simulation run"} - Analytics`,
            )
          }
          sx={{ flexShrink: 0, borderColor: "divider" }}
        >
          Export PDF
        </Button>
      </Stack>
      <Stack ref={printable} spacing={2.5}>
        {health?.show_banner && (
          <Alert severity="warning">
            <strong>Run health:</strong> {healthIssues.join(" · ")}
          </Alert>
        )}
        <RegressionBanner
          comparison={comparison}
          previousLabel={
            previousOrdinal != null ? `Run ${previousOrdinal}` : null
          }
        />
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(2,minmax(0,1fr))",
              sm: "repeat(3,minmax(0,1fr))",
              md: "repeat(5,minmax(0,1fr))",
              lg: "repeat(7,minmax(0,1fr))",
            },
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            borderRadius: 1,
            overflow: "hidden",
            // Right and bottom hairlines per cell; the outer edge clips them.
            "& > *": {
              boxShadow: (theme) =>
                `1px 0 0 ${theme.palette.divider}, 0 1px 0 ${theme.palette.divider}`,
            },
          }}
        >
          {headlines.map((card) => (
            <CustomTooltip
              key={card.key}
              show
              size="small"
              title={card.note || card.coverage}
            >
              <Box
                sx={{
                  px: 2,
                  py: 1.75,
                  minWidth: 0,
                  minHeight: 96,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "flex-start",
                }}
              >
                <Typography
                  typography="s2"
                  sx={{ color: "text.subtitle", fontWeight: 500 }}
                >
                  {card.label}
                </Typography>
                <Typography
                  sx={{
                    mt: 0.75,
                    fontSize: 24,
                    lineHeight: 1,
                    letterSpacing: -0.5,
                    fontVariantNumeric: "tabular-nums",
                    fontWeight: card.reported ? 600 : 400,
                    color: card.reported ? "text.primary" : "text.disabled",
                  }}
                >
                  {card.value}
                </Typography>
                <Typography
                  typography="s2"
                  sx={{
                    mt: "auto",
                    pt: 0.75,
                    color: "text.subtitle",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {card.reported || card.key === "drop_off"
                    ? card.coverage
                    : "Not reported"}
                </Typography>
              </Box>
            </CustomTooltip>
          ))}
        </Box>
        <Box
          component="section"
          aria-label="What to look at first"
          sx={{
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            borderRadius: 1,
            px: 2,
            py: 1.5,
          }}
        >
          <Typography component="h2" typography="s1" sx={{ fontWeight: 600 }}>
            What to look at first
          </Typography>
          {findings.length ? (
            <Box component="ul" sx={{ m: 0, mt: 1, pl: 2.5 }}>
              {findings.map((finding) => (
                <Typography component="li" key={finding} typography="s2">
                  {finding}
                </Typography>
              ))}
            </Box>
          ) : (
            <Typography
              typography="s2"
              sx={{ mt: 0.75, color: "text.secondary" }}
            >
              No priority findings in this run.
            </Typography>
          )}
        </Box>
        <LayoutBody
          layout={layout}
          ids={visibleIds}
          sectionOf={sectionOf}
          titleOf={titleOf}
          renderWidget={frame}
          actionsFor={actionsFor}
        />
      </Stack>
      <HiddenWidgetsPopover
        anchorEl={hiddenAnchor}
        onClose={() => setHiddenAnchor(null)}
        hidden={hidden}
        sectionOrder={layout.sectionOrder}
        onShow={layout.show}
        onRemove={layout.removeCustom}
        onReset={layout.reset}
      />
    </Stack>
  );
}
Dashboard.propTypes = {
  data: PropTypes.object.isRequired,
  envId: PropTypes.string,
  testId: PropTypes.string,
  executionId: PropTypes.string,
  onOpenCall: PropTypes.func,
  onOpenCalls: PropTypes.func,
};

function Reliability({ data, onOpenCalls }) {
  if (!data?.scenarios) return <NoMeasurement />;
  const tiles = [
    ["Passed every trial", data.consistent_pass, data.scenarios],
    ["Passed at least once", data.passed_at_least_once, data.scenarios],
    ["Flipped between trials", data.flaky, data.repeated],
  ];
  return (
    <>
      <Stack direction="row" gap={3} sx={{ px: 2, pb: 1.5 }} flexWrap="wrap">
        {tiles.map(([label, value, of]) => (
          <Box key={label}>
            <Typography sx={{ fontSize: 10, color: "text.secondary" }}>
              {label}
            </Typography>
            <Typography sx={{ fontSize: 20, fontWeight: 600 }}>
              {of ? `${value} / ${of}` : "-"}
            </Typography>
          </Box>
        ))}
      </Stack>
      {data.trials < 2 && (
        <Typography
          sx={{ px: 2, pb: 1, fontSize: 11, color: "text.secondary" }}
        >
          Run 2 or more trials to see which scenarios give different results on
          repeat.
        </Typography>
      )}
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" sx={tableSx}>
          <TableHead>
            <TableRow>
              <TableCell>Scenario</TableCell>
              <TableCell>Result</TableCell>
              <TableCell align="right">Trials</TableCell>
              <TableCell align="right">Inconclusive</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.rows.map((row) => (
              <TableRow
                key={row.scenario_key || row.scenario}
                hover={!!onOpenCalls}
                onClick={
                  onOpenCalls && row.call_ids?.length
                    ? () => onOpenCalls({ callExecutionId: row.call_ids })
                    : undefined
                }
                sx={onOpenCalls ? { cursor: "pointer" } : undefined}
              >
                <TableCell>{row.scenario}</TableCell>
                <TableCell
                  sx={{
                    color:
                      row.verdict === "flaky"
                        ? COLORS[3]
                        : row.verdict === "failed"
                          ? COLORS[2]
                          : undefined,
                  }}
                >
                  {VERDICTS[row.verdict]}
                </TableCell>
                {/* Inconclusive trials sit outside the denominator. */}
                <TableCell align="right">
                  {row.evaluated
                    ? `${row.passed}/${row.evaluated} trials passed`
                    : "No conclusive trial"}
                </TableCell>
                <TableCell align="right">
                  {row.error + row.inconclusive}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    </>
  );
}
Reliability.propTypes = { data: PropTypes.object, onOpenCalls: PropTypes.func };
