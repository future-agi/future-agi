import { describe, it, expect } from "vitest";
import { decodePeaks, peakDescriptor, peakOverview } from "../recordingPeaks";
import { resolveItemSchema } from "../contract";
import { parent, occurrenceId } from "./fixtures";
const descriptor = {
  v: 1,
  sample_rate: 8000,
  channels: 2,
  frame_count: 81,
  levels: [
    { frames_per_bucket: 80, buckets: 2 },
    { frames_per_bucket: 800, buckets: 1 },
    { frames_per_bucket: 8000, buckets: 1 },
  ],
};
const values = [
  -128, 52, 0, 0, -7, 125, 0, 0, -128, 52, -7, 125, -128, 52, -7, 125,
];
const bytes = new ArrayBuffer(values.length * 2);
values.forEach((v, i) => new DataView(bytes).setInt16(2 * i, v, true));
describe("authorized peak pyramid", () => {
  it("decodes signed LE pairs, aggregates asymmetric channels and preserves a final silent frame", () => {
    const d = peakDescriptor(JSON.stringify(descriptor), 32);
    expect(decodePeaks(d, bytes)).toEqual({
      pairs: [
        [-128, 125],
        [0, 0],
      ],
      width: 80,
      frames: 81,
    });
  });
  it.each([
    { v: 2 },
    { sample_rate: 1 },
    { frame_count: 0 },
    { channels: 3 },
    { levels: [...descriptor.levels].reverse() },
    { levels: [] },
    { frame_count: 9999999 },
  ])("rejects invalid descriptor %j", (v) => {
    expect(() =>
      peakDescriptor(JSON.stringify({ ...descriptor, ...v }), 32),
    ).toThrow();
  });
  it.each([null, "", "x".repeat(1025)])(
    "rejects missing or oversized header",
    (v) => expect(() => peakDescriptor(v, 32)).toThrow(),
  );
  it.each([0, 2, 32769, 31])("refuses the wrong advertised length %i", (size) =>
    expect(() => peakDescriptor(JSON.stringify(descriptor), size)).toThrow(),
  );
  it("rejects inverted sample pairs and trailing bytes", () => {
    const corrupt = bytes.slice(0);
    new DataView(corrupt).setInt16(0, 200, true);
    expect(() => decodePeaks(descriptor, corrupt)).toThrow();
    expect(() => decodePeaks(descriptor, new ArrayBuffer(33))).toThrow();
  });
  const segment = (id, start_ms, duration_ms) => ({
    occurrence_id: id,
    start_ms,
    duration_ms,
  });
  const a = {
      pairs: [
        [-6, 4],
        [0, 0],
      ],
      width: 1,
      frames: 2,
    },
    b = { pairs: [[-3, 8]], width: 1, frames: 1 };
  const assets = new Map([
    ["a", a],
    ["b", b],
  ]);
  it("draws repeated standalone IDs once and leaves space for gaps or overlapping tracks", () => {
    expect(
      peakOverview(
        { duration_ms: 4, segments: [segment("a", 0, 2), segment("a", 2, 2)] },
        assets,
        4,
      ),
    ).toEqual([
      [-6, 4],
      [-6, 4],
      [0, 0],
      [0, 0],
    ]);
    expect(
      peakOverview(
        { duration_ms: 5, segments: [segment("a", 1, 2), segment("b", 2, 2)] },
        assets,
        5,
      ),
    ).toEqual([null, [-6, 4], [-3, 8], [-3, 8], null]);
  });
  it("accepts peaks but never with a channel selector", () => {
    const request = {
      parent,
      occurrence_id: occurrenceId,
      representation: "peaks",
    };
    expect(resolveItemSchema.safeParse(request).success).toBe(true);
    expect(
      resolveItemSchema.safeParse({ ...request, channel: 0 }).success,
    ).toBe(false);
  });
});
