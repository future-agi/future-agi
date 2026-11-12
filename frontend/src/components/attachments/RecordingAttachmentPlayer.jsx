import { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Slider, Stack, Typography } from "@mui/material";
import { recordingProjectionSchema, recordingRequests, uuid } from "./contract";
import { resolveAttachments } from "./client";
import StreamingPlayback from "./StreamingPlayback";
import AttachmentStatus from "./AttachmentStatus";

const timeLabel = (seconds) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
const trackLabels = {
  user: "User",
  assistant: "Assistant",
  mixed: "Mixed recording",
  unknown: "Unassigned track",
};

// data.recording_attachment is an authorized read projection, NOT an SDK span
// attribute. Its presence always takes precedence over the legacy URL renderer.
export default function RecordingAttachmentPlayer({
  projectId,
  attachment,
  onInstance,
}) {
  // I04 shape: {occurrence_id, parent, state} plus manifest only when ready,
  // or a bare {state: "rejected"}. Anything else is shown as a coarse state.
  const parsed = useMemo(() => {
    if (!uuid.safeParse(projectId).success) return null;
    const result = recordingProjectionSchema.safeParse(attachment);
    return result.success ? result.data : null;
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
      attachment={parsed}
      manifest={parsed.manifest}
      onInstance={onInstance}
    />
  );
}
RecordingAttachmentPlayer.propTypes = {
  projectId: PropTypes.string,
  attachment: PropTypes.object,
  onInstance: PropTypes.func,
};

function ReadyRecording({ projectId, attachment, manifest, onInstance }) {
  const container = useRef(null);
  const playerRef = useRef(null);
  const [status, setStatus] = useState("loading");
  const [time, setTime] = useState(0);
  // Keep Pause available during buffering so pending play can be cancelled.
  const [playing, setPlaying] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [retryable, setRetryable] = useState(false);
  const duration = manifest.duration_ms / 1000;
  const requestKey = JSON.stringify(
    recordingRequests(attachment.occurrence_id, attachment.parent, manifest),
  );

  useEffect(() => {
    const controller = new AbortController();
    let renewal;
    let stopped = false;
    let player;
    let refreshing = false;
    let recoveryUsed = false;
    const stop = (next) => {
      stopped = true;
      clearTimeout(renewal);
      controller.abort();
      player?.destroy();
      playerRef.current = null;
      onInstance?.({ multitrack: null, wavesurfers: [] });
      setPlaying(false);
      setStatus(next);
    };
    const refresh = async () => {
      if (stopped || refreshing) return;
      refreshing = true;
      clearTimeout(renewal);
      try {
        const results = await resolveAttachments(
          projectId,
          JSON.parse(requestKey),
          controller.signal,
        );
        if (stopped) return;
        // Do not play an allowed half of a denied mix, or reconstruct directions.
        const unavailable = results.find((item) => item.status !== "ready");
        if (unavailable) {
          stop(unavailable.status);
          return;
        }
        if (!player) {
          const elements = results.map(() => {
            const element = document.createElement("audio");
            element.preload = "none";
            element.setAttribute("aria-hidden", "true");
            container.current.appendChild(element);
            return element;
          });
          player = new StreamingPlayback(elements, duration);
          playerRef.current = player;
          player.on("timeupdate", setTime);
          player.on("intent", setPlaying);
          player.on("buffering", setBuffering);
          player.on("error", () => {
            // One bounded reauthorization attempt, never a provider URL retry.
            if (recoveryUsed) {
              setRetryable(true);
              stop("not_found");
              return;
            }
            recoveryUsed = true;
            void refresh();
          });
          player.replaceSources(results.map((item) => item.gateway_path));
          onInstance?.({ multitrack: player, wavesurfers: [player] });
        } else player.replaceSources(results.map((item) => item.gateway_path));
        setStatus("ready");
        const expiry = Math.min(
          ...results.map((item) => Date.parse(item.expires_at)),
        );
        // Never spin on a near-expired grant. Backend must issue usable leases.
        if (expiry - Date.now() < 2000) {
          stop("not_found");
          return;
        }
        renewal = setTimeout(
          refresh,
          Math.min(2147483647, Math.max(1000, expiry - Date.now() - 10000)),
        );
      } catch {
        if (!stopped) {
          setRetryable(true);
          stop("not_found");
        }
      } finally {
        refreshing = false;
      }
    };
    setStatus("loading");
    setRetryable(false);
    void refresh();
    return () => {
      stopped = true;
      clearTimeout(renewal);
      controller.abort();
      player?.destroy();
      player?.elements.forEach((element) => element.remove());
      playerRef.current = null;
      onInstance?.({ multitrack: null, wavesurfers: [] });
    };
  }, [projectId, requestKey, duration, onInstance, attempt]);

  const currentGap = manifest.gaps.find(
    (gap) =>
      time * 1000 >= gap.start_ms &&
      time * 1000 < gap.start_ms + gap.duration_ms,
  );
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
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            spacing={2}
          >
            <Typography variant="body2" fontWeight="fontWeightMedium">
              {manifest.layout === "mixed"
                ? "Mixed recording"
                : "Separate recording tracks"}
            </Typography>
          </Stack>
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
              {playing ? "Pause" : "Play"}
            </Button>
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
            <Typography
              variant="caption"
              sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
            >
              {timeLabel(time)} / {timeLabel(duration)}
            </Typography>
          </Stack>
          {buffering && (
            <Typography variant="caption" role="status">
              Buffering recording…
            </Typography>
          )}
          <Typography variant="caption" color="text.secondary">
            {manifest.tracks
              .map((track) => trackLabels[track.role])
              .join(" · ")}
          </Typography>
          {(!manifest.capture_complete || manifest.gaps.length > 0) && (
            <Typography variant="caption" color="warning.main">
              Partial recording — some audio is missing
              {currentGap
                ? ` (${currentGap.reason === "truncated" ? "truncated" : "gap"} at this position)`
                : ""}
              .
            </Typography>
          )}
          <Typography variant="caption" color="text.secondary">
            {manifest.playout.length > 0 &&
            manifest.playout.every((part) => part.evidence === "played")
              ? "Playout confirmed for recorded intervals"
              : "Recording does not confirm what participants heard"}
          </Typography>
        </>
      )}
    </Stack>
  );
}
ReadyRecording.propTypes = {
  projectId: PropTypes.string.isRequired,
  attachment: PropTypes.object.isRequired,
  manifest: PropTypes.object.isRequired,
  onInstance: PropTypes.func,
};
