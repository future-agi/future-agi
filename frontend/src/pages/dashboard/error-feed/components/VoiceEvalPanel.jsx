import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import {
  clickable,
  focusRing,
} from "src/sections/simulate-v2/components/clickable";

// Marker pin tokens (status roles index theme.palette.status).
const FAIL_COLOR = "status.danger.solid";
const PASS_COLOR = "status.success.solid";
const AMBER_COLOR = "status.warning.solid";

// Deterministic decorative waveform — seeded from trace id for stable bars.
function waveformBars(seed = "default", count = 80) {
  let h = 0;
  for (let i = 0; i < seed.length; i += 1)
    h = (h * 31 + seed.charCodeAt(i)) | 0;
  const out = [];
  for (let i = 0; i < count; i += 1) {
    h = (h * 1103515245 + 12345) & 0x7fffffff;
    const norm = (h % 1000) / 1000;
    out.push(0.25 + norm * 0.75);
  }
  return out;
}

const fmtTime = (s) => {
  if (s == null || Number.isNaN(s)) return "–:––";
  const total = Math.max(0, Math.floor(s));
  const m = Math.floor(total / 60);
  const ss = total % 60;
  return `${m}:${ss.toString().padStart(2, "0")}`;
};

// ── Audio scrubber ──────────────────────────────────────────────────────────
function Scrubber({ duration, currentTime, markers, onSeek, seed, dense }) {
  const bars = useMemo(
    () => waveformBars(seed, dense ? 60 : 80),
    [seed, dense],
  );
  const trackRef = useRef(null);
  const progress = duration > 0 ? Math.min(1, currentTime / duration) : 0;

  const handleClick = (e) => {
    if (!trackRef.current || !onSeek) return;
    const rect = trackRef.current.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    onSeek(Math.max(0, Math.min(1, ratio)) * duration);
  };

  return (
    <Box
      ref={trackRef}
      onClick={handleClick}
      sx={{
        position: "relative",
        height: dense ? 36 : 48,
        cursor: "pointer",
        userSelect: "none",
        borderRadius: "6px",
        overflow: "hidden",
        bgcolor: "action.hover",
        display: "flex",
        alignItems: "center",
        px: 0.5,
      }}
    >
      {/* Bars */}
      <Box
        sx={{
          flex: 1,
          height: "100%",
          display: "flex",
          alignItems: "center",
          gap: "2px",
        }}
      >
        {bars.map((h, i) => {
          const barProgress = i / bars.length;
          const played = barProgress <= progress;
          return (
            <Box
              key={i}
              sx={{
                flex: 1,
                height: `${h * 100}%`,
                borderRadius: "2px",
                bgcolor: played ? "status.discovery.solid" : "text.disabled",
              }}
            />
          );
        })}
      </Box>

      {/* Playhead */}
      <Box
        sx={{
          position: "absolute",
          top: 0,
          bottom: 0,
          left: `${progress * 100}%`,
          width: 0,
          borderLeft: "2px solid",
          borderLeftColor: "status.discovery.solid",
          pointerEvents: "none",
        }}
      />

      {/* Markers */}
      {markers.map((m, i) => {
        const left = duration > 0 ? (m.t / duration) * 100 : 0;
        const color = m.kind === "this" ? FAIL_COLOR : AMBER_COLOR;
        return (
          <Tooltip key={i} title={`${fmtTime(m.t)} · ${m.label}`} arrow>
            <Box
              {...clickable(
                (e) => {
                  e.stopPropagation();
                  onSeek?.(m.t);
                },
                { label: `${fmtTime(m.t)} · ${m.label}` },
              )}
              sx={{
                ...focusRing,
                position: "absolute",
                top: 2,
                bottom: 2,
                left: `${left}%`,
                width: 10,
                ml: "-5px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  bgcolor: color,
                  border: "2px solid",
                  borderColor: "background.paper",
                  cursor: "pointer",
                }}
              />
            </Box>
          </Tooltip>
        );
      })}
    </Box>
  );
}
Scrubber.propTypes = {
  duration: PropTypes.number.isRequired,
  currentTime: PropTypes.number.isRequired,
  markers: PropTypes.array.isRequired,
  onSeek: PropTypes.func,
  seed: PropTypes.string,
  dense: PropTypes.bool,
};

