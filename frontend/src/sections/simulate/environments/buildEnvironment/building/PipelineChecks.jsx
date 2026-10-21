import PropTypes from "prop-types";
import { useMemo } from "react";
import { alpha, keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

import { BUILD_TONES } from "../buildTones";
import { PIPELINE_CHECKS_COPY } from "../build.constants";
import TimelineRow from "./TimelineRow";

/**
 * The build pipeline as a proper timeline, not a list of ticks.
 *
 * The plain checklist version was informative but flat — same shape row seven
 * times, no sense of a journey, no sense of *what* each step actually does
 * beyond its label. A real timeline earns its space:
 *
 *   Each step has its own icon that says what it is (a magnifier for reading,
 *   a database for seeding, a shield for validating). Status changes the tint
 *   rather than the icon, so the metaphor is consistent whether the step is
 *   done, running or waiting.
 *
 *   A rail connects the steps. It fills green as steps land — the row's
 *   background inherits a soft tint the moment it becomes the current one, so
 *   your eye lands on the right place without hunting.
 *
 *   A header bar summarises: how many of seven, how far along on a slim bar.
 *   That's the same summary the top pill shows, restated here at the size the
 *   panel calls for.
 */

const shimmer = keyframes`
  0%   { background-position: 0% 0; }
  100% { background-position: 200% 0; }
`;

export default function PipelineChecks({ pipeline }) {
  const all = useMemo(() => pipeline.filter((s) => s.phase === "setup"), [pipeline]);
  const total = all.length;
  const done = all.filter((s) => s.status === "done").length;
  const active = all.find((s) => s.status === "running");
  const percent = Math.round((done / Math.max(1, total)) * 100);

  /*
    Reveal steps as the pipeline reaches them, not all seven queued up front.
    Everything that has landed stays, the current running one appears, and the
    next queued step peeks in as a ghost row so you can see what's coming
    without the panel padding itself out with rows that haven't started. When
    all seven are done, the whole timeline is visible.
  */
  const rows = useMemo(() => {
    const runningIdx = all.findIndex((s) => s.status === "running");
    const failedIdx = all.findIndex((s) => s.status === "failed");
    if (failedIdx >= 0) return all.slice(0, failedIdx + 1);
    if (runningIdx >= 0) return all.slice(0, Math.min(all.length, runningIdx + 2));
    return all; /* everything done, or nothing running yet */
  }, [all]);

  if (!all.length) return null;

  return (
    <Box sx={{ px: 2.5, pt: 2.5, pb: 3 }}>
      {/* ── heading + progress ── */}
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.75 }}>
        <Typography
          sx={{
            typography: "s3", fontWeight: "fontWeightBold", color: "text.disabled",
            textTransform: "uppercase", letterSpacing: 0.5,
          }}
        >
          {PIPELINE_CHECKS_COPY.heading}
        </Typography>
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
          {done} of {total}
        </Typography>
      </Stack>
      <Box
        sx={{
          height: 3, borderRadius: 999, overflow: "hidden", mb: 2.5,
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
          position: "relative",
        }}
      >
        <Box
          sx={{
            height: "100%", width: `${percent}%`,
            bgcolor: done === total ? BUILD_TONES.green : BUILD_TONES.accent,
            transition: "width 0.4s ease",
          }}
        />
        {active && (
          <Box
            sx={{
              position: "absolute", inset: 0,
              background: (t) =>
                `linear-gradient(90deg, transparent 0%, ${alpha(t.palette.common.white, 0.18)} 50%, transparent 100%)`,
              backgroundSize: "220% 100%",
              animation: `${shimmer} 1.6s linear infinite`,
            }}
          />
        )}
      </Box>

      {/* ── timeline ── */}
      <Box sx={{ position: "relative" }}>
        {rows.map((step, i) => (
          <TimelineRow
            key={step.id}
            step={step}
            first={i === 0}
            last={i === rows.length - 1}
            index={i}
          />
        ))}
      </Box>
    </Box>
  );
}

PipelineChecks.propTypes = { pipeline: PropTypes.array };
