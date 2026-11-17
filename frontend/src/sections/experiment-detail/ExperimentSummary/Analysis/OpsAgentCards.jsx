import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import DeltaBadge from "src/sections/common/Metrics/DeltaBadge";
import { formatValue } from "src/sections/common/Metrics/metricFormat";
import AnalysisCard from "src/sections/common/Analysis/AnalysisCard";
import MiniHistogram from "src/sections/common/Analysis/MiniHistogram";

const tabular = { fontVariantNumeric: "tabular-nums" };
const tableSx = {
  "& td, & th": { typography: "s2", borderColor: "divider", py: 0.75 },
  "& th": {
    fontWeight: 600,
    color: "text.secondary",
    bgcolor: "background.neutral",
  },
};

const RunCell = ({ run, color }) => (
  <Stack direction="row" alignItems="center" gap={0.75}>
    <Box
      sx={{ width: 3, height: 14, borderRadius: 1, bgcolor: color?.solid }}
    />
    <Typography typography="s2" fontWeight={600}>
      {run.label}
    </Typography>
  </Stack>
);
RunCell.propTypes = { run: PropTypes.object, color: PropTypes.object };

/**
 * Ops card (PRODUCT-SPEC §3.13 M8.3): p50 / p99 latency, cost per row, total
 * cost and tokens per run with deltas vs the baseline run, and the latency
 * distribution.
 */
