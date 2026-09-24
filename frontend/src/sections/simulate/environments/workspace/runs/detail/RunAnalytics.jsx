import { useRef } from "react";
import PropTypes from "prop-types";
import {
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
  { id: "call_success", title: "Call successful", section: "Breakdowns" },
  {
    id: "goal_outcome",
    title: "Goal outcome breakdown",
    section: "Breakdowns",
  },
  { id: "sentiment", title: "User sentiment", section: "Breakdowns" },
  { id: "disconnection", title: "Disconnection reason", section: "Breakdowns" },
  {
    id: "evaluations",
    title: "Evaluations",
    section: "Evaluations",
    wide: true,
  },
  {
    id: "voice_slos",
    title: "Voice latency SLOs",
    section: "Voice latency SLOs",
  },
  {
    id: "pipeline_cost",
    title: "Cost breakdown by pipeline stage",
    section: "Voice latency SLOs",
  },
  {
    id: "csat",
    title: "CSAT distribution (0–10)",
    section: "CSAT and provider scores",
    wide: true,
  },
  { id: "task_latency", title: "Task latency", section: "Latency" },
  { id: "percentiles", title: "Latency percentiles", section: "Latency" },
  {
    id: "response_time",
    title: "Agent response time per call",
    section: "Latency",
    wide: true,
  },
  {
    id: "distribution",
    title: "Distribution summary",
    section: "Distribution",
    wide: true,
  },
  {
    id: "risk",
    title: "Use case risk",
    section: "Failure analysis",
    wide: true,
  },
  { id: "tools_volume", title: "Tool call volume", section: "Tools" },
  { id: "tools_failure", title: "Tool failure rate", section: "Tools" },
  { id: "slowest", title: "Slowest tasks", section: "Performance tails" },
  {
    id: "expensive",
    title: "Most expensive tasks",
    section: "Performance tails",
  },
];
const SECTIONS = [
  "Breakdowns",
  "Evaluations",
  "CSAT and provider scores",
  "Voice latency SLOs",
  "Latency",
  "Distribution",
  "Failure analysis",
  "Tools",
  "Performance tails",
];
const OUTCOMES = [
  { key: "passed", label: "Passed", color: COLORS[0] },
  { key: "failed", label: "Failed", color: COLORS[2] },
  { key: "error", label: "Errored", color: COLORS[3] },
  { key: "inconclusive", label: "Inconclusive", color: COLORS[5] },
];
const DISTRIBUTIONS = {
  end_to_end_ms: ["End-to-end latency", "ms"],
  duration_seconds: ["Task duration", "seconds"],
  tokens: ["Tokens per task", "number"],
  cost_cents: ["Cost per task", "cents"],
  turns: ["Turns per task", "number"],
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
  const open = onOpenCall
    ? (task) =>
        onOpenCall({
          id: task.id,
          scenario: task.label,
          simulationCallType: task.modality,
          provider: task.provider,
        })
    : undefined;
  const subtitles = {
    call_success:
      "Successful vs unsuccessful — the top-level task verdict; unknowns shown separately",
    goal_outcome: `${data.summary.total} tasks · shares use all tasks, including unknown outcomes`,
    sentiment: "How the counterparty came across during the task",
    disconnection: "Why each call ended",
    evaluations: `${dashboard.evaluation_summary.graders} graders · ${dashboard.evaluation_summary.passed} of ${dashboard.evaluation_summary.measured} measured checks passed (${format(dashboard.evaluation_summary.pass_rate, "percent")})`,
    csat: "Existing score · scores the run already produced — no new cost",
    response_time: "Platform, transcript timing",
    voice_slos: "p50 / p90 / p99 of recorded per-call pipeline timings (ms)",
    pipeline_cost:
      dashboard.series_mode === "time_buckets"
        ? "LLM / TTS / STT / Storage · up to 100 time buckets; costs summed per bucket"
        : "Recorded LLM / TTS / STT / Storage cost per call",
    task_latency:
      dashboard.series_mode === "time_buckets"
        ? "End-to-end task wall clock · average per time bucket"
        : "End-to-end task wall clock per call",
    percentiles: `p50 ${format(dashboard.distributions.find((row) => row.key === "end_to_end_ms")?.p50, "ms")} · p90 ${format(dashboard.distributions.find((row) => row.key === "end_to_end_ms")?.p90, "ms")} · p99 ${format(dashboard.distributions.find((row) => row.key === "end_to_end_ms")?.p99, "ms")}`,
    distribution: "p50 · p90 · p99 · max for every measured task metric",
    risk: `Weakest ${dashboard.use_case_risk.length} of ${dashboard.goal_count} goals`,
    tools_volume: `${dashboard.tools.total_invocations} recorded invocations · ${dashboard.tools.total_tools} tools · top 20`,
    tools_failure:
      "Failures among invocations with a recorded verdict · threshold at 40%",
    slowest: "Top 8 by wall-clock duration · select a task to inspect the call",
    expensive: "Top 8 by recorded cost · select a task to inspect the call",
  };
  const renderWidget = (id) => {
    if (
      ["call_success", "goal_outcome", "sentiment", "disconnection"].includes(
        id,
      )
    )
      return (
        <Donut
          data={dashboard.breakdowns.find((item) => item.key === id)}
          onOpen={id === "call_success" ? onOpenCalls : undefined}
        />
      );
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
                <TableCell>Grader</TableCell>
                <TableCell>Category</TableCell>
                <TableCell sx={{ minWidth: 180 }}>Pass rate</TableCell>
                <TableCell align="right">%</TableCell>
                <TableCell align="right">Passed / measured</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.evaluations.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>{row.name}</TableCell>
                  <TableCell>—</TableCell>
                  <TableCell>
                    {row.pass_rate == null ? (
                      "—"
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
          valueKey="duration_ms"
          bucketed={dashboard.series_mode === "time_buckets"}
        />
      );
    if (id === "percentiles")
      return (
        <TrendLine
          rows={dashboard.latency_percentiles}
          xKey="percentile"
          valueKey="value"
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
          {dashboard.distributions.map((row) => {
            const [label, unit] = DISTRIBUTIONS[row.key] || [row.key, "number"];
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
                <Typography sx={{ fontSize: 21, fontWeight: 600, my: 0.5 }}>
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
          xKey="goal"
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
            `${data.execution?.name || "Simulation run"} — Analytics`,
          )
        }
      />
      <Stack ref={printable} spacing={2.5}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(2,minmax(0,1fr))",
              md: "repeat(4,minmax(0,1fr))",
              lg: "repeat(7,minmax(0,1fr))",
            },
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            borderRadius: 1.5,
            overflow: "hidden",
          }}
        >
          {dashboard.metrics.map((metric) => (
            <Tooltip
              key={metric.key}
              title={metric.note || `${metric.measured ?? 0} measured calls`}
            >
              <Box
                sx={{
                  p: 1.5,
                  borderRight: "1px solid",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  minWidth: 0,
                }}
              >
                <Typography sx={{ fontSize: 10, color: "text.secondary" }}>
                  {metric.label}
                </Typography>
                <Typography sx={{ fontSize: 22, fontWeight: 650, mt: 0.5 }}>
                  {format(metric.value, metric.unit)}
                </Typography>
                <Typography
                  sx={{ fontSize: 10, color: "text.secondary", mt: 0.3 }}
                >
                  {metric.value == null
                    ? "Not recorded"
                    : `${metric.measured ?? 0} / ${metric.total} measured`}
                </Typography>
              </Box>
            </Tooltip>
          ))}
        </Box>
        {SECTIONS.map((section) => {
          const visible = WIDGETS.filter(
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
                      section === "Breakdowns"
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