// ── Player (real <audio> + scrubber + play/pause) ───────────────────────────
function useAudioPlayer({
  src,
  duration: durationProp,
  markers,
  seed,
  dense,
  active = true,
  onActiveChange,
}) {
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [mediaDuration, setMediaDuration] = useState(null);
  const audioRef = useRef(null);

  // The audio element's own duration wins once metadata loads (the span
  // attribute can lag the actual file by a second or two).
  const duration = mediaDuration ?? durationProp ?? 0;

  useEffect(() => {
    if (!src) return undefined;
    const audio = new Audio(src);
    audioRef.current = audio;
    const onTime = () => setCurrentTime(audio.currentTime);
    const onMeta = () => {
      if (Number.isFinite(audio.duration)) setMediaDuration(audio.duration);
    };
    const onEnded = () => setPlaying(false);
    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("loadedmetadata", onMeta);
    audio.addEventListener("ended", onEnded);
    return () => {
      audio.pause();
      audio.removeEventListener("timeupdate", onTime);
      audio.removeEventListener("loadedmetadata", onMeta);
      audio.removeEventListener("ended", onEnded);
      audioRef.current = null;
    };
  }, [src]);

  // Pause when the other side of a split view takes over playback.
  useEffect(() => {
    if (!active && playing) {
      audioRef.current?.pause();
      setPlaying(false);
    }
  }, [active, playing]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playing) {
      audio.pause();
      setPlaying(false);
    } else {
      onActiveChange?.();
      audio.play();
      setPlaying(true);
    }
  };

  const onSeek = (t) => {
    if (t == null) return;
    setCurrentTime(t);
    if (audioRef.current) audioRef.current.currentTime = t;
  };

  return {
    currentTime,
    playing,
    seekTo: onSeek,
    render: (
      <Stack gap={0.75}>
        <Stack direction="row" alignItems="center" gap={1}>
          <IconButton
            onClick={togglePlay}
            disabled={!src}
            sx={{
              width: 32,
              aspectRatio: "1",
              bgcolor: "status.discovery.solid",
              color: "status.discovery.onSolid",
              "&:hover": { bgcolor: "status.discovery.solid", opacity: 0.9 },
              "&.Mui-disabled": { bgcolor: "action.disabledBackground" },
              flexShrink: 0,
            }}
          >
            <Iconify
              icon={playing ? "solar:pause-linear" : "solar:play-linear"}
              width={16}
            />
          </IconButton>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Scrubber
              duration={duration}
              currentTime={currentTime}
              markers={markers}
              onSeek={onSeek}
              seed={seed}
              dense={dense}
            />
          </Box>
          <Typography
            fontSize="12px"
            fontWeight={600}
            color="text.secondary"
            sx={{
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              flexShrink: 0,
              minWidth: 72,
              textAlign: "right",
            }}
          >
            {fmtTime(currentTime)} / {fmtTime(duration)}
          </Typography>
        </Stack>
      </Stack>
    ),
  };
}

// ── Transcript ───────────────────────────────────────────────────────────────
function Transcript({ lines, currentTime, onSeek, dense }) {
  const scrollerRef = useRef(null);

  // Find active line: latest line whose t <= currentTime.
  const activeIdx = useMemo(() => {
    let idx = -1;
    for (let i = 0; i < lines.length; i += 1) {
      if (lines[i].t != null && lines[i].t <= currentTime) idx = i;
      else if (lines[i].t != null) break;
    }
    return idx;
  }, [lines, currentTime]);

  // Auto-scroll active line into view.
  useEffect(() => {
    if (activeIdx < 0 || !scrollerRef.current) return;
    const el = scrollerRef.current.querySelector(`[data-line='${activeIdx}']`);
    if (el?.scrollIntoView) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [activeIdx]);

  return (
    <Box
      ref={scrollerRef}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        maxHeight: dense ? 240 : 320,
        overflowY: "auto",
      }}
    >
      <Stack>
        {lines.map((line, i) => {
          const isActive = i === activeIdx;
          const isAgent = line.speaker === "agent";
          return (
            <Stack
              key={i}
              direction="row"
              gap={1}
              data-line={i}
              {...clickable(() => onSeek?.(line.t))}
              sx={{
                ...focusRing,
                px: 1.5,
                py: 0.85,
                borderBottom: "1px solid",
                borderColor: "divider",
                cursor: line.t != null ? "pointer" : "default",
                bgcolor: isActive ? "status.discovery.bg" : "transparent",
                "&:last-of-type": { borderBottom: "none" },
                "&:hover": {
                  bgcolor: isActive ? "status.discovery.bg" : "action.hover",
                },
              }}
            >
              <Typography
                fontSize="12px"
                color="text.disabled"
                sx={{
                  fontFamily: "ui-monospace, SFMono-Regular, monospace",
                  minWidth: 36,
                  flexShrink: 0,
                  mt: "2px",
                }}
              >
                {fmtTime(line.t)}
              </Typography>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography
                  fontSize="12px"
                  fontWeight={600}
                  sx={{
                    color: isAgent ? "accent.brand" : "text.secondary",
                    textTransform: "none",
                    letterSpacing: 0,
                    mb: 0.2,
                  }}
                >
                  {isAgent ? "Agent" : "Caller"}
                </Typography>
                <Typography
                  fontSize="12px"
                  color="text.primary"
                  sx={{ lineHeight: 1.55 }}
                >
                  {line.text}
                </Typography>
              </Box>
            </Stack>
          );
        })}
      </Stack>
    </Box>
  );
}
Transcript.propTypes = {
  lines: PropTypes.array.isRequired,
  currentTime: PropTypes.number.isRequired,
  onSeek: PropTypes.func,
  dense: PropTypes.bool,
};

