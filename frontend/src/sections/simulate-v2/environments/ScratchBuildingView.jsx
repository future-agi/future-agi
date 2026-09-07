import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, IconButton, Tooltip, LinearProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import StudioConsole from "../assistant/AssistantConsole";
import DerivingAnimation from "./DerivingAnimation";
import { SCRATCH_STAGES } from "./intake/scratchBuild";

/*
  Intermediate "building" view for the scratch flow. Shape mirrors
  TemplateReviewLayout — chat left, workspace panel right — so the
  transition into the review page reads as a swap, not a page load.

  The right side reuses the same DerivingAnimation the "build from
  agent" flow shows on its derivation screen (scanning code panel
  with particles flying into a filling sandbox), plus a live pipeline
  timeline underneath that pings each stage as it runs and stamps
  its elapsed time when it completes.

  The left side streams a rich builder narrative — not just prose
  notes: tool-call rows (name → result), file writes (path → note),
  and short section headers, so it reads like the derivation
  transcript on the agent flow.

  Fully client-side theatre — no backend derives anything — but the
  timings and event mix are close enough to the real thing that the
  CEO demo lands.
*/
const STAGES = SCRATCH_STAGES;

const OFFSETS = STAGES.reduce((acc, s, i) => {
  acc.push((acc[i - 1] || 0) + s.ms);
  return acc;
}, []);
const TOTAL_MS = OFFSETS[OFFSETS.length - 1];

