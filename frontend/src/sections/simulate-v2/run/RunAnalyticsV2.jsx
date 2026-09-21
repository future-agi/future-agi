import PropTypes from "prop-types";
import { useMemo } from "react";
import { useTheme } from "@mui/material/styles";
import { Box, Stack, Typography, Table, TableHead, TableRow, TableCell, TableBody } from "@mui/material";
import ReactApexChart from "react-apexcharts";
import { alpha } from "@mui/material/styles";
import { attribute, isMeasured, DOMAINS } from "../_mock/failures";
import { deriveUseCaseLabel } from "./TraceTable";

/**
 * Analytics — enterprise density. Prioritises numbers and small
 * charts over ornamentation. Reads like a monitoring console: a KPI
 * strip on top, a distribution chart beside sortable eval / attribution
 * tables, then a scatter of the run's tail. No decorative icons, no
 * per-row progress bars, no colored callout cards. Colour is reserved
 * for state (pass/fail/warn) and applied to text only.
 */

const RED   = "#DC2626";
const GREEN = "#16A34A";
const AMBER = "#CA8A04";
/* Softened variants for chart fills — the KPI/table colors stay the
   full-strength state hues, but chart bars/dots use a slightly less
   saturated anchor plus a top-down gradient wash so the visual noise
   drops but the state (pass/fail) is still unmistakable. */
const CHART_GREEN = "#34D399";
const CHART_RED   = "#F87171";

const numFmt = new Intl.NumberFormat();

/* ── kpi strip ─────────────────────────────────────────────────────── */

function Kpi({ label, value, sub, tone }) {
  return (
    <Box sx={{ px: 2, py: 1.5, minWidth: 0 }}>
      <Typography sx={{
        typography: "s3", color: "text.subtitle",
        fontSize: 10, fontWeight: 600,
        textTransform: "uppercase", letterSpacing: 0.5,
      }}>
        {label}
      </Typography>
      <Typography sx={{
        typography: "m1", fontWeight: 700,
        fontVariantNumeric: "tabular-nums",
        color: tone || "text.primary", lineHeight: 1.15, mt: 0.375,
        fontSize: 20,
      }}>
        {value}
      </Typography>
      {sub && (
        <Typography sx={{
          typography: "s3", color: "text.subtitle",
          fontSize: 10.5, mt: 0.25, fontVariantNumeric: "tabular-nums",
        }}>
          {sub}
        </Typography>
      )}
    </Box>
  );
}
Kpi.propTypes = { label: PropTypes.node, value: PropTypes.node, sub: PropTypes.node, tone: PropTypes.string };

function KpiStrip({ tasks, evals }) {
  const measured = tasks.filter(isMeasured);
  const passed = measured.filter((t) => t.status === "passed").length;
  const failed = measured.filter((t) => t.status === "failed").length;
  const errored = tasks.filter((t) => t.status === "error").length;
  const critical = tasks.filter((t) => t.critical && (t.status === "failed" || t.status === "error")).length;
  const passRate = measured.length ? Math.round((passed / measured.length) * 100) : 0;

  const durations = tasks.map((t) => t.durationMs || 0).filter((v) => v > 0).sort((a, b) => a - b);
  const latencies = tasks.map((t) => t.latencyMs || 0).filter((v) => v > 0).sort((a, b) => a - b);
  const percentile = (arr, p) => arr.length ? arr[Math.min(arr.length - 1, Math.floor((p / 100) * arr.length))] : 0;
  const medDur = percentile(durations, 50);
  const p95Dur = percentile(durations, 95);
  const medLat = percentile(latencies, 50);
  const p95Lat = percentile(latencies, 95);
  const totalTokens = tasks.reduce((a, t) => a + (t.tokens || 0), 0);
  const totalCost = tasks.reduce((a, t) => a + (t.cost || 0), 0);
  const evalCount = evals?.length || 0;

  const cells = [
    { label: "Pass rate",        value: `${passRate}%`,                   sub: `${passed} / ${measured.length}`, tone: passRate >= 80 ? GREEN : passRate >= 50 ? AMBER : RED },
    { label: "Failed",           value: numFmt.format(failed),            sub: `${errored} errored`, tone: failed ? RED : undefined },
    { label: "Critical fails",   value: numFmt.format(critical),          sub: "release blockers", tone: critical ? RED : GREEN },
    { label: "Median duration",  value: `${(medDur / 1000).toFixed(1)}s`, sub: `p95 ${(p95Dur / 1000).toFixed(1)}s` },
    { label: "Median latency",   value: `${Math.round(medLat)}ms`,        sub: `p95 ${Math.round(p95Lat)}ms` },
    { label: "Tokens",           value: numFmt.format(totalTokens),       sub: `avg ${numFmt.format(Math.round(totalTokens / Math.max(1, tasks.length)))}` },
    { label: "Cost",             value: `$${totalCost.toFixed(2)}`,       sub: `$${(totalCost / Math.max(1, tasks.length)).toFixed(3)} / task` },
    { label: "Evals",            value: numFmt.format(evalCount),         sub: "graders applied" },
  ];

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider", borderRadius: 1,
      bgcolor: "background.paper",
      display: "grid",
      gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(4, 1fr)", md: "repeat(8, 1fr)" },
      "& > * + *": { borderLeft: { md: "1px solid" }, borderColor: { md: "divider" } },
    }}>
      {cells.map((c) => <Kpi key={c.label} {...c} />)}
    </Box>
  );
}
KpiStrip.propTypes = { tasks: PropTypes.array, evals: PropTypes.array };

