import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Collapse, Fade } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * What a long-running thing is doing, while it does it — and afterwards.
 *
 * The version this replaces was six greyed-out sentences that ticked green in
 * turn, jammed into the top-left of an otherwise empty panel. It told you
 * something was happening and nothing about what. Worse, it evaporated the
 * moment it finished, so the one screen that could have explained where a
 * diagnosis came from existed for four seconds and was then gone.
 *
 * Two things fix that, and they are the same two the platform's optimization
 * step list already gets right:
 *
 *   A step reports what it found. "Classifying failures by domain" is a
 *   progress bar with words on it; "4 agent failures, 2 not the agent's" is the
 *   answer arriving early. The lines underneath are the working — the counts
 *   read, the evidence weighed — streamed in as the step runs, so the wait is
 *   spent reading rather than watching.
 *
 *   It survives. When the run finishes the trace collapses into one summary
 *   line and stays on the page, so "why does it say the scores are inflated"
 *   has an answer that is still there an hour later.
 */

const DOT = 22;

export default function RunTrace({
  steps = [],
  stepMs = 850,
  accent = "#7857FC",
  running = false,
  onDone,
  compact = false,
}) {
  /* -1 before anything starts, steps.length once every step has reported. */
  const [at, setAt] = useState(running ? 0 : steps.length);
  const [shown, setShown] = useState(running ? 0 : Infinity);
  const [elapsed, setElapsed] = useState([]);
  const started = useRef(null);

  const step = steps[at];
  const lines = step?.lines || [];

  /* Lines stream inside a step, then the step settles and the next begins.
     Timed off the number of lines so a step with real working to show gets the
     time to show it, rather than every step taking the same beat. */
  useEffect(() => {
    if (!running || at >= steps.length) return undefined;
    if (started.current === null) started.current = Date.now();

    if (shown < lines.length) {
      const t = setTimeout(() => setShown((n) => n + 1), Math.max(180, stepMs / (lines.length + 1)));
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => {
      setElapsed((e) => [...e, 120 + Math.round(stepMs * (0.6 + lines.length * 0.25))]);
      setAt((i) => i + 1);
      setShown(0);
    }, stepMs * 0.55);
    return () => clearTimeout(t);
  }, [running, at, shown, lines.length, steps.length, stepMs]);

  useEffect(() => {
    if (running && at >= steps.length && steps.length) {
      const t = setTimeout(() => onDone?.(), 320);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [running, at, steps.length, onDone]);

  return (
    <Stack sx={{ px: compact ? 0 : 1 }}>
      {steps.map((s, i) => {
        const done = i < at;
        const active = i === at && running;
        const last = i === steps.length - 1;
        const visible = done || active || !running;
        const stepLines = done || !running ? s.lines || [] : (s.lines || []).slice(0, shown);

        return (
          <Stack key={s.id || s.label} direction="row" spacing={1.5} sx={{ opacity: visible ? 1 : 0.35 }}>
            {/* rail */}
            <Stack alignItems="center" sx={{ width: DOT, flexShrink: 0 }}>
              <Box
                sx={{
                  width: DOT, height: DOT, borderRadius: "50%", display: "grid", placeItems: "center",
                  flexShrink: 0,
                  bgcolor: (t) => (done
                    ? alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1)
                    : active
                      ? alpha(accent, t.palette.mode === "dark" ? 0.16 : 0.1)
                      : "transparent"),
                  border: done || active ? "none" : "1px solid",
                  borderColor: "divider",
                }}
              >
                {done && <Iconify icon="solar:check-circle-bold" width={14} sx={{ color: "#16A34A" }} />}
                {active && (
                  <Box
                    sx={{
                      width: 9, height: 9, borderRadius: "50%",
                      border: "1.5px solid", borderColor: accent, borderTopColor: "transparent",
                      animation: "rt-spin 0.7s linear infinite",
                      "@keyframes rt-spin": { to: { transform: "rotate(360deg)" } },
                    }}
                  />
                )}
                {!done && !active && (
                  <Box sx={{ width: 4, height: 4, borderRadius: "50%", bgcolor: "text.disabled" }} />
                )}
              </Box>
              {!last && (
                <Box
                  sx={{
                    width: "1px", flex: 1, minHeight: 14,
                    bgcolor: done ? alpha("#16A34A", 0.35) : "divider",
                  }}
                />
              )}
            </Stack>

            {/* body */}
            <Box sx={{ flex: 1, minWidth: 0, pb: last ? 0 : 2 }}>
              {/*
                Row is: title/result on the left, elapsed pinned to the
                right. Earlier it all lived in a single wrap-row with a
                flex spacer before the time, so when the title + result
                filled the width the time wrapped onto its own line and
                landed mid-row visually. Pulling the time out to a sibling
                keeps it anchored to the corner regardless of how long
                the title runs.
              */}
              <Stack direction="row" alignItems="flex-start" spacing={1}>
                <Stack
                  direction="row" alignItems="baseline" spacing={1}
                  flexWrap="wrap" rowGap={0.25}
                  sx={{ flex: 1, minWidth: 0 }}
                >
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: active || done ? 700 : 600,
                      color: done || active ? "text.primary" : "text.disabled",
                    }}
                  >
                    {s.label}
                  </Typography>
                  {/* The finding, as soon as the step has one. This is
                      the part that makes waiting worth doing. */}
                  {done && s.result && (
                    <Fade in timeout={300}>
                      <Typography sx={{ typography: "s2", color: s.tone || "text.secondary", fontWeight: 600 }}>
                        {s.result}
                      </Typography>
                    </Fade>
                  )}
                </Stack>
                {done && elapsed[i] != null && (
                  <Typography
                    sx={{
                      typography: "s3", color: "text.disabled",
                      fontVariantNumeric: "tabular-nums",
                      flexShrink: 0, whiteSpace: "nowrap", mt: "2px",
                    }}
                  >
                    {(elapsed[i] / 1000).toFixed(1)}s
                  </Typography>
                )}
              </Stack>

              <Collapse in={stepLines.length > 0}>
                <Stack spacing={0.375} sx={{ mt: 0.625 }}>
                  {stepLines.map((line) => (
                    <Fade in key={line} timeout={260}>
                      <Stack direction="row" spacing={0.875} alignItems="flex-start">
                        <Box
                          sx={{
                            width: 3, height: 3, borderRadius: "50%", flexShrink: 0, mt: "7px",
                            bgcolor: "text.disabled",
                          }}
                        />
                        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{line}</Typography>
                      </Stack>
                    </Fade>
                  ))}
                </Stack>
              </Collapse>
            </Box>
          </Stack>
        );
      })}
    </Stack>
  );
}

