import { describe, expect, it } from "vitest";

import { normalizeTimestamp } from "./common";

describe("normalizeTimestamp", () => {
  it("preserves the instant represented by an explicit UTC timestamp", () => {
    const timestamp = "2026-09-14T10:00:00Z";

    expect(normalizeTimestamp(timestamp)).toBe(timestamp);
    expect(Date.parse(normalizeTimestamp(timestamp))).toBe(
      Date.parse(timestamp),
    );
  });

  it("preserves timestamps without timezone offsets", () => {
    const timestamp = "2026-09-14T10:00:00";

    expect(normalizeTimestamp(timestamp)).toBe(timestamp);
  });

  it("preserves explicit numeric timezone offsets", () => {
    const timestamp = "2026-09-14T10:00:00-05:00";

    expect(normalizeTimestamp(timestamp)).toBe(timestamp);
    expect(Date.parse(normalizeTimestamp(timestamp))).toBe(
      Date.parse(timestamp),
    );
  });
});
