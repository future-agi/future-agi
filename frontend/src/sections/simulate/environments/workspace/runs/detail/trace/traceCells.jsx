import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { interpolateColorBasedOnScore } from "src/utils/utils";

import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import { isBad } from "./traceTable.constants";

// Numeric metric cell with subtle severity tinting: only bad values turn red and
// pick up a warning glyph, so the eye lands on them without the column shifting.
export function MetricValue({ metric, value, suffix = "" }) {
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
MetricValue.propTypes = { metric: PropTypes.string, value: PropTypes.any, suffix: PropTypes.string };

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

// A labelled attribute chip used inside the persona cell.
export function Field({ icon, label, value }) {
  if (value == null || value === "") return null;
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.75}
      sx={{ px: 1, py: 0.5, borderRadius: 0.75, bgcolor: "background.neutral" }}
    >
      <Iconify icon={icon} width={13} sx={{ color: "text.subtitle", flexShrink: 0 }} />
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{label}:</Typography>
      <Typography noWrap sx={{ typography: "s3", color: "text.primary", fontWeight: "fontWeightMedium" }}>{value}</Typography>
    </Stack>
  );
}
Field.propTypes = { icon: PropTypes.string, label: PropTypes.string, value: PropTypes.any };
