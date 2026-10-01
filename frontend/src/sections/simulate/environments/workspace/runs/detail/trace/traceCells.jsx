import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { interpolateColorBasedOnScore } from "src/utils/utils";

import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import { isBad } from "./traceTable.constants";

// The loading bar a cell shows in place of its value while the call runs: one
// text line tall, the full width of the cell's content, sitting where the
// value's text will land so every loading cell in a row lines up.
export function CellSkeleton() {
  return (
    <Skeleton
      variant="rounded"
      width="100%"
      height={12}
      sx={{ my: "4px", bgcolor: "background.neutral" }}
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

// An eval cell with no score, and why. A call still running, or an eval the
// backend marks pending, shows the loading bar;
// a failed, errored or skipped eval, or a cancelled or failed call that was
// never scored, shows a dash with why; anything else reads N/A — the table
// can't tell a check that doesn't apply to this scenario from one not graded
// yet.
export function UnscoredEval({ result, callLive, callStatus }) {
  if ((!result && callLive) || result?.status === "pending") {
    // The metric cells' top padding, so the row's loading bars line up.
    return (
      <Box sx={{ px: 2, py: 1.5 }}>
        <CellSkeleton />
      </Box>
    );
  }
  let text = "N/A";
  let tip = "Not applicable to this scenario";
  if (!result && callStatus === "cancelled") {
    text = "-";
    tip = "Not evaluated: the call was cancelled";
  } else if (!result && callStatus === "failed") {
    text = "-";
    tip = "Not evaluated: the call failed";
  } else if (result?.status === "failed" || result?.status === "error") {
    text = "-";
    tip = result.reason ? `Evaluation failed: ${result.reason}` : "Evaluation failed";
  } else if (result?.status === "skipped") {
    text = "-";
    tip = result.reason ? `Skipped: ${result.reason}` : "Skipped";
  }
  return (
    <CustomTooltip show arrow size="small" title={tip}>
      <Box sx={{ p: 2, typography: "s2", color: "text.disabled" }}>{text}</Box>
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
