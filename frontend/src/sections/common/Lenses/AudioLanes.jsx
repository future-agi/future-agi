// The audio lanes of the Call lens: one reused
// `MultiTrackAudioPlayer` per run, each its documented [caller, agent] pair,
// registered on one SyncClock. The players' own transport is hidden (the
// lens transport drives every lane); lane heights are fixed, so nothing
// shifts while the audio loads. A lane without a recording keeps its slot
// and says why; a text-only lane says it is not comparable on latency.
import React, { useCallback, useEffect, useMemo, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, alpha, useTheme } from "@mui/material";
import MultiTrackAudioPlayer from "src/components/multi-track-audio-player/MultiTrackAudioPlayer";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fmtDuration } from "src/sections/common/Media/mediaRef";
import { useSeekableUrls } from "src/sections/common/Media/seekableUrl";
import LaneLabel from "./LaneLabel";

export const TRACK_H = 22;
export const LANE_H = TRACK_H * 2 + 6;
// IT-R2 G4 P6: a lane with no audio (text only / missing) is one slim row,
// so the transcript keeps the room.
export const NOTICE_H = 36;
/** The audio lanes' height for these calls (stable while loading). */
export const audioLanesHeight = (lanes, calls, gap = 6) =>
  (lanes ?? []).reduce((h, l, i) => {
    const state = laneAudioState(calls?.[l.runId]);
    return (
      h +
      (state === "text" || state === "missing" ? NOTICE_H : LANE_H) +
      (i ? gap : 0)
    );
  }, 0);
const CALLER_GREY = { light: "#64748B", dark: "#94A3B8" };

/** The ended line under a lane label: `Ended: resolved · 1 m 48 s`. */
export function endedLine(call, { passed } = {}) {
  if (!call) return "";
  const how =
    passed === true
      ? "resolved"
      : passed === false
        ? "not resolved"
        : call.ended_reason ?? call.status;
  return `Ended: ${how} · ${fmtDuration(call.duration_ms)}`;
}

function FitZoom({ instance, duration, containerRef }) {
  // wavesurfer-multitrack defaults to 10 px per second (horizontal scroll);
  // fit the whole call into the lane width and keep it fitted on resize.
  useEffect(() => {
    const mt = instance?.multitrack;
    const el = containerRef.current;
    if (!mt || !el || !duration) return undefined;
    let last = 0;
    const fitToWidth = () => {
      const w = el.offsetWidth;
      if (!w || w < 40) return;
      const px = Math.max(0.05, (w - 4) / duration);
      if (Math.abs(px - last) < 0.02) return;
      last = px;
      try {
        mt.zoom(px);
      } catch {
        /* not ready */
      }
    };
    fitToWidth();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(fitToWidth);
    ro.observe(el);
    return () => ro.disconnect();
  }, [instance, duration, containerRef]);
  return null;
}

FitZoom.propTypes = {
  instance: PropTypes.object,
  duration: PropTypes.number,
  containerRef: PropTypes.object,
};

function LaneAudio({
  lane,
  call,
  clock,
  agentColor,
  callerColor,
  axisSeconds,
  mark,
}) {
  const containerRef = useRef(null);
  const [instance, setInstance] = React.useState(null);
  // Exact seeks (turns, Align on turns, drift correction) need a seekable
  // source: see Media/seekableUrl.js.
  const seekable = useSeekableUrls([
    call.recording?.caller?.url,
    call.recording?.agent?.url,
  ]);
  const trackUrls = useMemo(
    () => [
      {
        url: seekable.urls[0],
        name: "Caller",
        color: callerColor,
        peaks: call.recording?.caller?.peaks,
      },
      {
        url: seekable.urls[1],
        name: "Agent",
        color: agentColor,
        peaks: call.recording?.agent?.peaks,
      },
    ],
    [call, agentColor, callerColor, seekable.urls],
  );
  const onInstance = useCallback(
    (inst) => {
      setInstance(inst);
      clock.register(lane.runId, inst);
    },
    [clock, lane.runId],
  );
  useEffect(
    () => () => {
      if (instance) clock.unregister(lane.runId, instance.multitrack);
    },
    [clock, lane.runId, instance],
  );
  return (
    <Box
      ref={containerRef}
      data-lane-audio={lane.runId}
      sx={{
        position: "relative",
        height: LANE_H,
        overflow: "hidden",
        minWidth: 0,
        flex: 1,
        // The reused player's own transport is driven by the lens transport.
        "& > .MuiStack-root > .MuiStack-root": { display: "none" },
        "& > .MuiStack-root > .MuiBox-root": {
          minHeight: `${LANE_H}px !important`,
          height: LANE_H,
          borderBottom: 0,
        },
        "& > .MuiStack-root > .MuiBox-root > .MuiBox-root:last-of-type": {
          minHeight: "0 !important",
        },
        "& .MuiTypography-s1": { typography: "s3" },
      }}
    >
      {seekable.ready ? (
        <MultiTrackAudioPlayer
          id={call.call_id}
          trackUrls={trackUrls}
          height={TRACK_H}
          allowDownload={false}
          onInstance={onInstance}
        />
      ) : (
        <Box
          sx={{
            height: LANE_H,
            borderRadius: 0.5,
            bgcolor: "background.neutral",
          }}
        />
      )}
      <FitZoom
        instance={instance}
        duration={axisSeconds || (call.duration_ms ?? 0) / 1000}
        containerRef={containerRef}
      />
      {/* IT-R1 G4: the differing turn, on every lane's waveform. */}
      {mark && axisSeconds > 0 ? (
        <Box
          aria-hidden
          data-diff-mark={mark.label}
          sx={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: `${Math.min(100, (mark.startMs / 1000 / axisSeconds) * 100)}%`,
            width: `${Math.max(0.6, ((mark.endMs - mark.startMs) / 1000 / axisSeconds) * 100)}%`,
            bgcolor: (t) => alpha(t.palette.warning.main, 0.16),
            borderLeft: "2px solid",
            borderColor: "warning.main",
            pointerEvents: "none",
            zIndex: 2,
          }}
        >
          <Typography
            typography="s3"
            sx={{
              position: "absolute",
              top: 0,
              left: 2,
              px: 0.25,
              fontSize: 10,
              lineHeight: "14px",
              color: "warning.dark",
              bgcolor: "background.paper",
              whiteSpace: "nowrap",
            }}
          >
            {mark.label}
          </Typography>
        </Box>
      ) : null}
    </Box>
  );
}

