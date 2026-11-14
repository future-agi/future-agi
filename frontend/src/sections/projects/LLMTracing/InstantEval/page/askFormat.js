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

// What a run read, for its header: its scope label in words, with a custom
// range's dates ("Oct 9 – Oct 16 · 1 filter"), or the run's window alone.
export function runScopeWords(label, window) {
  const dates = windowLabel(window);
  if (!label) return dates;
  return dates && label.startsWith("Custom range")
    ? label.replace("Custom range", dates)
    : scopeWords(label);
}

// "21 s", "4 min 5 s"; past ten minutes the seconds stop mattering:
// "35 min", "2 h 5 min".
export function formatElapsed(ms) {
  if (ms == null) return "";
  const seconds = Math.round(ms / 1000);
  if (seconds < 600) return formatSeconds(seconds);
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const rest = minutes % 60;
  return `${Math.floor(minutes / 60)} h${rest ? ` ${rest} min` : ""}`;
}

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

// How long a run has been answering: the time it actually ran, with the time
// it sat paused or stopped apart ("in 41 min (idle 3 h 33 min)"). Without
// that split, the wall time since it started.
export function runTime(run, lead) {
  const active = run.summary?.active_s;
  if (active == null)
    return run.elapsedMs != null
      ? `${lead}${formatElapsed(run.elapsedMs)}`
      : "";
  const idle = run.summary?.idle_s || 0;
  // A minute or so between answers is part of running, not a pause.
  const shown = idle >= 120 ? ` (idle ${formatElapsed(idle * 1000)})` : "";
  return `${lead}${formatElapsed(active * 1000)}${shown}`;
}
