import { endOfToday, isAfter, isValid } from "date-fns";

export const getMaxSelectableDate = (maxDate) => maxDate ?? endOfToday();

export const isDateSelectable = (date, maxDate) =>
  isValid(date) && !isAfter(date, getMaxSelectableDate(maxDate));
