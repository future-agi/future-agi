import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import MetricStrip from "src/components/metric-strip/MetricStrip";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { StatusChip, StatusDot } from "../../components/primitives";
import { scenarioTitle } from "../../_mock/scenarios";
import {
  GRADING_ERROR,
  denominatorLine,
  evaluationCountsOf,
  evaluatorLabelOf,
  trialLabelOf,
} from "../../_mock/evalEvidence";
import { OUTCOME_LABEL, outcomeOf } from "../../_mock/trialQuestions";
import ResultIdentity from "./ResultIdentity";

/*
  One saved evaluation result: what the evaluation scored, apart from what it
  couldn't read and what the judge didn't answer. The run's task checks are
  shown beside it and are not changed by it.
*/
const SectionHead = ({ title, sub, right }) => (
  <Stack direction="row" alignItems="flex-end" spacing={2} sx={{ mb: 1 }}>
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Typography sx={{ typography: "m3", fontWeight: 500 }}>
        {title}
      </Typography>
      {sub && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {sub}
        </Typography>
      )}
    </Box>
    {right}
  </Stack>
);
SectionHead.propTypes = {
  title: PropTypes.node,
  sub: PropTypes.node,
  right: PropTypes.node,
};

const cellSx = { typography: "s1", color: "text.secondary", py: 0, px: 1.5 };
const headSx = {
  typography: "s2",
  fontWeight: 500,
  color: "text.tertiary",
  bgcolor: "transparent",
  height: TABLE_HEADER_HEIGHT.default,
  py: 0,
  px: 1.5,
};

const PASSED = "Passed";
const FAILED = "Failed";
const NOT_READ = "Not read";