LaneAudio.propTypes = {
  lane: PropTypes.object.isRequired,
  call: PropTypes.object.isRequired,
  clock: PropTypes.object.isRequired,
  agentColor: PropTypes.string,
  callerColor: PropTypes.string,
  axisSeconds: PropTypes.number,
  mark: PropTypes.object,
};

function LaneNotice({ icon, color, text, tip }) {
  return (
    <CustomTooltip show={Boolean(tip)} title={tip} size="small" arrow>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{
          height: NOTICE_H,
          px: 1.5,
          flex: 1,
          minWidth: 0,
          borderRadius: 0.5,
          border: "1px dashed",
          borderColor: color === "warning" ? "warning.main" : "divider",
          bgcolor: (t) =>
            color === "warning"
              ? t.palette.mode === "dark"
                ? "rgba(245, 158, 11, 0.12)"
                : "rgba(245, 158, 11, 0.08)"
              : "background.neutral",
        }}
      >
        <Iconify
          icon={icon}
          width={16}
          sx={{
            color: color === "warning" ? "warning.dark" : "text.disabled",
            flexShrink: 0,
          }}
        />
        <Typography
          typography="s2"
          color={color === "warning" ? "warning.dark" : "text.secondary"}
          noWrap
        >
          {text}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}

LaneNotice.propTypes = {
  icon: PropTypes.string,
  color: PropTypes.string,
  text: PropTypes.string,
  tip: PropTypes.string,
};

/** What a lane can play: `audio` · `missing` · `text` · `loading`. */
export function laneAudioState(call) {
  if (!call) return "loading";
  if (call.depth === "text_only") return "text";
  if (!call.recording?.agent?.url && !call.recording?.caller?.url)
    return "missing";
  return "audio";
}

/**
 * @param {{lanes: object[], calls: Object<string, object|null>,
 *   clock: object, passedByRun?: Object<string, boolean>,
 *   labelWidth?: number}} props
 */
export default function AudioLanes({
  lanes,
  calls,
  clock,
  passedByRun = {},
  labelWidth = 220,
  marks = {},
}) {
  const theme = useTheme();
  const callerColor =
    theme.palette.mode === "dark" ? CALLER_GREY.dark : CALLER_GREY.light;
  // One time axis for every lane (px per second from the longest call), so
  // the playheads line up and a longer call reads longer.
  const axisSeconds = Math.max(
    0,
    ...lanes.map((l) => (calls[l.runId]?.duration_ms ?? 0) / 1000),
  );
  return (
    <Stack gap={0.75} data-audio-lanes="">
      {lanes.map((lane) => {
        const call = calls[lane.runId];
        const state = laneAudioState(call);
        return (
          <Stack
            key={lane.runId}
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ minWidth: 0 }}
            data-lane={lane.runId}
            data-lane-state={state}
          >
            <Box sx={{ width: labelWidth, flexShrink: 0, minWidth: 0 }}>
              <LaneLabel
                lane={lane}
                sub={endedLine(call, { passed: passedByRun[lane.runId] })}
              />
            </Box>
            <Stack
              aria-hidden={state !== "audio"}
              sx={{
                width: 40,
                flexShrink: 0,
                height:
                  state === "text" || state === "missing" ? NOTICE_H : LANE_H,
                justifyContent: "space-around",
              }}
            >
              {state === "audio"
                ? ["Caller", "Agent"].map((t) => (
                    <Typography
                      key={t}
                      typography="s3"
                      color="text.disabled"
                      noWrap
                      sx={{ lineHeight: `${TRACK_H}px` }}
                    >
                      {t}
                    </Typography>
                  ))
                : null}
            </Stack>
            {state === "loading" ? (
              <Box
                sx={{
                  height: LANE_H,
                  flex: 1,
                  borderRadius: 0.5,
                  bgcolor: "background.neutral",
                }}
              />
            ) : state === "text" ? (
              <LaneNotice
                icon="mdi:message-text-outline"
                text="Text only · not comparable on latency"
                tip="This run played the scenario as text, so there is no recording and no response gap to measure. Its transcript still lines up below."
              />
            ) : state === "missing" ? (
              <LaneNotice
                icon="mdi:alert-outline"
                color="warning"
                text={`Recording missing${call?.latency_source === "provider" ? " · latency reported by the provider" : ""}`}
                tip="The provider kept no recording for this call. The other lanes still play; this lane's latency comes from the provider's own numbers."
              />
            ) : (
              <LaneAudio
                lane={lane}
                call={call}
                clock={clock}
                callerColor={callerColor}
                agentColor={lane.color}
                axisSeconds={axisSeconds}
                mark={marks[lane.runId] ?? null}
              />
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}

AudioLanes.propTypes = {
  lanes: PropTypes.arrayOf(PropTypes.object).isRequired,
  calls: PropTypes.object.isRequired,
  clock: PropTypes.object.isRequired,
  passedByRun: PropTypes.object,
  labelWidth: PropTypes.number,
  marks: PropTypes.object,
};
