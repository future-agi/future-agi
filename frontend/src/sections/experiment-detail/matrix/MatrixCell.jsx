import React, { memo } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import TraceChip from "src/sections/common/Trace/TraceChip";
import { cellTraceChipProps } from "src/sections/common/Trace/traceChipModel";
import {
  cellNumbers,
  cellScoreList,
  cellTrialKind,
  fmtDuration,
  fmtMoney,
  unscoredResultText,
} from "./matrixModel";
import { fmtLatency } from "src/sections/common/Analysis/voiceSummaryModel";
import { fmtCost } from "src/sections/common/format/fmt";

/** Sequential heat tint (one hue, stronger = worse). */
export function heatTint(theme, v) {
  if (!Number.isFinite(v)) return "transparent";
  return alpha(
    theme.palette.error.main,
    0.03 + 0.32 * Math.max(0, Math.min(1, v)),
  );
}

const HEAT_WORDS = {
  latency: "no latency",
  interruptions: "no interruptions",
};

/** The call line under the pass / fail mark: `0.86 s · ⚡1` (+ heat value). */
function CallLine({ call, heat, cost }) {
  const main =
    heat === "latency"
      ? `p90 ${fmtLatency(call.p90)}`
      : heat === "cost"
        ? fmtCost(cost)
        : fmtLatency(call.p50);
  const tip = [
    `Latency p50 ${fmtLatency(call.p50)} · p90 ${fmtLatency(call.p90)}`,
    `Kept talking over the caller ${call.kept ?? 0} of ${call.bargeIns ?? 0} barge-ins`,
    call.recordingMissing
      ? "Recording missing: latency reported by the provider"
      : null,
    call.failedTool ? "A tool call failed in this call" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <CustomTooltip show size="small" placement="bottom" title={tip}>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        data-testid="matrix-call-chip"
        sx={{
          height: 20,
          px: 0.5,
          borderRadius: 0.5,
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
          alignSelf: "flex-start",
          maxWidth: "100%",
          minWidth: 0,
        }}
      >
        <Iconify
          icon="mdi:phone-outline"
          width={12}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography
          typography="s3"
          noWrap
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {main}
        </Typography>
        {call.kept ? (
          <Typography
            typography="s3"
            color="warning.dark"
            noWrap
            title={`Interruption: kept talking over the caller ${call.kept} of ${call.bargeIns ?? 0} times`}
          >
            {`⚡${call.kept}`}
          </Typography>
        ) : null}
        {call.failedTool ? (
          <Box
            component="span"
            title="Tool failure: a tool call failed in this call"
            sx={{ display: "inline-flex" }}
          >
            <Iconify
              icon="mdi:wrench-outline"
              width={12}
              sx={{ color: "error.main", flexShrink: 0 }}
            />
          </Box>
        ) : null}
      </Stack>
    </CustomTooltip>
  );
}
CallLine.propTypes = {
  call: PropTypes.object,
  heat: PropTypes.string,
  cost: PropTypes.number,
};

export const CELL_WIDTH = 132;
/** A run column grows to this when the page has room (MatrixGrid). */
export const CELL_MAX_WIDTH = 180;

/** Pass-rate tint (0 → red, 1 → green; alpha ≤ 0.10) or `transparent`. */
export function rateTint(theme, rate) {
  if (!Number.isFinite(rate)) return "transparent";
  if (rate >= 0.5)
    return alpha(theme.palette.success.main, 0.02 + (rate - 0.5) * 0.16);
  return alpha(theme.palette.error.main, 0.02 + (0.5 - rate) * 0.16);
}

const DOT = 10;

/**
 * One trial: ● pass · ○ fail · ⊘ didn't run · ! error · dashed = carried;
 * grey ● = done with no pass / fail, dashed grey ○ = not run (W28).
 */
