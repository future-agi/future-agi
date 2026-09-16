import PropTypes from "prop-types";
import { Box, Stack, Typography, Popover } from "@mui/material";

import { BUILD_TONES } from "./buildTones";
import { BUILD_HEADER_COPY } from "./build.constants";
import { PIPELINE_PHASES } from "./buildPipeline.constants";
import PipelineRow, { STEP_SHAPE } from "./PipelineRow";

/*
  The full pipeline, opened from the milestone pill.

  The four milestones summarise; the twelve steps say what actually happened to
  arrive at them. Rendered as a popover so the header stays compact — the
  ninety-per-cent case is "glance, keep working", not "audit the pipeline".
*/
export default function PipelinePopover({ anchor, onClose, pipeline = [], summary }) {
  const failed = !!summary?.failed;
  const running = !!summary?.running;
  const ready = summary?.done === summary?.total;

  return (
    <Popover
      open={!!anchor}
      anchorEl={anchor}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      transformOrigin={{ vertical: "top", horizontal: "center" }}
      PaperProps={{
        sx: {
          mt: 1, width: 420, borderRadius: 1.5, backgroundImage: "none",
          border: "1px solid", borderColor: "divider",
          boxShadow: (t) => (t.palette.mode === "dark"
            ? "0 12px 40px rgba(0,0,0,0.4)"
            : "0 12px 40px rgba(16,24,40,0.12)"),
        },
      }}
    >
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 2, py: 1.5, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold", lineHeight: 1.2 }}>
            {BUILD_HEADER_COPY.popoverTitle}
          </Typography>
          <Typography
            sx={{
              typography: "s3", lineHeight: 1.2, mt: 0.25,
              color: failed ? BUILD_TONES.red : "text.subtitle",
              fontWeight: failed ? "fontWeightSemiBold" : "fontWeightRegular",
            }}
          >
            {summary?.label}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={0.625}>
          <Box
            sx={{
              width: 7, height: 7, borderRadius: "50%",
              bgcolor: failed ? BUILD_TONES.red : running ? BUILD_TONES.amber : BUILD_TONES.green,
            }}
          />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {failed
              ? BUILD_HEADER_COPY.halted
              : running
                ? BUILD_HEADER_COPY.running
                : ready
                  ? BUILD_HEADER_COPY.readyShort
                  : BUILD_HEADER_COPY.paused}
          </Typography>
        </Stack>
      </Stack>

      <Box sx={{ maxHeight: 480, overflowY: "auto", py: 1 }}>
        {PIPELINE_PHASES.map((phase) => {
          const rows = pipeline.filter((step) => step.phase === phase.id);
          if (!rows.length) return null;
          return (
            <Box key={phase.id} sx={{ py: 1 }}>
              <Typography
                sx={{
                  px: 2, mb: 0.5, typography: "s3", fontWeight: "fontWeightBold", letterSpacing: 0.5,
                  color: "text.disabled", textTransform: "uppercase",
                }}
              >
                {phase.label}
              </Typography>
              <Stack>
                {rows.map((step) => <PipelineRow key={step.id} step={step} />)}
              </Stack>
            </Box>
          );
        })}
      </Box>
    </Popover>
  );
}

PipelinePopover.propTypes = {
  anchor: PropTypes.instanceOf(Element),
  onClose: PropTypes.func,
  pipeline: PropTypes.arrayOf(STEP_SHAPE),
  summary: PropTypes.shape({
    done: PropTypes.number,
    total: PropTypes.number,
    running: PropTypes.bool,
    failed: STEP_SHAPE,
    label: PropTypes.string,
  }),
};
