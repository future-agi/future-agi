import PropTypes from "prop-types";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { StatusDot } from "../../components/primitives";
import CopyCommandButton from "../../components/CopyCommandButton";
import { rebuildRun } from "../../_mock/comparison";
import { runPlanHref } from "../../_mock/runPlanLink";
import { productionTracesOf, TRACE_GOALS } from "../../_mock/traceImport";
import {
  STATE_MODES,
  REDACTION_RULES,
  regressionChainOf,
  sourceAccessOf,
} from "../../_mock/traceReview";

const MONO = "ui-monospace, Menlo, monospace";
const box = { border: "1px solid", borderColor: "divider", borderRadius: 1 };
const rowSx = {
  px: 1.25,
  py: 0.75,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};
const ACCESS_DOT = {
  ok: "passed",
  deleted: "cancelled",
  revoked: "needs_input",
  missing: "cancelled",
};
const RESULT = {
  passed: "Passed",
  failed: "Failed",
  flaky: "Flaky",
  unmeasured: "Not measured",
};
const when = (iso) =>
  iso
    ? new Date(iso).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })
    : "—";

function Line({ k, children }) {
  return (
    <Stack direction="row" spacing={1.5} sx={rowSx}>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          width: 120,
          flexShrink: 0,
        }}
      >
        {k}
      </Typography>
      <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
        {children}
      </Typography>
    </Stack>
  );
}
Line.propTypes = { k: PropTypes.string, children: PropTypes.node };

/**
 * Sources of a scenario made from production traces (roadmap traces.md
 * "Bulk behavior and provenance"): the exact traces — still openable,
 * deleted or no longer accessible — the import's goal, the evidence class,
 * what was redacted and decided at review; then the chain from the trace to
 * each run's result on it since, with Run as regression after a fix.
 */
export default function TraceSources({ row, env, envState }) {
  const navigate = useNavigate();
  const imp = row.traceImport || {};
  const review = row.traceReview || {};
  const traces = useMemo(
    () => productionTracesOf(env, envState),
    [env, envState],
  );
  const sources = (imp.sourceIds || []).map((id) => ({
    id,
    trace: traces.find((t) => t.traceId === id),
  }));
  const chain = useMemo(
    () => regressionChainOf(row, envState, (r) => rebuildRun(env, envState, r)),
    [row, env, envState],
  );
  const goal = TRACE_GOALS.find((g) => g.id === imp.goal);
  const redactions = Object.entries(review.redaction || {}).filter(
    ([, v]) => v !== "fake",
  );
  const decided = Object.entries(review.assumptions || {});

  return (
    <Stack
      spacing={1.5}
      sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        Sources
      </Typography>
      <Box sx={box}>
        {sources.map(({ id, trace }) => {
          const a = sourceAccessOf(trace);
          return (
            <Stack
              key={id}
              direction="row"
              spacing={1.25}
              alignItems="center"
              sx={rowSx}
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontFamily: MONO,
                  width: 56,
                  flexShrink: 0,
                }}
              >
                {id}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {trace
                  ? `agent ${trace.agentVersion} · ${trace.outcome === "failed" ? "failed" : "passed"} · ${when(trace.at)} · ${trace.spans} spans`
                  : "—"}
              </Typography>
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.5}
                sx={{ flexShrink: 0 }}
                title={a.line}
              >
                <StatusDot status={ACCESS_DOT[a.id]} size={5} live={false} />
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {a.label}
                </Typography>
              </Stack>
            </Stack>
          );
        })}
      </Box>
      {sources.some(({ trace }) => sourceAccessOf(trace).id !== "ok") && (
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", mt: -0.75 }}
        >
          {
            sources
              .map(({ trace }) => sourceAccessOf(trace))
              .find((a) => a.id !== "ok").line
          }
        </Typography>
      )}
      <Box sx={box}>
        <Line k="Project">{`${imp.project || "—"} · ${imp.range || ""}`}</Line>
        <Line k="Made as">
          {goal ? goal.label : "—"}
          {row.generatedVariant
            ? " · generated variant, not something that happened"
            : ""}
        </Line>
        <Line k="Evidence">
          {STATE_MODES[review.stateMode || "observed"]?.evidence || "—"}
        </Line>
        <Line k="Personal data">
          {redactions.length
            ? `Replaced with fake values, except ${redactions.map(([k, v]) => `${k} (${REDACTION_RULES[v].toLowerCase()})`).join(", ")}`
            : "Every kind replaced with fake values"}
        </Line>
        {decided.length > 0 && (
          <Line k="Inferred facts">
            {decided.map(([k, v]) => `${k}: ${v}`).join(" · ")}
          </Line>
        )}
        <Line k="Reviewed">
          {review.validated
            ? `Validated by ${review.validated.by || "—"} on ${when(review.validated.at)}`
            : "Added before review was recorded"}
        </Line>
      </Box>

      {/* Trace → scenario → version → result. */}
      <Box>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              flex: 1,
            }}
          >
            From production to now
          </Typography>
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="solar:play-linear" width={14} />}
            onClick={() => navigate(runPlanHref(env.id, { only: [row.id] }))}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Run as regression
          </Button>
        </Stack>
        <Box sx={box}>
          <Stack direction="row" spacing={1.25} sx={rowSx}>
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                width: 90,
                flexShrink: 0,
              }}
            >
              Production
            </Typography>
            <Typography sx={{ typography: "s2" }}>
              {sources
                .map(
                  ({ id, trace }) =>
                    `${id} ${trace?.outcome === "failed" ? "failed" : "passed"} on agent ${trace?.agentVersion || "—"}`,
                )
                .join(" · ")}
            </Typography>
          </Stack>
          {chain.map(({ run, status }) => (
            <Stack
              key={run.id}
              direction="row"
              spacing={1.25}
              alignItems="center"
              sx={{
                ...rowSx,
                cursor: "pointer",
                "&:hover": { bgcolor: "action.hover" },
              }}
              onClick={() =>
                navigate(paths.dashboard.simulate.simulationRun(env.id, run.id))
              }
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  width: 90,
                  flexShrink: 0,
                }}
              >{`Run ${run.ordinal ?? ""}`}</Typography>
              <Typography
                sx={{ typography: "s2", flex: 1 }}
              >{`agent ${run.agentVersion || "—"} · environment ${run.envVersion || "—"} · ${when(run.finishedAt)}`}</Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color:
                    status === "passed"
                      ? "status.success.fg"
                      : status === "failed"
                        ? "status.danger.fg"
                        : "text.secondary",
                }}
              >
                {RESULT[status] || status}
              </Typography>
            </Stack>
          ))}
          {!chain.length && (
            <Typography
              sx={{ ...rowSx, typography: "s3", color: "text.tertiary" }}
            >
              Not run since it was added — Run as regression tests it on the
              current agent.
            </Typography>
          )}
        </Box>
        {chain.length > 0 && (
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
          >
            {chain[chain.length - 1].status === "passed"
              ? `The production failure no longer happens on agent ${chain[chain.length - 1].run.agentVersion}. If the scenario's data changed since, its earlier runs are re-scored before calling it fixed.`
              : `Still ${RESULT[chain[chain.length - 1].status]?.toLowerCase() || "open"} on agent ${chain[chain.length - 1].run.agentVersion}.`}
          </Typography>
        )}
      </Box>
      <Box>
        <CopyCommandButton
          command={`agent-learn scenario show ${row.name || row.id}`}
          label="Copy as command"
        />
      </Box>
    </Stack>
  );
}
TraceSources.propTypes = {
  row: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
};
