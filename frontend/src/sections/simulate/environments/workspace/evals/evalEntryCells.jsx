import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { EVAL_ENTRY_SHAPE, costLabel, inputRowsOf, sourceLabel } from "./evalEntry";

const MONO = "ui-monospace, Menlo, monospace";

const chipSx = {
  height: 20,
  fontSize: 10,
  fontWeight: 600,
  bgcolor: "background.neutral",
  color: "text.secondary",
  "& .MuiChip-label": { px: 0.75 },
};

// Library/Custom and the cost line, both read straight off the entry (P25):
// "0.5 credits per run" or "0.5 credits per run + judge tokens", built from
// `credits_per_run` and `charges_judge_tokens` — never from `eval_type` or the
// name. `runMode` (the picker opened from inside a run) swaps the cost chip to
// "0.5 credits per call graded" / "… + judge tokens" — see `costLabel`.
export function EvalEntryChips({ entry, runMode = false }) {
  return (
    <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
      <Chip size="small" label={sourceLabel(entry)} sx={chipSx} />
      <Chip size="small" label={costLabel(entry, runMode)} sx={chipSx} />
    </Stack>
  );
}
EvalEntryChips.propTypes = { entry: EVAL_ENTRY_SHAPE, runMode: PropTypes.bool };

// "required key → what fills it", one row per `inputs[]` entry, in the order the
// API sent them. The right-hand side is `label` and nothing else (P1): the raw
// source name (voice_recording, scenario_columns.situation.value) is never shown.
export function EvalInputs({ entry, heading = "INPUTS" }) {
  const rows = inputRowsOf(entry);
  if (rows.length === 0) return null;
  return (
    <Box>
      {heading && (
        <Typography sx={{ fontSize: 10, color: "text.disabled", letterSpacing: 0.3, mb: 0.75 }}>
          {heading}
        </Typography>
      )}
      <Stack spacing={0.75}>
        {rows.map((row) => (
          <Stack key={row.key} direction="row" alignItems="center" spacing={1}>
            <Chip
              size="small"
              label={`{{${row.key}}}`}
              sx={{ ...chipSx, height: 22, fontFamily: MONO, fontWeight: 400 }}
            />
            <Iconify
              icon="solar:arrow-right-linear"
              width={13}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s3", color: "text.primary", minWidth: 0 }}>
              {row.label}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}
EvalInputs.propTypes = { entry: EVAL_ENTRY_SHAPE, heading: PropTypes.string };
