import { endOfToday, startOfToday, startOfTomorrow, sub } from "date-fns";
import { formatDate } from "src/utils/report-utils";

export const DEFAULT_OBSERVE_LIST_DATE_OPTION = "7D";

export const getDefaultDateRange = (dateOption) => {
  if (dateOption === "Today") {
    return {
      dateFilter: [formatDate(startOfToday()), formatDate(startOfTomorrow())],
      dateOption,
    };
  }

  const start =
    dateOption === "6M"
      ? sub(new Date(), { months: 6 })
      : sub(new Date(), { days: 7 });

  return {
    dateFilter: [formatDate(start), formatDate(endOfToday())],
    dateOption,
  };
};

export const getDefaultDateRangeForMode = (isUserMode, projectDateOption) =>
  getDefaultDateRange(isUserMode ? "Today" : projectDateOption);

export const getDefaultObserveListDateRangeForMode = (isUserMode) =>
  getDefaultDateRangeForMode(isUserMode, DEFAULT_OBSERVE_LIST_DATE_OPTION);