export default function ScratchBuildingView({ flowText, attachments, envName, onComplete, onBack }) {
  const [doneIds, setDoneIds] = useState([]);
  const [runningId, setRunningId] = useState(STAGES[0].id);
  const [elapsed, setElapsed] = useState({}); // stageId -> "1.8s"
  const [turns, setTurns] = useState(() => [
    {
      id: "greet",
      role: "assistant",
      steps: [{
        kind: "note",
        text: `Starting the build for ${envName}. I'll walk through it step by step — you can watch the pipeline on the right.`,
      }],
    },
    ...(flowText?.trim() ? [{ id: "u-flow", role: "user", text: flowText.trim() }] : []),
  ]);
  const completedRef = useRef(false);

  useEffect(() => {
    const timers = [];
    STAGES.forEach((stage, i) => {
      const startAt = i === 0 ? 0 : OFFSETS[i - 1];
      const doneAt = OFFSETS[i];

      timers.push(setTimeout(() => {
        setRunningId(stage.id);
        setTurns((prev) => [...prev, {
          id: `a-${stage.id}`,
          role: "assistant",
          title: stage.chatTitle,
          steps: stage.steps,
        }]);
      }, startAt));

      timers.push(setTimeout(() => {
        setDoneIds((prev) => (prev.includes(stage.id) ? prev : [...prev, stage.id]));
        setElapsed((prev) => ({ ...prev, [stage.id]: `${(stage.ms / 1000).toFixed(1)}s` }));
      }, doneAt - 60));
    });

    timers.push(setTimeout(() => {
      setRunningId(null);
    }, TOTAL_MS));

    timers.push(setTimeout(() => {
      if (!completedRef.current) {
        completedRef.current = true;
        onComplete?.();
      }
    }, TOTAL_MS + 900));

    return () => timers.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const progress = useMemo(() => {
    const base = (doneIds.length / STAGES.length) * 100;
    const partial = runningId && !doneIds.includes(runningId) ? (1 / STAGES.length) * 45 : 0;
    return Math.min(100, base + partial);
  }, [doneIds, runningId]);

  const allDone = doneIds.length === STAGES.length;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* header — mirrors TemplateReviewLayout */}
      <Stack
        direction="row" alignItems="center" spacing={2}
        sx={{ px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Tooltip arrow title="Back to intake">
          <IconButton size="small" onClick={onBack}>
            <Iconify icon="solar:alt-arrow-left-linear" width={17} />
          </IconButton>
        </Tooltip>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>
            {allDone ? envName : `Building ${envName}`}
          </Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            Reading your flow → building the world → drafting scenarios → wiring evals
          </Typography>
        </Box>
        <SetupBeingBuiltPill running={!allDone} />
      </Stack>

      <Box
        sx={{
          flex: 1, minHeight: 0, display: "grid", gap: 2, p: 2,
          gridTemplateColumns: { xs: "1fr", lg: "minmax(360px, 400px) 1fr" },
        }}
      >
        <SectionCard sx={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <StudioConsole
            turns={turns}
            running={runningId != null}
            chips={[]}
            onSend={() => {}}
            onChip={() => {}}
          />
        </SectionCard>

        <SectionCard sx={{
          height: "100%", minHeight: 0,
          display: "flex", flexDirection: "column",
          overflow: "hidden",
        }}>
          <Box sx={{ px: 3, pt: 3, pb: 1, flexShrink: 0 }}>
            <DerivingAnimation label={allDone ? "Environment ready" : "Building your environment"} />
          </Box>

          <Box sx={{
            px: 3, pt: 1, pb: 1.5, flexShrink: 0,
            borderTop: "1px solid", borderColor: "divider",
          }}>
            <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 1 }}>
              <Typography sx={{
                typography: "s3", fontWeight: 700, letterSpacing: 0.4,
                textTransform: "uppercase", color: "text.subtitle",
              }}>
                Setup — building the environment
              </Typography>
              <Box sx={{ flex: 1 }}>
                <LinearProgress
                  variant="determinate"
                  value={progress}
                  sx={{
                    height: 4, borderRadius: 999,
                    bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
                    "& .MuiLinearProgress-bar": { bgcolor: "#7857FC" },
                  }}
                />
              </Box>
              <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
                {doneIds.length} of {STAGES.length}
              </Typography>
            </Stack>
          </Box>

          <Box sx={{ px: 3, pb: 3, flex: 1, minHeight: 0, overflowY: "auto" }}>
            <PipelineTimeline
              stages={STAGES}
              doneIds={doneIds}
              runningId={runningId}
              elapsed={elapsed}
            />

            {attachments?.length > 0 && (
              <Box sx={{
                mt: 2, p: 1.5, borderRadius: 1.5,
                border: "1px dashed", borderColor: "divider",
                bgcolor: "background.neutral",
              }}>
                <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 0.75 }}>
                  Feeding the builder
                </Typography>
                <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
                  {attachments.map((f) => (
                    <Stack key={f.id || f.name}
                      direction="row" alignItems="center" spacing={0.75}
                      sx={{
                        px: 1, py: 0.5, borderRadius: 0.875,
                        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03),
                      }}
                    >
                      <Iconify icon="solar:paperclip-linear" width={12} sx={{ color: "text.subtitle" }} />
                      <Typography sx={{ typography: "s3", color: "text.secondary", maxWidth: 200 }} noWrap>
                        {f.name}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              </Box>
            )}
          </Box>
        </SectionCard>
      </Box>
    </Box>
  );
}

ScratchBuildingView.propTypes = {
  flowText: PropTypes.string,
  attachments: PropTypes.array,
  envName: PropTypes.string,
  onComplete: PropTypes.func,
  onBack: PropTypes.func,
};

/* ── pieces ─────────────────────────────────────────────────────────── */

function SetupBeingBuiltPill({ running }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1}
      sx={{
        px: 1.25, py: 0.625, borderRadius: 999, flexShrink: 0,
        border: "1px solid", borderColor: "divider",
      }}
    >
      <Box
        sx={{
          width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
          bgcolor: running ? "#7857FC" : "#16A34A",
          animation: running ? "chip-pulse 1.4s ease-in-out infinite" : "none",
          "@keyframes chip-pulse": {
            "0%,100%": { opacity: 0.4, transform: "scale(1)" },
            "50%": { opacity: 1, transform: "scale(1.15)" },
          },
        }}
      />
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {running ? "Setup being built" : "Setup ready"}
      </Typography>
    </Stack>
  );
}
SetupBeingBuiltPill.propTypes = { running: PropTypes.bool };

