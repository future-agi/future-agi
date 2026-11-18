import { describe, expect, it } from "vitest";
import {
  manifestSchema,
  occurrenceFromUri,
  recordingPlan,
  recordingRequests,
  resolveResponseSchema,
} from "../contract";
import {
  grant,
  mixedManifest,
  occurrenceId,
  parent,
  standaloneManifest,
  stereoManifest,
} from "./fixtures";
import { pipecatProjection, threeSegmentProjection } from "./pipecatRecording";
import { validateContractedResponse } from "src/api/contracts/openapi-contract";

describe("attachment read contract", () => {
  it("resolves an occurrence, not a blob or URL", () => {
    expect(occurrenceFromUri(`fi-attachment:${occurrenceId}`)).toBe(
      occurrenceId,
    );
    [
      "https://source.test/private?canary=secret",
      "fi-attachment:../../path",
      "fi-attachment:00000000-0000-0000-0000-000000000000",
      `fi-attachment:${occurrenceId}?token=secret`,
    ].forEach((uri) => expect(occurrenceFromUri(uri)).toBeNull());
  });
  it("requests one playable mixed recording without inferring speakers", () => {
    expect(recordingRequests(occurrenceId, parent, standaloneManifest)).toEqual(
      [{ occurrence_id: occurrenceId, parent, representation: "playable" }],
    );
  });
  it("uses explicit channel mapping, not left equals customer", () => {
    const requests = recordingRequests(occurrenceId, parent, stereoManifest);
    // Each track's channel derivative of EVERY segment, never the anchor's
    // channels stretched over the whole call.
    expect(requests).toHaveLength(stereoManifest.segments.length * 2);
    expect(requests.slice(0, 2)).toEqual([
      {
        occurrence_id: occurrenceId,
        parent,
        representation: "channel",
        channel: 0,
      },
      {
        occurrence_id: occurrenceId,
        parent,
        representation: "channel",
        channel: 1,
      },
    ]);
    expect(requests.slice(-2).map((item) => item.occurrence_id)).toEqual(
      Array(2).fill(stereoManifest.segments[239].occurrence_id),
    );
    expect(stereoManifest.tracks[0].role).toBe("assistant");
  });
  it.each([
    "https://source.test/private",
    "//source.test/private",
    "data:audio/wav;base64,secret",
    "/tracer/v1/attachment-media/../secret",
    `${grant.gateway_path}?secret=canary`,
    `${grant.gateway_path}#secret`,
  ])("denies non-gateway or secret-bearing media path %s", (gateway_path) => {
    expect(
      resolveResponseSchema.safeParse({ items: [{ ...grant, gateway_path }] })
        .success,
    ).toBe(false);
  });
  it("rejects source metadata even alongside a valid gateway", () => {
    expect(
      resolveResponseSchema.safeParse({
        items: [{ ...grant, source_url: "https://source.test/private" }],
      }).success,
    ).toBe(false);
  });
  it("rejects contradictory mixed, duplicate channel and oversized manifests", () => {
    for (const patch of [
      { duration_ms: 7200001 },
      { tracks: stereoManifest.tracks },
      { tracks: [{ ...mixedManifest.tracks[0], channels: [0, 0] }] },
      { tracks: [{ ...mixedManifest.tracks[0], channels: [2] }] },
      {
        gaps: [{ start_ms: 7199000, duration_ms: 2000, reason: "incomplete" }],
      },
    ])
      expect(
        manifestSchema.safeParse({ ...mixedManifest, ...patch }).success,
      ).toBe(false);
  });
});

describe("media-session exchange response contract", () => {
  // The exchange answers 204 with no body. Without a declared 204 the dev
  // response check validated "" against the error default and warned on
  // every exchange (one console warning per image batch / recording play).
  it("accepts the bodiless 204 the session exchange returns", () => {
    const result = validateContractedResponse({
      config: { url: "/tracer/v1/attachment-media-sessions", method: "post" },
      status: 204,
      data: "",
    });
    expect(result.skipped).not.toBe(true);
    expect(result.ok).toBe(true);
    expect(result.endpoint.template).toBe(
      "/tracer/v1/attachment-media-sessions",
    );
  });
});

describe("unit: recording playback plan from the manifest (TH-2379)", () => {
  const plan = ({ occurrence_id, parent: owner, manifest }) =>
    recordingPlan(occurrence_id, owner, manifest);
  it("plays the actual two-segment Pipecat call as a sequence on the timeline", () => {
    const { mode, parts, requests } = plan(pipecatProjection);
    const ids = pipecatProjection.manifest.segments.map(
      (segment) => segment.occurrence_id,
    );
    expect(mode).toBe("sequence");
    expect(
      parts.map(({ kind, occurrence_id, start_ms, duration_ms }) => [
        kind,
        occurrence_id,
        start_ms,
        duration_ms,
      ]),
    ).toEqual([
      ["segment", ids[0], 0, 30000],
      ["segment", ids[1], 30000, 6467],
    ]);
    expect(requests).toEqual(
      ids.map((occurrence_id) => ({
        occurrence_id,
        parent: pipecatProjection.parent,
        representation: "playable",
      })),
    );
    expect(
      plan(threeSegmentProjection).parts.map((part) => part.start_ms),
    ).toEqual([0, 30000, 60000]);
  });
  it("keeps a standalone file tiled on the timeline as ONE occurrence", () => {
    expect(recordingPlan(occurrenceId, parent, standaloneManifest)).toEqual({
      mode: "file",
      requests: [
        { occurrence_id: occurrenceId, parent, representation: "playable" },
      ],
    });
  });
  it("keeps separate channels simultaneous, whatever the segments", () => {
    expect(recordingPlan(occurrenceId, parent, stereoManifest).mode).toBe(
      "channels",
    );
  });
  it("keeps a gap in place between segments instead of closing it", () => {
    const { manifest } = pipecatProjection;
    const { parts } = plan({
      ...pipecatProjection,
      manifest: {
        ...manifest,
        capture_complete: false,
        segments: [
          manifest.segments[0],
          { ...manifest.segments[1], start_ms: 32000 },
        ],
        gaps: [{ start_ms: 30000, duration_ms: 2000, reason: "truncated" }],
        duration_ms: 38467,
      },
    });
    expect(parts.map((part) => [part.kind, part.start_ms])).toEqual([
      ["segment", 0],
      ["gap", 30000],
      ["segment", 32000],
    ]);
  });
  const [a, b] = pipecatProjection.manifest.segments;
  it.each([
    [
      "an anchor that is not the first segment",
      { occurrence_id: b.occurrence_id },
    ],
    [
      "indexes that disagree with timeline order",
      {
        manifest: {
          ...pipecatProjection.manifest,
          segments: [
            { ...a, index: 1 },
            { ...b, index: 0 },
          ],
        },
      },
    ],
    [
      "segments listed against their timeline order",
      {
        manifest: {
          ...pipecatProjection.manifest,
          segments: [b, a],
        },
      },
    ],
    [
      "one occurrence repeated among other segments",
      {
        manifest: {
          ...pipecatProjection.manifest,
          segments: [a, b, { ...a, index: 2, start_ms: 36467, duration_ms: 1 }],
          duration_ms: 36468,
        },
      },
    ],
  ])("refuses %s rather than reorder it", (_, change) => {
    const projection = { ...pipecatProjection, ...change };
    // Each is a schema-valid timeline; only the playback order is unsafe.
    expect(manifestSchema.safeParse(projection.manifest).success).toBe(true);
    expect(() => plan(projection)).toThrow();
  });
});
