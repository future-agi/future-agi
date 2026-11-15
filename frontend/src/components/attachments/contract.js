import { z } from "zod";

// Read-side subset of fi.attachments.v1. Never derive media URLs from telemetry.
export const uuid = z
  .string()
  .regex(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/)
  .refine((v) => /[1-9a-f]/.test(v));
const traceId = z
  .string()
  .regex(/^(?:[0-9a-f]{32}|[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12})$/)
  .refine((v) => /[1-9a-f]/.test(v));
export const parentSchema = z
  .object({
    trace_id: traceId,
    span_id: z
      .string()
      .regex(/^[0-9a-f]{16}$/)
      .refine((v) => /[1-9a-f]/.test(v)),
  })
  .strict();
export const scopeSchema = z
  .object({ organization_id: uuid, workspace_id: uuid, project_id: uuid })
  .strict();
// F01 Timestamp is RFC3339 date-time (max 40), not UTC-Z-only. Python's
// datetime.isoformat() serializes UTC as +00:00. Preserve the wire spelling.
const rfc3339 = z.string().datetime({ offset: true });
export const timestampSchema = z
  .string()
  .max(40)
  .refine(
    (value) =>
      rfc3339.safeParse(value.toUpperCase()).success &&
      /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) &&
      Number.isFinite(Date.parse(value)),
  );
const direction = z.enum(["input", "output", "mixed"]);
const token = z.string().regex(/^[a-zA-Z0-9_-]{1,128}$/);
const mediaTypes = {
  audio: [
    "audio/wav",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/webm",
    "audio/flac",
    "audio/L16",
  ],
  image: ["image/png", "image/jpeg", "image/webp"],
  video: ["video/mp4", "video/webm"],
  document: ["application/pdf"],
  file: ["application/octet-stream"],
};
export const referenceSchema = z
  .object({
    attachment_id: uuid,
    occurrence_id: uuid,
    direction,
    modality: z.enum(["audio", "image", "video", "document", "file"]),
    mime_type: z.string().max(128),
    purpose: z
      .enum([
        "conversation_recording",
        "turn_recording",
        "model_input",
        "model_output",
        "tool_result",
        "computer_screenshot",
        "diagnostic",
        "document",
      ])
      .optional(),
    conversation_id: token.optional(),
    turn_id: token.optional(),
    tool_call_id: token.optional(),
    message_index: z.number().int().min(0).max(65535).optional(),
    part_index: z.number().int().min(0).max(65535).optional(),
    role: z.enum(["user", "assistant", "tool", "system", "unknown"]).optional(),
    channel: z.number().int().min(0).max(31).optional(),
  })
  .strict()
  .refine((ref) => mediaTypes[ref.modality].includes(ref.mime_type));
export const occurrenceSchema = z
  .object({
    reference: referenceSchema,
    scope: scopeSchema,
    parent: parentSchema,
    state: z.enum([
      "pending_upload",
      "importing",
      "validating",
      "ready",
      "failed",
      "rejected",
      "expired",
      "deleted",
    ]),
    binding: z.enum(["pending", "bound"]),
    capture_complete: z.boolean(),
    policy_version: token,
    deletion_generation: z.number().int().nonnegative(),
    expires_at: timestampSchema,
    recording_id: uuid.optional(),
  })
  .strict();
export const occurrencePageSchema = z
  .object({
    items: z.array(occurrenceSchema).max(100),
    next_cursor: z
      .string()
      .regex(/^[a-zA-Z0-9_-]{1,128}$/)
      .nullable(),
  })
  .strict();
export const resolveItemSchema = z
  .object({
    occurrence_id: uuid,
    parent: parentSchema,
    // F01 also defines peaks/thumbnail; the backend serves only original and
    // playable, so the UI never requests a derivative that cannot exist.
    representation: z.enum(["original", "playable", "channel"]),
    channel: z.number().int().min(0).max(31).optional(),
  })
  .strict()
  .refine(
    (v) => (v.representation === "channel") === (v.channel !== undefined),
  );
