import { answerState, questionLabels, stackRank } from "./askAnswers";

// The timeline's geometry: rows go into equal time bins (columns), each
// column a stack of one square per row, sorted by answer.

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
export const DAY = 24 * HOUR;

export const BIN_STEPS = [
  MINUTE,
  2 * MINUTE,
  5 * MINUTE,
  10 * MINUTE,
  15 * MINUTE,
  30 * MINUTE,
  HOUR,
  2 * HOUR,
  3 * HOUR,
  4 * HOUR,
  6 * HOUR,
  8 * HOUR,
  12 * HOUR,
  DAY,
  2 * DAY,
  7 * DAY,
];

// About 90 columns: a day in 15 minutes (96), a week in 2 hours (84), a
// month in 8 hours (90).
export const MAX_COLUMNS = 96;

// The timeline is the page's main chart. With up to HERO_ROWS rows it takes
// fewer, wider columns so the squares can be 10–12px.
export const HERO_ROWS = 200;
const HERO_PITCH = 14;
const MIN_COLUMNS = 24;
export const MAX_SQUARE = 12;
export const MAX_PLOT_HEIGHT = 220;
export const MIN_ROWS_TALL = 5;

/** How many columns to aim for across `width` for `rowCount` rows. */
export function columnBudget(rowCount, width) {
  if (rowCount > HERO_ROWS || !width) return MAX_COLUMNS;
  return Math.max(
    MIN_COLUMNS,
    Math.min(MAX_COLUMNS, Math.floor(width / HERO_PITCH)),
  );
}

// A burst column must not set the scale for all the others: a column is cut
// at 1.5 × the 90th percentile of the non-empty columns (at least 12
// squares), with "+N" on top.
export const MIN_CAP = 12;
export const BADGE_H = 16;
// Cut down, squares smaller than this give way to thin slices.
const MIN_CAPPED_SQUARE = 6;

/** How many squares a column may stack before it is cut. */
export function stackCap(columns) {
  const counts = columns
    .map((column) => column.items.length)
    .filter((n) => n > 0)
    .sort((a, b) => a - b);
  if (!counts.length) return MIN_CAP;
  const p90 = counts[Math.max(0, Math.ceil(0.9 * counts.length) - 1)];
  return Math.max(MIN_CAP, Math.ceil(1.5 * p90));
}

/**
 * `cap` evenly spaced squares of a sorted stack, so a cut column keeps its
 * mix of answers.
 */
export function sampleStack(items, cap) {
  if (items.length <= cap) return items;
  return Array.from(
    { length: cap },
    (_, i) => items[Math.floor(((i + 0.5) * items.length) / cap)],
  );
}

/**
 * The squares' layout for `columnCount` columns across `width`, the tallest
 * stacking `maxStack` rows, columns cut at `cap`:
 *   {colW, w, h, rowGap, pitch, plotH, cap, capped}
 * Squares shrink (to a 1px gap under 10px) so the stacks fit MAX_PLOT_HEIGHT,
 * with room for "+N" when a column is cut. Where even cut columns would need
 * squares under 6px, every row becomes a thin slice of its column instead,
 * and nothing is cut.
 */
export function squareLayout(width, columnCount, maxStack, cap = Infinity) {
  const colW = width && columnCount ? width / columnCount : 0;
  const gap = colW >= 9 ? 2 : 1;
  const fitW = Math.max(3, Math.min(MAX_SQUARE, Math.floor(colW - gap)));
  const capped = maxStack > cap;
  const shown = capped ? cap : maxStack;
  const room = MAX_PLOT_HEIGHT - (capped ? BADGE_H : 0);
  let w = fitW;
  let h = fitW;
  let rowGap = gap;
  if (shown * (h + rowGap) > room) {
    const per = Math.floor(room / shown);
    h = per - gap;
    if (h < 10) {
      rowGap = 1;
      h = per - 1;
    }
    h = Math.min(h, fitW);
    w = h;
  }
  if (h < (capped ? MIN_CAPPED_SQUARE : 3)) {
    const slice = MAX_PLOT_HEIGHT / maxStack;
    return {
      colW,
      w: fitW,
      h: slice,
      rowGap: 0,
      pitch: slice,
      plotH: MAX_PLOT_HEIGHT,
      cap: Infinity,
      capped: false,
    };
  }
  const pitch = h + rowGap;
  return {
    colW,
    w,
    h,
    rowGap,
    pitch,
    plotH: Math.max(MIN_ROWS_TALL, shown) * pitch + (capped ? BADGE_H : 0),
    cap: capped ? cap : Infinity,
    capped,
  };
}

