import { formatSeconds } from "../instantEvalUnits";

// Words and numbers the Ask page shows.

const DAY_MS = 24 * 3600 * 1000;

// "Today", "Yesterday", "Earlier this week", "Older": runs grouped by when.
export function dayGroup(value, now = new Date()) {
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "Older";
  const startOfDay = new Date(now);
  startOfDay.setHours(0, 0, 0, 0);
  if (at >= startOfDay) return "Today";
  const days = (startOfDay - at) / DAY_MS;
  if (days <= 1) return "Yesterday";
  if (days <= 6) return "Earlier this week";
  return "Older";
}

// The views' short date labels in words: "Past 7D" → "Past 7 days".
const PERIOD_WORDS = { D: "day", M: "month", mins: "minute", hrs: "hour" };
export function scopeWords(label) {
  if (!label) return "";
  return label.replace(
    /\bPast (\d+) ?(D|M|mins|hrs)\b/,
    (_, count, unit) =>
      `Past ${count} ${PERIOD_WORDS[unit]}${count === "1" ? "" : "s"}`,
  );
}

export const formatElapsed = (ms) =>
  ms == null ? "" : formatSeconds(ms / 1000);

// "Oct 9 – Oct 16" for a window, or one day when it spans one.
export function windowLabel(window) {
  if (!window || window.length !== 2) return "";
  const [start, end] = window.map((value) => new Date(value));
  if ([start, end].some((date) => Number.isNaN(date.getTime()))) return "";
  const day = (date) =>
    date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return day(start) === day(end) ? day(start) : `${day(start)} – ${day(end)}`;
}

// "gpt-4o-mini · 3 turns · 2 min 28 s · error"
export function facetLine(facets = {}) {
  return [
    facets.model,
    facets.turns != null &&
      `${facets.turns} turn${facets.turns === 1 ? "" : "s"}`,
    facets.duration_ms != null && formatSeconds(facets.duration_ms / 1000),
    facets.error && "errored",
  ]
    .filter(Boolean)
    .join(" · ");
}