export function OpsCard({
  analysis,
  colors,
  baselineId,
  baselineLabel,
  bare = false,
}) {
  const m = Object.fromEntries(analysis.metrics.map((x) => [x.key, x]));
  const latency = m["ops:latency"];
  const cost = m["ops:cost"];
  const tokens = m["ops:tokens"];
  if (!latency && !cost) return null;
  const stat = (metric, id) => metric?.per_run.find((p) => p.run_id === id);
  const vs = (metric, id) => metric?.vs_baseline.find((v) => v.run_id === id);
  const delta = (metric, id, opts = {}) => {
    const v = vs(metric, id);
    if (!v || id === baselineId) return null;
    return (
      <DeltaBadge
        def={metric}
        delta={v.delta}
        ci95={v.ci95}
        significant={v.significant}
        n={v.n_shared}
        vsLabel={baselineLabel}
        typography="s3"
        {...opts}
      />
    );
  };
  const cell = (metric, id, field = "value") => {
    const s = stat(metric, id);
    if (!s || !s.n) return "—";
    return formatValue(metric, s[field], {
      agg: field === "value" ? "avg" : field,
    });
  };
  const latencyRuns = analysis.runs.filter((r) => stat(latency, r.run_id)?.n);
  return (
    <AnalysisCard
      id="metric-ops"
      icon={bare ? undefined : "mdi:timer-outline"}
      title={bare ? undefined : "Latency and cost"}
      sx={bare ? { pt: 1.5 } : undefined}
    >
      <Table size="small" sx={tableSx}>
        <TableHead>
          <TableRow>
            <TableCell>Run</TableCell>
            <TableCell align="right">p50 latency</TableCell>
            <TableCell align="right">p99</TableCell>
            <TableCell align="right">Cost / row</TableCell>
            <TableCell align="right">Total cost</TableCell>
            <TableCell align="right">Tokens / row</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {analysis.runs.map((run) => (
            <TableRow key={run.run_id}>
              <TableCell>
                <RunCell run={run} color={colors?.[run.run_id]} />
              </TableCell>
              <TableCell align="right" sx={tabular}>
                <Stack alignItems="flex-end">
                  {cell(latency, run.run_id, "p50")}
                  {delta(latency, run.run_id)}
                </Stack>
              </TableCell>
              <TableCell align="right" sx={tabular}>
                {cell(latency, run.run_id, "p99")}
              </TableCell>
              <TableCell align="right" sx={tabular}>
                <Stack alignItems="flex-end">
                  {cell(cost, run.run_id)}
                  {delta(cost, run.run_id)}
                </Stack>
              </TableCell>
              <TableCell align="right" sx={tabular}>
                {run.cost_total != null
                  ? formatValue(cost, run.cost_total)
                  : "—"}
              </TableCell>
              <TableCell align="right" sx={tabular}>
                <Stack alignItems="flex-end">
                  {stat(tokens, run.run_id)?.n
                    ? Math.round(
                        stat(tokens, run.run_id).value,
                      ).toLocaleString()
                    : "—"}
                  {delta(tokens, run.run_id)}
                </Stack>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {latencyRuns.length > 0 && (
        <Box sx={{ mt: 1.5 }}>
          <Typography typography="s2" color="text.secondary" sx={{ mb: 0.5 }}>
            Latency per row
          </Typography>
          <MiniHistogram
            histogram={latency.histogram}
            runs={latencyRuns}
            colors={colors}
            formatX={(v) => `${(v / 1000).toFixed(1)} s`}
          />
        </Box>
      )}
    </AnalysisCard>
  );
}

OpsCard.propTypes = {
  analysis: PropTypes.object.isRequired,
  colors: PropTypes.object,
  baselineId: PropTypes.string,
  baselineLabel: PropTypes.string,
  bare: PropTypes.bool,
};

/**
 * Agent card (PRODUCT-SPEC §3.13 M8.3, agent runs in view): steps, tool
 * calls, tool error rate and LLM calls per run; the Tool usage table ranked
 * by error rate (`lookup_order` errors → rows with tool errors); and "Rows
 * where #4A took more steps than #3A (n)".
 */
export function AgentCard({
  analysis,
  colors,
  baselineId,
  baselineLabel,
  links,
  bare = false,
}) {
  const agents = analysis.agent ?? [];
  if (!agents.length) return null;
  const runById = Object.fromEntries(analysis.runs.map((r) => [r.run_id, r]));
  const steps = analysis.metrics.find((m) => m.key === "agent:steps");
  const toolNames = [
    ...new Set(agents.flatMap((a) => a.tools.map((t) => t.tool))),
  ];
  const toolOf = (a, name) => a.tools.find((t) => t.tool === name);
  const worstRate = (name) =>
    Math.max(0, ...agents.map((a) => toolOf(a, name)?.error_rate ?? 0));
  const tools = toolNames
    .sort((x, y) => worstRate(y) - worstRate(x) || x.localeCompare(y))
    .slice(0, 8);
  const pct1 = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
  const candidate = agents.find(
    (a) => a.run_id !== baselineId && a.more_steps_than_baseline != null,
  );

  return (
    <AnalysisCard
      id="metric-agent"
      icon={bare ? undefined : "mdi:robot-outline"}
      title={bare ? undefined : "Agent"}
      sx={bare ? { pt: 1.5 } : undefined}
    >
      <Table size="small" sx={tableSx}>
        <TableHead>
          <TableRow>
            <TableCell>Run</TableCell>
            <TableCell align="right">Avg steps</TableCell>
            <TableCell align="right">Max</TableCell>
            <TableCell align="right">Tool calls / row</TableCell>
            <TableCell align="right">Tool error rate</TableCell>
            <TableCell align="right">LLM calls / row</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {agents.map((a) => {
            const run = runById[a.run_id];
            const v = steps?.vs_baseline.find((x) => x.run_id === a.run_id);
            return (
              <TableRow key={a.run_id}>
                <TableCell>
                  <RunCell run={run} color={colors?.[a.run_id]} />
                </TableCell>
                <TableCell align="right" sx={tabular}>
                  <Stack alignItems="flex-end">
                    {a.avg_steps ?? "—"}
                    {v && a.run_id !== baselineId && (
                      <DeltaBadge
                        def={steps}
                        delta={v.delta}
                        ci95={v.ci95}
                        significant={v.significant}
                        n={v.n_shared}
                        vsLabel={baselineLabel}
                        typography="s3"
                      />
                    )}
                  </Stack>
                </TableCell>
                <TableCell align="right" sx={tabular}>
                  {a.max_steps ?? "—"}
                </TableCell>
                <TableCell align="right" sx={tabular}>
                  {a.avg_tool_calls ?? "—"}
                </TableCell>
                <TableCell align="right" sx={tabular}>
                  {a.rows_with_tool_errors > 0 ? (
                    <CustomTooltip
                      show
                      size="small"
                      placement="top"
                      arrow
                      title={`${a.rows_with_tool_errors} rows had a tool error. Open them.`}
                    >
                      <Link
                        component={RouterLink}
                        to={links.toolErrors(a.run_id)}
                        underline="hover"
                        color="error.main"
                        sx={{ fontWeight: 600 }}
                      >
                        {pct1(a.tool_error_rate)}
                      </Link>
                    </CustomTooltip>
                  ) : (
                    pct1(a.tool_error_rate)
                  )}
                </TableCell>
                <TableCell align="right" sx={tabular}>
                  {a.avg_llm_calls ?? "—"}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ mt: 1.5, mb: 0.5 }}
      >
        Tool usage
      </Typography>
      <Table size="small" sx={tableSx}>
        <TableHead>
          <TableRow>
            <TableCell>Tool</TableCell>
            {agents.map((a) => (
              <React.Fragment key={a.run_id}>
                <TableCell align="right">
                  {runById[a.run_id]?.label} calls / row
                </TableCell>
                <TableCell align="right">
                  {runById[a.run_id]?.label} errors
                </TableCell>
              </React.Fragment>
            ))}
            <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
              p50
            </TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {tools.map((name) => (
            <TableRow key={name}>
              <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>
                {name}
              </TableCell>
              {agents.map((a) => {
                const t = toolOf(a, name);
                const bad = (t?.error_rate ?? 0) > 0;
                return (
                  <React.Fragment key={a.run_id}>
                    <TableCell align="right" sx={tabular}>
                      {t ? t.calls_per_row : "—"}
                    </TableCell>
                    <TableCell align="right" sx={tabular}>
                      {!t ? (
                        "—"
                      ) : bad ? (
                        <CustomTooltip
                          show
                          size="small"
                          placement="top"
                          arrow
                          title={`${t.errors} of ${t.calls} ${name} calls failed on ${runById[a.run_id]?.label}. Open rows with tool errors.`}
                        >
                          <Link
                            component={RouterLink}
                            to={links.toolErrors(a.run_id, name)}
                            underline="hover"
                            color="error.main"
                            sx={{ fontWeight: 600 }}
                          >
                            {pct1(t.error_rate)}
                          </Link>
                        </CustomTooltip>
                      ) : (
                        <Box component="span" sx={{ color: "text.disabled" }}>
                          0%
                        </Box>
                      )}
                    </TableCell>
                  </React.Fragment>
                );
              })}
              <TableCell
                align="right"
                sx={{ ...tabular, whiteSpace: "nowrap" }}
              >
                {(() => {
                  const ms = agents
                    .map((a) => toolOf(a, name)?.p50_ms)
                    .filter((x) => x != null);
                  return ms.length ? `${Math.round(Math.max(...ms))} ms` : "—";
                })()}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {candidate && candidate.more_steps_than_baseline > 0 && (
        <Typography typography="s2" color="text.secondary" sx={{ mt: 1 }}>
          Rows where {runById[candidate.run_id]?.label} took more steps than{" "}
          {baselineLabel}:{" "}
          <Link
            component={RouterLink}
            to={links.moreSteps(candidate.run_id)}
            underline="hover"
            sx={{ fontWeight: 600 }}
          >
            {candidate.more_steps_than_baseline}
          </Link>
        </Typography>
      )}
    </AnalysisCard>
  );
}

AgentCard.propTypes = {
  analysis: PropTypes.object.isRequired,
  colors: PropTypes.object,
  baselineId: PropTypes.string,
  baselineLabel: PropTypes.string,
  links: PropTypes.object.isRequired,
  bare: PropTypes.bool,
};