// ── Judge reason card (mirrors EvalIOPanel's card) ──────────────────────────
function JudgeReasonCard({ reason, score }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        p: 1.5,
        bgcolor: "action.hover",
      }}
    >
      <Stack direction="row" alignItems="center" gap={0.75} sx={{ mb: 0.75 }}>
        <Iconify
          icon="solar:scale-linear"
          width={14}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          fontSize="12px"
          fontWeight={600}
          color="text.secondary"
          sx={{ textTransform: "none", letterSpacing: 0 }}
        >
          Evaluator reasoning
        </Typography>
        <Box sx={{ flex: 1 }} />
        {score != null && (
          <Typography
            fontSize="12px"
            fontWeight={600}
            color="text.primary"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {score.toFixed(2)} / 1.00
          </Typography>
        )}
      </Stack>
      <Typography fontSize="13px" color="text.primary" sx={{ lineHeight: 1.6 }}>
        {reason}
      </Typography>
    </Box>
  );
}
JudgeReasonCard.propTypes = {
  reason: PropTypes.string.isRequired,
  score: PropTypes.number,
};

// Prefer `messages` (carry seconds_from_start for seek); fall back to `transcript` (no offsets).
function toTranscriptLines(detail) {
  const messages = detail?.messages ?? [];
  const fromMessages = messages
    .filter(
      (m) =>
        ["user", "bot", "assistant"].includes(m?.role) &&
        (m?.message ?? m?.content),
    )
    .map((m) => ({
      t: m.seconds_from_start ?? m.secondsFromStart ?? null,
      speaker: m.role === "user" ? "caller" : "agent",
      text: m.message ?? m.content,
    }));
  if (fromMessages.length) return fromMessages;

  const transcript = detail?.transcript ?? [];
  return transcript
    .filter((l) => l?.content)
    .map((l) => ({
      t: l.seconds_from_start ?? null,
      speaker: l.role === "user" ? "caller" : "agent",
      text: l.content,
    }));
}

function useVoiceCallData(traceId) {
  const { data: detail, isLoading } = useVoiceCallDetail(traceId, !!traceId);
  const lines = useMemo(() => toTranscriptLines(detail), [detail]);
  const recordingUrl =
    detail?.recording_url ||
    detail?.recording?.mono?.combined_url ||
    detail?.stereo_recording_url ||
    null;
  return {
    isLoading,
    lines,
    recordingUrl,
    duration: detail?.duration_seconds ?? null,
  };
}

function CallColumn({
  title,
  accentColor,
  call,
  seed,
  activeSide,
  side,
  onActivate,
}) {
  const player = useAudioPlayer({
    src: call.recordingUrl,
    duration: call.duration,
    markers: [],
    seed,
    dense: true,
    active: activeSide === side,
    onActiveChange: () => onActivate(side),
  });
  return (
    <Stack gap={0.75}>
      <Stack direction="row" alignItems="center" gap={0.5}>
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            bgcolor: accentColor,
          }}
        />
        <Typography
          fontSize="12px"
          fontWeight={600}
          sx={{
            color: accentColor,
            textTransform: "none",
            letterSpacing: 0,
          }}
        >
          {title}
        </Typography>
      </Stack>
      {call.isLoading ? (
        <Stack alignItems="center" sx={{ py: 3 }}>
          <CircularProgress size={18} />
        </Stack>
      ) : (
        <>
          {call.recordingUrl ? (
            player.render
          ) : (
            <Typography fontSize="12px" color="text.disabled">
              No recording available — transcript only.
            </Typography>
          )}
          {call.lines.length > 0 && (
            <Transcript
              lines={call.lines}
              currentTime={player.currentTime}
              onSeek={
                call.recordingUrl
                  ? (t) => {
                      onActivate(side);
                      player.seekTo(t);
                    }
                  : undefined
              }
              dense
            />
          )}
        </>
      )}
    </Stack>
  );
}
CallColumn.propTypes = {
  title: PropTypes.string.isRequired,
  accentColor: PropTypes.string.isRequired,
  call: PropTypes.object.isRequired,
  seed: PropTypes.string,
  activeSide: PropTypes.string,
  side: PropTypes.string,
  onActivate: PropTypes.func,
};