export function TrialDot({ kind, carried, tooltip }) {
  const common = {
    width: DOT,
    height: DOT,
    borderRadius: "50%",
    flexShrink: 0,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    boxSizing: "border-box",
  };
  let dot;
  if (kind === "pass")
    dot = <Box sx={{ ...common, bgcolor: "success.main" }} />;
  else if (kind === "fail")
    dot = (
      <Box
        sx={{ ...common, border: "1.5px solid", borderColor: "error.main" }}
      />
    );
  else if (kind === "error")
    dot = (
      <Box
        sx={{
          ...common,
          bgcolor: "error.main",
          color: "common.white",
          fontSize: 8,
          fontWeight: 700,
          lineHeight: 1,
        }}
      >
        !
      </Box>
    );
  else if (kind === "infra")
    dot = (
      <Box
        sx={{
          ...common,
          border: "1.5px solid",
          borderColor: "warning.main",
          position: "relative",
          "&::after": {
            content: '""',
            position: "absolute",
            width: 9,
            height: 1.5,
            bgcolor: "warning.main",
            transform: "rotate(-45deg)",
          },
        }}
      />
    );
  else if (kind === "done")
    dot = <Box sx={{ ...common, bgcolor: "text.disabled" }} />;
  else if (kind === "skipped")
    dot = (
      <Box
        sx={{
          ...common,
          border: "1.5px dashed",
          borderColor: "text.disabled",
        }}
      />
    );
  else
    dot = (
      <Box
        sx={{
          ...common,
          border: "1.5px solid",
          borderColor: "text.disabled",
          "@keyframes matrixPulse": {
            "0%, 100%": { opacity: 0.35 },
            "50%": { opacity: 1 },
          },
          animation: "matrixPulse 1.2s ease-in-out infinite",
        }}
      />
    );
  return (
    <CustomTooltip show size="small" placement="top" arrow title={tooltip}>
      <Box
        component="span"
        data-trial={kind ?? "pending"}
        sx={{
          display: "inline-flex",
          p: "2px",
          borderRadius: "50%",
          outline: carried ? "1px dashed" : "none",
          outlineColor: "text.secondary",
          outlineOffset: -1,
        }}
      >
        {dot}
      </Box>
    </CustomTooltip>
  );
}

TrialDot.propTypes = {
  kind: PropTypes.string,
  carried: PropTypes.bool,
  tooltip: PropTypes.string,
};

const KIND_WORD = {
  pass: "passed",
  fail: "failed",
  error: "error · counted as a fail",
  infra: "didn't run · not counted",
  done: "done · no pass/fail result",
  skipped: "not run",
  pending: "running",
};

/** `Trial 2 · failed · order_found ✕ · $0.010 · 9.1 s`. */
export function trialTooltip(cell, i, { carriedFrom } = {}) {
  const kind = cellTrialKind(cell, i);
  const parts = [`Trial ${i + 1}`, KIND_WORD[kind]];
  const failed = cell?.failed_evals?.[i];
  const first = Array.isArray(failed) ? failed[0] : failed;
  if ((kind === "fail" || kind === "error") && first) parts.push(`${first} ✕`);
  const err = cell?.error?.[i];
  if (kind === "error" && err?.text)
    parts.push(
      err.timeout_s != null
        ? `timed out at step ${err.step}`
        : `step ${err.step} · ${err.text}`,
    );
  if (kind !== "infra" && kind !== "pending" && kind !== "skipped") {
    const c = cell?.cost_usd?.[i];
    const w = cell?.wall_ms?.[i];
    if (Number.isFinite(c)) parts.push(fmtMoney(c));
    if (Number.isFinite(w)) parts.push(fmtDuration(w));
  }
  const text = parts.join(" · ");
  return carriedFrom
    ? `${text}. Carried from ${carriedFrom} (not rerun)`
    : text;
}

/**
 * One row × run cell: trial dots, pass-rate tint, compact TraceChip and a
 * 2 px cost bar (relative to the row's most expensive run).
 */
