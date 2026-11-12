import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Paper, Popper, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { flattenTrace } from "src/sections/common/Align/normalize";
import { firstWrongStepText } from "src/sections/common/Align/milestones";
import CopyId from "src/sections/common/ux/CopyId";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import RunTrajectory from "src/sections/common/Trajectory/RunTrajectory";
import Term from "src/sections/common/ux/Term";
import { fmtPct } from "src/sections/common/format/fmt";
import { fmtLatency } from "src/sections/common/Analysis/voiceSummaryModel";

/** A voice call's numbers in the hover card. */
function CallStats({ call }) {
  if (!call) return null;
  if (call.textOnly)
    return (
      <Typography typography="s2" color="text.secondary">
        Text only · no audio, so no latency, interruptions or WER
      </Typography>
    );
  const rows = [
    [
      <Term key="t" k="latencyP90">
        Latency
      </Term>,
      `p50 ${fmtLatency(call.p50)} · p90 ${fmtLatency(call.p90)}`,
    ],
    ["Kept talking", `${call.kept ?? 0} of ${call.bargeIns ?? 0} barge-ins`],
    [
      <Term key="w" k="wer">
        WER
      </Term>,
      fmtPct(call.wer),
    ],
    ["Ended", call.endedReason ?? "—"],
  ];
  return (
    <Box
      data-testid="hover-call-stats"
      sx={{
        display: "grid",
        gridTemplateColumns: "88px 1fr",
        columnGap: 1,
        rowGap: 0.25,
        mb: 0.75,
      }}
    >
      {rows.map(([k, v], i) => (
        // eslint-disable-next-line react/no-array-index-key
        <React.Fragment key={i}>
          <Typography typography="s2" color="text.secondary">
            {k}
          </Typography>
          <Typography
            typography="s2"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {v}
          </Typography>
        </React.Fragment>
      ))}
      {call.recordingMissing ? (
        <Typography
          typography="s3"
          color="warning.dark"
          sx={{ gridColumn: "1 / -1" }}
        >
          Recording missing: latency reported by the provider
        </Typography>
      ) : null}
      {call.failedTool ? (
        <Typography
          typography="s3"
          color="error.main"
          sx={{ gridColumn: "1 / -1" }}
        >
          A tool call failed in this call
        </Typography>
      ) : null}
    </Box>
  );
}
CallStats.propTypes = { call: PropTypes.object };
import {
  causeMarker,
  cellNumbers,
  firstFailedEval,
  fmtDuration,
  fmtMoney,
  unscoredResultText,
} from "./matrixModel";

/** The short value a step returned (`"8830"` from `{order_ref: "8830"}`). */
export function returnedValue(output) {
  if (output == null) return null;
  if (typeof output === "string") return output.length <= 24 ? output : null;
  if (typeof output === "object" && !Array.isArray(output)) {
    const prefer = ["order_ref", "order_id", "id", "value", "result"];
    const key =
      prefer.find((k) => typeof output[k] === "string") ??
      Object.keys(output).find((k) => typeof output[k] === "string");
    return key ? output[key] : null;
  }
  return null;
}

/**
 * `First wrong step: #2B step 1 · classify_intent` (G0 copy) and, under it,
 * the value that step returned in trial 1's trace (`Returned "8830"`).
 */
function FirstWrongStep({ cause, traceId, runLabel }) {
  const q = useGetTraceDetail(traceId);
  const value = useMemo(() => {
    if (!q.data || !cause?.first_divergence?.node) return null;
    const step = flattenTrace(q.data).find(
      (s) => s.name === cause.first_divergence.node,
    );
    return returnedValue(step?.output);
  }, [q.data, cause]);
  return (
    <>
      <Typography typography="s2" sx={{ fontVariantNumeric: "tabular-nums" }}>
        {firstWrongStepText(causeMarker(cause, runLabel))}
      </Typography>
      {value ? (
        <Typography typography="s2" color="text.secondary">
          {`Returned "${value}"`}
        </Typography>
      ) : null}
    </>
  );
}

FirstWrongStep.propTypes = {
  cause: PropTypes.object,
  traceId: PropTypes.string,
  runLabel: PropTypes.string,
};

/**
 * Cell hover card: `#2B · T26 · 0 of 3 trials passed` · failed eval ·
 * first wrong step · `$0.011 · 9.6 s` · Open trajectory · Compare with #2A ·
 * Add a note · Copy row id. Opens after 300 ms; stays while the pointer is
 * on it; Esc closes it (the Matrix handles Esc).
 */
