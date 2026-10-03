import { observePresetDateFilter } from "./timeWindowPresets";

export const DEFAULT_OBSERVE_LIST_DATE_OPTION = "7D";

// The default window is the same helper a picked preset uses, so the default
// "Past 7D" and a picked "Past 7D" send one identical window (hour-floored
// start, next-midnight end) and share the exact chart snapshot.
export const getDefaultDateRange = (dateOption) => {
  if (dateOption === "Today") {
    return {
      dateFilter: observePresetDateFilter("Today"),
      dateOption,
    };
  }

  return {
    dateFilter: observePresetDateFilter(dateOption === "6M" ? "6M" : "7D"),
    dateOption,
  };
};

export const getDefaultDateRangeForMode = (isUserMode, projectDateOption) =>
  getDefaultDateRange(isUserMode ? "Today" : projectDateOption);

export const getDefaultObserveListDateRangeForMode = (isUserMode) =>
  getDefaultDateRangeForMode(isUserMode, DEFAULT_OBSERVE_LIST_DATE_OPTION);
