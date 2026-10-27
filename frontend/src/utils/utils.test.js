import { describe, expect, it } from "vitest";

import { formatISOCustom } from "./utils";

describe("formatISOCustom", () => {
  it("returns the exact UTC instant for a Date created from a UTC timestamp", () => {
    const date = new Date("2026-01-15T10:30:00.000Z");
    expect(formatISOCustom(date)).toBe("2026-01-15T10:30:00.000Z");
  });

  it("does not shift the instant by the local timezone offset", () => {
    const date = new Date(Date.UTC(2026, 5, 30, 18, 45, 12));
    expect(formatISOCustom(date)).toBe("2026-06-30T18:45:12.000Z");
  });

  it("round-trips to the same epoch millisecond", () => {
    const input = new Date("2026-03-01T00:00:00.000Z");
    const output = formatISOCustom(input);
    expect(new Date(output).getTime()).toBe(input.getTime());
  });

  it("always produces a well-formed UTC ISO string", () => {
    const output = formatISOCustom(new Date("2026-03-01T12:00:00.000Z"));
    expect(output).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  });

  it("accepts date strings like the ComplexFilter datetime transform passes", () => {
    expect(formatISOCustom("2026-01-15T10:30:00.000Z")).toBe(
      "2026-01-15T10:30:00.000Z",
    );
  });
});
