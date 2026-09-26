import {
  addDays,
  format,
  isValid,
  parseISO,
  startOfDay,
  sub,
  subDays,
} from "date-fns";
import { fDate, fDateTime } from "src/utils/format-time";

export const TIME_PERIOD_OPTIONS = [
  { title: "30 mins" },
  { title: "6 hrs" },
  { title: "Today" },
  { title: "Yesterday" },
  { title: "7D" },
  { title: "30D" },
  { title: "3M" },
  { title: "6M" },
  { title: "12M" },
];

const DURATIONS = {
  "30 mins": { minutes: 30 },
  "6 hrs": { hours: 6 },
  "7D": { days: 7 },
  "30D": { days: 30 },
  "3M": { months: 3 },
  "6M": { months: 6 },
  "12M": { months: 12 },
};

const TOKEN_BY_TITLE = {
  "30 mins": "30m",
  "6 hrs": "6h",
  Today: "today",
  Yesterday: "yesterday",
  "7D": "7d",
  "30D": "30d",
  "3M": "3m",
  "6M": "6m",
  "12M": "12m",
  Custom: "custom",
};

const TITLE_BY_TOKEN = Object.fromEntries(
  Object.entries(TOKEN_BY_TITLE).map(([title, token]) => [token, title]),
);

const DAY_MS = 24 * 60 * 60 * 1000;

export const toDate = (v) => {
  if (!v) return null;
  const d = typeof v === "string" ? parseISO(v) : v;
  return isValid(d) ? d : null;
};

export const presetToToken = (title) => TOKEN_BY_TITLE[title] || "custom";

export const tokenToPreset = (token) => TITLE_BY_TOKEN[token] || null;

// Every bound is derived from `now` so a caller can compute a window without
// the global clock; defaulting it keeps the live behaviour identical.
export function presetToRange(key, now = new Date()) {
  const dayStart = startOfDay(now);
  const nextDayStart = addDays(dayStart, 1);
  if (key === "Today") return [dayStart, nextDayStart];
  if (key === "Yesterday") return [subDays(dayStart, 1), dayStart];
  if (key === "30 mins" || key === "6 hrs") {
    return [sub(now, DURATIONS[key]), now];
  }
  const duration = DURATIONS[key];
  if (!duration) return null;
  return [sub(now, duration), nextDayStart];
}

// Presets end at startOfTomorrow so the query covers all of today; showing that
// boundary would read as "extends into tomorrow".
const lastCoveredInstant = (end) =>
  end.getHours() === 0 && end.getMinutes() === 0 && end.getSeconds() === 0
    ? sub(end, { seconds: 1 })
    : end;

// Sub-day is judged on the stored span — Today is stored as a full 24h but
// displays as 23:59:59, which would otherwise look sub-day.
export function formatTimeWindow(start, end, { isCustom = false } = {}) {
  const s = toDate(start);
  const rawEnd = toDate(end);
  if (!s || !rawEnd || rawEnd.getTime() < s.getTime()) return "";

  const e = isCustom ? rawEnd : lastCoveredInstant(rawEnd);
  const span = rawEnd.getTime() - s.getTime();
  const sameDay = format(s, "yyyy-MM-dd") === format(e, "yyyy-MM-dd");

  if (span > 0 && span < DAY_MS) {
    return sameDay
      ? `${fDate(s)}, ${format(s, "p")} – ${format(e, "p")}`
      : `${fDateTime(s)} – ${fDateTime(e)}`;
  }
  return sameDay ? fDate(s) : `${fDate(s)} – ${fDate(e)}`;
}
