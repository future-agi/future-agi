import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Collapse, Fade, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { triedLabelOf } from "../../_mock/optimizer";
import { clickable, focusRing } from "../../components/clickable";

/*
  A running improvement, shown the way Debug failures shows a read: one
  progress strip (what it's doing, how far along), the steps as single lines
  with their working behind a click, and the candidate runs arriving one at a
  time, newest first. The full trace with every line stays in the record.
*/

// eslint-disable-next-line no-restricted-syntax -- ds: data colour
const GREEN = "#16A34A";
const num = { fontVariantNumeric: "tabular-nums" };
const head = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  textTransform: "none",
  letterSpacing: 0.5,
};

const stepTime = (s, stepMs) =>
  Math.round(stepMs * (1.1 + 0.3 * (s.lines?.length || 0)));

export default function ImprovementProgress({
  title,
  subtitle,
  steps,
  trials = [],
  stepMs = 800,
  onDone,
}) {
  /* `at` is the step running; `shownRuns` how many candidate runs have reported. */
  const [at, setAt] = useState(0);
  const [shownRuns, setShownRuns] = useState(0);
  const [times, setTimes] = useState([]);
  const [open, setOpen] = useState(null);
  const step = steps[at];
  const isTrials = step?.id === "trials";
  /* The page re-renders every second with fresh step objects — the timers
     read them through refs so a re-render never restarts a step. */
  const stepsRef = useRef(steps);
  stepsRef.current = steps;
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    if (at >= stepsRef.current.length) {
      const t = setTimeout(() => onDoneRef.current?.(), 400);
      return () => clearTimeout(t);
    }
    /* The candidate-run step reports each run in turn, then settles. */
    if (isTrials && shownRuns < trials.length) {
      const t = setTimeout(
        () => setShownRuns((n) => n + 1),
        Math.round(stepMs * 0.9),
      );
      return () => clearTimeout(t);
    }
    const ms = isTrials
      ? Math.round(stepMs * 0.5)
      : stepTime(stepsRef.current[at], stepMs);
    const t = setTimeout(() => {
      setTimes((x) => [
        ...x,
        isTrials ? Math.round(stepMs * (0.9 * trials.length + 0.5)) : ms,
      ]);
      setAt((i) => i + 1);
    }, ms);
    return () => clearTimeout(t);
  }, [at, shownRuns, isTrials, steps.length, trials.length, stepMs]);

  const done = at >= steps.length;
  const progress = steps.length
    ? Math.min(
        1,
        (at + (isTrials && trials.length ? shownRuns / trials.length : 0)) /
          steps.length,
      )
    : 0;
  const runsStarted =
    steps.findIndex((s) => s.id === "trials") <= at &&
    steps.some((s) => s.id === "trials");
  const runs = trials.slice(
    0,
    runsStarted
      ? at > steps.findIndex((s) => s.id === "trials")
        ? trials.length
        : shownRuns
      : 0,
  );

  return (
    <Stack spacing={3} sx={{ px: { xs: 2.5, md: 3 }, py: 2.5 }}>
      {/* ── the strip ── */}
      <Box>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Box
            sx={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              flexShrink: 0,
              border: "1.5px solid",
              borderColor: "primary.main",
              borderTopColor: "transparent",
              animation: "ip-spin 0.7s linear infinite",
              "@keyframes ip-spin": { to: { transform: "rotate(360deg)" } },
            }}
          />
          <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
            {title}
          </Typography>
          <Typography sx={{ typography: "s2", fontWeight: 600, ...num }}>
            {Math.min(at + 1, steps.length)}
            <Box
              component="span"
              sx={{ color: "text.tertiary", fontWeight: 500 }}
            >{` / ${steps.length} steps`}</Box>
          </Typography>
        </Stack>
        <Box
          sx={{
            mt: 1,
            height: 4,
            borderRadius: 2,
            overflow: "hidden",
            bgcolor: (t) => alpha(t.palette.text.primary, 0.08),
          }}
        >
          <Box
            sx={{
              height: "100%",
              borderRadius: 2,
              width: `${progress * 100}%`,
              transition: "width 300ms ease",
              bgcolor: (t) => alpha(t.palette.text.primary, 0.6),
            }}
          />
        </Box>
        <Typography
          noWrap
          sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
        >
          {done
            ? "Finishing up"
            : isTrials && trials.length
              ? `Run ${Math.min(shownRuns + 1, trials.length)} of ${trials.length}`
              : `Now · ${step?.label || ""}`}
          {subtitle && (
            <Box
              component="span"
              sx={{ color: "text.disabled" }}
            >{`  ·  ${subtitle}`}</Box>
          )}
        </Typography>
      </Box>

      {/* ── candidate runs, newest first ── */}
      {runs.length > 0 && (
        <Box>
          <Stack
            direction="row"
            alignItems="baseline"
            spacing={1}
            sx={{ mb: 1 }}
          >
            <Typography sx={head}>Runs</Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", ...num }}
            >{`${runs.length} of ${trials.length} so far`}</Typography>
          </Stack>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              overflow: "hidden",
              "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            {[...runs].reverse().map((t) => {
              const rejected = t.brokeBlocker || t.cheats;
              return (
                <Stack
                  key={t.n}
                  direction="row"
                  alignItems="center"
                  spacing={1.5}
                  sx={{
                    px: 1.75,
                    py: 1,
                    animation: "ip-in 360ms ease both",
                    "@keyframes ip-in": {
                      from: { opacity: 0, transform: "translateY(-6px)" },
                      to: { opacity: 1, transform: "none" },
                    },
                  }}
                >
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      width: 52,
                      flexShrink: 0,
                      ...num,
                    }}
                  >{`Run ${t.n}`}</Typography>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      width: 44,
                      flexShrink: 0,
                      textAlign: "right",
                      ...num,
                    }}
                  >{`${t.score}%`}</Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      width: 76,
                      flexShrink: 0,
                      color: rejected
                        ? "status.danger.fg"
                        : t.improved
                          ? "status.success.fg"
                          : "text.tertiary",
                    }}
                  >
                    {rejected
                      ? "Rejected"
                      : t.improved
                        ? "New best"
                        : "No better"}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.secondary",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {triedLabelOf(t, trials)}
                  </Typography>
                </Stack>
              );
            })}
          </Box>
        </Box>
      )}

      {/* ── the steps, one line each ── */}
      <Box>
        <Typography sx={{ ...head, mb: 1 }}>Steps</Typography>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
            "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {steps.map((s, i) => {
            const finished = i < at;
            const active = i === at;
            const expanded = open === i && finished;
            return (
              <Box key={s.id || s.label}>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1.25}
                  {...clickable(
                    finished && s.lines?.length
                      ? () => setOpen((o) => (o === i ? null : i))
                      : null,
                    { expanded },
                  )}
                  sx={{
                    ...focusRing,
                    px: 1.75,
                    py: 1,
                    opacity: finished || active ? 1 : 0.45,
                    ...(finished &&
                      s.lines?.length && {
                        cursor: "pointer",
                        "&:hover": { bgcolor: "action.hover" },
                      }),
                  }}
                >
                  <Box
                    sx={{
                      width: 16,
                      display: "grid",
                      placeItems: "center",
                      flexShrink: 0,
                    }}
                  >
                    {finished && (
                      <Iconify
                        icon="solar:check-circle-bold"
                        width={16}
                        sx={{ color: "status.success.fg" }}
                      />
                    )}
                    {active && (
                      <Box
                        sx={{
                          width: 10,
                          height: 10,
                          borderRadius: "50%",
                          border: "1.5px solid",
                          borderColor: "primary.main",
                          borderTopColor: "transparent",
                          animation: "ip-spin 0.7s linear infinite",
                        }}
                      />
                    )}
                    {!finished && !active && (
                      <Box
                        sx={{
                          width: 5,
                          height: 5,
                          borderRadius: "50%",
                          bgcolor: "text.disabled",
                        }}
                      />
                    )}
                  </Box>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontWeight: finished || active ? 600 : 500,
                      flexShrink: 1,
                      minWidth: 0,
                    }}
                  >
                    {s.label}
                  </Typography>
                  {finished && s.result && (
                    <Fade in timeout={300}>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s3",
                          color:
                            s.tone === GREEN
                              ? "status.success.fg"
                              : "text.tertiary",
                          flex: 1,
                          minWidth: 0,
                        }}
                      >
                        {s.result}
                      </Typography>
                    </Fade>
                  )}
                  {!(finished && s.result) && <Box sx={{ flex: 1 }} />}
                  {finished && times[i] != null && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.disabled",
                        flexShrink: 0,
                        ...num,
                      }}
                    >{`${(times[i] / 1000).toFixed(1)}s`}</Typography>
                  )}
                  {finished && s.lines?.length > 0 && (
                    <Iconify
                      icon={
                        expanded
                          ? "solar:alt-arrow-up-linear"
                          : "solar:alt-arrow-down-linear"
                      }
                      width={14}
                      sx={{ color: "text.tertiary", flexShrink: 0 }}
                    />
                  )}
                </Stack>
                <Collapse in={expanded} unmountOnExit>
                  <Stack
                    spacing={0.375}
                    sx={{ pl: "46px", pr: 1.75, pb: 1.25 }}
                  >
                    {(s.lines || [])
                      .filter(
                        (l) => !(s.id === "trials" && /^run \d+ —/.test(l)),
                      )
                      .map((l) => (
                        <Typography
                          key={l}
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {l}
                        </Typography>
                      ))}
                  </Stack>
                </Collapse>
              </Box>
            );
          })}
        </Box>
      </Box>
    </Stack>
  );
}

ImprovementProgress.propTypes = {
  title: PropTypes.string,
  subtitle: PropTypes.string,
  steps: PropTypes.array.isRequired,
  trials: PropTypes.array,
  stepMs: PropTypes.number,
  onDone: PropTypes.func,
};
