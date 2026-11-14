import { hourLabel } from "../../results/heatmap";
import { questionLabels } from "./askAnswers";
import {
  NONE_KEY,
  OTHER_KEY,
  WEEKDAYS,
  dayKeyOf,
  groupEntries,
  hourStart,
  parseDayHour,
  parseDayKey,
  tally,
  toMs,
  wilsonLower,
} from "./askSummary";
import { standOutCells } from "./askStats";
import { clampWindow, dayText } from "./timelineBins";

export { wilsonLower };

// The heatmap's pivots, all read from the summary's counts. A time grid's
// cell counts its rows (`n`), the ones answered so far, and each answer, so a
// colour is always a share of real counts; a matrix's cell is one answer's
// count in a group (or another question's answer), shaded by its share of
// the row.
//   dayHour      one row per day, a column per hour (GitHub-contribution
//                style), or per 3 hours when the hours would be too thin
//   weekdayHour  Monday to Sunday by hour, every week summed
//   modelDay     each model by day: a change that starts with a deploy
//   user, model, turns   the group's rows by answer
//   questions    the focused question's answers against another question's
//                (its own card, AskCrossTab)

export const PIVOTS = [
  { value: "dayHour", label: "Day × Hour", kind: "time" },
  { value: "weekdayHour", label: "Weekday × Hour", kind: "time" },
  { value: "modelDay", label: "Model × Day", kind: "time" },
  { value: "user", label: "Users", kind: "matrix" },
  { value: "model", label: "Models", kind: "matrix" },
  { value: "turns", label: "Turns", kind: "matrix" },
];

const MAX_DAYS = 31;
export const MAX_USERS = 15;
// Hourly columns once a cell holds this many rows on average, or the window
// is short enough to show every hour anyway.
const MIN_PER_HOUR_CELL = 3;
const HOURLY_DAYS = 10;

// Five steps of a share; fewer than 3 answered rows is too few to trust, so
// the cell is drawn lighter.
export const LEVELS = 5;
export const SMALL_N = 3;

/** 0 (none) to LEVELS - 1 (at or above `peak`) for a share 0..1. */
export const shareLevel = (share, peak = 1) =>
  share <= 0
    ? 0
    : Math.min(
        LEVELS - 1,
        1 + Math.floor((share / (peak || 1)) * (LEVELS - 1) - 1e-9),
      );

/**
 * The top of a shading scale: the highest share among cells with enough
 * answers, rounded up to a 10% step (100% from 90%), at least 10%.
 */
export function nicePeak(shares) {
  const top = Math.max(0, ...shares);
  if (top >= 0.9) return 1;
  return Math.max(0.1, Math.ceil(top * 10 - 1e-9) / 10);
}

const shortDay = (date) => dayText(date.getTime(), true);

const iso = (ms) => new Date(ms).toISOString();

const hourWords = (hour) => {
  const h = hour % 12 === 0 ? 12 : hour % 12;
  return `${h} ${hour % 24 < 12 ? "AM" : "PM"}`;
};
const span = (from, to) => `${hourWords(from)}–${hourWords(to % 24)}`;

// Every calendar day of the window when it is short enough, else the days
// that have rows; never past now.
function dayRows(cellDays, window, now) {
  const days = new Set(cellDays);
  const clamped = clampWindow(window, [], now);
  const start = toMs(clamped?.[0]);
  const end = toMs(clamped?.[1]);
  if (Number.isFinite(start) && Number.isFinite(end)) {
    const from = new Date(start);
    const all = [];
    for (
      let cursor = new Date(
        from.getFullYear(),
        from.getMonth(),
        from.getDate(),
      );
      cursor.getTime() < end && all.length <= MAX_DAYS;
      cursor = new Date(
        cursor.getFullYear(),
        cursor.getMonth(),
        cursor.getDate() + 1,
      )
    )
      all.push(dayKeyOf(cursor));
    if (all.length <= MAX_DAYS) all.forEach((day) => days.add(day));
  }
  return [...days].sort().slice(-MAX_DAYS);
}

