import { describe, expect, it } from "vitest";
import { formatISOCustom } from "src/utils/utils";

describe("formatISOCustom", () => {
  it("returns the exact UTC instant, preserving milliseconds", () => {
    const instant = new Date("2026-01-15T10:30:45.678Z");

    // The pre-fix implementation reformatted the local wall-clock time and
    // hardcoded ".000Z", so it dropped the milliseconds (and, for non-UTC
    // users, shifted the whole timestamp by the UTC offset). This exact-match
    // assertion fails against that behavior in every timezone.
    expect(formatISOCustom(instant)).toBe("2026-01-15T10:30:45.678Z");
  });

  it("round-trips to the same instant it was given", () => {
    const instant = new Date("2026-07-04T23:15:00.500Z");

    expect(new Date(formatISOCustom(instant)).getTime()).toBe(
      instant.getTime(),
    );
  });
});
