import PropTypes from "prop-types";
import { alpha, keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

import { BUILD_TONES } from "../buildTones";
import { STEP_DURATION } from "../buildPipeline.constants";
import { PIPELINE_CHECKS_COPY } from "../build.constants";

const rowIn = keyframes`
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
`;

const pulseRing = keyframes`
  0%,100% { box-shadow: 0 0 0 3px ${alpha(BUILD_TONES.accent, 0.18)}; }
  50%     { box-shadow: 0 0 0 6px ${alpha(BUILD_TONES.accent, 0.28)}; }
`;

export default function TimelineRow({ step, first, last, elapsed, index }) {
  const done = step.status === "done";
  const running = step.status === "running";
  const failed = step.status === "failed";
  const pending = !done && !running && !failed;

  const tone = failed ? BUILD_TONES.red : done ? BUILD_TONES.green : running ? BUILD_TONES.accent : null;
  const durationLabel = done
    ? `${STEP_DURATION[step.id]?.toFixed(1) || "—"}s`
    : running
      ? (elapsed / 1000).toFixed(1) + "s"
      : "";

  return (
    <Stack
      direction="row" spacing={1.5}
      sx={{
        position: "relative",
        pt: first ? 0 : 0.75,
        pb: last ? 0 : 0.75,
        animation: `${rowIn} 0.32s ease-out both`,
        animationDelay: `${index * 60}ms`,
      }}
    >
      {/* ── rail column ── */}
      <Box
        sx={{
          position: "relative", width: 30, flexShrink: 0,
          display: "flex", flexDirection: "column", alignItems: "center",
        }}
      >
        {/* connector above */}
        {!first && (
          <Box
            sx={{
              position: "absolute", top: 0, height: 12, width: 2, borderRadius: 999,
              bgcolor: done ? alpha(BUILD_TONES.green, 0.45) : (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.08),
            }}
          />
        )}

        {/*
          Status medallion.

          Circles, not squares — the square version looked like a status LED
          from a rack-mounted appliance, and eight of them stacked reads as
          heavy. A circle sits on the rail more naturally and carries the tick
          without the visual weight.

          The tick is inline SVG, not Iconify, because Iconify fetches its
          glyphs asynchronously and the first paint of a completed row showed
          "just a green box" while the icon was loading — which is what you
          were seeing.
        */}
        <Box
          sx={{
            mt: first ? 0 : 1.5,
            width: 24, height: 24, borderRadius: "50%", display: "grid", placeItems: "center",
            flexShrink: 0,
            bgcolor: done ? (t) => alpha(BUILD_TONES.green, t.palette.mode === "dark" ? 0.2 : 0.14)
              : failed ? (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.2 : 0.14)
                : "background.paper",
            border: done ? `1.5px solid ${alpha(BUILD_TONES.green, 0.55)}`
              : failed ? `1.5px solid ${alpha(BUILD_TONES.red, 0.55)}`
                : running ? `2px solid ${BUILD_TONES.accent}`
                  : pending ? (t) => `1.5px dashed ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.2 : 0.18)}`
                    : "none",
            color: done ? BUILD_TONES.green : failed ? BUILD_TONES.red : (tone || "text.disabled"),
            animation: running ? `${pulseRing} 1.8s ease-in-out infinite` : "none",
            transition: "background-color 0.3s ease, border-color 0.3s ease",
          }}
        >
          {done && (
            <Box
              component="svg"
              viewBox="0 0 24 24" fill="none" stroke={BUILD_TONES.green}
              strokeWidth={3.25} strokeLinecap="round" strokeLinejoin="round"
              sx={{ width: 13, height: 13, display: "block" }}
            >
              <polyline points="5,12.5 10,17.5 19,7" />
            </Box>
          )}
          {failed && (
            <Box
              component="svg"
              viewBox="0 0 24 24" fill="none" stroke={BUILD_TONES.red}
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
                bgcolor: BUILD_TONES.accent,
                animation: `${pulseRing} 1.2s ease-in-out infinite`,
              }}
            />
          )}
          {pending && (
            <Box
              sx={{
                width: 5, height: 5, borderRadius: "50%",
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.2 : 0.18),
              }}
            />
          )}
        </Box>

        {/* connector below */}
        {!last && (
          <Box
            sx={{
              flex: 1, width: 2, borderRadius: 999, mt: 0.5,
              minHeight: 20,
              bgcolor: done ? alpha(BUILD_TONES.green, 0.45) : (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.08),
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
          px: 1.5, py: 1.125, borderRadius: 1.25,
          bgcolor: running ? (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.08 : 0.04) : "transparent",
          border: running ? "1px solid" : "1px solid transparent",
          borderColor: running ? alpha(BUILD_TONES.accent, 0.3) : "transparent",
          transition: "background-color 0.3s ease, border-color 0.3s ease",
        }}
      >
        <Stack direction="row" alignItems="baseline" spacing={1}>
          <Typography
            sx={{
              typography: "s2", fontWeight: "fontWeightBold",
              color: pending ? "text.subtitle" : "text.primary",
            }}
          >
            {step.label}
          </Typography>
          <Box flex={1} />
          {durationLabel && (
            <Typography
              sx={{
                typography: "s3", fontVariantNumeric: "tabular-nums",
                color: done ? "text.disabled" : running ? BUILD_TONES.accent : "text.disabled",
                fontWeight: running ? "fontWeightSemiBold" : "fontWeightMedium",
              }}
            >
              {durationLabel}
            </Typography>
          )}
          {pending && (
            <Typography sx={{ typography: "s3", color: "text.disabled" }}>
              {PIPELINE_CHECKS_COPY.queued}
            </Typography>
          )}
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {step.detail}
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
  }),
  first: PropTypes.bool,
  last: PropTypes.bool,
  elapsed: PropTypes.number,
  index: PropTypes.number,
};