const MatrixCell = ({
  cell,
  runLabel,
  rowLabel,
  runKind,
  rowMaxCost,
  showCost,
  dense,
  focused,
  notInVersion,
  onOpen,
  onHover,
  onLeave,
  onMenu,
  cellId,
  call,
  heat = "result",
  heatValue,
  metricNames,
  metricDefs,
}) => {
  const height = dense ? 40 : 56;
  if (!cell)
    return (
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={notInVersion ? `Not in ${notInVersion}` : "Not run"}
      >
        <Box
          role="gridcell"
          id={cellId}
          aria-label={`${runLabel} ${rowLabel}: not run`}
          sx={{
            height,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: "action.hover",
            borderRight: "1px solid",
            borderBottom: "1px solid",
            borderColor: "divider",
            outline: focused ? "2px solid" : "none",
            outlineColor: "primary.main",
            outlineOffset: -2,
          }}
        >
          <Typography typography="s2" color="text.disabled">
            —
          </Typography>
        </Box>
      </CustomTooltip>
    );

  const n = cellNumbers(cell);
  // A text-only call has no audio, so latency / interruption heat
  // has nothing to colour: `—` with the reason.
  if (call?.textOnly && HEAT_WORDS[heat])
    return (
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`Text only · ${HEAT_WORDS[heat]}, so no ${heat === "latency" ? "latency" : "count"} here. Task completion: ${n.pass}/${n.scored}.`}
      >
        <Box
          role="gridcell"
          id={cellId}
          aria-label={`${runLabel} ${rowLabel}: text only, no ${heat}`}
          onClick={onOpen}
          sx={{
            height,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            bgcolor: "action.hover",
            borderRight: "1px solid",
            borderBottom: "1px solid",
            borderColor: "divider",
            outline: focused ? "2px solid" : "none",
            outlineColor: "primary.main",
            outlineOffset: -2,
          }}
        >
          <Typography typography="s2" color="text.disabled">
            —
          </Typography>
        </Box>
      </CustomTooltip>
    );
  const trials = cell.trials ?? [];
  // W28: a try scored by an eval with no pass / fail rule shows the row's
  // score (as the Data tab does), never a blank `—`.
  const unscored = unscoredResultText(cell, metricNames, metricDefs);
  const valueText = n.scored
    ? `${n.pass}/${n.scored}`
    : cellScoreList(cell, metricNames, metricDefs)[0]?.text ?? "—";
  const carriedFrom = cell.carried_from ?? null;
  const chip = cellTraceChipProps(cell, 1, { sourceKind: runKind });
  const compactLabel = String(chip.label ?? "").split(" · ")[0];
  const costShare =
    showCost && Number.isFinite(n.cost) && rowMaxCost > 0
      ? Math.max(0.04, n.cost / rowMaxCost)
      : 0;
  return (
    <Box
      role="gridcell"
      id={cellId}
      aria-label={
        unscored
          ? `${runLabel} ${rowLabel}: ${unscored}`
          : `${runLabel} ${rowLabel}: ${n.pass} of ${n.scored} trials passed`
      }
      onClick={onOpen}
      onContextMenu={
        onMenu
          ? (e) => {
              e.preventDefault();
              onMenu(e);
            }
          : undefined
      }
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
      sx={(t) => ({
        position: "relative",
        height,
        px: 1,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: dense ? 0.25 : 0.5,
        cursor: "pointer",
        bgcolor:
          heat === "result" ? rateTint(t, n.rate) : heatTint(t, heatValue),
        borderRight: "1px solid",
        borderBottom: "1px solid",
        borderColor: "divider",
        outline: focused ? `2px solid ${t.palette.primary.main}` : "none",
        outlineOffset: -2,
        "&:hover": { boxShadow: `inset 0 0 0 1px ${t.palette.divider}` },
      })}
    >
      <Stack direction="row" alignItems="center" gap={0.25}>
        {trials.map((t, i) => (
          <TrialDot
            // eslint-disable-next-line react/no-array-index-key
            key={i}
            kind={cellTrialKind(cell, i)}
            carried={Boolean(carriedFrom)}
            tooltip={trialTooltip(cell, i, { carriedFrom })}
          />
        ))}
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ ml: "auto", fontVariantNumeric: "tabular-nums" }}
        >
          {valueText}
        </Typography>
      </Stack>
      {call && !call.textOnly ? (
        <CallLine call={call} heat={heat} cost={n.cost} />
      ) : call?.textOnly ? (
        <Typography typography="s3" color="text.secondary" noWrap>
          Text only
        </Typography>
      ) : (
        !dense && (
          <Box
            sx={{ minWidth: 0, display: "flex" }}
            onClick={(e) => e.stopPropagation()}
          >
            <TraceChip
              state={chip.state}
              label={compactLabel || chip.label}
              mode={chip.mode}
              tooltip={chip.tooltip}
              compact
              onOpen={onOpen}
            />
          </Box>
        )
      )}
      {costShare > 0 && (
        <Box
          sx={{
            position: "absolute",
            left: 0,
            bottom: 0,
            height: 2,
            width: `${Math.round(costShare * 100)}%`,
            bgcolor: "text.disabled",
            opacity: 0.6,
          }}
        />
      )}
    </Box>
  );
};

MatrixCell.propTypes = {
  cell: PropTypes.object,
  runLabel: PropTypes.string,
  rowLabel: PropTypes.string,
  runKind: PropTypes.string,
  rowMaxCost: PropTypes.number,
  showCost: PropTypes.bool,
  dense: PropTypes.bool,
  focused: PropTypes.bool,
  notInVersion: PropTypes.string,
  onOpen: PropTypes.func,
  // IT-R4 G6 P7: right-click → `Side by side with baseline` (cell menu).
  onMenu: PropTypes.func,
  onHover: PropTypes.func,
  onLeave: PropTypes.func,
  cellId: PropTypes.string,
  // callStats of a voice call, the heat mode and its 0–1 value.
  call: PropTypes.object,
  heat: PropTypes.string,
  heatValue: PropTypes.number,
  // W28: `{metric key: eval name}` for a cell's scores (model.metricNames).
  metricNames: PropTypes.object,
  // `{metric key: metric def}`: a choice eval's score reads its label
  // (model.metricDefs).
  metricDefs: PropTypes.object,
};

export default memo(MatrixCell);
