// One call in the row view / a card (R4 lane M): the reused
// MultiTrackAudioPlayer (its documented [caller, agent] pair), the reused
// TranscriptView built from the call's turns (click a turn to seek), and a
// 6-cell KPI strip with where its latency comes from. `Copy transcript`
// and `Download recording` sit in the header. VoiceDetailDrawerV2 is not
// embedded: it is a full drawer with its own data fetching; the pieces it
// is built from are reused here instead.
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import MultiTrackAudioPlayer from "src/components/multi-track-audio-player/MultiTrackAudioPlayer";
import TranscriptView from "src/components/VoiceDetailDrawerV2/TranscriptView";
import useVoiceAudioStore from "src/components/VoiceDetailDrawerV2/voiceAudioStore";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CopyButton from "src/sections/common/ux/CopyButton";
import {
  EMPTY,
  NUM_SX,
  fmtCost,
  fmtMs,
  fmtPct,
} from "src/sections/common/format/fmt";
import { SourceLabel } from "src/sections/common/Lenses/CallMetricStrip";
import {
  callMetricsOf,
  toolChipText,
} from "src/sections/common/Lenses/lensModel";
import { claimAudio, releaseAudio } from "./audioBus";
import { fmtDuration } from "./mediaRef";
import { useSeekableUrls } from "./seekableUrl";

const ROLE = { caller: "user", agent: "assistant", tool: "tool" };

/** The call's turns in TranscriptView's shape (seconds from call start). */
export function transcriptOf(call) {
  const out = [];
  (call?.turns ?? []).forEach((t) => {
    out.push({
      id: `t${t.i}`,
      role: ROLE[t.role] ?? t.role,
      content: t.text,
      start: Number.isFinite(t.start_ms) ? t.start_ms / 1000 : undefined,
      end: Number.isFinite(t.end_ms) ? t.end_ms / 1000 : undefined,
    });
    if (t.tool)
      out.push({
        id: `t${t.i}-tool`,
        role: "tool",
        content: toolChipText(t.tool),
        tool_calls: [
          { name: t.tool.name, arguments: t.tool.args, result: t.tool.result },
        ],
        start: Number.isFinite(t.start_ms) ? t.start_ms / 1000 : undefined,
      });
  });
  return out;
}

/**
 * How the call was placed (pure): `Text only`, `Full voice`, or
 * `Replay of call_audio (2 recorded caller lines)` when the caller re-spoke
 * the row's recording (options-truth L2 G7).
 */
export function depthWords(call) {
  if (call?.depth === "text_only") return "Text only";
  if (call?.replay?.caller_lines) {
    const n = call.replay.caller_lines;
    const from = call.replay.column ? ` of ${call.replay.column}` : "";
    return `Replay${from} (${n} recorded caller line${n === 1 ? "" : "s"})`;
  }
  return "Full voice";
}

/** Plain text of a call for `Copy transcript`. */
export function transcriptText(call) {
  return (call?.turns ?? [])
    .map(
      (t) =>
        `${t.role === "caller" ? "Caller" : "Agent"}: ${t.text}${t.tool ? `\n  [${toolChipText(t.tool)}]` : ""}`,
    )
    .join("\n");
}

const KPIS = [
  ["duration", "Duration", (v) => fmtDuration(v)],
  ["p50", "Latency p50", fmtMs],
  ["p90", "Latency p90", fmtMs],
  [
    "kept",
    "Kept talking",
    (v, m) => (m.bargeIns ? `${v} of ${m.bargeIns}` : String(v)),
  ],
  ["wer", "WER", fmtPct],
  ["cost", "Cost per call", fmtCost],
];

let seq = 0;