/** The smallest step that fits `spanMs` in at most `max` columns. */
export function chooseBinMs(spanMs, max = MAX_COLUMNS) {
  const span = Math.max(spanMs || 0, HOUR);
  return (
    BIN_STEPS.find((step) => Math.ceil(span / step) <= max) ||
    BIN_STEPS[BIN_STEPS.length - 1]
  );
}

const toMs = (value) => {
  if (value === null || value === undefined || value === "") return NaN;
  const ms =
    value instanceof Date ? value.getTime() : new Date(value).getTime();
  return Number.isFinite(ms) ? ms : NaN;
};

/**
 * The window to draw: a view's window can end in the future (a "Past 7 days"
 * window that runs to midnight), and nothing can happen there, so it stops
 * at `now`, or at the latest row if that is later.
 */
export function clampWindow(window, records = [], now = Date.now()) {
  if (!Array.isArray(window) || window.length !== 2) return window;
  const end = toMs(window[1]);
  if (!Number.isFinite(end)) return window;
  const latest = Math.max(
    now,
    ...records.map((r) => toMs(r.time)).filter(Number.isFinite),
  );
  return end > latest ? [window[0], new Date(latest).toISOString()] : window;
}

const localMidnight = (ms) => {
  const date = new Date(ms);
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
  ).getTime();
};

/**
 * Columns for the records of one question:
 *   {binMs, origin, columns: [{index, start, end, items}], dayTicks, maxStack}
 * Bins count from local midnight of the first day, so a day boundary always
 * falls on a column edge. Every bin between the first and the last is kept,
 * empty or not, so a quiet stretch shows as a gap. `items` are
 * {id, record, state, answer, unsure, rank}, bottom of the stack first;
 * `errors` counts the column's rows whose trace had an error.
 */
