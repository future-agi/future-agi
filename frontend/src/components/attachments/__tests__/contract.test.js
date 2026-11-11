import { describe, expect, it } from "vitest";
import {
  manifestSchema,
  occurrenceFromUri,
  recordingRequests,
  resolveResponseSchema,
} from "../contract";
import {
  grant,
  mixedManifest,
  occurrenceId,
  parent,
  stereoManifest,
} from "./fixtures";

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
    expect(recordingRequests(occurrenceId, parent, mixedManifest)).toEqual([
      { occurrence_id: occurrenceId, parent, representation: "playable" },
    ]);
  });
  it("uses explicit channel mapping, not left equals customer", () => {
    expect(recordingRequests(occurrenceId, parent, stereoManifest)).toEqual([
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
