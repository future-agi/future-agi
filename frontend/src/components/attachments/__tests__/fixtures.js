// @ts-check
// Synthetic fixtures checked against the runtime schemas; no live credentials.
export const projectId = "10000000-0000-0000-0000-000000000001";
export const occurrenceId = "20000000-0000-0000-0000-000000000002";
export const parent = {
  trace_id: "30000000-0000-0000-0000-000000000003",
  span_id: "4000000000000004",
};
export const scope = {
  project_id: projectId,
  organization_id: "50000000-0000-0000-0000-000000000005",
  workspace_id: "60000000-0000-0000-0000-000000000006",
};
/** @satisfies {import("zod").infer<typeof import("../contract").manifestSchema>} */
export const mixedManifest = {
  schema_version: 1,
  recording_id: "70000000-0000-0000-0000-000000000007",
  timebase: "session_relative_ms",
  sample_format: "s16le",
  sample_rate_hz: 24000,
  channels: 2,
  codec: "pcm",
  container: "wav",
  layout: "mixed",
  tracks: [
    {
      track_id: "80000000-0000-0000-0000-000000000008",
      channels: [0, 1],
      direction: "mixed",
      role: "mixed",
    },
  ],
  segments: Array.from({ length: 240 }, (_, index) => ({
    occurrence_id: `b0000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`,
    index,
    start_ms: index * 30000,
    duration_ms: 30000,
  })),
  gaps: [],
  playout: [],
  capture_complete: true,
  duration_ms: 7200000,
};
/** @satisfies {import("zod").infer<typeof import("../contract").manifestSchema>} */
export const stereoManifest = {
  ...mixedManifest,
  layout: "stereo",
  tracks: [
    {
      track_id: "80000000-0000-0000-0000-000000000008",
      channels: [0],
      direction: "output",
      role: "assistant",
    },
    {
      track_id: "90000000-0000-0000-0000-000000000009",
      channels: [1],
      direction: "input",
      role: "user",
    },
  ],
};
/** @satisfies {import("zod").infer<typeof import("../contract").occurrenceSchema>} */
export const occurrence = {
  reference: {
    attachment_id: "a0000000-0000-0000-0000-00000000000a",
    occurrence_id: occurrenceId,
    direction: "mixed",
    modality: "audio",
    mime_type: "audio/wav",
  },
  scope,
  parent,
  state: "ready",
  binding: "bound",
  capture_complete: true,
  policy_version: "v1",
  deletion_generation: 0,
  expires_at: "2099-01-01T00:00:00Z",
};
/** @satisfies {import("zod").infer<typeof import("../contract").resolveResultSchema>} */
export const grant = {
  occurrence_id: occurrenceId,
  status: "ready",
  gateway_path: `/tracer/v1/attachment-media/${occurrenceId}/synthetic-grant`,
  expires_at: "2099-01-01T00:00:00Z",
};
export const projection = {
  occurrence_id: occurrenceId,
  parent,
  state: "ready",
  origin: "pipecat",
  manifest: mixedManifest,
};
