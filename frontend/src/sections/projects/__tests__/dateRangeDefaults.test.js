import { startOfToday, startOfTomorrow } from "date-fns";
import { formatDate } from "src/utils/report-utils";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_OBSERVE_LIST_DATE_OPTION,
  getDefaultDateRange,
  getDefaultDateRangeForMode,
  getDefaultObserveListDateRangeForMode,
} from "../dateRangeDefaults";

describe("project list default date ranges", () => {
  it("returns the exact Today boundaries used by user detail tabs", () => {
    expect(getDefaultDateRange("Today")).toEqual({
      dateFilter: [formatDate(startOfToday()), formatDate(startOfTomorrow())],
      dateOption: "Today",
    });
  });

  it("uses a shared seven-day default for observe lists", () => {
    expect(DEFAULT_OBSERVE_LIST_DATE_OPTION).toBe("7D");
    expect(getDefaultDateRangeForMode(false, "6M").dateOption).toBe("6M");
    expect(
      getDefaultDateRangeForMode(false, DEFAULT_OBSERVE_LIST_DATE_OPTION)
        .dateOption,
    ).toBe("7D");
    expect(getDefaultObserveListDateRangeForMode(false).dateOption).toBe("7D");
  });

  it("uses Today for both user-detail callers", () => {
    expect(getDefaultDateRangeForMode(true, "6M").dateOption).toBe("Today");
    expect(getDefaultDateRangeForMode(true, "7D").dateOption).toBe("Today");
    expect(getDefaultObserveListDateRangeForMode(true).dateOption).toBe(
      "Today",
    );
  });
});
