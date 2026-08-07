import {
  parseISO,
  startOfDay,
  differenceInDays,
  subDays,
  format,
} from "date-fns";
import { toValidDate } from "src/utils/format-time";

export const isDateRangeMoreThan7Days = (dateArray) => {
  const [startDate, endDate] = dateArray;

  const start = parseISO(startDate);
  const end = parseISO(endDate);

  const adjustedEnd = subDays(end, 1);

  const startNormalized = startOfDay(start);
  const endNormalized = startOfDay(adjustedEnd);

  const diffInDays = differenceInDays(endNormalized, startNormalized);

  return diffInDays > 7;
};

export const isDateRangeLessThan90Days = (dateArray) => {
  const [startDate, endDate] = dateArray;

  const start = parseISO(startDate);
  const end = parseISO(endDate);

  // Adjust end date by subtracting 1 day (to match your existing logic)
  const adjustedEnd = subDays(end, 1);

  const startNormalized = startOfDay(start);
  const endNormalized = startOfDay(adjustedEnd);

  const diffInDays = differenceInDays(endNormalized, startNormalized);

  return diffInDays < 90; // 3 months ≈ 90 days
};

export const determineDateOption = (dateFilter) => {
  if (!dateFilter || dateFilter.length !== 2) {
    return "Custom";
  }

  const start = new Date(dateFilter[0].split(" ")[0]);
  const end = new Date(dateFilter[1].split(" ")[0]);
  start.setHours(0, 0, 0, 0);
  end.setHours(0, 0, 0, 0);

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const diffDays = (end - start) / (1000 * 60 * 60 * 24);

  if (
    start.getTime() === today.getTime() &&
    end.getTime() === today.getTime() + 24 * 60 * 60 * 1000
  ) {
    return "Today";
  }

  if (diffDays > 365) {
    return "12M";
  }
  if (diffDays > 180) {
    return "6M";
  }
  if (diffDays > 90) {
    return "3M";
  }
  if (diffDays >= 30) {
    return "30D";
  }
  if (diffDays > 7) {
    return "7D";
  }
  if (diffDays === 1) {
    return "Yesterday";
  }

  return "Custom";
};

/**
 * Formats a date value to "MM-dd-yyyy, HH:mm".
 * @param {any} params - The parameter object that contains the value.
 * @returns {string} - The formatted date string or an empty string.
 */
export const dateValueFormatter = (params) => {
  const date = params?.value ? new Date(params.value) : null;

  return date && !isNaN(date.getTime())
    ? format(date, "MM-dd-yyyy, HH:mm")
    : "";
};

export const timeAgoFormatter = (value) => {
  const date = toValidDate(value);
  if (!date) return "";

  const now = new Date();
  const diff = now - date;

  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(diff / (1000 * 60));
  const hours = Math.floor(diff / (1000 * 60 * 60));
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  const months = Math.floor(days / 30);
  const years = Math.floor(days / 365);

  const format = (value, unit) => `${value} ${unit}${value > 1 ? "s" : ""} ago`;

  if (seconds < 60) return format(seconds, "sec");
  if (minutes < 60) return format(minutes, "min");
  if (hours < 24) return format(hours, "hour");
  if (days < 30) return format(days, "day");
  if (months < 12) return format(months, "month");
  return format(years, "year");
};
