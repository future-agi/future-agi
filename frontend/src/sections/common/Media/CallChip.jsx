// A call in a cell: `● 1 m 48 s · p50 900 ms · 1 interruption`;
// `Text only · 9 turns`; `compact` (72 px Matrix cells) `900 ms · ⚡1`.
// The dot is filled for a full-voice call with a recording, hollow for
// text only, amber ring when the recording is missing.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { NUM_SX } from "src/sections/common/format/fmt";
import { callChipText, callChipTooltip } from "./mediaModel";
import MiniWaveform from "./MiniWaveform";

// `wave` (Comfortable rows): the agent track's waveform above the chip.
export default function CallChip({
  call,
  compact = false,
  onOpen,
  wave = false,
}) {
  const peaks = call.recording?.agent?.peaks;
  const textOnly = call.depth === "text_only";
  const missing = !textOnly && !call.recording;
  const Comp = onOpen ? "button" : "span";
  const chip = (
    <CustomTooltip
      show
      size="small"
      arrow
      title={<Box sx={{ whiteSpace: "pre-line" }}>{callChipTooltip(call)}</Box>}
    >
      <Stack
        component={Comp}
        type={onOpen ? "button" : undefined}
        direction="row"
        alignItems="center"
        gap={0.75}
        onClick={
          onOpen
            ? (e) => {
                e.stopPropagation();
                onOpen();
              }
            : undefined
        }
        data-call-chip={textOnly ? "text" : missing ? "missing" : "voice"}
        sx={{
          p: 0,
          border: 0,
          bgcolor: "transparent",
          font: "inherit",
          color: "text.primary",
          cursor: onOpen ? "pointer" : "default",
          minWidth: 0,
          maxWidth: "100%",
        }}
      >
        <Box
          aria-hidden
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            flexShrink: 0,
            border: "1.5px solid",
            borderColor: missing
              ? "warning.main"
              : textOnly
                ? "text.disabled"
                : "text.primary",
            bgcolor: textOnly || missing ? "transparent" : "text.primary",
          }}
        />
        <Typography typography={compact ? "s3" : "s2"} noWrap sx={NUM_SX}>
          {callChipText(call, { compact })}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
  if (!wave || compact || !Array.isArray(peaks) || !peaks.length) return chip;
  return (
    <Stack gap={0.25} sx={{ minWidth: 0 }} data-call-wave="">
      <Box sx={{ color: "text.secondary", lineHeight: 0 }} aria-hidden>
        <MiniWaveform peaks={peaks} width={120} height={18} />
      </Box>
      {chip}
    </Stack>
  );
}

CallChip.propTypes = {
  call: PropTypes.object.isRequired,
  compact: PropTypes.bool,
  onOpen: PropTypes.func,
  wave: PropTypes.bool,
};