/* ── use case risk list ────────────────────────────────────────────── */

/* A compact ranked table of the weakest scenario categories. Cell
   layout: label · runs · failed · pass-rate w/ inline bar. Always
   renders — bars at 0% still show a hairline so the reader sees the
   row shape. Reads like an SRE risk register, not a decorative chart. */
function UseCaseRiskList({ tasks }) {
  const TOP_N = 7;
  const { rows, totalGroups, totalRuns } = useMemo(() => {
    if (!tasks?.length) return { rows: [], totalGroups: 0, totalRuns: 0 };
    const groups = new Map();
    let measured = 0;
    tasks.forEach((t) => {
      if (!isMeasured(t)) return;
      measured += 1;
      const label = deriveUseCaseLabel(t) || "Uncategorised";
      const row = groups.get(label) || { passed: 0, failed: 0, total: 0 };
      row.total += 1;
      if (t.status === "passed") row.passed += 1;
      else row.failed += 1;
      groups.set(label, row);
    });
    const all = [...groups.entries()]
      .map(([label, { passed, failed, total }]) => ({
        label, passed, failed, total,
        rate: total ? Math.round((passed / total) * 100) : 0,
      }))
      .sort((a, b) => a.rate - b.rate || b.total - a.total)
      .slice(0, TOP_N);
    return { rows: all, totalGroups: groups.size, totalRuns: measured };
  }, [tasks]);

  const suffix = totalGroups > TOP_N ? ` · showing weakest ${TOP_N} of ${totalGroups}` : "";
  return (
    <Panel
      title="Use case risk"
      subtitle={`Pass rate = tasks whose evals all passed ÷ measured tasks in the category. ${numFmt.format(totalRuns)} measured tasks${suffix}.`}
    >
      {rows.length === 0 ? (
        <Box sx={{ p: 3, textAlign: "center" }}>
          <Typography variant="s3" sx={{ color: "text.subtitle", fontSize: 12 }}>
            No measured tasks yet.
          </Typography>
        </Box>
      ) : (
        <DataTableShell>
          <TableHead>
            <TableRow>
              <TableCell>Use case</TableCell>
              <TableCell align="right" sx={{ width: 60 }}>Runs</TableCell>
              <TableCell align="right" sx={{ width: 60 }}>Failed</TableCell>
              <TableCell sx={{ width: 220 }}>Pass rate</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => {
              const barColor = r.rate >= 80 ? GREEN : r.rate >= 50 ? AMBER : RED;
              return (
                <TableRow key={r.label} hover>
                  <TableCell sx={{
                    maxWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }} title={r.label}>
                    {r.label}
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", color: "text.subtitle" }}>
                    {r.total}
                  </TableCell>
                  <TableCell align="right" sx={{
                    fontVariantNumeric: "tabular-nums",
                    color: r.failed > 0 ? RED : "text.subtitle",
                    fontWeight: r.failed > 0 ? 600 : 400,
                  }}>
                    {r.failed}
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" alignItems="center" spacing={1.25}>
                      <Box sx={{
                        flex: 1, height: 6, borderRadius: 999,
                        bgcolor: (t) => t.palette.mode === "dark" ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)",
                        overflow: "hidden",
                      }}>
                        <Box sx={{
                          height: "100%",
                          width: `${Math.max(r.rate, 1.5)}%`,
                          bgcolor: barColor,
                          borderRadius: 999,
                          transition: "width 240ms ease",
                        }}/>
                      </Box>
                      <Typography sx={{
                        typography: "s3", fontSize: 11.5, fontWeight: 600,
                        color: barColor, fontVariantNumeric: "tabular-nums",
                        minWidth: 36, textAlign: "right",
                      }}>
                        {r.rate}%
                      </Typography>
                    </Stack>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </DataTableShell>
      )}
    </Panel>
  );
}
UseCaseRiskList.propTypes = { tasks: PropTypes.array };

/* ── turns × outcome bars ──────────────────────────────────────────── */

/* A scatter over (turns, duration) collapses at the same x for every
   task with the same turn count, so a dozen dots pile onto one column
   and become unreadable. This replaces it with a bar chart per turn
   bucket, stacked by outcome — same "does complexity correlate with
   failure?" question, but every task is visible and countable. */
function TurnBars({ tasks }) {
  const theme = useTheme();
  const { categories, series } = useMemo(() => {
    if (!tasks?.length) return { categories: [], series: [] };
    const counts = new Map(); // turnCount → { passed, failed }
    tasks.forEach((t) => {
      const n = t.steps?.length || 0;
      if (!n) return;
      const row = counts.get(n) || { passed: 0, failed: 0 };
      if (t.status === "passed") row.passed += 1;
      else row.failed += 1;
      counts.set(n, row);
    });
    const turns = [...counts.keys()].sort((a, b) => a - b);
    return {
      categories: turns.map((n) => `${n}`),
      series: [
        { name: "Passed", data: turns.map((n) => counts.get(n).passed), color: CHART_GREEN },
        { name: "Failed", data: turns.map((n) => counts.get(n).failed), color: CHART_RED },
      ],
    };
  }, [tasks]);

  return (
    <PanelChart
      title="Tasks by turn count"
      subtitle="One bar per turn count, stacked by outcome. Rising red on the right = complex tasks fail more."
    >
      <ReactApexChart
        type="bar" height={180}
        series={series}
        options={{
          chart: {
            type: "bar", stacked: true, toolbar: { show: false },
            animations: { enabled: false }, background: "transparent",
            fontFamily: theme.typography.fontFamily,
          },
          theme: { mode: theme.palette.mode },
          plotOptions: { bar: { columnWidth: "68%", borderRadius: 2 } },
          dataLabels: { enabled: false },
          legend: { show: false },
          stroke: { show: false },
          fill: {
            type: "gradient",
            gradient: {
              type: "vertical",
              shadeIntensity: 0.15,
              opacityFrom: 0.95,
              opacityTo: 0.55,
              stops: [0, 100],
            },
          },
          xaxis: {
            categories,
            title: { text: "turns", style: { color: theme.palette.text.subtitle, fontSize: "10px", fontWeight: 400 } },
            axisBorder: { show: false }, axisTicks: { show: false },
            labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, rotate: 0 },
          },
          yaxis: {
            title: { text: "tasks", style: { color: theme.palette.text.subtitle, fontSize: "10px", fontWeight: 400 } },
            labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${Math.round(v)}` },
          },
          grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 0, right: 0, top: -6, bottom: -6 } },
          colors: [CHART_GREEN, CHART_RED],
          tooltip: {
            shared: true, intersect: false,
            x: { formatter: (v) => `${v} turns` },
            y: { formatter: (v) => `${v} tasks` },
          },
        }}
      />
    </PanelChart>
  );
}
TurnBars.propTypes = { tasks: PropTypes.array };

/* ── evaluations table ─────────────────────────────────────────────── */

function EvalsTable({ tasks, evals }) {
  const rows = useMemo(() => {
    if (!evals?.length) return [];
    return evals.map((e) => {
      const results = tasks.map((t) => t.evalResults?.find((r) => r.id === e.id)).filter(Boolean);
      const passed = results.filter((r) => r.passed).length;
      const total = results.length;
      const passRate = total ? Math.round((passed / total) * 100) : 0;
      return { id: e.id, name: e.name, category: e.category || "—", passRate, passed, total };
    }).sort((a, b) => a.passRate - b.passRate);
  }, [tasks, evals]);

  return (
    <Panel title="Evaluations" subtitle="Grader pass rates, weakest first.">
      <DataTableShell>
        <TableHead>
          <TableRow>
            <Th>Evaluation</Th>
            <Th>Category</Th>
            <Th align="right">Passed</Th>
            <Th align="right">Total</Th>
            <Th align="right">Pass rate</Th>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => {
            const tone = r.passRate >= 80 ? GREEN : r.passRate >= 50 ? AMBER : RED;
            return (
              <TableRow key={r.id} hover>
                <Td sx={{ fontWeight: 600 }}>{r.name}</Td>
                <Td sx={{ color: "text.subtitle" }}>{r.category}</Td>
                <Td align="right" mono>{r.passed}</Td>
                <Td align="right" mono>{r.total}</Td>
                <Td align="right" mono sx={{ color: tone, fontWeight: 700 }}>{r.passRate}%</Td>
              </TableRow>
            );
          })}
        </TableBody>
      </DataTableShell>
    </Panel>
  );
}
EvalsTable.propTypes = { tasks: PropTypes.array, evals: PropTypes.array };

/* ── attribution table ────────────────────────────────────────────── */

function AttributionTable({ tasks }) {
  const rows = useMemo(() => {
    const held = {};
    tasks.forEach((t) => {
      if (t.status === "passed") return;
      const d = attribute(t);
      if (!d) return;
      (held[d.id] = held[d.id] || 0), held[d.id]++;
    });
    const total = Object.values(held).reduce((a, v) => a + v, 0);
    return Object.entries(held).map(([id, count]) => ({
      id,
      label: DOMAIN_LABEL[id]?.label || id,
      short: DOMAIN_LABEL[id]?.short || id,
      retry: DOMAIN_LABEL[id]?.retry || "—",
      count,
      share: total ? Math.round((count / total) * 100) : 0,
    })).sort((a, b) => b.count - a.count);
  }, [tasks]);

  if (!rows.length) return null;

  return (
    <Panel title="Failure attribution" subtitle="Which layer to look at first — everything upstream of the agent has to hold before a failure is agent behaviour.">
      <DataTableShell>
        <TableHead>
          <TableRow>
            <Th>Domain</Th>
            <Th>Retry policy</Th>
            <Th align="right">Failures</Th>
            <Th align="right">Share</Th>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id} hover>
              <Td sx={{ fontWeight: 600 }}>{r.label}</Td>
              <Td sx={{ color: "text.subtitle" }}>{r.retry}</Td>
              <Td align="right" mono>{r.count}</Td>
              <Td align="right" mono sx={{ color: r.id === "agent" ? RED : "text.subtitle" }}>{r.share}%</Td>
            </TableRow>
          ))}
        </TableBody>
      </DataTableShell>
    </Panel>
  );
}
AttributionTable.propTypes = { tasks: PropTypes.array };

const DOMAIN_LABEL = {
  agent:       { label: "Agent behaviour",   short: "Agent",       retry: "Recorded · never retried" },
  environment: { label: "Environment",       short: "Environment", retry: "Retried when transient" },
  transport:   { label: "Transport",         short: "Transport",   retry: "Bounded retry, new call id" },
  simulator:   { label: "Simulated caller",  short: "Simulator",   retry: "Retried within policy" },
  grading:     { label: "Grading",           short: "Grading",     retry: "Re-graded from evidence" },
};

/* ── slowest / most expensive tables ──────────────────────────────── */

function SlowestTable({ tasks }) {
  const rows = useMemo(() => (
    [...tasks]
      .filter((t) => t.durationMs != null)
      .sort((a, b) => b.durationMs - a.durationMs)
      .slice(0, 8)
  ), [tasks]);

  return (
    <Panel title="Slowest tasks">
      <DataTableShell>
        <TableHead>
          <TableRow>
            <Th>Task</Th>
            <Th align="right">Turns</Th>
            <Th align="right">Tokens</Th>
            <Th align="right">Duration</Th>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((t) => (
            <TableRow key={t.id} hover>
              <Td noWrap>{t.title || t.name || t.id}</Td>
              <Td align="right" mono>{t.steps?.length ?? "—"}</Td>
              <Td align="right" mono>{t.tokens != null ? numFmt.format(t.tokens) : "—"}</Td>
              <Td align="right" mono sx={{ fontWeight: 700 }}>{((t.durationMs || 0) / 1000).toFixed(1)}s</Td>
            </TableRow>
          ))}
        </TableBody>
      </DataTableShell>
    </Panel>
  );
}
SlowestTable.propTypes = { tasks: PropTypes.array };

function ExpensiveTable({ tasks }) {
  const rows = useMemo(() => (
    [...tasks]
      .filter((t) => t.cost != null)
      .sort((a, b) => b.cost - a.cost)
      .slice(0, 8)
  ), [tasks]);
  if (!rows.length) return null;
  return (
    <Panel title="Most expensive tasks">
      <DataTableShell>
        <TableHead>
          <TableRow>
            <Th>Task</Th>
            <Th align="right">Tokens</Th>
            <Th align="right">Duration</Th>
            <Th align="right">Cost</Th>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((t) => (
            <TableRow key={t.id} hover>
              <Td noWrap>{t.title || t.name || t.id}</Td>
              <Td align="right" mono>{t.tokens != null ? numFmt.format(t.tokens) : "—"}</Td>
              <Td align="right" mono>{((t.durationMs || 0) / 1000).toFixed(1)}s</Td>
              <Td align="right" mono sx={{ fontWeight: 700 }}>${(t.cost || 0).toFixed(2)}</Td>
            </TableRow>
          ))}
        </TableBody>
      </DataTableShell>
    </Panel>
  );
}
ExpensiveTable.propTypes = { tasks: PropTypes.array };

/* ── layout primitives ─────────────────────────────────────────────── */

function Panel({ title, subtitle, children, minHeight }) {
  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider", borderRadius: 1,
      bgcolor: "background.paper", display: "flex", flexDirection: "column",
      minHeight,
    }}>
      <Box sx={{
        px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider",
      }}>
        <Typography sx={{
          typography: "s3", color: "text.primary", fontWeight: 700,
          fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5,
        }}>
          {title}
        </Typography>
        {subtitle && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 11, mt: 0.25 }}>
            {subtitle}
          </Typography>
        )}
      </Box>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        {children}
      </Box>
    </Box>
  );
}
Panel.propTypes = { title: PropTypes.node, subtitle: PropTypes.node, children: PropTypes.node, minHeight: PropTypes.number };

function PanelChart({ title, subtitle, children }) {
  return (
    <Panel title={title} subtitle={subtitle}>
      <Box sx={{ px: 1, py: 1 }}>{children}</Box>
    </Panel>
  );
}
PanelChart.propTypes = { title: PropTypes.node, subtitle: PropTypes.node, children: PropTypes.node };

function DataTableShell({ children }) {
  return (
    <Table size="small" sx={{
      "& th, & td": { borderColor: "divider", py: 0.75, px: 2 },
      "& th": { fontSize: 10.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4, color: "text.subtitle" },
      "& td": { fontSize: 12.5, color: "text.primary" },
      "& tr:last-of-type td": { borderBottom: "none" },
    }}>
      {children}
    </Table>
  );
}
DataTableShell.propTypes = { children: PropTypes.node };

function Th({ children, align = "left" }) {
  return <TableCell align={align}>{children}</TableCell>;
}
Th.propTypes = { children: PropTypes.node, align: PropTypes.string };

function Td({ children, align = "left", mono, noWrap, sx }) {
  return (
    <TableCell
      align={align}
      sx={{
        fontVariantNumeric: mono ? "tabular-nums" : undefined,
        whiteSpace: noWrap ? "nowrap" : undefined,
        maxWidth: noWrap ? 280 : undefined,
        overflow: noWrap ? "hidden" : undefined,
        textOverflow: noWrap ? "ellipsis" : undefined,
        ...sx,
      }}
    >
      {children}
    </TableCell>
  );
}
Td.propTypes = { children: PropTypes.node, align: PropTypes.string, mono: PropTypes.bool, noWrap: PropTypes.bool, sx: PropTypes.object };

/* ── shell ─────────────────────────────────────────────────────────── */

export default function RunAnalyticsV2({ tasks, evals }) {
  return (
    <Stack spacing={2}>
      <KpiStrip tasks={tasks} evals={evals} />
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, alignItems: "start" }}>
        <UseCaseRiskList tasks={tasks} />
        <TurnBars tasks={tasks} />
      </Box>
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" } }}>
        <EvalsTable tasks={tasks} evals={evals} />
        <AttributionTable tasks={tasks} />
      </Box>
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" } }}>
        <SlowestTable tasks={tasks} />
        <ExpensiveTable tasks={tasks} />
      </Box>
    </Stack>
  );
}
RunAnalyticsV2.propTypes = {
  tasks: PropTypes.array,
  evals: PropTypes.array,
  env: PropTypes.object,
  stats: PropTypes.object,
};
