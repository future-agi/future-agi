import { useEffect, useMemo, useRef, useState } from "react";
import { recordingClock } from "./recordingClock";
import RecordingSeekBar from "./RecordingSeekBar";
import useRecordingPeaks from "./useRecordingPeaks";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { Box, Button, Slider, Stack, Typography } from "@mui/material";
import {
  recordingLanes,
  recordingPlan,
  recordingProjectionSchema,
  uuid,
} from "./contract";
import { resolveAttachments } from "./client";
import SequentialPlayback from "./SequentialPlayback";
import AttachmentStatus from "./AttachmentStatus";

const timeLabel = (seconds) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
const trackLabels = {
  user: "User",
  assistant: "Assistant",
  mixed: "Mixed recording",
  unknown: "Unassigned track",
};
const layoutLabels = {
  mixed: "Mixed recording",
  stereo: "Stereo recording",
  separate_tracks: "Separate recording tracks",
};
// Why the manifest says no audio exists at a position, in the viewer's words.
const gapLabels = {
  truncated: "truncated",
  policy_denied: "withheld by policy",
  queue_full: "dropped during capture",
  bootstrap_pending: "not yet captured",
  source_unavailable: "source unavailable",
  incomplete: "upload incomplete",
};
// What the producer could prove about agent audio at a position.
const playoutLabels = {
  played: "Played to the participant at this position",
  generated: "Generated at this position; playout not confirmed",
  truncated: "Playout was cut off at this position",
  unknown: "Playout unknown at this position",
};
const playoutWords = {
  played: "played",
  truncated: "cut off",
  unknown: "unknown",
};
const covers = (interval, ms) =>
  ms >= interval.start_ms && ms < interval.start_ms + interval.duration_ms;
// Playout text at a position from EVERY manifest interval covering it.
// Intervals may overlap and their order means nothing, so the evidence is a
// set read in one fixed order. "generated" only says audio was produced, so
// any other evidence there (an explicit "played", "truncated" or "unknown")
// says more and replaces it. Differing evidence is never resolved by picking
// one: it is shown together as unconfirmed, so "played" is never shown alone
// where the manifest also says playout was cut off or unknown.
function playoutAt(playout, ms) {
  const found = new Set(
    playout
      .filter((interval) => covers(interval, ms))
      .map((interval) => interval.evidence),
  );
  if (found.size > 1) found.delete("generated");
  const evidence = ["played", "truncated", "unknown", "generated"].filter(
    (value) => found.has(value),
  );
  if (evidence.length < 2)
    return evidence.length ? playoutLabels[evidence[0]] : null;
  const words = evidence.map((value) => playoutWords[value]);
  const last = words.pop();
  return `Playout not confirmed at this position (evidence: ${words.join(", ")} and ${last})`;
}

// data.recording_attachment is an authorized read projection, NOT an SDK span
// attribute. Its presence always takes precedence over the legacy URL renderer.
export default function RecordingAttachmentPlayer({
  projectId,
  attachment,
  variant = "voice",
  onInstance,
}) {
  // I04 shape: {occurrence_id, parent, state} plus manifest only when ready,
  // or a bare {state: "rejected"}. Anything else is shown as a coarse state.
  const parsed = useMemo(() => {
    if (!uuid.safeParse(projectId).success) return null;
    const result = recordingProjectionSchema.safeParse(attachment);
    if (!result.success) return null;
    if (result.data.state !== "ready") return result.data;
    try {
      const { occurrence_id, parent, manifest } = result.data;
      return {
        ...result.data,
        plan: recordingPlan(occurrence_id, parent, manifest),
      };
    } catch {
      // A timeline that cannot be played as given is never repaired here.
      return { state: "not_found" };
    }
  }, [projectId, attachment]);
  if (!parsed || parsed.state !== "ready")
    return (
      <AttachmentStatus
        status={parsed?.state || attachment?.state || "not_found"}
      />
    );
  const identity = `${projectId}/${parsed.occurrence_id}/${parsed.parent.trace_id}/${parsed.parent.span_id}`;
  return (
    <ReadyRecording
      key={identity}
      projectId={projectId}
      variant={variant}
      parent={parsed.parent}
      plan={parsed.plan}
      manifest={parsed.manifest}
      onInstance={onInstance}
    />
  );
}
RecordingAttachmentPlayer.propTypes = {
  variant: PropTypes.oneOf(["voice", "trace"]),
  projectId: PropTypes.string,
  attachment: PropTypes.object,
  onInstance: PropTypes.func,
};

