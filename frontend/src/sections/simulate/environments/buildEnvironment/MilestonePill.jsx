import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { BUILD_TONES } from "./buildTones";
import { BUILD_HEADER_COPY } from "./build.constants";
import { STEP_SHAPE } from "./PipelineRow";

// The header fingerprint. A single at-a-glance snapshot of the build pipeline,
// rendered as a button so it can anchor the pipeline popover (the designer
// declared `pipeAnchor` but never wired it — the pill is the only sensible
// anchor). Failed → red; running or not-yet-built → neutral pill with a pulsing
// dot; otherwise green "Ready to run".
export default function MilestonePill({ pipeline = [], summary, setupDone, onClick }) {
  const running = pipeline.find((st) => st.status === "running") || null;

  const content = (() => {
    if (summary?.failed) {
      return (
        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{
            px: 1.25, py: 0.625, borderRadius: 999,
            border: "1px solid", borderColor: alpha(BUILD_TONES.red, 0.4),
            bgcolor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: BUILD_TONES.red }} />
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: BUILD_TONES.red }}>
            {BUILD_HEADER_COPY.failedAt(summary.failed.label)}
          </Typography>
        </Stack>
      );
    }
    if (running || !setupDone) {
      return (
        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{ px: 1.25, py: 0.625, borderRadius: 999, border: "1px solid", borderColor: "divider" }}
        >
          <Box
            sx={{
              width: 6, height: 6, borderRadius: "50%", bgcolor: BUILD_TONES.accent, flexShrink: 0,
              animation: "chip-pulse 1.4s ease-in-out infinite",
              "@keyframes chip-pulse": {
                "0%,100%": { opacity: 0.4, transform: "scale(1)" },
                "50%": { opacity: 1, transform: "scale(1.15)" },
              },
            }}
          />
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
            {running?.label || BUILD_HEADER_COPY.setupBuilding}
          </Typography>
        </Stack>
      );
    }
    return (
      <Stack
        direction="row" alignItems="center" spacing={0.75}
        sx={{
          px: 1.125, py: 0.5, borderRadius: 999,
          border: "1px solid", borderColor: alpha(BUILD_TONES.green, 0.35),
          bgcolor: (t) => alpha(BUILD_TONES.green, t.palette.mode === "dark" ? 0.14 : 0.08),
        }}
      >
        <Iconify icon="solar:check-circle-bold" width={13} sx={{ color: BUILD_TONES.green }} />
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: BUILD_TONES.green }}>
          {BUILD_HEADER_COPY.ready}
        </Typography>
      </Stack>
    );
  })();

  return (
    <Box
      role="button"
      tabIndex={0}
      aria-haspopup="dialog"
      aria-label={BUILD_HEADER_COPY.popoverTitle}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick?.(e);
        }
      }}
      sx={{ display: "inline-flex", cursor: "pointer" }}
    >
      {content}
    </Box>
  );
}

MilestonePill.propTypes = {
  pipeline: PropTypes.arrayOf(STEP_SHAPE),
  summary: PropTypes.shape({
    done: PropTypes.number,
    total: PropTypes.number,
    running: PropTypes.bool,
    failed: STEP_SHAPE,
    label: PropTypes.string,
  }),
  setupDone: PropTypes.bool,
  onClick: PropTypes.func,
};