const MatrixHoverCard = ({
  anchorEl,
  open,
  row,
  runLabel,
  cell,
  cause,
  pairLabel,
  onOpenTrajectory,
  onCompare,
  onAddNote,
  addNoteDisabledReason,
  onEnter,
  onLeave,
  call,
  metricNames,
  metricDefs,
}) => {
  const [showTrajectory, setShowTrajectory] = useState(false);
  const key = `${row?.row_id}|${runLabel}`;
  useEffect(() => setShowTrajectory(false), [key]);
  if (!open || !row || !cell) return null;
  const n = cellNumbers(cell);
  // W28: a cell with no pass / fail reads its scores (`output-check 1`).
  const result =
    unscoredResultText(cell, metricNames, metricDefs) ??
    `${n.pass} of ${n.scored} ${n.scored === 1 ? "trial" : "trials"} passed`;
  // `eval:voice_task_completion` → `Voice task completion` (no raw keys).
  const rawFailed = firstFailedEval(cell);
  const failed = rawFailed
    ? String(rawFailed)
        .replace(/^eval:/, "")
        .replace(/_/g, " ")
        .replace(/^./, (c) => c.toUpperCase())
    : null;
  const firstError = cell.first_error;
  const traceId = cell.trace_ids?.[0] ?? null;
  return (
    <Popper
      open
      anchorEl={anchorEl}
      placement="right-start"
      sx={{ zIndex: (t) => t.zIndex.modal - 1 }}
      modifiers={[{ name: "offset", options: { offset: [0, 4] } }]}
    >
      <Paper
        elevation={6}
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
        role="dialog"
        aria-label={`${runLabel} ${row.label}`}
        sx={{
          p: 1.5,
          width: showTrajectory ? 460 : 300,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <Stack direction="row" alignItems="center" gap={0.5}>
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            sx={{ flex: 1, fontVariantNumeric: "tabular-nums" }}
          >
            {`${runLabel} · ${row.label} · ${result}`}
          </Typography>
          <CopyId id={row.row_id} label="Row" />
        </Stack>
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ mb: 0.75 }}
        >
          {row.title}
        </Typography>
        <CallStats call={call} />
        <Stack spacing={0.25} sx={{ mb: 1 }}>
          {failed && (
            <Typography typography="s2">
              <Box component="span" sx={{ color: "error.main" }}>
                {`${failed} ✕`}
              </Box>
              {cell.failed_evals?.some((f) => (f?.length ?? 0) > 0) ? (
                <Box component="span" sx={{ color: "text.secondary" }}>
                  {" · later evals not reached"}
                </Box>
              ) : null}
            </Typography>
          )}
          {causeMarker(cause, runLabel) ? (
            <FirstWrongStep
              cause={cause}
              traceId={traceId}
              runLabel={runLabel}
            />
          ) : firstError ? (
            <Typography typography="s2">
              {firstError.timeout_s != null
                ? `Timed out at step ${firstError.step} · ${firstError.timeout_s} s`
                : `Failed at step ${firstError.step} · ${firstError.text}`}
            </Typography>
          ) : null}
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {`${fmtMoney(n.cost)} · ${fmtDuration(n.wall)}`}
          </Typography>
        </Stack>
        {showTrajectory ? (
          <Box
            sx={{ mb: 1, height: 240, overflow: "hidden" }}
            data-testid="hover-trajectory"
          >
            <RunTrajectory
              traceId={traceId}
              height={240}
              compact
              showDetail={false}
            />
          </Box>
        ) : null}
        <Stack direction="row" gap={0.5} flexWrap="wrap">
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title={
              traceId ? "Show trial 1's steps here" : "No trace for this cell"
            }
          >
            <span>
              <Button
                size="small"
                variant="outlined"
                disabled={!traceId}
                startIcon={
                  <Iconify icon="mdi:timeline-text-outline" width={16} />
                }
                onClick={() => setShowTrajectory((v) => !v)}
              >
                {showTrajectory ? "Hide trajectory" : "Open trajectory"}
              </Button>
            </span>
          </CustomTooltip>
          <KeyHintTooltip title="Open side by side" keys={["Enter"]}>
            <Button
              size="small"
              variant="contained"
              startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
              onClick={onOpenTrajectory}
            >
              Open side by side
            </Button>
          </KeyHintTooltip>
          {pairLabel && (
            <CustomTooltip
              show
              size="small"
              placement="top"
              arrow
              title={`${runLabel} and ${pairLabel} only, side by side`}
            >
              <Button size="small" variant="outlined" onClick={onCompare}>
                {`Compare with ${pairLabel}`}
              </Button>
            </CustomTooltip>
          )}
          <CustomTooltip
            show={Boolean(addNoteDisabledReason)}
            size="small"
            placement="top"
            arrow
            title={addNoteDisabledReason ?? ""}
          >
            <span>
              <Button
                size="small"
                color="inherit"
                disabled={Boolean(addNoteDisabledReason)}
                startIcon={
                  <Iconify icon="mdi:comment-text-outline" width={16} />
                }
                onClick={onAddNote}
              >
                Add a note
              </Button>
            </span>
          </CustomTooltip>
        </Stack>
      </Paper>
    </Popper>
  );
};

MatrixHoverCard.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool,
  row: PropTypes.object,
  runLabel: PropTypes.string,
  cell: PropTypes.object,
  cause: PropTypes.object,
  pairLabel: PropTypes.string,
  onOpenTrajectory: PropTypes.func,
  onCompare: PropTypes.func,
  onAddNote: PropTypes.func,
  addNoteDisabledReason: PropTypes.string,
  onEnter: PropTypes.func,
  onLeave: PropTypes.func,
  // callStats of the cell's call (voice data).
  call: PropTypes.object,
  // W28: `{metric key: eval name}` for a cell's scores (model.metricNames).
  metricNames: PropTypes.object,
  // `{metric key: metric def}`: a choice eval's score reads its label
  // (model.metricDefs).
  metricDefs: PropTypes.object,
};

export default MatrixHoverCard;
