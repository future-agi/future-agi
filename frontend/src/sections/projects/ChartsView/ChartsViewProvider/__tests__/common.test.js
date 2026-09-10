import { describe, expect, it } from "vitest";

import { normalizeTimestamp } from "../common";

describe("normalizeTimestamp", () => {
  // Epoch milliseconds are timezone-independent, so these expectations hold
  // whatever timezone the test process runs in.
  it.each([
    ["2024-01-01T00:00:00Z", 1704067200000],
    ["2024-01-01T00:00:00+05:30", 1704047400000],
    ["2024-01-01T00:00:00-07:00", 1704092400000],
    ["2024-07-01T12:34:56.789Z", 1719837296789],
  ])("maps %s to the instant it names", (timestamp, expected) => {
    expect(normalizeTimestamp(timestamp)).toBe(expected);
  });

  it("keeps offsets apart instead of collapsing them to the same value", () => {
    expect(normalizeTimestamp("2024-01-01T00:00:00+05:30")).not.toBe(
      normalizeTimestamp("2024-01-01T00:00:00Z"),
    );
  });

  it("reads an offset-free timestamp as local time", () => {
    expect(normalizeTimestamp("2024-01-01T00:00:00")).toBe(
      new Date(2024, 0, 1, 0, 0, 0).getTime(),
    );
  });

  it("returns null for missing or unparseable timestamps", () => {
    expect(normalizeTimestamp(null)).toBeNull();
    expect(normalizeTimestamp(undefined)).toBeNull();
    expect(normalizeTimestamp("")).toBeNull();
    expect(normalizeTimestamp("not-a-date")).toBeNull();
  });
});