function PipelineTimeline({ stages, doneIds, runningId, elapsed }) {
  return (
    <Stack sx={{ position: "relative" }}>
      {stages.map((s, i) => {
        const done = doneIds.includes(s.id);
        const running = s.id === runningId && !done;
        const pending = !done && !running;
        const isLast = i === stages.length - 1;
        return (
          <Stack
            key={s.id}
            direction="row" alignItems="flex-start" spacing={2}
            sx={{ position: "relative", pb: isLast ? 0 : 2 }}
          >
            {/* rail dot + connector */}
            <Box sx={{ position: "relative", display: "flex", flexDirection: "column", alignItems: "center", flexShrink: 0 }}>
              <Box sx={{
                width: 24, height: 24, borderRadius: "50%",
                display: "grid", placeItems: "center",
                bgcolor: (t) => {
                  if (done) return alpha("#16A34A", t.palette.mode === "dark" ? 0.2 : 0.14);
                  if (running) return alpha("#7857FC", t.palette.mode === "dark" ? 0.22 : 0.14);
                  return alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04);
                },
                color: done ? "#16A34A" : (running ? "#7857FC" : "text.disabled"),
                boxShadow: (t) => (running
                  ? `0 0 0 4px ${alpha("#7857FC", t.palette.mode === "dark" ? 0.18 : 0.12)}`
                  : "none"),
                transition: "box-shadow .3s ease, background-color .3s ease",
                zIndex: 1,
              }}>
                {done ? (
                  <Iconify icon="solar:check-circle-bold" width={14} />
                ) : running ? (
                  <Box sx={{
                    width: 12, height: 12, borderRadius: "50%",
                    border: "2px solid", borderColor: "#7857FC", borderTopColor: "transparent",
                    animation: "spin 0.8s linear infinite",
                    "@keyframes spin": { to: { transform: "rotate(360deg)" } },
                  }} />
                ) : (
                  <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "text.disabled" }} />
                )}
              </Box>
              {!isLast && (
                <Box sx={{
                  width: 2, flex: 1, minHeight: 30,
                  bgcolor: (t) => (done
                    ? alpha("#16A34A", t.palette.mode === "dark" ? 0.35 : 0.28)
                    : alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06)),
                  mt: 0.5,
                }} />
              )}
            </Box>

            <Stack direction="row" alignItems="center" spacing={1} sx={{
              flex: 1, minWidth: 0,
              px: 1.5, py: 1.25, borderRadius: 1.25,
              bgcolor: (t) => (running
                ? alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04)
                : "transparent"),
              transition: "background-color .3s ease",
            }}>
              <Box flex={1} minWidth={0}>
                <Typography sx={{
                  typography: "s2", fontWeight: 700,
                  color: pending ? "text.subtitle" : "text.primary",
                }}>
                  {s.label}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {s.detail}
                </Typography>
              </Box>
              {elapsed[s.id] && (
                <Typography sx={{
                  typography: "s3", color: "text.subtitle", flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}>
                  {elapsed[s.id]}
                </Typography>
              )}
              {running && !elapsed[s.id] && (
                <Typography sx={{
                  typography: "s3", color: "#7857FC", fontWeight: 700, flexShrink: 0,
                }}>
                  Running…
                </Typography>
              )}
              {pending && (
                <Typography sx={{
                  typography: "s3", color: "text.disabled", flexShrink: 0,
                }}>
                  queued
                </Typography>
              )}
            </Stack>
          </Stack>
        );
      })}
    </Stack>
  );
}
PipelineTimeline.propTypes = {
  stages: PropTypes.array, doneIds: PropTypes.array,
  runningId: PropTypes.string, elapsed: PropTypes.object,
};
