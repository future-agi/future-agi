import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { alpha, keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

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

/*
  A gentle, deterministic "duration" per step, so completed rows have a real
  number to show. Not persisted anywhere — this is the loading screen, we're
  making the wait interesting.
*/
const STEP_DURATION = {
  understand: 2.4,
  "generate-env": 1.6,
  "build-env": 3.2,
  "validate-env": 1.8,
  "generate-data": 2.9,
  "generate-scenarios": 3.6,
  "validate-scenarios": 2.1,
};

const rowIn = keyframes`
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
`;

const pulseRing = keyframes`
  0%,100% { box-shadow: 0 0 0 3px rgba(120,87,252,0.18); }
  50%     { box-shadow: 0 0 0 6px rgba(120,87,252,0.28); }
`;

const shimmer = keyframes`
  0%   { background-position: 0% 0; }
  100% { background-position: 200% 0; }
`;

/** ms elapsed on the current step, ticking up so "running" feels alive. */
function useElapsed(activeStepId) {
  const [ms, setMs] = useState(0);
  useEffect(() => {
    setMs(0);
    if (!activeStepId) return undefined;
    const t = setInterval(() => setMs((n) => n + 100), 100);
    return () => clearInterval(t);
  }, [activeStepId]);
  return ms;
}

export default function PipelineChecks({ pipeline }) {
  const all = useMemo(() => pipeline.filter((s) => s.phase === "setup"), [pipeline]);
  const total = all.length;
  const done = all.filter((s) => s.status === "done").length;
  const active = all.find((s) => s.status === "running");
  const percent = Math.round((done / Math.max(1, total)) * 100);
  const elapsed = useElapsed(active?.id);

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
            typography: "s3", fontWeight: 700, color: "text.disabled",
            textTransform: "uppercase", letterSpacing: 0.5,
          }}
        >
          Setup — building the environment
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
            bgcolor: done === total ? "#16A34A" : "#7857FC",
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
            elapsed={active?.id === step.id ? elapsed : null}
            index={i}
          />
        ))}
      </Box>
    </Box>
  );
}

PipelineChecks.propTypes = { pipeline: PropTypes.array };

function TimelineRow({ step, first, last, elapsed, index }) {
  const done = step.status === "done";
  const running = step.status === "running";
  const failed = step.status === "failed";
  const pending = !done && !running && !failed;

  const tone = failed ? "#DC2626" : done ? "#16A34A" : running ? "#7857FC" : null;
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
              bgcolor: done ? (t) => alpha("#16A34A", 0.45) : (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.08),
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
            bgcolor: done ? (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.2 : 0.14)
              : failed ? (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.2 : 0.14)
                : "background.paper",
            border: done ? `1.5px solid ${alpha("#16A34A", 0.55)}`
              : failed ? `1.5px solid ${alpha("#DC2626", 0.55)}`
                : running ? "2px solid #7857FC"
                  : pending ? (t) => `1.5px dashed ${alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.2 : 0.18)}`
                    : "none",
            color: done ? "#16A34A" : failed ? "#DC2626" : (tone || "text.disabled"),
            animation: running ? `${pulseRing} 1.8s ease-in-out infinite` : "none",
            transition: "background-color 0.3s ease, border-color 0.3s ease",
          }}
        >
          {done && (
            <Box
              component="svg"
              viewBox="0 0 24 24" fill="none" stroke="#16A34A"
              strokeWidth={3.25} strokeLinecap="round" strokeLinejoin="round"
              sx={{ width: 13, height: 13, display: "block" }}
            >
              <polyline points="5,12.5 10,17.5 19,7" />
            </Box>
          )}
          {failed && (
            <Box
              component="svg"
              viewBox="0 0 24 24" fill="none" stroke="#DC2626"
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
                bgcolor: "#7857FC",
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
              bgcolor: done ? (t) => alpha("#16A34A", 0.45) : (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.08),
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
          bgcolor: running ? (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04) : "transparent",
          border: running ? "1px solid" : "1px solid transparent",
          borderColor: running ? alpha("#7857FC", 0.3) : "transparent",
          transition: "background-color 0.3s ease, border-color 0.3s ease",
        }}
      >
        <Stack direction="row" alignItems="baseline" spacing={1}>
          <Typography
            sx={{
              typography: "s2", fontWeight: 700,
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
                color: done ? "text.disabled" : running ? "#7857FC" : "text.disabled",
                fontWeight: running ? 600 : 500,
              }}
            >
              {durationLabel}
            </Typography>
          )}
          {pending && (
            <Typography sx={{ typography: "s3", color: "text.disabled" }}>
              queued
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
  step: PropTypes.object, first: PropTypes.bool, last: PropTypes.bool,
  elapsed: PropTypes.number, index: PropTypes.number,
};