/**
 * A time grid for one question:
 *   {rows, columns, cells: Map("row|col" -> cell), overall, step, sparse}
 * Each cell is {n, answered, failed, pending, errors, byLabel, filter,
 * title}. `step` is 1 (hourly columns), 3 or 6; `resolution` forces it, or
 * is "dense" to pick the finest step whose cells hold enough answers.
 */
export function buildTimeGrid({
  summary,
  question,
  pivot = "dayHour",
  window,
  now = Date.now(),
  resolution,
}) {
  const configId = question?.configId;
  const labels = question ? questionLabels(question) : [];
  const parsed = groupEntries(summary, "day_hour")
    .map((entry) => ({ ...parseDayHour(entry.key), entry }))
    .filter((cell) => cell.day && Number.isFinite(cell.hour));
  const view =
    window ||
    (summary?.window ? [summary.window.from, summary.window.to] : null);
  const days = dayRows(
    parsed.map((cell) => cell.day),
    view,
    now,
  );
  const answeredTotal = parsed.reduce(
    (sum, cell) => sum + tally(cell.entry, configId, labels).answered,
    0,
  );
  const perHourCell =
    pivot === "weekdayHour"
      ? answeredTotal / (7 * 24)
      : answeredTotal / Math.max(1, days.length * 24);
  const hourly =
    (pivot === "dayHour" && days.length <= HOURLY_DAYS) ||
    perHourCell >= MIN_PER_HOUR_CELL;
  // "dense": the finest of 1, 3 or 6 hours whose cells with answers hold 3
  // or more on average, for grids that split the answers further (one per
  // answer).
  const denseStep = () => {
    for (const candidate of [1, 3, 6]) {
      const filled = new Set(
        parsed
          .filter((cell) => tally(cell.entry, configId, labels).answered > 0)
          .map(
            (cell) =>
              `${pivot === "weekdayHour" ? parseDayKey(cell.day).getDay() : cell.day}|${Math.floor(cell.hour / candidate)}`,
          ),
      );
      if (answeredTotal >= MIN_PER_HOUR_CELL * filled.size) return candidate;
    }
    return 6;
  };
  const step =
    resolution === "dense" ? denseStep() : resolution || (hourly ? 1 : 3);
  const columns = [];
  for (let from = 0; from < 24; from += step)
    columns.push({
      key: String(from),
      from,
      to: from + step,
      label: hourLabel(from),
      title: span(from, from + step),
    });
  const weekdayOf = (day) => parseDayKey(day).getDay() || 7;
  const rows =
    pivot === "weekdayHour"
      ? [1, 2, 3, 4, 5, 6, 7].map((w) => ({
          key: String(w),
          label: WEEKDAYS[w],
          title: `${WEEKDAYS[w]}s`,
        }))
      : days.map((day) => {
          const date = parseDayKey(day);
          return {
            key: day,
            label: shortDay(date),
            title: shortDay(date),
            date,
          };
        });
  const cells = new Map();
  parsed.forEach((cell) => {
    const rowKey =
      pivot === "weekdayHour" ? String(weekdayOf(cell.day)) : cell.day;
    const from = Math.floor(cell.hour / step) * step;
    const key = `${rowKey}|${from}`;
    const t = tally(cell.entry, configId, labels);
    if (!cells.has(key))
      cells.set(key, {
        n: 0,
        answered: 0,
        failed: 0,
        pending: 0,
        errors: 0,
        byLabel: new Map(labels.map((label) => [label, 0])),
        parts: [],
      });
    const into = cells.get(key);
    into.n += t.n;
    into.answered += t.answered;
    into.failed += t.failed + t.skipped;
    into.pending += t.pending;
    into.errors += t.errors;
    t.byLabel.forEach((count, label) =>
      into.byLabel.set(label, (into.byLabel.get(label) || 0) + count),
    );
    into.parts.push(cell);
  });
  const rowOf = new Map(rows.map((row) => [row.key, row]));
  cells.forEach((cell, key) => {
    const [rowKey, fromText] = key.split("|");
    const from = Number(fromText);
    const row = rowOf.get(rowKey);
    cell.title = `${row?.title || rowKey} · ${span(from, from + step)}`;
    if (pivot === "weekdayHour") {
      // Every such hour of every such day.
      const dim = step === 3 ? "hour3" : "day_hour";
      cell.filter = {
        group: [
          ...new Set(
            cell.parts.map(
              (part) =>
                `${dim}:${part.day}|${String(step === 3 ? from : part.hour).padStart(2, "0")}`,
            ),
          ),
        ].sort(),
      };
    } else
      cell.filter = {
        time_from: iso(hourStart(rowKey, from)),
        time_to: iso(hourStart(rowKey, from + step)),
      };
    delete cell.parts;
  });
  const overall = { n: 0, answered: 0, byLabel: new Map() };
  cells.forEach((cell) => {
    overall.n += cell.n;
    overall.answered += cell.answered;
    cell.byLabel.forEach((count, label) =>
      overall.byLabel.set(label, (overall.byLabel.get(label) || 0) + count),
    );
  });
  return {
    pivot,
    rows,
    columns,
    cells,
    overall,
    step,
    sparse: step > 1 && !hourly,
  };
}

