import { startOfToday, startOfTomorrow } from "date-fns";
import { formatDate } from "src/utils/report-utils";
import { describe, expect, it } from "vitest";
import {
  getDefaultDateRange,
  getDefaultDateRangeForMode,
} from "../dateRangeDefaults";

describe("project list default date ranges", () => {
  it("returns the exact Today boundaries used by user detail tabs", () => {
    expect(getDefaultDateRange("Today")).toEqual({
      dateFilter: [formatDate(startOfToday()), formatDate(startOfTomorrow())],
      dateOption: "Today",
    });
  });

  it("retains the existing project defaults", () => {
    expect(getDefaultDateRangeForMode(false, "6M").dateOption).toBe("6M");
    expect(getDefaultDateRangeForMode(false, "7D").dateOption).toBe("7D");
  });

  it("uses Today for both user-detail callers", () => {
    expect(getDefaultDateRangeForMode(true, "6M").dateOption).toBe("Today");
    expect(getDefaultDateRangeForMode(true, "7D").dateOption).toBe("Today");
  });
});