RunTrace.propTypes = {
  steps: PropTypes.array,
  stepMs: PropTypes.number,
  accent: PropTypes.string,
  running: PropTypes.bool,
  onDone: PropTypes.func,
  compact: PropTypes.bool,
};

/**
 * The live panel: a heading, an elapsed clock, and the trace.
 *
 * Given room to breathe, because this is the whole screen while it runs — the
 * previous one put six lines of 12px text in the top-left corner of a panel
 * 700px wide and left the rest empty.
 */
export function RunTracePanel({ title, subtitle, steps, stepMs, accent = "#7857FC", onDone }) {
  const [ticks, setTicks] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTicks((n) => n + 1), 100);
    return () => clearInterval(t);
  }, []);

  return (
    <Box sx={{ px: { xs: 2.5, md: 4 }, py: { xs: 3, md: 4 } }}>
      <Stack
        direction="row" alignItems="flex-start" spacing={1.5}
        sx={{ mb: 3, pb: 2.5, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Box
          sx={{
            width: 34, height: 34, borderRadius: 1.25, display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(accent, t.palette.mode === "dark" ? 0.16 : 0.1), color: accent,
          }}
        >
          <Iconify icon="solar:magic-stick-3-linear" width={18} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>{title}</Typography>
          {subtitle && (
            <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}>{subtitle}</Typography>
          )}
        </Box>
        <Typography
          sx={{ typography: "s2", color: "text.subtitle", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
        >
          {(ticks / 10).toFixed(1)}s
        </Typography>
      </Stack>

      {/*
        No max-width cap. The earlier 720px cap left ~340px of empty
        space on the right in the 1060px optimizer drawer, which is why
        every elapsed time read as "floating in the middle" — it was
        anchored to the trace's right edge, but the trace was pinned
        narrow. Let it fill the panel's own padding.
      */}
      <RunTrace steps={steps} stepMs={stepMs} accent={accent} running onDone={onDone} />
    </Box>
  );
}

RunTracePanel.propTypes = {
  title: PropTypes.string,
  subtitle: PropTypes.string,
  steps: PropTypes.array,
  stepMs: PropTypes.number,
  accent: PropTypes.string,
  onDone: PropTypes.func,
};

/**
 * The trace after the fact.
 *
 * One line by default, because nobody reads it every time — and every line of
 * it when somebody wants to know how a conclusion was reached, which is the
 * whole reason to keep it.
 */
export function RunTraceLog({ label, steps, accent = "#7857FC" }) {
  const [open, setOpen] = useState(false);

  return (
    <Box>
      <Stack
        direction="row" alignItems="center" spacing={1}
        onClick={() => setOpen((o) => !o)}
        sx={{
          px: 2.5, py: 1.25, cursor: "pointer", borderRadius: 1,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify icon="solar:checklist-minimalistic-linear" width={15} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s2", color: "text.secondary", fontWeight: 600 }}>{label}</Typography>
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {open ? "Hide steps" : "View steps"}
        </Typography>
        <Iconify
          icon={open ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
          width={15} sx={{ color: "text.subtitle" }}
        />
      </Stack>
      <Collapse in={open}>
        <Box sx={{ px: 2.5, pb: 2.5, pt: 1, maxWidth: 720 }}>
          <RunTrace steps={steps} accent={accent} running={false} />
        </Box>
      </Collapse>
    </Box>
  );
}

RunTraceLog.propTypes = { label: PropTypes.string, steps: PropTypes.array, accent: PropTypes.string };
