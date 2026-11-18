import { describe, expect, it } from "vitest";
import { normalizeTimestamp } from "../ChartsViewProvider/common";

describe("normalizeTimestamp", () => {
  it("keeps a Z-suffixed timestamp parseable at the same instant", () => {
    const out = normalizeTimestamp("2026-09-03T10:00:00Z");
    expect(/([+-]\d{2}:\d{2}|Z)$/.test(out)).toBe(true);
    expect(new Date(out).toISOString()).toBe("2026-09-03T10:00:00.000Z");
  });

  it("keeps an explicit numeric offset attached to the timestamp", () => {
    const out = normalizeTimestamp("2026-09-03T10:00:00+05:30");
    expect(/([+-]\d{2}:\d{2}|Z)$/.test(out)).toBe(true);
    expect(new Date(out).toISOString()).toBe("2026-09-03T04:30:00.000Z");
  });

  it("treats a timestamp with no offset as UTC instead of local time", () => {
    const out = normalizeTimestamp("2026-09-03T10:00:00");
    expect(out.endsWith("Z")).toBe(true);
    expect(new Date(out).toISOString()).toBe("2026-09-03T10:00:00.000Z");
  });

  it.each([undefined, null, ""])(
    "passes empty values through (%j)",
    (value) => {
      expect(normalizeTimestamp(value)).toBe(value);
    },
  );

  it("passes epoch numbers through untouched", () => {
    expect(normalizeTimestamp(1756886400000)).toBe(1756886400000);
  });
});