export default function EvaluationResultView({
  result,
  tasks,
  runId,
  runLabel,
  onRunAgain,
  onOpenTask,
}) {
  const [focus, setFocus] = useState(null);
  const counts = evaluationCountsOf(result);
  const chosen = useMemo(() => {
    const ids = new Set(result.taskIds);
    return tasks.filter((t) => ids.has(t.id));
  }, [result.taskIds, tasks]);

  const labelOf = (t) => {
    const r = result.results[t.id];
    if (!r) return result.skipped[t.id] ? NOT_READ : null;
    return r.error ? GRADING_ERROR : r.passed ? PASSED : FAILED;
  };
  const rows = chosen.filter((t) => !focus || labelOf(t) === focus);
  const toggle = (label) => setFocus((f) => (f === label ? null : label));
  const pct = (n) =>
    counts.evaluated ? `${Math.round((n / counts.evaluated) * 100)}%` : "—";

  const strip = [
    {
      key: PASSED,
      label: PASSED,
      value: counts.passed,
      hint: pct(counts.passed),
      onClick: () => toggle(PASSED),
    },
    {
      key: FAILED,
      label: FAILED,
      value: counts.failed,
      hint: pct(counts.failed),
      onClick: () => toggle(FAILED),
    },
    ...(counts.errors
      ? [
          {
            key: GRADING_ERROR,
            label: GRADING_ERROR,
            value: counts.errors,
            hint: "Judge didn't answer",
            onClick: () => toggle(GRADING_ERROR),
          },
        ]
      : []),
    ...(counts.skipped
      ? [
          {
            key: NOT_READ,
            label: NOT_READ,
            value: counts.skipped,
            hint: "No evidence to read",
            onClick: () => toggle(NOT_READ),
          },
        ]
      : []),
  ];

  /* The evaluation's result beside the run's task checks. */
  const outcomes = ["passed", "failed", "unmeasured", "error"].filter((o) =>
    chosen.some((t) => outcomeOf(t) === o),
  );
  const cross = (label, o) =>
    chosen.filter((t) => outcomeOf(t) === o && labelOf(t) === label).length;

  return (
    <Box>
      <Stack direction="row" alignItems="flex-start" spacing={2}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "m1", fontWeight: 600 }}>
            {evaluatorLabelOf(result.evaluator)}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            {runLabel} · {result.scopeLabel} · {chosen.length} trials · pass at{" "}
            {Math.round((result.evaluator.threshold ?? 0.8) * 100)}
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:restart-linear" width={14} />}
          onClick={onRunAgain}
        >
          Run again
        </Button>
      </Stack>

      <ResultIdentity
        result={result}
        trials={chosen}
        runId={runId}
        runLabel={runLabel}
      />

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 2 }}>
        <StatusDot status="passed" />
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {counts.evaluated + counts.errors} trials read in {result.durationS} s
        </Typography>
      </Stack>

      <MetricStrip items={strip} sx={{ mt: 1.5 }} />
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
        {denominatorLine(counts)}
      </Typography>

      <Box sx={{ mt: 3 }}>
        <SectionHead title="Against the run's task checks" />
        <Table size="small" sx={{ maxWidth: 640 }}>
          <TableHead>
            <TableRow>
              <TableCell sx={headSx}>Evaluation</TableCell>
              {outcomes.map((o) => (
                <TableCell key={o} align="right" sx={headSx}>
                  Task checks {OUTCOME_LABEL[o].toLowerCase()}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {[PASSED, FAILED].map((label) => (
              <TableRow key={label} sx={{ height: ROW_HEIGHT.default }}>
                <TableCell
                  sx={{ ...cellSx, color: "text.primary", fontWeight: 500 }}
                >
                  {label}
                </TableCell>
                {outcomes.map((o) => (
                  <TableCell
                    key={o}
                    align="right"
                    sx={{ ...cellSx, fontVariantNumeric: "tabular-nums" }}
                  >
                    {cross(label, o)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>

      <Box sx={{ mt: 3 }}>
        <SectionHead
          title={`Trials ${rows.length}`}
          sub={
            focus ? (
              <>
                Showing {focus}
                {" · "}
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => setFocus(null)}
                  sx={{ ml: 0.5, color: "text.primary" }}
                >
                  Show all
                </Button>
              </>
            ) : (
              "Click a trial to open its conversation and tool calls"
            )
          }
        />
        <Table size="small" sx={{ tableLayout: "fixed" }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ ...headSx, width: 96 }}>Trial</TableCell>
              <TableCell sx={{ ...headSx, width: "24%" }}>Scenario</TableCell>
              <TableCell sx={{ ...headSx, width: 120 }}>Task checks</TableCell>
              <TableCell sx={{ ...headSx, width: 150 }}>Result</TableCell>
              <TableCell sx={headSx}>Why</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((t) => {
              const r = result.results[t.id];
              const label = labelOf(t);
              const why = r ? r.reason : result.skipped[t.id] || "";
              return (
                <TableRow
                  key={t.id}
                  hover
                  onClick={() => onOpenTask?.(t)}
                  sx={{ height: ROW_HEIGHT.default, cursor: "pointer" }}
                >
                  <TableCell sx={cellSx}>
                    <Typography noWrap sx={{ typography: "s1" }}>
                      {trialLabelOf(t, runId)}
                    </Typography>
                  </TableCell>
                  <TableCell
                    sx={{ ...cellSx, color: "text.primary", fontWeight: 500 }}
                  >
                    <Typography
                      noWrap
                      sx={{ typography: "s1", fontWeight: 500 }}
                    >
                      {scenarioTitle(t.title || t.name)}
                    </Typography>
                  </TableCell>
                  <TableCell sx={cellSx}>
                    <StatusChip status={t.status} />
                  </TableCell>
                  <TableCell sx={cellSx}>
                    <Typography noWrap sx={{ typography: "s1" }}>
                      {r && !r.error
                        ? `${label} · ${Math.round(r.score * 100)}`
                        : label}
                    </Typography>
                  </TableCell>
                  <TableCell sx={cellSx}>
                    <Tooltip title={why} placement="top-start">
                      <Typography noWrap sx={{ typography: "s1" }}>
                        {why}
                      </Typography>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}
EvaluationResultView.propTypes = {
  result: PropTypes.object.isRequired,
  tasks: PropTypes.array.isRequired,
  runId: PropTypes.string.isRequired,
  runLabel: PropTypes.string.isRequired,
  onRunAgain: PropTypes.func,
  onOpenTask: PropTypes.func,
};