export const resolveResultSchema = z
  .discriminatedUnion("status", [
    z
      .object({
        occurrence_id: uuid,
        status: z.literal("ready"),
        gateway_path: z
          .string()
          .regex(
            /^\/tracer\/v1\/attachment-media\/(?!00000000-0000-0000-0000-000000000000\/)[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\/[a-zA-Z0-9_-]{1,128}$/,
          )
          .max(194),
        expires_at: timestampSchema,
      })
      .strict(),
    ...["not_found", "pending", "failed", "expired", "deleted"].map((status) =>
      z.object({ occurrence_id: uuid, status: z.literal(status) }).strict(),
    ),
  ])
  .refine(
    (item) =>
      item.status !== "ready" ||
      ("gateway_path" in item &&
        item.gateway_path.split("/")[4] === item.occurrence_id),
    { message: "Gateway occurrence mismatch" },
  );
export const resolveResponseSchema = z
  .object({ items: z.array(resolveResultSchema).max(64) })
  .strict();
const ms = z.number().int().min(0).max(7200000);
const interval = { start_ms: ms, duration_ms: ms.refine((v) => v > 0) };
export const manifestSchema = z
  .object({
    schema_version: z.literal(1),
    recording_id: uuid,
    timebase: z.literal("session_relative_ms"),
    sample_format: z.enum(["s16le", "s24le", "s32le", "f32le", "encoded"]),
    sample_rate_hz: z.number().int().min(8000).max(192000),
    channels: z.number().int().min(1).max(32),
    codec: z.enum(["pcm", "opus", "mp3", "aac", "flac"]),
    container: z.enum(["wav", "ogg", "mp3", "mp4", "webm", "flac", "raw"]),
    layout: z.enum(["mixed", "stereo", "separate_tracks"]),
    tracks: z
      .array(
        z
          .object({
            track_id: uuid,
            channels: z.array(z.number().int().min(0).max(31)).min(1).max(32),
            direction,
            role: z.enum(["user", "assistant", "mixed", "unknown"]),
          })
          .strict(),
      )
      .min(1)
      .max(32),
    // The UI never decodes or joins segments: it plays each occurrence's
    // served bytes in turn (see recordingPlan).
    segments: z
      .array(
        z
          .object({
            occurrence_id: uuid,
            index: z.number().int().min(0).max(65535),
            start_ms: ms,
            duration_ms: z.number().int().min(1).max(30000),
            track_id: uuid.optional(),
          })
          .strict(),
      )
      .max(65536),
    gaps: z
      .array(
        z
          .object({
            ...interval,
            track_id: uuid.optional(),
            reason: z.enum([
              "queue_full",
              "bootstrap_pending",
              "source_unavailable",
              "policy_denied",
              "truncated",
              "incomplete",
            ]),
          })
          .strict(),
      )
      .max(65536),
    playout: z
      .array(
        z
          .object({
            ...interval,
            evidence: z.enum(["generated", "played", "unknown", "truncated"]),
          })
          .strict(),
      )
      .max(65536),
    capture_complete: z.boolean(),
    duration_ms: ms.refine((v) => v > 0),
  })
  .strict()
  .superRefine((m, ctx) => {
    const channels = m.tracks.flatMap((t) => t.channels);
    if (
      channels.length !== m.channels ||
      new Set(channels).size !== channels.length ||
      channels.some((n) => n >= m.channels) ||
      new Set(m.tracks.map((t) => t.track_id)).size !== m.tracks.length
    )
      ctx.addIssue({ code: "custom", message: "Invalid channel mapping" });
    if (
      m.layout === "mixed" &&
      (m.tracks.length !== 1 ||
        m.tracks[0].direction !== "mixed" ||
        m.tracks[0].role !== "mixed")
    )
      ctx.addIssue({ code: "custom", message: "Invalid mixed track" });
    if (m.layout !== "mixed" && m.tracks.some((t) => t.channels.length !== 1))
      ctx.addIssue({ code: "custom", message: "Channel derivatives required" });
    if (
      [...m.segments, ...m.gaps, ...m.playout].some(
        (g) => g.start_ms + g.duration_ms > m.duration_ms,
      )
    )
      ctx.addIssue({ code: "custom", message: "Invalid interval" });
    if (m.capture_complete && m.gaps.length)
      ctx.addIssue({ code: "custom", message: "Incomplete capture" });
    const timelines = new Map(
      m.layout === "separate_tracks"
        ? m.tracks.map((track) => [track.track_id, []])
        : [["shared", []]],
    );
    for (const part of [...m.segments, ...m.gaps]) {
      const key = m.layout === "separate_tracks" ? part.track_id : "shared";
      if (
        !timelines.has(key) ||
        (m.layout !== "separate_tracks" && part.track_id)
      ) {
        ctx.addIssue({ code: "custom", message: "Invalid timeline" });
        return;
      }
      timelines.get(key).push(part);
    }
    for (const timeline of timelines.values()) {
      let end = 0;
      for (const part of timeline.sort((a, b) => a.start_ms - b.start_ms)) {
        if (part.start_ms !== end) {
          ctx.addIssue({
            code: "custom",
            message: "Unaccounted recording interval",
          });
          break;
        }
        end = part.start_ms + part.duration_ms;
      }
      if (end !== m.duration_ms)
        ctx.addIssue({ code: "custom", message: "Incomplete timeline" });
    }
  });

// Per-span read projection added by tracer view_enrichment to an observation
// span's `attachments`. Statuses are coarse: `pending` = not yet bound to the
// span, `partial` = ready but capture incomplete. Never a URL or grant.
export const spanAttachmentSchema = z
  .object({
    reference: referenceSchema,
    status: z.enum([
      "pending",
      "pending_upload",
      "importing",
      "validating",
      "ready",
      "partial",
      "failed",
    ]),
    capture_complete: z.boolean(),
    recording_id: uuid.optional(),
  })
  .strict();
export const spanAttachmentsSchema = z.array(spanAttachmentSchema).max(64);

// I04 Voice projection (`data.recording_attachment`). `manifest` exists only
// when the server proved the recording playable as-is; two recordings on one
// root are projected as a bare rejected state rather than guessed between.
export const recordingProjectionSchema = z.union([
  z.object({ state: z.literal("rejected") }).strict(),
  z
    .object({
      occurrence_id: uuid,
      parent: parentSchema,
      state: z.enum([
        "pending",
        "pending_upload",
        "importing",
        "validating",
        "waiting",
        "failed",
      ]),
    })
    .strict(),
  z
    .object({
      occurrence_id: uuid,
      parent: parentSchema,
      state: z.literal("ready"),
      manifest: manifestSchema,
    })
    .strict(),
]);

/** @typedef {import('zod').infer<typeof manifestSchema>} RecordingManifest */
/** @typedef {import('zod').infer<typeof resolveItemSchema>} ResolveItem */
/** @typedef {import('zod').infer<typeof scopeSchema>} AttachmentScope */

/**
 * How a ready recording plays, from its manifest alone (throws when unsafe).
 * - `channels`: stereo/separate tracks play simultaneously from server channel
 *   derivatives of the anchor occurrence.
 * - `file`: every timeline tile names the anchor, with no gaps. A standalone
 *   file longer than 30 s is tiled in the manifest but is still ONE file.
 * - `sequence`: distinct segment occurrences of one logical recording play one
 *   after another at their own `start_ms`, with gaps kept as silent time.
 *   F01 order is required as given (index = position, unique identities,
 *   start order); a malformed timeline is refused, never sorted into shape.
 */
export function recordingPlan(occurrenceId, parent, manifest) {
  uuid.parse(occurrenceId);
  parentSchema.parse(parent);
  const m = manifestSchema.parse(manifest);
  const playable = (occurrence_id) => ({
    occurrence_id,
    parent,
    representation: "playable",
  });
  if (m.layout !== "mixed")
    return {
      mode: "channels",
      requests: m.tracks.map((track) => ({
        occurrence_id: occurrenceId,
        parent,
        representation: "channel",
        channel: track.channels[0],
      })),
    };
  if (!m.segments.length) throw new Error("No recorded segment");
  if (
    !m.gaps.length &&
    m.segments.every((segment) => segment.occurrence_id === occurrenceId)
  )
    return { mode: "file", requests: [playable(occurrenceId)] };
  const seen = new Set();
  m.segments.forEach((segment, index) => {
    if (
      segment.index !== index ||
      (index > 0 && segment.start_ms <= m.segments[index - 1].start_ms) ||
      seen.has(segment.occurrence_id)
    )
      throw new Error("Unordered or repeated segment");
    seen.add(segment.occurrence_id);
  });
  // The projection anchors on its first timeline segment; anything else
  // belongs to a different recording.
  if (m.segments[0].occurrence_id !== occurrenceId)
    throw new Error("Anchor is not the first segment");
  const parts = [
    ...m.segments.map(({ occurrence_id, start_ms, duration_ms }) => ({
      kind: "segment",
      occurrence_id,
      start_ms,
      duration_ms,
      request: playable(occurrence_id),
    })),
    ...m.gaps.map(({ start_ms, duration_ms }) => ({
      kind: "gap",
      start_ms,
      duration_ms,
    })),
  ].sort((a, b) => a.start_ms - b.start_ms);
  return {
    mode: "sequence",
    parts,
    requests: m.segments.map((segment) => playable(segment.occurrence_id)),
  };
}

export function recordingRequests(occurrenceId, parent, manifest) {
  return recordingPlan(occurrenceId, parent, manifest).requests;
}

export function occurrenceFromUri(value) {
  if (typeof value !== "string" || !value.startsWith("fi-attachment:"))
    return null;
  const id = value.slice("fi-attachment:".length);
  return uuid.safeParse(id).success ? id : null;
}
