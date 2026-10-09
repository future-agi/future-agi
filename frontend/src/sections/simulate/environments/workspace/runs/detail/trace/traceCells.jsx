import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { interpolateColorBasedOnScore } from "src/utils/utils";

import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import { PENDING_EVAL_STATUS, isBad } from "./traceTable.constants";

// MUI's wave shimmer: one 2s cycle, counted from when each bar mounts.
const SHIMMER_CYCLE_MS = 2000;

// The loading bar a cell shows in place of its value while the call runs: one
// text line tall, the full width of the cell's content, sitting where the
// value's text will land so every loading cell in a row lines up. Bars mount at
// different times (a group opened, a page loaded), so each starts its shimmer
// as far into the cycle as the page clock is, keeping every bar in step.
export function CellSkeleton() {
  const [phase] = useState(
    () => `-${Math.round(performance.now() % SHIMMER_CYCLE_MS)}ms`,
  );
  return (
    <Skeleton
      variant="rounded"
      width="100%"
      height={12}
      style={{ "--skeleton-phase": phase }}
      sx={{
        my: "4px",
        bgcolor: "background.neutral",
        "&::after": { animationDelay: "var(--skeleton-phase)" },
      }}
    />
  );
}

// Numeric metric cell with subtle severity tinting: only bad values turn red and
// pick up a warning glyph, so the eye lands on them without the column shifting.
// While the call is still running an empty value shows the loading skeleton,
// matching the eval cells, rather than a dash.
export function MetricValue({ metric, value, suffix = "", loading = false }) {
  if (value == null && loading) return <CellSkeleton />;
  if (value == null) {
    return <Typography component="span" sx={{ typography: "s2", color: "text.disabled" }}>-</Typography>;
  }
  const bad = isBad(metric, typeof value === "number" ? value : Number(value));
  return (
    <Stack direction="row" alignItems="center" spacing={0.625}>
      {bad && (
        <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: BUILD_TONES.red, flexShrink: 0 }} />
      )}
      <Typography
        component="span"
        sx={{
          typography: "s2", fontVariantNumeric: "tabular-nums",
          color: bad ? BUILD_TONES.red : "text.secondary",
          fontWeight: bad ? 600 : 400,
        }}
      >
        {typeof value === "number" ? value.toLocaleString() : value}{suffix}
      </Typography>
    </Stack>
  );
}
MetricValue.propTypes = {
  metric: PropTypes.string,
  value: PropTypes.any,
  suffix: PropTypes.string,
  loading: PropTypes.bool,
};

