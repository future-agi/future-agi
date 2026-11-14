import PropTypes from "prop-types";
import { keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

import { PIPELINE_CHECKS_COPY } from "../build.constants";

const rowIn = keyframes`
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
`;

const pulseDot = keyframes`
  0%,100% { opacity: 1; }
  50%     { opacity: 0.35; }
`;

export default function TimelineRow({ step, first, last, index }) {
  const done = step.status === "done";
  const running = step.status === "running";
  const failed = step.status === "failed";
  const pending = !done && !running && !failed;

  // Status picks the role: done is success, running is info, failed is danger.
  const role = failed ? "danger" : done ? "success" : running ? "info" : null;

  return (
    <Stack
      direction="row" spacing={1.5}
      sx={{
        position: "relative",
        pt: first ? 0 : 1,
        pb: last ? 0 : 1,
        animation: `${rowIn} 0.32s ease-out both`,
        animationDelay: `${index * 60}ms`,
      }}
    >
      {/* ── rail column ── */}
      <Box
        sx={{
          position: "relative", width: 32, flexShrink: 0,
          display: "flex", flexDirection: "column", alignItems: "center",
        }}
      >
        {/* connector above */}
        {!first && (
          <Box
            sx={{
              position: "absolute", top: 0, height: 16, width: 2, borderRadius: "1px",
              bgcolor: done ? "status.success.border" : "divider",
            }}
          />
        )}

        {/*
          Status medallion.

          Circles, not squares — a circle sits on the rail more naturally and
          carries the tick without the visual weight. Done, running and failed
          take the tinted status fill; a step that has not started is a dashed
          outline.

          The tick is inline SVG, not Iconify, because Iconify fetches its
          glyphs asynchronously and the first paint of a completed row showed
          "just a green box" while the icon was loading.
        */}
        <Box
          sx={{
            mt: first ? 0.5 : 2,
            width: 24, height: 24, borderRadius: "50%", display: "grid", placeItems: "center",
            flexShrink: 0,
            bgcolor: role ? `status.${role}.bg` : "background.paper",
            border: pending ? "1.5px dashed" : "none",
            borderColor: "divider",
            color: role ? `status.${role}.fg` : "text.disabled",
            transition: "background-color 0.3s ease, border-color 0.3s ease",
          }}
        >
          {done && (
            <Box
              component="svg"
              viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth={3.25} strokeLinecap="round" strokeLinejoin="round"
              sx={{ width: 12, height: 12, display: "block" }}
            >
              <polyline points="5,12.5 10,17.5 19,7" />
            </Box>
          )}
          {failed && (
            <Box
              component="svg"
              viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth={3} strokeLinecap="round"
              sx={{ width: 12, height: 12, display: "block" }}
            >
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="18" y1="6" x2="6" y2="18" />
            </Box>
          )}
          {running && (
            <Box
              sx={{
                width: 8, height: 8, borderRadius: "50%",
                bgcolor: "status.info.solid",
                animation: `${pulseDot} 1.2s ease-in-out infinite`,
                "@media (prefers-reduced-motion: reduce)": { animation: "none" },
              }}
            />
          )}
          {pending && (
            <Box
              sx={{ width: 4, height: 4, borderRadius: "50%", bgcolor: "text.disabled" }}
            />
          )}
        </Box>

        {/* connector below */}
        {!last && (
          <Box
            sx={{
              flex: 1, width: 2, borderRadius: "1px", mt: 0.5,
              minHeight: 20,
              bgcolor: done ? "status.success.border" : "divider",
              transition: "background-color 0.4s ease",
            }}
          />
        )}
      </Box>

      {/* ── body ── */}
      <Box
        sx={{
          flex: 1, minWidth: 0,
          mt: first ? 0 : 1.5,
          px: 1.5, py: 0.5, borderRadius: "8px",
          bgcolor: running ? "action.hover" : "transparent",
          transition: "background-color 0.3s ease",
        }}
      >
        <Stack direction="row" alignItems="baseline" spacing={1}>
          <Typography
            sx={{
              typography: "s1", fontWeight: 500,
              color: pending ? "text.secondary" : "text.primary",
            }}
          >
            {step.label}
          </Typography>
          <Box flex={1} />
          {pending && (
            <Typography sx={{ typography: "s3", color: "text.disabled" }}>
              {PIPELINE_CHECKS_COPY.queued}
            </Typography>
          )}
        </Stack>
        <Typography
          sx={{ typography: "s3", color: failed ? "status.danger.fg" : "text.secondary", mt: 0.25 }}
        >
          {/* On the failed step, show why it failed (the job's failure message)
              rather than the generic step description — this is the "status part"
              that must read as stuck/failed. */}
          {failed ? step.failure?.detail || step.detail : step.detail}
        </Typography>
      </Box>
    </Stack>
  );
}

TimelineRow.propTypes = {
  step: PropTypes.shape({
    id: PropTypes.string,
    status: PropTypes.string,
    label: PropTypes.string,
    detail: PropTypes.string,
    failure: PropTypes.shape({ detail: PropTypes.string }),
  }),
  first: PropTypes.bool,
  last: PropTypes.bool,
  index: PropTypes.number,
};
