import { describe, expect, it } from "vitest";
import { format } from "date-fns";
import { getDateRangeDisplayEnd } from "../dateRange";

describe("date-only range labels", () => {
  // The remote runner must execute this suite separately with TZ=UTC,
  // TZ=Asia/Kolkata and TZ=America/Los_Angeles.
  it.each([
    [2, 8, "08/03/2026"],
    [10, 1, "01/11/2026"],
    [10, 2, "02/11/2026"],
  ])(
    "shows the selected day across timezone/DST boundaries (%s/%s)",
    (month, day, label) => {
      const range = [
        new Date(2026, month, day),
        new Date(2026, month, day + 1),
      ];
      const isoRange = range.map((date) => date.toISOString());
      const original = [...isoRange];
      expect(format(getDateRangeDisplayEnd(range), "dd/MM/yyyy")).toBe(label);
      expect(format(getDateRangeDisplayEnd(isoRange), "dd/MM/yyyy")).toBe(
        label,
      );
      expect(isoRange).toEqual(original);
      expect(range[1].getDate()).toBe(day + 1);
    },
  );

  it("does not subtract a day from inclusive-local legacy bounds", () => {
    const range = ["2026-11-01 00:00:00", "2026-11-04 00:00:00"];
    expect(
      format(getDateRangeDisplayEnd(range, "inclusive-local"), "dd/MM/yyyy"),
    ).toBe("04/11/2026");
  });

  it.each([
    ["2026-11-01 12:34:56", "2026-11-04 00:00:00"],
    ["2026-11-01 00:00:00", "2026-11-04 12:34:56"],
    ["2026-11-01 00:00:00", "2026-11-04 23:59:59"],
  ])("keeps arbitrary timestamp display dates: %s to %s", (start, end) => {
    expect(format(getDateRangeDisplayEnd([start, end]), "dd/MM/yyyy")).toBe(
      "04/11/2026",
    );
  });
});
