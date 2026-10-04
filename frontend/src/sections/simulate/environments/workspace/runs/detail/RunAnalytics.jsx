import { useRef, useState } from "react";
import PropTypes from "prop-types";
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
  Tooltip,
  Typography,
} from "@mui/material";
import { useRunAnalytics } from "src/api/simulate-environments/runAnalytics";
import EmptyState from "../../../components/EmptyState";
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
import DashboardControls, {
  printDashboard,
} from "./analytics/DashboardControls";
import DashboardHistogram from "./analytics/DashboardHistogram";
import { CHART_GUIDE } from "./analytics/chartGuide";

const WIDGETS = [
  { id: "goal_outcome", title: "Goal outcome breakdown", section: "Outcomes" },
  {
    id: "disconnection",
    title: "How calls ended",
    section: "Outcomes",
    shown: true,
  },
  {
    id: "provider_success",
    title: "Provider's own success flag",
    section: "Outcomes",
  },
  { id: "sentiment", title: "Provider sentiment", section: "Outcomes" },
  {
    id: "reliability",
    title: "Reliability across trials",
    section: "Reliability",
    wide: true,
  },
  {
    id: "evaluations",
    title: "Evaluations",
    section: "Evaluations",
    wide: true,
    shown: true,
  },
  {
    id: "voice_slos",
    title: "Voice pipeline latency",
    section: "Voice latency",
  },
  {
    id: "pipeline_cost",
    title: "Cost breakdown by pipeline stage",
    section: "Voice latency",
  },
  {
    id: "csat",
    title: "CSAT distribution (0–10)",
    section: "CSAT and provider scores",
    wide: true,
  },
  { id: "task_latency", title: "Agent latency", section: "Latency" },
  {
    id: "percentiles",
    title: "Agent response time percentiles",
    section: "Latency",
  },
  {
    id: "response_time",
    title: "Agent response time per call",
    section: "Latency",
    wide: true,
    shown: true,
  },
  {
    id: "distribution",
    title: "Distribution summary",
    section: "Distribution",
    wide: true,
  },
  {
    id: "risk",
    title: "Weakest scenarios",
    section: "Failure analysis",
    wide: true,
    shown: true,
  },
  { id: "tools_volume", title: "Tool call volume", section: "Tools" },
  { id: "tools_failure", title: "Tool failure rate", section: "Tools" },
  { id: "slowest", title: "Slowest calls", section: "Performance tails" },
  {
    id: "expensive",
    title: "Most expensive calls",
    section: "Performance tails",
  },
];
const SECTIONS = [
  "Outcomes",
  "Reliability",
  "Evaluations",
  "CSAT and provider scores",
  "Voice latency",
  "Latency",
  "Distribution",
  "Failure analysis",
  "Tools",
  "Performance tails",
];
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
  { key: "passed", label: "Passed", color: COLORS[0] },
  { key: "failed", label: "Failed", color: COLORS[2] },
  { key: "error", label: "Errored", color: COLORS[3] },
  { key: "inconclusive", label: "Not evaluated", color: COLORS[5] },
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

export default function RunAnalytics({ executionId, onOpenCall, onOpenCalls }) {
  return (
    <AnalyticsDashboard
      key={executionId}
      executionId={executionId}
      onOpenCall={onOpenCall}
      onOpenCalls={onOpenCalls}
    />
  );
}
RunAnalytics.propTypes = {
  executionId: PropTypes.string.isRequired,
  onOpenCall: PropTypes.func,
  onOpenCalls: PropTypes.func,
};

