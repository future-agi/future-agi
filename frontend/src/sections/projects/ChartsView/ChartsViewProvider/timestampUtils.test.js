import { describe, expect, it } from "vitest";

import { parseTimestampToMs } from "./timestampUtils";

describe("parseTimestampToMs", () => {
  it("preserves the offset so an offset-suffixed string parses to the exact epoch ms", () => {
    expect(parseTimestampToMs("2024-01-01T14:30:00+05:30")).toBe(
      new Date("2024-01-01T09:00:00Z").getTime(),
    );
  });

  it("parses Z-suffixed timestamps as UTC", () => {
    expect(parseTimestampToMs("2024-01-01T14:30:00Z")).toBe(
      Date.parse("2024-01-01T14:30:00Z"),
    );
  });

  it("parses negative offsets correctly", () => {
    expect(parseTimestampToMs("2024-01-01T14:30:00-08:00")).toBe(
      Date.parse("2024-01-01T22:30:00Z"),
    );
  });

  it("still returns a finite number for timestamps with no offset", () => {
    const result = parseTimestampToMs("2024-01-01T14:30:00");
    expect(typeof result).toBe("number");
    expect(Number.isFinite(result)).toBe(true);
  });

  it("returns null for missing values", () => {
    expect(parseTimestampToMs(null)).toBeNull();
    expect(parseTimestampToMs(undefined)).toBeNull();
  });

  it("returns null for invalid strings", () => {
    expect(parseTimestampToMs("not-a-date")).toBeNull();
  });

  it("passes through epoch-ms numbers and Date objects", () => {
    const ms = Date.parse("2024-01-01T09:00:00Z");
    expect(parseTimestampToMs(ms)).toBe(ms);
    expect(parseTimestampToMs(new Date(ms))).toBe(ms);
  });
});
