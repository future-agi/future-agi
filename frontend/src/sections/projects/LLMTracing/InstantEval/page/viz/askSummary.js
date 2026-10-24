import { ERROR_ANSWER, SKIPPED_ANSWER } from "../../instantEvalAnswers";
import { answerState, questionLabels } from "./askAnswers";
import { chooseBinMs, clampWindow, dayText } from "./timelineBins";

// The run as counts: the shape GET /tracer/instant-eval/summary/ returns, so
// every chart reads the same thing at 100 rows and at 100,000.
//   {status, total, answered, failed, pending, sample, questions,
//    counts:  {config_id: {label: n, "__error__": n}},
//    unsure:  {config_id: {label: n}},
//    timeline: {bin_ms, from, to, bins: [{t, n, pending, errors, counts}]},
//    groups: {day_hour, day, hour3, weekday, user, model, turns, error,
//             user_by_answer: {config_id: {label: [user entry]}}},
//    cross:  {config_a: {config_b: {label_a: {label_b: n}}}}}
// Bins and group entries carry {n, errors, failed, counts}: `errors` are rows
// whose own trace had an error, `failed` rows Jev could not answer, and their
// counts are sparse (a missing label is 0). Keys are in the viewer's time
// zone: day_hour "2026-10-13|08", day "2026-10-13", hour3 "2026-10-13|06"
// (06:00–09:00 that day), weekday "1" (Monday) to "7"; user is the end user's
// id ("__none__" without one, "__other__" past the 50 busiest), model its
// name, model_day "model|YYYY-MM-DD", turns "1", "2-3", "4-6", "7+", error
// "error" or "ok". user_by_answer lists, for each answer, the 50 users who
// give it most clearly more than the run does (a z-score against the run's
// share; more rows first on a tie; at least 3 rows): users the 50 busiest
// miss, such as a cohort that is rarely helped. When the page
// has only rows (a run of up to 1,500), summarizeRecords builds the same
// thing in the browser.
// A user question's rows are its users, each counted once under the answer
// most of their sessions got (`sessions_read` says how many sessions it
// read), placed at their latest session; it has no user groups, and
// `groups.user_consistency` instead: {config_id: {sessions: {label: n},
// users: {n: users}, tiers: {label: [[n, k, users]]}}}, how many users had k
// of their n answered sessions (3 or more) get each answer.

export const SQUARES_MAX = 1500;
export const MAX_BINS = 120;
export const MAX_USER_GROUPS = 50;
export const USERS_BY_ANSWER = 50;
export const MIN_USER_ROWS = 3;
export const OTHER_KEY = "__other__";
export const NONE_KEY = "__none__";

export const TURN_BUCKETS = [
  { key: "1", min: 1, max: 1, text: "1 turn" },
  { key: "2-3", min: 2, max: 3, text: "2–3 turns" },
  { key: "4-6", min: 4, max: 6, text: "4–6 turns" },
  { key: "7+", min: 7, max: Infinity, text: "7+ turns" },
];

// ISO weekdays: 1 is Monday, 7 Sunday.
export const WEEKDAYS = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const isoWeekday = (date) => date.getDay() || 7;

const pad = (n) => String(n).padStart(2, "0");

export const toMs = (value) => {
  if (value === null || value === undefined || value === "") return NaN;
  if (typeof value === "number") return value;
  const ms =
    value instanceof Date ? value.getTime() : new Date(value).getTime();
  return Number.isFinite(ms) ? ms : NaN;
};

/** "2026-10-13" for a local date. */
export const dayKeyOf = (date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

/** Local midnight of a "2026-10-13" key. */
export function parseDayKey(key) {
  const [y, m, d] = String(key).split("-").map(Number);
  return new Date(y, m - 1, d);
}

/** {day, hour, date} of a "2026-10-13|08" key. */
export function parseDayHour(key) {
  const [day, hour] = String(key).split("|");
  return { day, hour: Number(hour), date: parseDayKey(day) };
}

/** The local hour `hour` of the day `day`, as epoch ms (DST-safe). */
export const hourStart = (day, hour) => {
  const date = parseDayKey(day);
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
    hour,
  ).getTime();
};

