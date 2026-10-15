import { describe, expect, it } from "vitest";
import { normalizeTimestamp } from "../common";

describe("normalizeTimestamp", () => {
  it("preserves absolute instants with explicit offsets", () => {
    expect(normalizeTimestamp("2026-10-14T15:30:00Z")).toBe(Date.parse("2026-10-14T15:30:00Z"));
    expect(normalizeTimestamp("2026-10-14T21:00:00+05:30")).toBe(Date.parse("2026-10-14T15:30:00Z"));
    expect(normalizeTimestamp("2026-10-14T10:30:00-05:00")).toBe(Date.parse("2026-10-14T15:30:00Z"));
  });

  it("interprets offset-less server timestamps as UTC", () => {
    expect(normalizeTimestamp("2026-10-14T15:30:00")).toBe(Date.parse("2026-10-14T15:30:00Z"));
    expect(normalizeTimestamp("2026-10-14 15:30:00")).toBe(Date.parse("2026-10-14T15:30:00Z"));
  });

  it("handles empty and invalid timestamps", () => {
    expect(normalizeTimestamp(null)).toBeNull();
    expect(normalizeTimestamp("")).toBe("");
    expect(Number.isNaN(normalizeTimestamp("not-a-date"))).toBe(true);
  });
});
