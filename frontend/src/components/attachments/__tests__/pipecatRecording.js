// @ts-check
import { gatewayPath } from "./fixtures";
// The `recording_attachment` projection of the actual Pipecat 1.15 call that
// TH-2379 acceptance recorded (synthetic staging data, no credentials): the
// stored manifest of trace 0b0af9fe… as PR #3941's enrich_voice_detail
// projects it, anchored on the first timeline segment. Keys, values and key
// order are kept verbatim from the saved evidence (observed-replay.json).
export const pipecatProjection = {
  occurrence_id: "b9603dcf-4b37-41a7-9c97-5e7ab9f2c780",
  parent: {
    trace_id: "0b0af9fe-f50f-417e-be6d-21accf8a4226",
    span_id: "7597a4efa95a498a",
  },
  state: "ready",
  manifest: {
    gaps: [],
    codec: "pcm",
    layout: "mixed",
    tracks: [
      {
        role: "mixed",
        channels: [0],
        track_id: "b1cc2da7-aca6-45b1-9184-17c62cd3bbe1",
        direction: "mixed",
      },
    ],
    playout: [],
    channels: 1,
    segments: [
      {
        index: 0,
        start_ms: 0,
        duration_ms: 30000,
        occurrence_id: "b9603dcf-4b37-41a7-9c97-5e7ab9f2c780",
      },
      {
        index: 1,
        start_ms: 30000,
        duration_ms: 6467,
        occurrence_id: "271de01f-2a09-4f34-a412-128b6e25874d",
      },
    ],
    timebase: "session_relative_ms",
    container: "wav",
    duration_ms: 36467,
    recording_id: "6e285218-faf9-481d-9fe5-3e5389ff091e",
    sample_format: "s16le",
    sample_rate_hz: 16000,
    schema_version: 1,
    capture_complete: true,
  },
};

// NOT observed: the three-segment shape of PR #3941's backend regression
// (test_attachment_voice_manifest durations 30000 + 30000 + 6467 ms), on the
// observed call's parent and encoding with synthetic occurrence ids.
const third = [
  "b9603dcf-4b37-41a7-9c97-5e7ab9f2c780",
  "c1a0e7d2-5b3f-4c19-8e6a-2f4d7b9e0a13",
  "271de01f-2a09-4f34-a412-128b6e25874d",
];
export const threeSegmentProjection = {
  ...pipecatProjection,
  manifest: {
    ...pipecatProjection.manifest,
    segments: [30000, 30000, 6467].map((duration_ms, index) => ({
      index,
      start_ms: index * 30000,
      duration_ms,
      occurrence_id: third[index],
    })),
    duration_ms: 66467,
  },
};

/**
 * A ready resolve result as playback.issue() returns it.
 * @param {string} occurrence_id
 * @param {string} [expires_at]
 */
export const segmentGrant = (
  occurrence_id,
  expires_at = "2099-01-01T00:00:00+00:00",
) => ({
  occurrence_id,
  status: "ready",
  gateway_path: gatewayPath(
    occurrence_id,
    `grant-${occurrence_id.slice(0, 8)}`,
  ),
  expires_at,
});