/** A question from the page ({configId, answerType}) or the summary. */
export function normalizeQuestion(question) {
  if (!question) return null;
  const configId = question.configId ?? question.config_id;
  if (!configId) return null;
  return {
    ...question,
    configId: String(configId),
    question: question.question || "",
    answerType: question.answerType ?? question.answer_type,
    labels: questionLabels(question),
  };
}

/** The questions to chart: the page's when it has them, else the summary's. */
export function chartQuestions(questions, summary) {
  const list = questions?.length ? questions : summary?.questions || [];
  return list.map(normalizeQuestion).filter(Boolean);
}

/**
 * One question's counts in a summary entry (the whole run, a bin or a group):
 * {n, answered, failed, skipped, pending, errors, byLabel: Map}. `answered`
 * counts every answer, listed label or not; `pending` is what is left.
 */
export function tally(entry, configId, labels = []) {
  const counts = entry?.counts?.[configId] || {};
  const byLabel = new Map(labels.map((label) => [label, 0]));
  let answered = 0;
  Object.entries(counts).forEach(([label, count]) => {
    if (label === ERROR_ANSWER || label === SKIPPED_ANSWER) return;
    answered += count;
    if (byLabel.has(label)) byLabel.set(label, byLabel.get(label) + count);
  });
  const skipped = counts[SKIPPED_ANSWER] || 0;
  const n = Number.isFinite(entry?.n) ? entry.n : answered + skipped;
  // Bins and groups may give failures per row (`failed`) rather than per
  // question: exact for one question, but with several it counts rows where
  // any question failed, so B's failures would read as A's. Only a one-
  // question entry falls back to it.
  const questionsInEntry = Object.keys(entry?.counts || {}).length;
  const failed =
    ERROR_ANSWER in counts
      ? counts[ERROR_ANSWER]
      : questionsInEntry > 1
        ? 0
        : Math.min(entry?.failed || 0, Math.max(0, n - answered - skipped));
  return {
    n,
    answered,
    failed,
    skipped,
    pending: Math.max(0, n - answered - failed - skipped),
    errors: entry?.errors || 0,
    byLabel,
  };
}

/** The whole run's counts for one question (from `counts` and `total`). */
export function runTally(summary, question) {
  if (!summary || !question) return tally(null, null, []);
  return tally(
    {
      n: summary.total,
      counts: summary.counts,
    },
    question.configId,
    question.labels,
  );
}

/**
 * The share a cell or user surely has: the lower end of the 95% Wilson interval of
 * `k` of `n`. A few rows can't make it large, so it shades a busy grid
 * without lighting up noise.
 */
export function wilsonLower(k, n, z = 1.96) {
  if (!n) return 0;
  const p = k / n;
  const z2 = z * z;
  const centre = p + z2 / (2 * n);
  const spread = z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n));
  return Math.max(0, (centre - spread) / (1 + z2 / n));
}

/** The rows a summary read: a sample of the view, or all of it. */
export function sampleOf(summary) {
  const sample = summary?.sample;
  const read = sample?.read ?? summary?.total ?? 0;
  const population = sample?.population ?? read;
  return {
    read,
    population,
    isSample: Boolean(sample?.is_sample) && population > read,
  };
}

/** The group entries of one dimension, as an array. */
export const groupEntries = (summary, dimension) =>
  Array.isArray(summary?.groups?.[dimension]) ? summary.groups[dimension] : [];

export const turnBucket = (turns) =>
  Number.isFinite(turns) && turns > 0
    ? TURN_BUCKETS.find((b) => turns >= b.min && turns <= b.max)
    : null;

const hourText = (hour) => {
  const h = hour % 12 === 0 ? 12 : hour % 12;
  return `${h} ${hour % 24 < 12 ? "AM" : "PM"}`;
};

/** "6–9 AM", "9 PM–12 AM" for a 3-hour bucket starting at `hour`. */
export function hour3Text(hour) {
  const from = hourText(hour);
  const to = hourText((hour + 3) % 24);
  return from.slice(-2) === to.slice(-2)
    ? `${from.slice(0, -3)}–${to}`
    : `${from}–${to}`;
}

/**
 * The summary group keys a row falls in, and the user's name:
 * {day_hour, day, hour3, weekday, user, userName, model, model_day, turns,
 * error}.
 */