/**
 * Each model by day, from groups.model_day, in the time grid's shape (rows
 * are models, busiest first; columns are the window's days).
 */
export function buildModelDayGrid({
  summary,
  question,
  window,
  now = Date.now(),
}) {
  const configId = question?.configId;
  const labels = question ? questionLabels(question) : [];
  const parsed = groupEntries(summary, "model_day")
    .map((entry) => {
      const key = String(entry.key);
      const at = key.lastIndexOf("|");
      return { model: key.slice(0, at), day: key.slice(at + 1), entry };
    })
    .filter((cell) => cell.model && cell.model !== NONE_KEY && cell.day);
  const view =
    window ||
    (summary?.window ? [summary.window.from, summary.window.to] : null);
  const days = dayRows(
    parsed.map((cell) => cell.day),
    view,
    now,
  );
  const volume = new Map();
  const cells = new Map();
  parsed.forEach(({ model, day, entry }) => {
    const t = tally(entry, configId, labels);
    volume.set(model, (volume.get(model) || 0) + t.n);
    cells.set(`${model}|${day}`, {
      n: t.n,
      answered: t.answered,
      failed: t.failed + t.skipped,
      pending: t.pending,
      errors: t.errors,
      byLabel: t.byLabel,
      title: `${model} · ${dayText(parseDayKey(day).getTime(), true)}`,
      filter: { group: `model_day:${model}|${day}` },
    });
  });
  const rows = [...volume.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([model]) => ({ key: model, label: model, title: model }));
  const columns = days.map((day, index) => {
    const ms = parseDayKey(day).getTime();
    return {
      key: day,
      from: index,
      label: dayText(ms),
      title: dayText(ms, true),
      tick: index % 7 === 0,
    };
  });
  const overall = { n: 0, answered: 0, byLabel: new Map() };
  cells.forEach((cell) => {
    overall.n += cell.n;
    overall.answered += cell.answered;
    cell.byLabel.forEach((count, label) =>
      overall.byLabel.set(label, (overall.byLabel.get(label) || 0) + count),
    );
  });
  return {
    pivot: "modelDay",
    rows,
    columns,
    cells,
    overall,
    step: null,
    sparse: false,
  };
}

/**
 * The top of a grid's lower-bound scale for `label`: the highest lower bound
 * (`top`) and the scale's rounded top (`peak`).
 */
export function gridLowerPeak(grid, label) {
  let top = 0;
  grid.cells.forEach((cell) => {
    if (cell.answered)
      top = Math.max(
        top,
        wilsonLower(cell.byLabel.get(label) || 0, cell.answered),
      );
  });
  return { top, peak: nicePeak([top]) };
}

/**
 * The peak share of `label` over a grid's cells with enough answers, the top
 * of its shading scale.
 */
