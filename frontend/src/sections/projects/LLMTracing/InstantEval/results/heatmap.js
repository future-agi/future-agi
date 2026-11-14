import { ERROR_ANSWER } from "../instantEvalAnswers";

// The answer heatmap's grid: one row per calendar day, one cell per hour.

export const HOURS = Array.from({ length: 24 }, (_, hour) => hour);
const MAX_DAY_ROWS = 31;

export const dayKey = (date) =>
  `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;

export const hourLabel = (hour) => {
  const h = hour % 12 === 0 ? 12 : hour % 12;
  return `${h}${hour < 12 ? "a" : "p"}`;
};

export const hourRange = (hour) =>
  `${hourLabel(hour).replace("a", " AM").replace("p", " PM")}–${hourLabel(
    (hour + 1) % 24,
  )
    .replace("a", " AM")
    .replace("p", " PM")}`;

export const dayLabel = (date) =>
  date.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });

// Rows are calendar days in the viewer's timezone. The scope window decides
// the rows when it is short enough to show every day (empty days included, so
// a quiet day reads as quiet rather than disappearing); otherwise only days
// that have traces are shown.
export function buildHeatmap({ traces, configId, label, window }) {
  const start = window?.[0] ? new Date(window[0]) : null;
  const end = window?.[1] ? new Date(window[1]) : null;
  const days = [];
  if (start && end && !Number.isNaN(start) && !Number.isNaN(end)) {
    const cursor = new Date(
      start.getFullYear(),
      start.getMonth(),
      start.getDate(),
    );
    while (cursor <= end && days.length <= MAX_DAY_ROWS) {
      days.push(new Date(cursor));
      cursor.setDate(cursor.getDate() + 1);
    }
  }
  const useWindow = days.length > 0 && days.length <= MAX_DAY_ROWS;
  const cells = new Map();
  const seenDays = new Map();
  traces.forEach((trace) => {
    const time = new Date(trace.time);
    if (Number.isNaN(time.getTime())) return;
    const key = `${dayKey(time)}|${time.getHours()}`;
    const cell = cells.get(key) || {
      total: 0,
      matches: 0,
      answered: 0,
      traceIds: [],
      first: time.getTime(),
      last: time.getTime(),
    };
    const answer = trace.answers?.[configId];
    cell.total += 1;
    cell.first = Math.min(cell.first, time.getTime());
    cell.last = Math.max(cell.last, time.getTime());
    if (answer !== undefined && answer !== ERROR_ANSWER) cell.answered += 1;
    if (answer === label) {
      cell.matches += 1;
      cell.traceIds.push(trace.traceId);
    }
    cells.set(key, cell);
    if (!seenDays.has(dayKey(time)))
      seenDays.set(
        dayKey(time),
        new Date(time.getFullYear(), time.getMonth(), time.getDate()),
      );
  });
  const rows = (useWindow ? days : [...seenDays.values()].sort((a, b) => a - b))
    .slice(-MAX_DAY_ROWS)
    .map((date) => ({
      date,
      cells: HOURS.map((hour) => cells.get(`${dayKey(date)}|${hour}`) || null),
    }));
  return rows;
}