export function groupKeysOf(record) {
  const keys = {};
  const ms = toMs(record?.time);
  if (Number.isFinite(ms)) {
    const date = new Date(ms);
    const day = dayKeyOf(date);
    const hour = date.getHours();
    keys.day_hour = `${day}|${pad(hour)}`;
    keys.day = day;
    keys.hour3 = `${day}|${pad(Math.floor(hour / 3) * 3)}`;
    keys.weekday = String(isoWeekday(date));
  }
  const user = record?.end_user_id || record?.user_id;
  keys.user = user ? String(user) : NONE_KEY;
  keys.userName = record?.user_id || keys.user;
  const facets = record?.facets || {};
  keys.model = facets.model ? String(facets.model) : NONE_KEY;
  if (keys.day) keys.model_day = `${keys.model}|${keys.day}`;
  const bucket = turnBucket(facets.turns);
  if (bucket) keys.turns = bucket.key;
  if (typeof facets.error === "boolean")
    keys.error = facets.error ? "error" : "ok";
  return keys;
}

const shortDay = (key, withWeekday) =>
  dayText(parseDayKey(key).getTime(), withWeekday);

const GROUP_LABEL = {
  day_hour: (key) => {
    const { day, hour } = parseDayHour(key);
    return `${shortDay(day)}, ${hourText(hour)}`;
  },
  day: (key) => shortDay(key, true).replace(",", ""),
  hour3: (key) => {
    const { day, hour } = parseDayHour(key);
    return `${shortDay(day)}, ${hour3Text(hour)}`;
  },
  weekday: (key) => WEEKDAYS[Number(key)] || key,
  user: (key) => key,
  model: (key) => (key === NONE_KEY ? "Unknown model" : key),
  model_day: (key) => {
    const at = key.lastIndexOf("|");
    const model = key.slice(0, at);
    return `${model === NONE_KEY ? "Unknown model" : model} · ${shortDay(key.slice(at + 1))}`;
  },
  turns: (key) => TURN_BUCKETS.find((b) => b.key === key)?.text || key,
  error: (key) => (key === "error" ? "Trace had an error" : "No trace error"),
};

/**
 * The summary of `records` (up to a few thousand) in the server's shape, so
 * a run that has its rows draws exactly what a large run draws from counts.
 */