// ── Main VoiceEvalPanel ──────────────────────────────────────────────────────
export default function VoiceEvalPanel({ trace, evalScore, successTraceId }) {
  const traceSeed = trace?.id ?? "default";
  const [splitView, setSplitView] = useState(false);
  const [activeSide, setActiveSide] = useState("fail");

  const failCall = useVoiceCallData(trace?.id);
  // Only fetched once the user opens the comparison.
  const passCall = useVoiceCallData(splitView ? successTraceId : null);

  const evidence = trace?.evidence ?? {};
  const judgeReason = evidence.judge_reason ?? null;
  const judgeScore =
    evidence.score ?? (typeof evalScore === "number" ? evalScore : null);

  const singlePlayer = useAudioPlayer({
    src: failCall.recordingUrl,
    duration: failCall.duration,
    markers: [],
    seed: traceSeed,
    dense: false,
  });

  if (failCall.isLoading) {
    return (
      <Stack alignItems="center" justifyContent="center" sx={{ py: 6 }}>
        <CircularProgress size={22} />
      </Stack>
    );
  }

  if (!failCall.lines.length && !failCall.recordingUrl) {
    return (
      <Stack
        alignItems="center"
        justifyContent="center"
        gap={0.5}
        sx={{ py: 5 }}
      >
        <Iconify
          icon="solar:end-call-linear"
          width={20}
          sx={{ color: "text.disabled" }}
        />
        <Typography fontSize="12px" color="text.secondary">
          No voice evidence found for this call.
        </Typography>
      </Stack>
    );
  }

  return (
    <Stack gap={1.25}>
      {/* Compare toggle — only when the cluster has a real working call. */}
      {successTraceId && (
        <Stack direction="row" alignItems="center" gap={1}>
          <Box sx={{ flex: 1 }} />
          <Button
            size="small"
            variant={splitView ? "text" : "outlined"}
            startIcon={
              <Iconify
                icon={
                  splitView
                    ? "solar:list-linear"
                    : "solar:transfer-horizontal-linear"
                }
                width={14}
              />
            }
            onClick={() => setSplitView((v) => !v)}
            sx={{
              borderColor: "divider",
              color: splitView ? "text.secondary" : "text.primary",
            }}
          >
            {splitView ? "Single view" : "Compare with passing"}
          </Button>
        </Stack>
      )}

      {!splitView ? (
        <Stack gap={1.25}>
          {failCall.recordingUrl ? (
            singlePlayer.render
          ) : (
            <Typography fontSize="12px" color="text.disabled">
              No recording available — transcript only.
            </Typography>
          )}
          {failCall.lines.length > 0 && (
            <Transcript
              lines={failCall.lines}
              currentTime={singlePlayer.currentTime}
              onSeek={failCall.recordingUrl ? singlePlayer.seekTo : undefined}
            />
          )}
        </Stack>
      ) : (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
            gap: 1.5,
          }}
        >
          <CallColumn
            title="Failing call"
            accentColor={FAIL_COLOR}
            call={failCall}
            seed={`${traceSeed}-fail`}
            activeSide={activeSide}
            side="fail"
            onActivate={setActiveSide}
          />
          <CallColumn
            title="Working call"
            accentColor={PASS_COLOR}
            call={passCall}
            seed={`${traceSeed}-pass`}
            activeSide={activeSide}
            side="pass"
            onActivate={setActiveSide}
          />
        </Box>
      )}

      {judgeReason && (
        <JudgeReasonCard reason={judgeReason} score={judgeScore} />
      )}
    </Stack>
  );
}
VoiceEvalPanel.propTypes = {
  trace: PropTypes.object,
  evalScore: PropTypes.number,
  successTraceId: PropTypes.string,
};
