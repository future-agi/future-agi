import { isValid, startOfDay, subDays } from "date-fns";

/**
 * Display-only end date. The caller's API contract, not the timestamp's string
 * format, determines whether the end is inclusive. Exact timestamp ranges keep
 * their displayed date; only whole local calendar-day ranges exclude a day.
 * @param {[string | Date | number, string | Date | number]} value
 * @param {"half-open" | "inclusive-local"} rangeConvention
 * @returns {Date}
 */
export function getDateRangeDisplayEnd(value, rangeConvention = "half-open") {
  const start = new Date(value[0]);
  const end = new Date(value[1]);
  if (
    rangeConvention === "half-open" &&
    isValid(start) &&
    isValid(end) &&
    end > start &&
    +start === +startOfDay(start) &&
    +end === +startOfDay(end)
  ) {
    return subDays(end, 1);
  }
  return end;
}