export function summarizeRecords({
  records = [],
  questions = [],
  window: viewWindow,
  now = Date.now(),
  status,
  maxBins = MAX_BINS,
  unit,
} = {}) {
  const qs = questions.map(normalizeQuestion).filter(Boolean);
  const emptyCounts = () => Object.fromEntries(qs.map((q) => [q.configId, {}]));
  const counts = emptyCounts();
  const unsure = emptyCounts();
  const cross = {};
  qs.forEach((a) => {
    cross[a.configId] = {};
    qs.forEach((b) => {
      if (a.configId !== b.configId) cross[a.configId][b.configId] = {};
    });
  });
  const groups = Object.fromEntries(
    Object.keys(GROUP_LABEL).map((dimension) => [dimension, new Map()]),
  );
  const bump = (object, key, by = 1) => {
    object[key] = (object[key] || 0) + by;
  };
  let answeredRows = 0;
  let failedRows = 0;
  let pendingRows = 0;

  // One pass: each row's states, then every count it adds to.
  const rows = records.map((record) => {
    const states = qs.map((q) => answerState(record, q.configId));
    const pending = states.some(
      (s) => s.state === "waiting" || s.state === "reading",
    );
    const failed = states.some((s) => s.state === "failed");
    if (pending) pendingRows += 1;
    else if (failed) failedRows += 1;
    else answeredRows += 1;
    return {
      record,
      states,
      pending,
      failed,
      error: record?.facets?.error === true,
    };
  });
  const addTo = (entry, row) => {
    entry.n += 1;
    if (row.error) entry.errors += 1;
    if (row.pending) entry.pending += 1;
    if (row.failed) entry.failed += 1;
    qs.forEach((q, i) => {
      const { state, answer } = row.states[i];
      const into = entry.counts[q.configId];
      if (state === "answered") bump(into, answer);
      else if (state === "failed") bump(into, ERROR_ANSWER);
      else if (state === "skipped") bump(into, SKIPPED_ANSWER);
    });
  };
  const entryOf = (dimension, key, label) => {
    const map = groups[dimension];
    if (!map.has(key))
      map.set(key, {
        key,
        label: label || GROUP_LABEL[dimension](key),
        n: 0,
        errors: 0,
        failed: 0,
        pending: 0,
        counts: emptyCounts(),
      });
    return map.get(key);
  };

  rows.forEach((row) => {
    qs.forEach((q, i) => {
      const { state, answer, unsure: low } = row.states[i];
      if (state === "answered") {
        bump(counts[q.configId], answer);
        if (low) bump(unsure[q.configId], answer);
      } else if (state === "failed") bump(counts[q.configId], ERROR_ANSWER);
      else if (state === "skipped") bump(counts[q.configId], SKIPPED_ANSWER);
    });
    qs.forEach((a, i) => {
      const sa = row.states[i];
      if (sa.state !== "answered") return;
      qs.forEach((b, j) => {
        const sb = row.states[j];
        if (i === j || sb.state !== "answered") return;
        const byA = cross[a.configId][b.configId];
        byA[sa.answer] = byA[sa.answer] || {};
        bump(byA[sa.answer], sb.answer);
      });
    });
    const { userName, ...keys } = groupKeysOf(row.record);
    Object.entries(keys).forEach(([dimension, key]) => {
      if (dimension === "user" && unit === "users") return;
      addTo(
        entryOf(
          dimension,
          key,
          dimension === "user" && key !== NONE_KEY ? userName : null,
        ),
        row,
      );
    });
  });

  // The 50 busiest users, then everyone else as one entry.
  const users = [...groups.user.values()].sort(
    (a, b) => b.n - a.n || a.key.localeCompare(b.key),
  );
  const userEntries = users.slice(0, MAX_USER_GROUPS);
  if (users.length > MAX_USER_GROUPS) {
    const other = {
      key: OTHER_KEY,
      label: `${users.length - MAX_USER_GROUPS} other users`,
      n: 0,
      errors: 0,
      failed: 0,
      pending: 0,
      counts: emptyCounts(),
    };
    users.slice(MAX_USER_GROUPS).forEach((entry) => {
      other.n += entry.n;
      other.errors += entry.errors;
      other.failed += entry.failed;
      other.pending += entry.pending;
      qs.forEach((q) =>
        Object.entries(entry.counts[q.configId]).forEach(([label, n]) =>
          bump(other.counts[q.configId], label, n),
        ),
      );
    });
    userEntries.push(other);
  }

  // For each answer, the users who give it more than the run does, most
  // clearly first: ranked by z = (k − a·p0) / √(a·p0·(1 − p0)), where k is
  // the user's count of the answer, a their rows that answered the question
  // and p0 the run's share of the answer.
  const userByAnswer = {};
  if (unit !== "users")
    qs.forEach((q) => {
      userByAnswer[q.configId] = {};
      const run = tally({ n: 0, counts }, q.configId, q.labels);
      q.labels.forEach((label) => {
        const p0 = run.answered
          ? (run.byLabel.get(label) || 0) / run.answered
          : 0;
        const z = new Map(
          users.map((entry) => {
            const a = tally(entry, q.configId, q.labels).answered;
            const k = entry.counts[q.configId][label] || 0;
            const spread = Math.sqrt(a * p0 * (1 - p0));
            return [entry.key, spread ? (k - a * p0) / spread : 0];
          }),
        );
        userByAnswer[q.configId][label] = users
          .filter(
            (entry) =>
              entry.key !== NONE_KEY &&
              entry.n >= MIN_USER_ROWS &&
              (entry.counts[q.configId][label] || 0) > 0,
          )
          .sort(
            (a, b) =>
              z.get(b.key) - z.get(a.key) ||
              b.n - a.n ||
              a.key.localeCompare(b.key),
          )
          .slice(0, USERS_BY_ANSWER);
      });
    });

  const sortedKeys = (dimension) =>
    [...groups[dimension].values()].sort((a, b) =>
      a.key < b.key ? -1 : a.key > b.key ? 1 : 0,
    );

  return {
    status: status || (pendingRows ? "running" : "done"),
    total: records.length,
    answered: answeredRows,
    failed: failedRows,
    pending: pendingRows,
    sample: {
      population: records.length,
      read: records.length,
      is_sample: false,
    },
    questions: qs.map((q) => ({
      config_id: q.configId,
      question: q.question,
      answer_type: q.answerType,
      labels: q.labels,
    })),
    counts,
    unsure,
    timeline: buildBins(rows, qs, viewWindow, now, maxBins, addTo, emptyCounts),
    groups: {
      day_hour: sortedKeys("day_hour"),
      day: sortedKeys("day"),
      hour3: sortedKeys("hour3"),
      weekday: sortedKeys("weekday"),
      user: userEntries,
      model: [...groups.model.values()].sort((a, b) => b.n - a.n),
      model_day: sortedKeys("model_day"),
      turns: TURN_BUCKETS.map((b) => groups.turns.get(b.key)).filter(Boolean),
      error: sortedKeys("error").reverse(),
      user_by_answer: userByAnswer,
    },
    cross: qs.length > 1 ? cross : {},
  };
}