function ReadyRecording({
  projectId,
  parent,
  variant,
  plan,
  manifest,
  onInstance,
}) {
  const trace = variant === "trace";
  const container = useRef(null);
  const playerRef = useRef(null);
  const [status, setStatus] = useState("loading");
  const [time, setTime] = useState(0);
  // Keep Pause available during buffering so pending play can be cancelled.
  const [playing, setPlaying] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [retryable, setRetryable] = useState(false);
  const peaks = useRecordingPeaks({
    projectId,
    parent,
    manifest,
    enabled: trace && status === "ready",
  });
  const durationMs = manifest.duration_ms;
  const duration = durationMs / 1000;
  const planKey = JSON.stringify(plan);

  useEffect(() => {
    // One engine for every layout: each lane (the mix, a stereo channel or a
    // separate track) gets one hidden element, authorized only as it plays.
    const lanes = recordingLanes(JSON.parse(planKey), durationMs);
    let released = false;
    const elements = lanes.map(() => {
      const element = document.createElement("audio");
      element.preload = "none";
      element.setAttribute("aria-hidden", "true");
      container.current.appendChild(element);
      return element;
    });
    const playback = new SequentialPlayback(
      elements,
      lanes,
      durationMs / 1000,
      (parts, signal, options) =>
        resolveAttachments(
          projectId,
          parts.map((part) => part.request),
          signal,
          options,
        ),
    );
    const release = () => {
      released = true;
      playback.destroy();
      playerRef.current = null;
      onInstance?.({ multitrack: null, wavesurfers: [] });
    };
    // A refused recording keeps no element, source or bytes behind.
    const remove = () => elements.forEach((element) => element.remove());
    playback.on("timeupdate", setTime);
    playback.on("intent", setPlaying);
    playback.on("buffering", setBuffering);
    playback.on("ready", () => {
      if (released) return;
      playerRef.current = playback;
      setStatus("ready");
      onInstance?.({ multitrack: playback, wavesurfers: [playback] });
    });
    playback.on("unavailable", ({ status: next, retryable: retry }) => {
      if (released) return;
      release();
      remove();
      setPlaying(false);
      setBuffering(false);
      setRetryable(retry);
      setStatus(next);
    });
    // A retried recording starts again at 0; never show the old position.
    setTime(0);
    setStatus("loading");
    setRetryable(false);
    playback.prepare();
    return () => {
      release();
      remove();
    };
  }, [projectId, planKey, durationMs, onInstance, attempt]);

  const at = time * 1000;
  const separate = manifest.layout === "separate_tracks";
  // Gaps are the manifest's own intervals: per track for separate tracks,
  // otherwise one shared timeline. Nothing is inferred from playback.
  const missing = manifest.gaps.filter((gap) => covers(gap, at));
  const missingText = separate
    ? missing
        .map((gap) => {
          const track = manifest.tracks.find(
            (candidate) => candidate.track_id === gap.track_id,
          );
          return `${trackLabels[track?.role] || trackLabels.unknown} track: ${gapLabels[gap.reason]}`;
        })
        .join("; ")
    : missing[0] && gapLabels[missing[0].reason];
  const playout = playoutAt(manifest.playout, at);
  return (
    <Stack spacing={1.5} sx={{ p: 2 }} aria-label="Attachment recording">
      <Box ref={container} sx={{ display: "none" }} />
      {status !== "ready" ? (
        <>
          <AttachmentStatus status={status} />
          {retryable && (
            <Button
              color="inherit"
              size="small"
              onClick={() => setAttempt((value) => value + 1)}
            >
              Retry recording
            </Button>
          )}
        </>
      ) : (
        <>
          {!trace && (
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              spacing={2}
            >
              <Typography variant="body2" fontWeight="fontWeightMedium">
                {layoutLabels[manifest.layout]}
              </Typography>
            </Stack>
          )}
          <Stack direction="row" alignItems="center" spacing={2}>
            <Button
              variant="contained"
              color="inherit"
              onClick={() =>
                playing
                  ? playerRef.current?.pause()
                  : void playerRef.current?.play()
              }
              aria-label={playing ? "Pause recording" : "Play recording"}
            >
              {trace ? (
                <Iconify
                  icon={playing ? "solar:pause-bold" : "solar:play-bold"}
                  width={16}
                />
              ) : playing ? (
                "Pause"
              ) : (
                "Play"
              )}
            </Button>
            {trace ? (
              <RecordingSeekBar
                seconds={time}
                duration={duration}
                onSeek={(v) => playerRef.current?.setTime(v)}
                peaks={peaks}
              />
            ) : (
              <Slider
                aria-label="Recording position"
                min={0}
                max={duration}
                step={0.1}
                value={time}
                onChange={(_, value) => playerRef.current?.setTime(value)}
                getAriaValueText={timeLabel}
                sx={{ color: "text.primary" }}
              />
            )}
            <Typography
              variant="caption"
              sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
            >
              {trace ? recordingClock(time, duration) : timeLabel(time)} /{" "}
              {trace ? recordingClock(duration, duration) : timeLabel(duration)}
            </Typography>
          </Stack>
          {buffering && (
            <Typography variant="caption" role="status">
              Buffering recording…
            </Typography>
          )}
          {!trace && (
            <Typography variant="caption" color="text.secondary">
              {manifest.tracks
                .map((track) => trackLabels[track.role])
                .join(" · ")}
            </Typography>
          )}
          {(!manifest.capture_complete || manifest.gaps.length > 0) && (
            <Typography variant="caption" color="warning.main">
              Partial recording — some audio is missing
              {missingText ? ` (${missingText} at this position)` : ""}.
            </Typography>
          )}
          {!trace && (
            <Typography variant="caption" color="text.secondary">
              {manifest.playout.length > 0 &&
              manifest.playout.every((part) => part.evidence === "played")
                ? "Playout confirmed for recorded intervals"
                : "Recording does not confirm what participants heard"}
            </Typography>
          )}
          {playout &&
            (!trace ||
              playout === playoutLabels.truncated ||
              playout.startsWith("Playout not confirmed")) && (
              <Typography variant="caption" color="text.secondary">
                {playout}
              </Typography>
            )}
        </>
      )}
    </Stack>
  );
}
ReadyRecording.propTypes = {
  parent: PropTypes.object.isRequired,
  variant: PropTypes.string.isRequired,
  projectId: PropTypes.string.isRequired,
  plan: PropTypes.object.isRequired,
  manifest: PropTypes.object.isRequired,
  onInstance: PropTypes.func,
};