export function buildTimeline({
  records = [],
  configId,
  question,
  window: viewWindow,
  now,
  maxColumns = MAX_COLUMNS,
}) {
  const labels = questionLabels(question);
  const window = clampWindow(viewWindow, records, now);
  const times = records.map((r) => toMs(r.time)).filter(Number.isFinite);
  const windowEnd = toMs(window?.[1]);
  const from = Math.min(toMs(window?.[0]) || Infinity, ...times);
  const to = Math.max(windowEnd || -Infinity, ...times);
  if (!Number.isFinite(from) || !Number.isFinite(to))
    return { binMs: HOUR, origin: 0, columns: [], dayTicks: [], maxStack: 0 };
  const binMs = chooseBinMs(to - from, maxColumns);
  const origin = localMidnight(from);
  // Day-sized bins step by calendar day in the viewer's zone, so a DST
  // change (a 23- or 25-hour day) never shifts a column off midnight.
  const daysPerBin = binMs % DAY === 0 ? binMs / DAY : 0;
  const originDate = new Date(origin);
  const dayStart = (days) =>
    new Date(
      originDate.getFullYear(),
      originDate.getMonth(),
      originDate.getDate() + days,
    ).getTime();
  const binOf = (ms) =>
    daysPerBin
      ? Math.floor(Math.round((localMidnight(ms) - origin) / DAY) / daysPerBin)
      : Math.floor((ms - origin) / binMs);
  const binStart = (index) =>
    daysPerBin ? dayStart(index * daysPerBin) : origin + index * binMs;
  const first = binOf(from);
  // A window that ends on a column edge does not open one more column; a row
  // at the very end still gets its own.
  const last = Math.max(
    first,
    Number.isFinite(windowEnd) ? binOf(windowEnd - 1) : first,
    ...times.map(binOf),
  );
  const columns = [];
  for (let index = first; index <= last; index += 1)
    columns.push({
      index: index - first,
      start: binStart(index),
      end: binStart(index + 1),
      items: [],
      errors: 0,
    });
  records.forEach((record) => {
    const ms = toMs(record.time);
    if (!Number.isFinite(ms)) return;
    const column = columns[binOf(ms) - first];
    if (!column) return;
    const { state, answer, unsure } = answerState(record, configId);
    if (record.facets?.error === true) column.errors += 1;
    column.items.push({
      id: record.id,
      record,
      state,
      answer,
      unsure,
      rank: stackRank(state, answer, labels),
      ms,
    });
  });
  columns.forEach((column) =>
    column.items.sort(
      (a, b) =>
        a.rank - b.rank || Number(a.unsure) - Number(b.unsure) || a.ms - b.ms,
    ),
  );
  // Where each local midnight falls, in columns from the left edge.
  const dayTicks = [];
  const startMs = origin + first * binMs;
  const endMs = origin + (last + 1) * binMs;
  for (
    let day = localMidnight(startMs);
    day <= endMs;
    day = localMidnight(day + DAY + 2 * HOUR)
  ) {
    if (day >= startMs && day < endMs)
      dayTicks.push({ at: (day - startMs) / binMs, ms: day });
  }
  return {
    binMs,
    origin,
    columns,
    dayTicks,
    maxStack: Math.max(0, ...columns.map((column) => column.items.length)),
  };
}

const timeText = (ms) => {
  const date = new Date(ms);
  const minutes = date.getMinutes();
  const hours = date.getHours() % 12 === 0 ? 12 : date.getHours() % 12;
  const suffix = date.getHours() < 12 ? "AM" : "PM";
  return minutes
    ? `${hours}:${String(minutes).padStart(2, "0")} ${suffix}`
    : `${hours} ${suffix}`;
};

// Formatting a date is slow when each call builds its formatter, and the
// charts name hundreds of days and stretches: keep two.
const DAY_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
});
const WEEKDAY_FORMAT = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
});

/** "Oct 13", or "Tue, Oct 13" with the weekday. */
export const dayText = (ms, withWeekday = false) =>
  (withWeekday ? WEEKDAY_FORMAT : DAY_FORMAT).format(new Date(ms));

/**
 * A time range in words: "Oct 13, 2 PM – 6 PM", "Oct 13 – Oct 15" for whole
 * days, "Oct 13, 10 PM – Oct 14, 2 AM" across midnight.
 */
export function rangeText(startMs, endMs) {
  const wholeDays =
    localMidnight(startMs) === startMs && localMidnight(endMs) === endMs;
  if (wholeDays) {
    const lastDay = localMidnight(endMs - 1);
    return lastDay <= startMs
      ? dayText(startMs)
      : `${dayText(startMs)} – ${dayText(lastDay)}`;
  }
  const sameDay = localMidnight(startMs) === localMidnight(endMs - 1);
  return sameDay
    ? `${dayText(startMs)}, ${timeText(startMs)} – ${timeText(endMs)}`
    : `${dayText(startMs)}, ${timeText(startMs)} – ${dayText(endMs)}, ${timeText(endMs)}`;
}

/**
 * Columns `from`..`to` (either order) as a selection: the time filter, its
 * label, and how many rows it holds (0 selects nothing).
 */
export function selectColumns(columns, from, to) {
  const lo = Math.max(0, Math.min(from, to));
  const hi = Math.min(columns.length - 1, Math.max(from, to));
  if (!columns.length || hi < lo) return { count: 0, filter: null, label: "" };
  const count = columns
    .slice(lo, hi + 1)
    .reduce(
      (sum, column) => sum + (column.items ? column.items.length : column.n),
      0,
    );
  return {
    count,
    filter: {
      time_from: new Date(columns[lo].start).toISOString(),
      time_to: new Date(columns[hi].end).toISOString(),
    },
    label: rangeText(columns[lo].start, columns[hi].end),
  };
}