const localMidnight = (ms) => {
  const date = new Date(ms);
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
  ).getTime();
};

// Equal bins from local midnight of the first day to the end of the window
// (stopped at now), every bin kept so quiet stretches show as gaps.
function buildBins(rows, qs, viewWindow, now, maxBins, addTo, emptyCounts) {
  const records = rows.map((row) => row.record);
  const window = clampWindow(viewWindow, records, now);
  const times = records.map((r) => toMs(r.time)).filter(Number.isFinite);
  const windowEnd = toMs(window?.[1]);
  const from = Math.min(toMs(window?.[0]) || Infinity, ...times);
  const to = Math.max(windowEnd || -Infinity, ...times);
  if (!Number.isFinite(from) || !Number.isFinite(to))
    return { bin_ms: 60 * 60 * 1000, from: null, to: null, bins: [] };
  const binMs = chooseBinMs(to - from, maxBins);
  const origin = localMidnight(from);
  const first = Math.floor((from - origin) / binMs);
  const last = Math.max(
    first,
    Number.isFinite(windowEnd)
      ? Math.floor((windowEnd - 1 - origin) / binMs)
      : first,
    ...times.map((ms) => Math.floor((ms - origin) / binMs)),
  );
  const bins = [];
  for (let index = first; index <= last; index += 1)
    bins.push({
      t: origin + index * binMs,
      n: 0,
      pending: 0,
      errors: 0,
      failed: 0,
      counts: emptyCounts(),
    });
  rows.forEach((row) => {
    const ms = toMs(row.record.time);
    if (!Number.isFinite(ms)) return;
    const bin = bins[Math.floor((ms - origin) / binMs) - first];
    if (bin) addTo(bin, row);
  });
  return {
    bin_ms: binMs,
    from: origin + first * binMs,
    to: origin + (last + 1) * binMs,
    bins,
  };
}

/**
 * Whether one row is in a chart's selection, for pages that hold the rows:
 *   {time_from, time_to}             the row's time is in [from, to)
 *   {config_id, answer}              it got that answer
 *   {answers: [{config_id, answer}]} it got every one of them
 *   {group: "dim:key" | [...]}       it is in that group (or any of them)
 * Keys combine with AND.
 */
export function matchesFilter(record, filter) {
  if (!filter) return true;
  const ms = toMs(record?.time);
  if (filter.time_from && !(ms >= toMs(filter.time_from))) return false;
  if (filter.time_to && !(ms < toMs(filter.time_to))) return false;
  const answers = [
    ...(filter.config_id
      ? [{ config_id: filter.config_id, answer: filter.answer }]
      : []),
    ...(filter.answers || []),
  ];
  if (
    answers.some(
      ({ config_id: configId, answer }) =>
        answerState(record, configId).answer !== answer,
    )
  )
    return false;
  if (filter.group) {
    const keys = groupKeysOf(record);
    const wanted = Array.isArray(filter.group) ? filter.group : [filter.group];
    const hit = wanted.some((group) => {
      const at = group.indexOf(":");
      return keys[group.slice(0, at)] === group.slice(at + 1);
    });
    if (!hit) return false;
  }
  return true;
}