export default function CallView({
  call,
  compact = false,
  maxTranscript = 240,
}) {
  const idRef = useRef(null);
  if (!idRef.current) {
    seq += 1;
    idRef.current = `call-view-${seq}`;
  }
  const textOnly = call.depth === "text_only";
  const hasAudio = Boolean(
    call.recording?.agent?.url || call.recording?.caller?.url,
  );
  const seekable = useSeekableUrls([
    call.recording?.caller?.url,
    call.recording?.agent?.url,
  ]);
  const trackUrls = useMemo(
    () => [
      {
        url: seekable.urls[0],
        name: "Caller",
        color: "#64748B",
        peaks: call.recording?.caller?.peaks,
      },
      {
        url: seekable.urls[1],
        name: "Agent",
        color: "#7C3AED",
        peaks: call.recording?.agent?.peaks,
      },
    ],
    [seekable.urls, call],
  );
  const transcript = useMemo(() => transcriptOf(call), [call]);
  const m = useMemo(() => callMetricsOf(call), [call]);
  const rafRef = useRef(null);
  const [instance, setInstance] = useState(null);

  // Drive TranscriptView's store from this player (one CallView at a time
  // owns it: the row view shows one row).
  const onInstance = useCallback((inst) => {
    setInstance(inst);
    const store = useVoiceAudioStore.getState();
    store.setInstance(inst);
    let maxDur = 0;
    (inst.wavesurfers ?? []).forEach((w) => {
      maxDur = Math.max(maxDur, w?.getDuration?.() ?? 0);
    });
    store.setDuration(maxDur);
    let last = null;
    const tick = () => {
      const mt = inst.multitrack;
      const s = useVoiceAudioStore.getState();
      try {
        s.setCurrentTime(mt.getCurrentTime());
        const playing = mt.isPlaying();
        if (playing !== last) {
          s.setIsPlaying(playing);
          if (playing) claimAudio(idRef.current, () => mt.pause());
          else releaseAudio(idRef.current);
          last = playing;
        }
      } catch {
        return;
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, []);
  useEffect(
    () => () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      releaseAudio(idRef.current);
      useVoiceAudioStore.getState().reset();
    },
    [],
  );
  const boxRef = useRef(null);
  // The waveform's scroller (made by the multitrack player) is a scroll
  // region keyboard users can reach too (a11y #17).
  useEffect(() => {
    const el = boxRef.current;
    if (!instance || !el || typeof window === "undefined") return;
    el.querySelectorAll("div").forEach((d) => {
      const o = window.getComputedStyle(d);
      if (!/(auto|scroll)/.test(`${o.overflowX} ${o.overflowY}`)) return;
      if (!d.hasAttribute("tabindex")) d.setAttribute("tabindex", "0");
      if (!d.hasAttribute("aria-label"))
        d.setAttribute("aria-label", "Call waveform, caller and agent tracks");
    });
  }, [instance]);
  // Fit the whole call into the player width.
  useEffect(() => {
    const mt = instance?.multitrack;
    const el = boxRef.current;
    const dur = (call.duration_ms ?? 0) / 1000;
    if (!mt || !el || !dur) return undefined;
    const fitToWidth = () => {
      try {
        mt.zoom(Math.max(0.05, (el.offsetWidth - 8) / dur));
      } catch {
        /* not ready */
      }
    };
    fitToWidth();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(fitToWidth);
    ro.observe(el);
    return () => ro.disconnect();
  }, [instance, call.duration_ms]);

  return (
    <Stack gap={1} data-call-view={call.call_id} sx={{ minWidth: 0 }}>
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Typography typography="s2" color="text.secondary" sx={NUM_SX}>
          {`${depthWords(call)} · ${call.provider ?? "call"} · ${fmtDuration(call.duration_ms)} · ended: ${call.ended_reason ?? call.status}`}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <CopyButton
          value={() => transcriptText(call)}
          tooltip="Copy transcript"
        />
        {hasAudio ? (
          <Button
            size="small"
            color="inherit"
            component="a"
            href={seekable.urls[1] ?? seekable.urls[0] ?? undefined}
            disabled={!seekable.ready}
            download={`${call.call_id}-agent.wav`}
            startIcon={<Iconify icon="mdi:download" width={16} />}
            sx={{ typography: "s3", height: 26 }}
          >
            Download recording
          </Button>
        ) : null}
      </Stack>
      {hasAudio ? (
        <Box
          ref={boxRef}
          sx={{
            minHeight: (compact ? 24 : 30) * 2 + 64,
            // The reused player reserves 170 px for its waveform; two tracks
            // and the time axis need far less.
            "& > .MuiStack-root > .MuiBox-root > .MuiBox-root:last-of-type": {
              minHeight: "0 !important",
            },
          }}
        >
          {seekable.ready ? (
            <MultiTrackAudioPlayer
              id={call.call_id}
              trackUrls={trackUrls}
              height={compact ? 24 : 30}
              allowDownload={false}
              onInstance={onInstance}
            />
          ) : (
            <Box
              sx={{
                height: 120,
                borderRadius: 0.5,
                bgcolor: "background.neutral",
              }}
            />
          )}
        </Box>
      ) : (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-call-notice={textOnly ? "text" : "missing"}
          sx={{
            px: 1.5,
            py: 1,
            borderRadius: 0.5,
            border: "1px dashed",
            borderColor: textOnly ? "divider" : "warning.main",
            bgcolor: textOnly
              ? "background.neutral"
              : "rgba(245, 158, 11, 0.08)",
          }}
        >
          <Iconify
            icon={textOnly ? "mdi:message-text-outline" : "mdi:alert-outline"}
            width={16}
            sx={{ color: textOnly ? "text.disabled" : "warning.dark" }}
          />
          <Typography
            typography="s2"
            color={textOnly ? "text.secondary" : "warning.dark"}
          >
            {textOnly
              ? "Text only · no recording, no latency to compare"
              : `Recording missing${call.latency_source === "provider" ? " · latency reported by the provider" : ""}`}
          </Typography>
        </Stack>
      )}
      <Box
        // A scroll region keyboard users can reach (a11y #17).
        tabIndex={0}
        role="region"
        aria-label={`Call transcript, ${transcript.length} turns`}
        sx={{
          maxHeight: maxTranscript,
          overflow: "auto",
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
          },
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
        }}
      >
        <TranscriptView
          transcript={transcript}
          embedded
          hideTimelineStrip={!hasAudio}
          enableTemporalFeatures={!textOnly}
        />
      </Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(6, minmax(0, 1fr))",
          gap: 1,
        }}
        data-call-kpis=""
      >
        {KPIS.map(([k, label, f]) => (
          <Stack key={k} sx={{ minWidth: 0 }}>
            <Typography typography="s3" color="text.secondary" noWrap>
              {label}
            </Typography>
            <CustomTooltip
              show={m[k] == null}
              size="small"
              title={
                textOnly
                  ? "Text only: there is no audio to time, interrupt or transcribe."
                  : "Not measured on this call."
              }
            >
              <Typography
                typography="s1"
                sx={NUM_SX}
                noWrap
                color={m[k] == null ? "text.disabled" : "text.primary"}
              >
                {m[k] == null ? EMPTY : f(m[k], m)}
              </Typography>
            </CustomTooltip>
          </Stack>
        ))}
      </Box>
      <SourceLabel
        lanes={[{ runId: call.call_id, label: call.call_id }]}
        calls={[call]}
      />
    </Stack>
  );
}

CallView.propTypes = {
  call: PropTypes.object.isRequired,
  compact: PropTypes.bool,
  maxTranscript: PropTypes.number,
};
