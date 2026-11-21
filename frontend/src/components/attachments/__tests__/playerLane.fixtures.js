// @ts-check
// Recording projections in the shapes the pinned contract defines (traceAI
// f0d52e7a spec/attachments/v1 RecordingManifest + validate.py) and the
// Python recorder emits (fi_instrumentation.attachments.recording):
// - stereo: ONE shared timeline of two-channel WAV segments (<= 30 s, no
//   per-segment track_id), tracks mapped to channel 0 / channel 1 with their
//   declared roles; each channel is served as its own derivative;
// - separate_tracks: one mono WAV segment stream per track; every segment and
//   gap carries its track_id, coverage holds per track, indexes follow start
//   order, and segments on different tracks may start together.
// The Voice projection anchors on segment index 0 (view_enrichment). Synthetic
// identities only; no credentials.
import { gatewayPath } from "./fixtures";

/** @param {string} prefix @param {number} n */
const id = (prefix, n) =>
  `${prefix}-0000-4000-8000-${String(n).padStart(12, "0")}`;

export const laneParent = {
  trace_id: "0b0af9fef50f417ebe6d21accf8a4226",
  span_id: "7597a4efa95a498a",
};
export const userTrack = id("a1000000", 1);
export const assistantTrack = id("a2000000", 2);
const tracks = [
  { track_id: userTrack, channels: [0], direction: "input", role: "user" },
  {
    track_id: assistantTrack,
    channels: [1],
    direction: "output",
    role: "assistant",
  },
];
const pcm = {
  schema_version: 1,
  recording_id: id("b7000000", 7),
  timebase: "session_relative_ms",
  sample_format: "s16le",
  sample_rate_hz: 16000,
  channels: 2,
  codec: "pcm",
  container: "wav",
};

/** Three stereo segments: 30 s + 30 s + 6.467 s. */
export const stereoSegments = [1, 2, 3].map((n) => id("c5000000", n));
export const stereoProjection = {
  occurrence_id: stereoSegments[0],
  parent: laneParent,
  state: "ready",
  manifest: {
    ...pcm,
    layout: "stereo",
    tracks,
    segments: [30000, 30000, 6467].map((duration_ms, index) => ({
      occurrence_id: stereoSegments[index],
      index,
      start_ms: index * 30000,
      duration_ms,
    })),
    gaps: [],
    playout: [],
    capture_complete: true,
    duration_ms: 66467,
  },
};

/**
 * Separate tracks, 66.467 s. The user track lost 2 s at 30 s (source
 * unavailable), so its segments start at 0, 32 and 62 s while the assistant's
 * start at 0, 30 and 60 s. Agent playout: the first half-minute was played,
 * the next was generated without playout confirmation.
 */
export const userSegments = [1, 2, 3].map((n) => id("d1000000", n));
export const assistantSegments = [1, 2, 3].map((n) => id("d2000000", n));
export const tracksProjection = {
  occurrence_id: userSegments[0],
  parent: laneParent,
  state: "ready",
  manifest: {
    ...pcm,
    layout: "separate_tracks",
    tracks,
    segments: [
      [userSegments[0], userTrack, 0, 30000],
      [assistantSegments[0], assistantTrack, 0, 30000],
      [assistantSegments[1], assistantTrack, 30000, 30000],
      [userSegments[1], userTrack, 32000, 30000],
      [assistantSegments[2], assistantTrack, 60000, 6467],
      [userSegments[2], userTrack, 62000, 4467],
    ].map(([occurrence_id, track_id, start_ms, duration_ms], index) => ({
      occurrence_id,
      index,
      start_ms,
      duration_ms,
      track_id,
    })),
    gaps: [
      {
        start_ms: 30000,
        duration_ms: 2000,
        reason: "source_unavailable",
        track_id: userTrack,
      },
    ],
    playout: [
      { start_ms: 0, duration_ms: 30000, evidence: "played" },
      { start_ms: 30000, duration_ms: 30000, evidence: "generated" },
    ],
    capture_complete: false,
    duration_ms: 66467,
  },
};

/**
 * A ready result per requested item, each its own grant token, as the
 * gateway issues them (one grant per occurrence/representation/channel).
 * @param {{occurrence_id: string}} item
 * @param {number} n
 * @param {number} [lifetimeMs]
 */
export const issued = (item, n, lifetimeMs = 60000) => ({
  occurrence_id: item.occurrence_id,
  status: "ready",
  gateway_path: gatewayPath(item.occurrence_id, `grant-${n}`),
  expires_at: new Date(Date.now() + lifetimeMs).toISOString(),
});
