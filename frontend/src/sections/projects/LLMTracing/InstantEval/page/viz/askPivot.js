import {
  HOURS,
  dayKey,
  dayLabel,
  hourLabel,
  hourRange,
} from "../../results/heatmap";
import { answerState, questionLabels } from "./askAnswers";
import { clampWindow } from "./timelineBins";

// The heatmap's pivots. Every cell counts the rows it holds (`total`), the
// ones answered so far (`answered`) and the ones that gave the answer in
// focus (`matches`), so a colour is always a share of real counts.
//   dayHour   one row per day, one column per hour (GitHub-contribution style)
//   userDay   the 15 busiest users by day
//   modelDay  each model by day
//   questions the focused question's answers against another question's

export const PIVOTS = [
  { value: "dayHour", label: "Day × Hour" },
  { value: "userDay", label: "User × Day" },
  { value: "modelDay", label: "Model × Day" },
  { value: "questions", label: "Question × Question" },
];

const MAX_DAYS = 31;
export const MAX_USERS = 15;

const emptyCell = () => ({
  total: 0,
  answered: 0,
  matches: 0,
  errors: 0,
  ids: [],
});

const startOfDay = (time) =>
  new Date(time.getFullYear(), time.getMonth(), time.getDate());

// Every calendar day of the window when it is short enough, else the days
// that have rows.
function dayColumns(records, window) {
  const days = new Map();
  const add = (date) => {
    const key = dayKey(date);
    if (!days.has(key)) days.set(key, startOfDay(date));
  };
  const start = window?.[0] ? new Date(window[0]) : null;
  const end = window?.[1] ? new Date(window[1]) : null;
  if (
    start &&
    end &&
    !Number.isNaN(start.getTime()) &&
    !Number.isNaN(end.getTime())
  ) {
    const cursor = startOfDay(start);
    let count = 0;
    while (cursor <= end && count <= MAX_DAYS) {
      add(new Date(cursor));
      cursor.setDate(cursor.getDate() + 1);
      count += 1;
    }
    if (count > MAX_DAYS) days.clear();
  }
  records.forEach((record) => {
    const time = new Date(record.time);
    if (!Number.isNaN(time.getTime())) add(time);
  });
  return [...days.values()]
    .sort((a, b) => a - b)
    .slice(-MAX_DAYS)
    .map((date) => ({
      key: dayKey(date),
      label: date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      }),
      title: dayLabel(date),
      date,
    }));
}

const shortDay = (date) =>
  date.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });

/**
 * {rows: [{key, label, title}], columns: [{key, label, title}],
 *  cells: Map("row|col" -> cell), overall: {answered, matches}, against?}
 * `against` is the other question in the Question × Question pivot. A cell's
 * `errors` counts its rows whose run hit an error.
 */
export function buildPivot({
  records = [],
  questions = [],
  configId,
  answer,
  pivot = "dayHour",
  againstId,
  window: viewWindow,
  now,
}) {
  const window = clampWindow(viewWindow, records, now);
  const question =
    questions.find((q) => q.configId === configId) || questions[0];
  const cells = new Map();
  const overall = { answered: 0, matches: 0, total: 0 };
  const place = (rowKey, colKey, record, state) => {
    const key = `${rowKey}|${colKey}`;
    const cell = cells.get(key) || emptyCell();
    cell.total += 1;
    cell.ids.push(record.id);
    if (record.facets?.error === true) cell.errors += 1;
    if (state.state === "answered") {
      cell.answered += 1;
      if (state.answer === answer) cell.matches += 1;
    }
    cells.set(key, cell);
  };
  records.forEach((record) => {
    const state = answerState(record, question?.configId);
    overall.total += 1;
    if (state.state === "answered") {
      overall.answered += 1;
      if (state.answer === answer) overall.matches += 1;
    }
  });

  if (pivot === "questions") {
    const against =
      questions.find(
        (q) => q.configId === againstId && q.configId !== question?.configId,
      ) || questions.find((q) => q.configId !== question?.configId);
    if (!question || !against) return { rows: [], columns: [], cells, overall };
    const rows = questionLabels(question).map((label) => ({
      key: label,
      label,
      title: `“${question.question}” was “${label}”`,
    }));
    const columns = questionLabels(against).map((label) => ({
      key: label,
      label,
      title: `“${against.question}” was “${label}”`,
    }));
    // A cell's share here is of its row: of the rows that answered `row` to
    // the focused question, how many answered `col` to the other.
    records.forEach((record) => {
      const a = answerState(record, question.configId);
      const b = answerState(record, against.configId);
      if (a.state !== "answered" || b.state !== "answered") return;
      const key = `${a.answer}|${b.answer}`;
      const cell = cells.get(key) || emptyCell();
      cell.total += 1;
      cell.answered += 1;
      cell.matches += 1;
      if (record.facets?.error === true) cell.errors += 1;
      cell.ids.push(record.id);
      cells.set(key, cell);
    });
    return { rows, columns, cells, overall, against };
  }

  if (pivot === "dayHour") {
    const rows = dayColumns(records, window).map((day) => ({
      ...day,
      label: shortDay(day.date),
    }));
    const columns = HOURS.map((hour) => ({
      key: String(hour),
      label: hourLabel(hour),
      title: hourRange(hour),
    }));
    records.forEach((record) => {
      const time = new Date(record.time);
      if (Number.isNaN(time.getTime())) return;
      place(
        dayKey(time),
        String(time.getHours()),
        record,
        answerState(record, question?.configId),
      );
    });
    return { rows, columns, cells, overall };
  }

  const columns = dayColumns(records, window);
  const rowOf = (record) =>
    pivot === "userDay"
      ? record.user_id || record.end_user_id || ""
      : record.facets?.model || "";
  const volume = new Map();
  records.forEach((record) => {
    const key = rowOf(record);
    if (key) volume.set(key, (volume.get(key) || 0) + 1);
  });
  const keys = [...volume.entries()]
    .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
    .slice(0, pivot === "userDay" ? MAX_USERS : undefined)
    .map(([key]) => key);
  const kept = new Set(keys);
  records.forEach((record) => {
    const key = rowOf(record);
    const time = new Date(record.time);
    if (!kept.has(key) || Number.isNaN(time.getTime())) return;
    place(key, dayKey(time), record, answerState(record, question?.configId));
  });
  return {
    rows: keys.map((key) => ({ key, label: key, title: key })),
    columns,
    cells,
    overall,
    hidden: volume.size - keys.length,
  };
}

// Five steps of the focused answer's share; fewer than 3 answered rows is
// too few to trust, so the cell is drawn lighter.
export const LEVELS = 5;
export const SMALL_N = 3;

/** 0 (no share) to LEVELS - 1 (all of them) for a share 0..1. */
export const shareLevel = (share) =>
  share <= 0
    ? 0
    : Math.min(LEVELS - 1, 1 + Math.floor(share * (LEVELS - 1) - 1e-9));