// A single eval cell — a score heat-tint with the reason on hover. Choice evals
// carry no numeric score, so they render their label plainly instead.
export function Score({ result }) {
  if (result?.score == null) {
    return (
      <Box sx={{ p: 2, typography: "s2", color: "text.secondary" }}>
        {result?.label || "-"}
      </Box>
    );
  }
  const bgcolor = interpolateColorBasedOnScore(result.score, 1);
  return (
    <CustomTooltip show={!!result.reason} arrow title={result.reason || ""}>
      <Box
        sx={{
          position: "absolute", inset: 0,
          display: "flex", alignItems: "center",
          px: 2, py: 1.5, bgcolor, color: "text.primary",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", fontVariantNumeric: "tabular-nums" }}>
          {Math.round(result.score * 100)}%
        </Typography>
      </Box>
    </CustomTooltip>
  );
}
Score.propTypes = { result: PropTypes.object };

// How a scoring state that ended without a value reads in a cell, and the tip
// to fall back on when the server sent no reason.
const ENDED_UNSCORED = {
  failed: { text: "Failed", color: BUILD_TONES.red },
  timed_out: { text: "Timed out", color: BUILD_TONES.amber },
  skipped: { text: "Not scored", color: "text.disabled" },
};

// A cell for a score that will not come: a word in the state's tone, the reason
// on hover.
export function EndedUnscored({ state, tip, sx }) {
  const { text, color } = ENDED_UNSCORED[state];
  return (
    <CustomTooltip show arrow size="small" title={tip}>
      <Box component="span" sx={{ typography: "s2", color, ...sx }}>
        {text}
      </Box>
    </CustomTooltip>
  );
}
EndedUnscored.propTypes = {
  state: PropTypes.oneOf(Object.keys(ENDED_UNSCORED)).isRequired,
  tip: PropTypes.string.isRequired,
  sx: PropTypes.object,
};

// The CSAT cell. With a CSAT status from the server it says whether CSAT is
// still scoring, failed, timed out or was skipped; without one (an older
// payload) it loads while the call is live, as the other metric cells do.
export function CsatValue({ value, status, reason, callLive }) {
  if (status === "pending") return <CellSkeleton />;
  if (status === "failed")
    return <EndedUnscored state="failed" tip={reason || "CSAT could not be scored."} />;
  if (status === "timed_out")
    return <EndedUnscored state="timed_out" tip={reason || "CSAT timed out."} />;
  if (status === "skipped")
    return <EndedUnscored state="skipped" tip={reason || "CSAT was not scored."} />;
  return <MetricValue metric="csat" value={value} loading={!status && callLive} />;
}
CsatValue.propTypes = {
  value: PropTypes.number,
  status: PropTypes.string,
  reason: PropTypes.string,
  callLive: PropTypes.bool,
};

// An eval cell with no score, and why. A call still running, or an eval the
// backend marks pending, shows the loading bar. A failed, timed-out or skipped
// eval says so, with the reason on hover. A cancelled or failed call that was
// never scored, or an eval with no entry on a finished call (it doesn't apply
// to this scenario), shows a dash with why.
export function UnscoredEval({ result, callLive, callStatus }) {
  if ((!result && callLive) || result?.status === PENDING_EVAL_STATUS) {
    // The metric cells' top padding, so the row's loading bars line up.
    return (
      <Box sx={{ px: 2, py: 1.5 }}>
        <CellSkeleton />
      </Box>
    );
  }
  const status = result?.status;
  const reason = result?.reason;
  if (status === "failed" || status === "error") {
    const tip = reason ? `Evaluation failed: ${reason}` : "Evaluation failed";
    return <EndedUnscored state="failed" tip={tip} sx={{ display: "block", p: 2 }} />;
  }
  if (status === "timed_out" || status === "skipped") {
    const tip = reason || (status === "skipped" ? "Not scored" : "Scoring timed out");
    return <EndedUnscored state={status} tip={tip} sx={{ display: "block", p: 2 }} />;
  }
  let tip = "Not applicable to this scenario";
  if (result) tip = "Scored, but no value came back";
  else if (callStatus === "cancelled") tip = "Not evaluated: the call was cancelled";
  else if (callStatus === "failed") tip = "Not evaluated: the call failed";
  return (
    <CustomTooltip show arrow size="small" title={tip}>
      <Box sx={{ p: 2, typography: "s2", color: "text.disabled" }}>-</Box>
    </CustomTooltip>
  );
}
UnscoredEval.propTypes = {
  result: PropTypes.object,
  callLive: PropTypes.bool,
  // The call's execution_status — a cancelled or failed call was never scored.
  callStatus: PropTypes.string,
};

// A labelled attribute chip used inside the persona cell.
export function Field({ icon, label, value }) {
  if (value == null || value === "") return null;
  return (
    // The value wraps rather than truncating, so a long persona (traits) reads
    // in full; the icon and label stay pinned to its first line.
    <Stack
      direction="row" alignItems="flex-start" spacing={0.75}
      sx={{ px: 1, py: 0.5, borderRadius: 0.75, bgcolor: "background.neutral" }}
    >
      <Iconify icon={icon} width={13} sx={{ color: "text.subtitle", flexShrink: 0, mt: "3px" }} />
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>{label}:</Typography>
      <Typography
        sx={{
          typography: "s3", color: "text.primary", fontWeight: "fontWeightMedium",
          minWidth: 0, overflowWrap: "anywhere",
        }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
Field.propTypes = { icon: PropTypes.string, label: PropTypes.string, value: PropTypes.any };