export function gridPeak(grid, label) {
  let top = 0;
  grid.cells.forEach((cell) => {
    if (cell.answered >= SMALL_N)
      top = Math.max(top, (cell.byLabel.get(label) || 0) / cell.answered);
  });
  // Named in the legend as it is ("0% → 64%"), at least 1%.
  return Math.max(0.01, Math.round(top * 100) / 100);
}

/**
 * A matrix: rows are a group's entries (or the focused question's labels),
 * columns another question's labels or this one's.
 *   {rows: [{key, label, title, counts: Map, answered, n, filter}], columns:
 *    [{key, label, title}], standOut: Map("row|col" -> test), against?,
 *    hidden, cellFilter(row, col)}
 */
export function buildMatrix({
  summary,
  questions = [],
  question,
  pivot,
  againstId,
  maxRows = MAX_USERS,
}) {
  if (!question)
    return { rows: [], columns: [], standOut: new Map(), hidden: 0 };
  const configId = question.configId;
  const labels = questionLabels(question);
  if (pivot === "questions") {
    const against =
      questions.find(
        (q) => q.configId === againstId && q.configId !== configId,
      ) || questions.find((q) => q.configId !== configId);
    if (!against)
      return { rows: [], columns: [], standOut: new Map(), hidden: 0 };
    const theirLabels = questionLabels(against);
    const forward = summary?.cross?.[configId]?.[against.configId];
    const backward = summary?.cross?.[against.configId]?.[configId];
    const count = (mine, theirs) =>
      forward ? forward[mine]?.[theirs] || 0 : backward?.[theirs]?.[mine] || 0;
    const rows = labels.map((label) => {
      const counts = new Map(
        theirLabels.map((theirs) => [theirs, count(label, theirs)]),
      );
      const answered = [...counts.values()].reduce((s, v) => s + v, 0);
      return {
        key: label,
        label,
        title: `“${question.question}” was “${label}”`,
        counts,
        answered,
        n: answered,
        filter: { config_id: configId, answer: label },
      };
    });
    const columns = theirLabels.map((label) => ({
      key: label,
      label,
      title: `“${against.question}” was “${label}”`,
    }));
    return {
      rows,
      columns,
      against,
      hidden: 0,
      standOut: standOutCells(rows, theirLabels),
      allRows: rows,
      cellFilter: (row, col) => ({
        answers: [
          { config_id: configId, answer: row.key },
          { config_id: against.configId, answer: col.key },
        ],
      }),
    };
  }
  const entries = groupEntries(summary, pivot);
  const named = entries.filter(
    (entry) => entry.key !== OTHER_KEY && entry.key !== NONE_KEY,
  );
  const ordered =
    pivot === "turns"
      ? named
      : [...named].sort(
          (a, b) => b.n - a.n || String(a.key).localeCompare(String(b.key)),
        );
  const shown = ordered.slice(0, maxRows);
  const toRow = (entry) => {
    const t = tally(entry, configId, labels);
    return {
      key: entry.key,
      label: entry.label || entry.key,
      title: entry.label || entry.key,
      counts: t.byLabel,
      answered: t.answered,
      n: t.n,
      filter: { group: `${pivot}:${entry.key}` },
    };
  };
  const rows = shown.map(toRow);
  // The comparison is against every other row of the run, listed or not.
  const everyone = entries.map(toRow);
  const tested = standOutCells(everyone, labels, {
    skip: (row) => row.key === OTHER_KEY || row.key === NONE_KEY,
  });
  return {
    rows,
    columns: labels.map((label) => ({
      key: label,
      label,
      title: `“${label}”`,
    })),
    hidden: entries.length - shown.length,
    hiddenRows: entries
      .filter((entry) => !shown.includes(entry))
      .reduce((sum, entry) => sum + (entry.n || 0), 0),
    standOut: tested,
    allRows: everyone,
    cellFilter: (row, col) => ({
      ...row.filter,
      config_id: configId,
      answer: col.key,
    }),
  };
}
