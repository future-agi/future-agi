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
import { formatDate } from "src/utils/report-utils";

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

// Observe's exact charts cache one snapshot per window, so a rolling preset's
// start moves in whole hours rather than every second: every visit in the same
// hour sends the same window and can be served from that snapshot.
const ROLLING_PRESET_START_STEP_MS = 60 * 60 * 1000;

// Floors on epoch milliseconds, i.e. on UTC hours: every viewer in one UTC
// hour shares the start. The window's identity is still per timezone, since
// its end is the viewer's next local midnight, and the start's local
// wall-clock moves with the zone (half-hour zones and Lord Howe's 30-minute
// DST see an extra identity where the lookback crosses a local boundary).
const floorToStep = (date, stepMs) =>
  new Date(Math.floor(date.getTime() / stepMs) * stepMs);

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

// The presets an Observe date site offers: Today, Yesterday and the rolling
// day-or-longer ones. The sub-day presets are not offered there, and would
// send a window that moves every second, so they are not served.
const OBSERVE_PRESETS = new Set([
  "Today",
  "Yesterday",
  "7D",
  "30D",
  "3M",
  "6M",
  "12M",
]);

// The one window every Observe preset site sends (default load, toolbar pick,
// compare pills, DateRangePill), formatted as the list/graph date filter.
// A default "Past 7D" and a picked "Past 7D" are therefore byte-identical: the
// start of a rolling preset (7D .. 12M) is floored to the UTC hour and the
// end is the next local midnight; Today and Yesterday are never rounded.
// Returns null for Custom, the sub-day presets and unknown keys.
export function observePresetDateFilter(key, now = new Date()) {
  if (!OBSERVE_PRESETS.has(key)) return null;
  const [start, end] = presetToRange(key, now);
  const rolling = key in DURATIONS;
  return [
    rolling ? floorToStep(start, ROLLING_PRESET_START_STEP_MS) : start,
    end,
  ].map(formatDate);
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
