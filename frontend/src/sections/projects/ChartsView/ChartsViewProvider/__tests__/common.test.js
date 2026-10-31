import { describe, expect, it } from "vitest";
import { convertToISO, normalizeTimestamp } from "../common";

describe("normalizeTimestamp", () => {
  it("preserves UTC timestamp with Z indicator", () => {
    expect(normalizeTimestamp("2026-10-16T10:00:00Z")).toBe(
      "2026-10-16T10:00:00.000Z",
    );
  });

  it("normalizes ISO timestamp with +00:00 offset to UTC", () => {
    expect(normalizeTimestamp("2026-10-16T10:00:00+00:00")).toBe(
      "2026-10-16T10:00:00.000Z",
    );
  });

  it("normalizes space-separated timestamp with +00:00 offset", () => {
    expect(normalizeTimestamp("2026-10-16 10:00:00+00:00")).toBe(
      "2026-10-16T10:00:00.000Z",
    );
  });

  it("normalizes space-separated timestamp with 2-digit +00 offset", () => {
    expect(normalizeTimestamp("2026-10-16 10:00:00+00")).toBe(
      "2026-10-16T10:00:00.000Z",
    );
  });

  it("treats backend SQL timestamp without timezone offset as UTC", () => {
    expect(normalizeTimestamp("2026-10-16 10:00:00")).toBe(
      "2026-10-16T10:00:00.000Z",
    );
  });

  it("treats ISO timestamp without timezone offset as UTC", () => {
    expect(normalizeTimestamp("2026-10-16T10:00:00")).toBe(
      "2026-10-16T10:00:00.000Z",
    );
  });

  it("preserves fractional seconds in timestamps", () => {
    expect(normalizeTimestamp("2026-10-16T10:00:00.123Z")).toBe(
      "2026-10-16T10:00:00.123Z",
    );
    expect(normalizeTimestamp("2026-10-16 10:00:00.123")).toBe(
      "2026-10-16T10:00:00.123Z",
    );
  });

  it("correctly converts non-UTC offsets to standard UTC ISO string", () => {
    // 10:00 in UTC-5 is 15:00 UTC
    expect(normalizeTimestamp("2026-10-16T10:00:00-05:00")).toBe(
      "2026-10-16T15:00:00.000Z",
    );
    // 15:30 in UTC+5:30 is 10:00 UTC
    expect(normalizeTimestamp("2026-10-16 15:30:00+05:30")).toBe(
      "2026-10-16T10:00:00.000Z",
    );
  });

  it("handles Date objects correctly", () => {
    const date = new Date("2026-10-16T10:00:00Z");
    expect(normalizeTimestamp(date)).toBe("2026-10-16T10:00:00.000Z");
  });

  it("returns numeric epoch timestamps as-is", () => {
    expect(normalizeTimestamp(1700000000000)).toBe(1700000000000);
  });

  it("returns falsy values without modification", () => {
    expect(normalizeTimestamp(null)).toBe(null);
    expect(normalizeTimestamp(undefined)).toBe(undefined);
    expect(normalizeTimestamp("")).toBe("");
  });

  it("returns invalid date strings safely without throwing", () => {
    expect(normalizeTimestamp("not-a-date")).toBe("not-a-date");
  });
});

describe("convertToISO", () => {
  it("converts an array of date representations to ISO strings", () => {
    const dates = ["2026-10-16T00:00:00Z", "2026-10-17T00:00:00Z"];
    const result = convertToISO(dates);
    expect(result).toEqual([
      "2026-10-16T00:00:00.000Z",
      "2026-10-17T00:00:00.000Z",
    ]);
  });
});
