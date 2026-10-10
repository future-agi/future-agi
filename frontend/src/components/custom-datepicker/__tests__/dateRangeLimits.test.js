import { describe, expect, it } from "vitest";
import { getMaxSelectableDate, isDateSelectable } from "../dateRangeLimits";

describe("dateRangeLimits", () => {
  it("treats dates through the max date as selectable", () => {
    const maxDate = new Date("2026-07-08T23:59:59.999");

    expect(isDateSelectable(new Date("2026-07-08T00:00:00"), maxDate)).toBe(
      true,
    );
    expect(isDateSelectable(new Date("2026-07-08T23:59:59.999"), maxDate)).toBe(
      true,
    );
  });

  it("rejects dates after the max date", () => {
    const maxDate = new Date("2026-07-08T23:59:59.999");

    expect(isDateSelectable(new Date("2026-07-09T00:00:00"), maxDate)).toBe(
      false,
    );
  });

  it("rejects invalid dates and preserves an explicit max date", () => {
    const maxDate = new Date("2026-07-08T23:59:59.999");

    expect(isDateSelectable(new Date("invalid"), maxDate)).toBe(false);
    expect(getMaxSelectableDate(maxDate)).toBe(maxDate);
  });
});
