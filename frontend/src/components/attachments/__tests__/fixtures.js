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
  // tracer.services.attachments.playback: expiry.isoformat(), UTC offset retained.
  expires_at: "2099-01-01T00:00:00+00:00",
};
/** @satisfies {import("zod").infer<typeof import("../contract").resolveResultSchema>} */
export const grant = {
  occurrence_id: occurrenceId,
  status: "ready",
  gateway_path: `/tracer/v1/attachment-media/${occurrenceId}/synthetic-grant`,
  // tracer.services.attachments.playback: expiry.isoformat(), UTC offset retained.
  expires_at: "2099-01-01T00:00:00+00:00",
};
// I04 as shipped by tracer view_enrichment: no origin, manifest only if ready.
export const projection = {
  occurrence_id: occurrenceId,
  parent,
  state: "ready",
  manifest: mixedManifest,
};
/** A ready per-span attachment status as projected on observation spans. */
export function spanImage(index, { reference = {}, ...rest } = {}) {
  const id = String(index + 1).padStart(12, "0");
  return {
    reference: {
      attachment_id: `c0000000-0000-0000-0000-${id}`,
      occurrence_id: `d0000000-0000-0000-0000-${id}`,
      direction: "input",
      modality: "image",
      mime_type: "image/png",
      purpose: "model_input",
      message_index: 0,
      part_index: index,
      ...reference,
    },
    status: "ready",
    capture_complete: true,
    ...rest,
  };
}
export const readyGrant = (occurrence_id) => ({
  occurrence_id,
  status: "ready",
  gateway_path: `/tracer/v1/attachment-media/${occurrence_id}/grant-${occurrence_id.slice(-4)}`,
  expires_at: "2099-01-01T00:00:00+00:00",
});