function AnalyticsDashboard({ executionId, onOpenCall, onOpenCalls }) {
  const { data, isPending, isError, refetch } = useRunAnalytics(executionId);
  const printable = useRef(null);
  const [showDetails, setShowDetails] = useState(false);
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
  const dashboard = data.dashboard;
  const latencyLabel =
    dashboard.metrics.find((metric) => metric.key === "agent_latency")?.label ||
    "Agent latency";
  // Call length kept in the payload for earlier builds of this page.
  const distributionRows = dashboard.distributions.filter(
    (row) => row.key !== "end_to_end_ms",
  );
  const latencyAt = (percentile) =>
    dashboard.agent_latency_percentiles?.find(
      (row) => row.percentile === percentile,
    )?.value;
  const open = onOpenCall
    ? (task) =>
        onOpenCall({
          id: task.id,
          scenario: task.label,
          simulationCallType: task.modality,
          provider: task.provider,
        })
    : undefined;
  const { summary, reliability } = data;
  const interval = reliability?.pass_rate_interval;
  const evalSummary = dashboard.evaluation_summary;
  const subtitles = {
    goal_outcome: `${summary.measured} evaluated of ${summary.total} · errored and not-evaluated calls never count against the agent`,
    disconnection:
      "Every provider's end reason mapped to one list; unrecognised reasons stay visible",
    provider_success:
      "The provider's own judgement as reported; it never replaces your evals",
    sentiment:
      "As reported by the provider; the platform does not compute sentiment",
    reliability: `${reliability.scenarios} scenarios × ${reliability.trials} trial${reliability.trials === 1 ? "" : "s"}${interval ? ` · pass rate 95% range ${format(interval.low, "percent")}–${format(interval.high, "percent")}` : ""}`,
    evaluations: `${evalSummary.graders} evals · ${evalSummary.passed} of ${evalSummary.measured} evaluated calls passed every eval (${format(evalSummary.pass_rate, "percent")})${evalSummary.errored_checks ? ` · ${evalSummary.errored_checks} checks could not run` : ""}`,
    csat: "Scorer CSAT on a 0–10 scale; a provider success flag is never mixed in",
    response_time:
      "Each call's average wait before the agent replies; one long pause inside a call is not visible here",
    voice_slos: "p50 / p90 / p99 of recorded per-call pipeline timings (ms)",
    pipeline_cost:
      dashboard.series_mode === "time_buckets"
        ? "LLM / TTS / STT / Storage · up to 100 time buckets; costs summed per bucket"
        : "Recorded LLM / TTS / STT / Storage cost per call",
    task_latency:
      dashboard.series_mode === "time_buckets"
        ? `${latencyLabel} · average per time bucket`
        : `${latencyLabel} per call`,
    percentiles: `p50 ${format(latencyAt(50), "ms")} · p90 ${format(latencyAt(90), "ms")} · p99 ${format(latencyAt(99), "ms")} of per-call averages`,
    distribution: "p50 · p90 · p99 · max for every measured metric",
    risk: `Weakest ${dashboard.use_case_risk.length} of ${dashboard.goal_count} scenarios · fewer than 3 evaluated calls ranked last`,
    tools_volume: `${dashboard.tools.total_invocations} recorded invocations · ${dashboard.tools.total_tools} tools · top 20`,
    tools_failure:
      "Failures among invocations with a recorded verdict · threshold at 40%",
    slowest: "Top 8 by wall-clock duration · select a call to inspect it",
    expensive: "Top 8 by recorded cost · select a call to inspect it",
  };
  const breakdown = (key) =>
    dashboard.breakdowns.find((item) => item.key === key);
  // Provider-only charts render only when some call reported the value.
  const availableWidgets = WIDGETS.filter(
    (widget) =>
      !["provider_success", "sentiment"].includes(widget.id) ||
      breakdown(widget.id),
  );
  const widgets = availableWidgets.filter(
    (widget) => widget.shown || showDetails,
  );
  const metricsByKey = Object.fromEntries(
    dashboard.metrics.map((metric) => [metric.key, metric]),
  );
  const passRate = metricsByKey.pass_rate;
  const dropOff = metricsByKey.drop_off;
  const costPerPass = metricsByKey.cost_per_pass;
  const comparison = dashboard.comparison;
  const health = dashboard.run_health;
  const passMargin =
    interval && passRate?.value != null
      ? Math.max(passRate.value - interval.low, interval.high - passRate.value)
      : null;
  const headlines = [
    {
      key: "pass_rate",
      label: "Calls passed",
      value: format(passRate?.value, "percent"),
      reported: passRate?.value != null,
      detail: passMargin == null ? null : `±${number(passMargin)} pts`,
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
      note: "Slow 1-in-20 per-call average response time",
    },
    {
      key: "cost_per_pass",
      label: "Cost / pass",
      value: format(costPerPass?.value, "cents"),
      reported: costPerPass?.value != null,
      coverage: `${costPerPass?.measured ?? 0} / ${costPerPass?.total ?? 0} reported`,
      note: costPerPass?.note,
    },
    {
      key: "csat_satisfied",
      label: "CSAT satisfied",
      value: format(dashboard.csat.satisfied_percent, "percent"),
      reported: dashboard.csat.satisfied_percent != null,
      coverage: `${dashboard.csat.satisfied} / ${dashboard.csat.measured} scored`,
      note: "Share of true 0–10 CSAT scores at 8 or above",
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
  ];
  const healthIssues = [];
  const notConnected = (health?.attempted ?? 0) - (health?.connected ?? 0);
  if (notConnected)
    healthIssues.push(
      `${notConnected} call${notConnected === 1 ? "" : "s"} did not connect`,
    );
  if (health?.errored)
    healthIssues.push(
      `${health.errored} call${health.errored === 1 ? "" : "s"} failed to run`,
    );
  if (health?.not_evaluated)
    healthIssues.push(
      `${health.not_evaluated} call${health.not_evaluated === 1 ? " was" : "s were"} not evaluated`,
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
      `${format(dashboard.agent_response_time.at_or_above_target_percent, "percent")} of measured calls exceeded the response-time target.`,
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
    if (id === "reliability") return <Reliability data={reliability} />;
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
                    <Tooltip title={`${row.measured} measured calls`}>
                      <span>{row.label}</span>
                    </Tooltip>
                  </TableCell>
                  {["p50", "p90", "p99"].map((p) => (
                    <TableCell key={p} align="right">
                      {format(row[p], "ms")}
                    </TableCell>
                  ))}
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
          <Typography
            sx={{ px: 2, pb: 1.5, fontSize: 10, color: "text.secondary" }}
          >
            Transport cost is not recorded. Storage is shown separately.
          </Typography>
        </>
      );
    if (id === "task_latency")
      return (
        <TrendLine
          rows={dashboard.series}
          xKey="label"
          valueKey="latency_ms"
          valueLabel={latencyLabel}
          bucketed={dashboard.series_mode === "time_buckets"}
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
  return (
    <>
      <DashboardControls
        onPrint={() =>
          printDashboard(
            printable.current,
            `${data.execution?.name || "Simulation run"} - Analytics`,
          )
        }
      />
      <Box
        className="analytics-no-print"
        sx={{ display: "flex", justifyContent: "flex-end", mt: -1.5, mb: 2 }}
      >
        <Button
          size="small"
          variant="text"
          onClick={() => setShowDetails((current) => !current)}
        >
          {showDetails ? "Hide detailed analytics" : "Show detailed analytics"}
        </Button>
      </Box>
      <Stack ref={printable} spacing={2.5}>
        {health?.show_banner && (
          <Alert severity="warning">
            <strong>Run health:</strong> {healthIssues.join(" · ")}
          </Alert>
        )}
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(2,minmax(0,1fr))",
              md: "repeat(3,minmax(0,1fr))",
              lg: "repeat(6,minmax(0,1fr))",
            },
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            borderRadius: 1.5,
            overflow: "hidden",
          }}
        >
          {headlines.map((card) => (
            <Tooltip key={card.key} title={card.note || card.coverage}>
              <Box
                sx={{
                  p: 1.5,
                  borderRight: "1px solid",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  minWidth: 0,
                }}
              >
                <Typography
                  sx={{
                    fontSize: 10,
                    fontWeight: 600,
                    color: "text.secondary",
                  }}
                >
                  {card.label}
                </Typography>
                <Stack direction="row" alignItems="baseline" spacing={0.75}>
                  <Typography
                    sx={{
                      fontSize: 22,
                      mt: 0.5,
                      fontWeight: card.reported ? 650 : 400,
                      color: card.reported ? "text.primary" : "text.disabled",
                    }}
                  >
                    {card.value}
                  </Typography>
                  {card.detail && (
                    <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
                      {card.detail}
                    </Typography>
                  )}
                </Stack>
                <Typography
                  sx={{ fontSize: 10, color: "text.secondary", mt: 0.3 }}
                >
                  {card.reported || card.key === "drop_off"
                    ? card.coverage
                    : "Not reported"}
                </Typography>
              </Box>
            </Tooltip>
          ))}
        </Box>
        <Box
          component="section"
          aria-label="What to look at first"
          sx={{
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            borderRadius: 1.5,
            px: 2,
            py: 1.5,
          }}
        >
          <Typography component="h2" sx={{ fontSize: 13, fontWeight: 600 }}>
            What to look at first
          </Typography>
          {findings.length ? (
            <Box component="ul" sx={{ m: 0, mt: 1, pl: 2.5 }}>
              {findings.map((finding) => (
                <Typography component="li" key={finding} sx={{ fontSize: 12 }}>
                  {finding}
                </Typography>
              ))}
            </Box>
          ) : (
            <Typography
              sx={{ mt: 0.75, fontSize: 12, color: "text.secondary" }}
            >
              No priority findings in this run.
            </Typography>
          )}
        </Box>
        {SECTIONS.map((section) => {
          const visible = widgets.filter(
            (widget) => widget.section === section,
          );
          if (!visible.length) return null;
          return (
            <Box key={section}>
              <Typography
                component="h2"
                sx={{
                  fontSize: 13,
                  fontWeight: 600,
                  mb: 1,
                  pt: 1,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                {section}
              </Typography>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: {
                    xs: "1fr",
                    md: "repeat(2,minmax(0,1fr))",
                    lg:
                      section === "Outcomes"
                        ? "repeat(4,minmax(0,1fr))"
                        : "repeat(2,minmax(0,1fr))",
                  },
                  gap: 1.5,
                }}
              >
                {visible.map((widget) => (
                  <Widget
                    key={widget.id}
                    {...widget}
                    subtitle={subtitles[widget.id]}
                    help={CHART_GUIDE[widget.id]}
                  >
                    {renderWidget(widget.id)}
                  </Widget>
                ))}
              </Box>
            </Box>
          );
        })}
      </Stack>
    </>
  );
}
AnalyticsDashboard.propTypes = RunAnalytics.propTypes;

function Reliability({ data }) {
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
              <TableCell align="right">Passed / evaluated</TableCell>
              <TableCell align="right">Evaluated / total trials</TableCell>
              <TableCell align="right">Errored</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.rows.map((row) => (
              <TableRow key={row.scenario_key || row.scenario}>
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
                <TableCell align="right">
                  {row.passed} / {row.evaluated}
                </TableCell>
                <TableCell align="right">
                  {row.evaluated} / {row.runs}
                </TableCell>
                <TableCell align="right">{row.error}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    </>
  );
}
Reliability.propTypes = { data: PropTypes.object };