// At scale, fewer and wider columns: about 60–90 read as a shape rather than
// a barcode.
export const MAX_DENSITY_COLUMNS = 90;

// Several bins' counts as one.
function mergeTallies(list) {
  const byLabel = new Map();
  const sum = {
    n: 0,
    answered: 0,
    failed: 0,
    skipped: 0,
    pending: 0,
    errors: 0,
  };
  list.forEach((t) => {
    Object.keys(sum).forEach((key) => {
      sum[key] += t[key] || 0;
    });
    t.byLabel.forEach((count, label) =>
      byLabel.set(label, (byLabel.get(label) || 0) + count),
    );
  });
  return { ...sum, byLabel };
}

/**
 * The density timeline's columns from the summary's bins, for one question:
 *   {binMs, columns: [{index, start, end, n, answered, failed, skipped,
 *    pending, errors, byLabel}], dayTicks, maxN}
 * `tallyOf(bin)` reads a bin's counts for the question. Bins after now (and
 * after the last row) are dropped, since a window can end at a midnight still
 * to come, and neighbouring bins are merged so there are at most
 * `maxColumns`.
 */
export function densityColumns({
  timeline,
  tallyOf,
  now = Date.now(),
  maxColumns = MAX_DENSITY_COLUMNS,
}) {
  const baseMs = timeline?.bin_ms || HOUR;
  const bins = (timeline?.bins || [])
    .map((bin) => ({ bin, t: toMs(bin.t) }))
    .filter(({ t }) => Number.isFinite(t))
    .sort((a, b) => a.t - b.t);
  if (!bins.length)
    return { binMs: baseMs, columns: [], dayTicks: [], maxN: 0 };
  const lastRow = Math.max(
    -Infinity,
    ...bins.filter(({ bin }) => bin.n > 0).map(({ t }) => t),
  );
  const stop = Math.max(now, lastRow + baseMs);
  const kept = bins.filter(({ t }) => t < stop);
  const per = Math.max(1, Math.ceil(kept.length / maxColumns));
  const columns = [];
  for (let i = 0; i < kept.length; i += per) {
    const group = kept.slice(i, i + per);
    const next = kept[i + per];
    columns.push({
      index: columns.length,
      start: group[0].t,
      end: next ? next.t : group[group.length - 1].t + baseMs,
      ...mergeTallies(group.map(({ bin }) => tallyOf(bin))),
    });
  }
  // Where each local midnight falls, in columns from the left edge.
  const dayTicks = [];
  const first = columns[0].start;
  const end = columns[columns.length - 1].end;
  for (
    let day = localMidnight(first);
    day <= end;
    day = localMidnight(day + DAY + 2 * HOUR)
  ) {
    if (day < first || day >= end) continue;
    const column = columns.find((c) => c.start <= day && day < c.end);
    if (column)
      dayTicks.push({
        at: column.index + (day - column.start) / (column.end - column.start),
        ms: day,
      });
  }
  return {
    binMs: baseMs * per,
    columns,
    dayTicks,
    maxN: Math.max(0, ...columns.map((column) => column.n)),
  };
}

/** Like stackCap, for columns that are counts rather than stacks. */
export function countCap(columns) {
  return stackCap(
    columns.map((column) => ({ items: { length: column.n || 0 } })),
  );
}

/** "2-hour columns", "15-minute columns", "1-day columns". */
export function binText(binMs) {
  if (binMs >= DAY) {
    const days = Math.round(binMs / DAY);
    return `${days}-day columns`;
  }
  if (binMs >= HOUR) {
    const hours = Math.round(binMs / HOUR);
    return `${hours}-hour columns`;
  }
  return `${Math.round(binMs / MINUTE)}-minute columns`;
}
