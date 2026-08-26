import {
  differenceInDays,
  differenceInHours,
  differenceInMinutes,
  differenceInMonths,
  endOfToday,
  format,
  startOfToday,
  startOfTomorrow,
  startOfYesterday,
} from "date-fns";
import { toDate } from "./timeWindowPresets";

// LEGACY. Tasks saved before filters.date_preset existed carry only an absolute
// range, so their preset has to be measured back out.
const sameDay = (a, b) => format(a, "yyyy-MM-dd") === format(b, "yyyy-MM-dd");

// The Custom calendar is date-only, so a hand-picked range starts at midnight;
// a generated preset carries the o'clock it was made at.
const isMidnight = (d) =>
  d.getHours() === 0 && d.getMinutes() === 0 && d.getSeconds() === 0;

// Order matters: 30 mins/6 hrs before Today (a 30-minute window is same-day, so
// Today would swallow it); the midnight guard after Today/Yesterday, which
// legitimately start at midnight, and before the duration branches.
export function inferPreset(start, end) {
  const s = toDate(start);
  const e = toDate(end);
  if (!s || !e) return "Custom";

  const minutes = differenceInMinutes(e, s);
  const hours = differenceInHours(e, s);
  if (minutes >= 25 && minutes <= 35) return "30 mins";
  if (hours >= 5.5 && hours <= 6.5) return "6 hrs";

  if (
    sameDay(s, startOfToday()) &&
    (sameDay(e, startOfTomorrow()) || sameDay(e, endOfToday()))
  ) {
    return "Today";
  }
  if (sameDay(s, startOfYesterday()) && sameDay(e, startOfToday())) {
    return "Yesterday";
  }

  if (isMidnight(s)) return "Custom";

  const days = differenceInDays(e, s);
  const months = differenceInMonths(e, s);
  if (days >= 6 && days <= 8) return "7D";
  if (days >= 29 && days <= 31) return "30D";
  if (months >= 2.8 && months <= 3.2) return "3M";
  if (months >= 5.8 && months <= 6.2) return "6M";
  if (months >= 11.8 && months <= 12.2) return "12M";

  return "Custom";
}

// Today and Yesterday are matched on the calendar day alone, so any same-day
// window infers Today and any window straddling one midnight infers Yesterday.
// Re-anchoring either would rewrite the range the user actually picked, so a
// measured-out preset only ever carries a relative one.
export function inferPresetForLegacy(start, end) {
  const preset = inferPreset(start, end);
  return preset === "Today" || preset === "Yesterday" ? "Custom" : preset;
}
