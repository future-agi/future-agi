import { UNITS } from "src/sections/projects/observePopulation";
import {
  formatCount,
  formatShare,
  isYesNo,
  questionLabels,
} from "./askAnswers";
import {
  MIN_USER_ROWS,
  NONE_KEY,
  OTHER_KEY,
  TURN_BUCKETS,
  WEEKDAYS,
  chartQuestions,
  groupEntries,
  hourStart,
  parseDayHour,
  parseDayKey,
  runTally,
  sampleOf,
  summarizeRecords,
  tally,
  wilsonLower,
} from "./askSummary";
import { dayText } from "./timelineBins";

export { TURN_BUCKETS };

// "What stands out": every group of rows (a day, a 3-hour stretch of a day, a
// weekday, the weekend, a time of day, a user, a model, a turn count, errors,
// another question's answer) is compared with the rest for an answer, using
// Fisher's exact test on the summary's counts, and the p-values are corrected
// across every comparison (Benjamini–Hochberg). A category question with no
// answer in focus tests every label, all in one correction. Every number in a
// callout is a count; nothing is generated, and the same counts give the same
// callouts.
//
// Three things keep the correction honest without row ids:
//   - time groups are sets of Day × Hour cells, so two that hold the same
//     cells are the same rows and are counted once, and the error group is
//     the same rows as a stretch whose rows all errored when the counts say
//     so;
//   - a group that is the exact complement of another (error / no error,
//     weekend / weekdays) is the same comparison, counted once;
//   - a group too small to ever reach significance whatever its answers are
//     is not counted against the others (Tarone's rule for discrete tests,
//     as adapted to Benjamini–Hochberg by Gilbert, 2005).

export const Q_MAX = 0.1;
export const MIN_DELTA = 0.1;
// A rare answer stands out at a smaller gap when its share doubles or halves.
export const MIN_RATIO = 2;
export const MIN_DELTA_RARE = 0.05;
// A table cell is outlined at a smaller gap than a callout: the table shows
// every cell's share already, and the outline marks the ones that clearly
// differ, 5 points or more above the other rows.
export const MIN_DELTA_TABLE = 0.05;
export const MIN_ROWS = 3;
export const MAX_PATTERNS = 5;
const WINDOW_HOURS = 3;
// A model's change needs this many days on each side of the day it starts.
const MIN_SPLIT_DAYS = 3;
// Below this, q-values are all "certain" and the bigger finding ranks first.
const Q_FLOOR = 1e-12;

// ln(n!) by summing, cached.
const LOG_FACTORIAL = [0];
function logFactorial(n) {
  for (let i = LOG_FACTORIAL.length; i <= n; i += 1)
    LOG_FACTORIAL[i] = LOG_FACTORIAL[i - 1] + Math.log(i);
  return LOG_FACTORIAL[n];
}

const logChoose = (n, k) =>
  logFactorial(n) - logFactorial(k) - logFactorial(n - k);

// Tables with up to this many possible values are summed term by term.
const EXACT_SUPPORT = 60;
// Walking out from the mode, terms this small (against the mode's) end it.
const NEGLIGIBLE = 1e-18;

/**
 * Two-sided Fisher exact p for the 2×2 table [[a, b], [c, d]]: the sum of
 * the probabilities of every table with the same margins that is no more
 * likely than the one observed. Large tables walk out from the most likely
 * table with the hypergeometric ratio and stop once terms are negligible,
 * so a 100,000-row run costs about as much as a small one.
 */
export function fisherExact(a, b, c, d) {
  const row1 = a + b;
  const col1 = a + c;
  const n = a + b + c + d;
  if (n === 0) return 1;
  const lo = Math.max(0, row1 + col1 - n);
  const hi = Math.min(row1, col1);
  const logDenominator = logChoose(n, row1);
  const logProb = (x) =>
    logChoose(col1, x) + logChoose(n - col1, row1 - x) - logDenominator;
  if (hi - lo <= EXACT_SUPPORT) {
    const observed = Math.exp(logProb(a));
    let p = 0;
    for (let x = lo; x <= hi; x += 1) {
      const px = Math.exp(logProb(x));
      if (px <= observed * (1 + 1e-7)) p += px;
    }
    return Math.min(1, p);
  }
  const mode = Math.min(
    hi,
    Math.max(lo, Math.floor(((row1 + 1) * (col1 + 1)) / (n + 2))),
  );
  const logMode = logProb(mode);
  const observed = Math.exp(logProb(a) - logMode) * (1 + 1e-7);
  let total = 1;
  let tail = observed >= 1 ? 1 : 0;
  const rest = n - row1 - col1;
  let term = 1;
  for (let x = mode; x < hi && term > NEGLIGIBLE; x += 1) {
    term *= ((row1 - x) * (col1 - x)) / ((x + 1) * (rest + x + 1));
    total += term;
    if (term <= observed) tail += term;
  }
  term = 1;
  for (let x = mode; x > lo && term > NEGLIGIBLE; x -= 1) {
    term *= (x * (rest + x)) / ((row1 - x + 1) * (col1 - x + 1));
    total += term;
    if (term <= observed) tail += term;
  }
  // So unlikely that no term walked is smaller: the observed table's own
  // probability is the right size.
  if (tail === 0) return Math.exp(logProb(a));
  return Math.min(1, tail / total);
}

/**
 * Benjamini–Hochberg q-values, in the order of `ps`, correcting for `m`
 * comparisons (at least as many as there are p-values).
 */
export function benjaminiHochberg(ps, m = ps.length) {
  const order = ps.map((p, i) => [p, i]).sort((x, y) => x[0] - y[0]);
  const qs = new Array(ps.length);
  let running = 1;
  for (let rank = ps.length; rank >= 1; rank -= 1) {
    const [p, i] = order[rank - 1];
    running = Math.min(running, (p * m) / rank);
    qs[i] = Math.min(1, running);
  }
  return qs;
}

/**
 * The smallest p-value a group of `n` rows could reach, given `total` rows
 * of which `hits` gave the answer: every row of the group gives it, or none.
 */
export function minimumP(n, total, hits) {
  const p = (k) => fisherExact(k, n - k, hits - k, total - n - hits + k);
  return Math.min(p(Math.min(n, hits)), p(Math.max(0, n - (total - hits))));
}

/**
 * Tarone's count: the smallest K such that at most K groups could reach
 * `alpha / K`. Groups that cannot are left out of the correction.
 */
export function taroneCount(minimumPs, alpha = Q_MAX) {
  const sorted = [...minimumPs].sort((x, y) => x - y);
  // How many groups could reach `level`: a binary search of the sorted list.
  const could = (level) => {
    let lo = 0;
    let hi = sorted.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (sorted[mid] <= level) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  };
  for (let k = 1; k <= sorted.length; k += 1)
    if (could(alpha / k) <= k) return k;
  return sorted.length;
}

const hourText = (hour) => {
  const h = hour % 12 === 0 ? 12 : hour % 12;
  return { h, half: hour % 24 < 12 ? "AM" : "PM" };
};

/** "2–5 PM", "11 AM–2 PM". */
export function hoursText(from, to) {
  const a = hourText(from);
  const b = hourText(to);
  return a.half === b.half
    ? `${a.h}–${b.h} ${b.half}`
    : `${a.h} ${a.half}–${b.h} ${b.half}`;
}

const unitNoun = (unit) => (UNITS[unit] || UNITS.sessions).plural;

const iso = (ms) => new Date(ms).toISOString();

// Partitions split the answered rows between their groups, so a group of a
// partition can be the complement of another, or imply it.
const PARTITIONS = new Set([
  "error",
  "model",
  "turns",
  "question",
  "weekday",
  "weekpart",
  "tod",
]);

// When two time groups hold the same cells, the one kept says it best.
const PRIORITY = [
  "error",
  "window",
  "day",
  "weekday",
  "weekpart",
  "tod",
  "model",
  "turns",
  "question",
  "user",
];

const PLURAL_WEEKDAY = [
  "",
  "Mondays",
  "Tuesdays",
  "Wednesdays",
  "Thursdays",
  "Fridays",
  "Saturdays",
  "Sundays",
];

// A group's answers to one question, summed over summary entries.
function sumTallies(tallies, labels) {
  const out = {
    n: 0,
    answered: 0,
    errors: 0,
    byLabel: new Map(labels.map((label) => [label, 0])),
  };
  tallies.forEach((t) => {
    out.n += t.n;
    out.answered += t.answered;
    out.errors += t.errors;
    t.byLabel.forEach((count, label) =>
      out.byLabel.set(label, (out.byLabel.get(label) || 0) + count),
    );
  });
  return out;
}

/**
 * Every group of the run for `question`, from the summary's counts:
 * {key, dimension, partition, where, filter, n, answered, errors, byLabel,
 *  cells?: "day|hour,…" (time groups), day?, from?, to?}.
 */
export function buildGroups(summary, questions, question, unit) {
  const { configId, labels } = question;
  const groups = [];
  const add = (group) => groups.push({ partition: group.dimension, ...group });

  // Time: every group is a set of Day × Hour cells.
  const cells = groupEntries(summary, "day_hour")
    .map((entry) => {
      const { day, hour } = parseDayHour(entry.key);
      const t = tally(entry, configId, labels);
      return { key: entry.key, day, hour, entry, t };
    })
    .filter((cell) => cell.day && Number.isFinite(cell.hour));
  const timeGroup = (list) => {
    const sum = sumTallies(
      list.map((cell) => cell.t),
      labels,
    );
    const answeredCells = list
      .filter((cell) => cell.t.answered > 0)
      .map((cell) => cell.key)
      .sort();
    return { ...sum, cells: answeredCells.join(","), list };
  };
  const byDay = new Map();
  cells.forEach((cell) => {
    if (!byDay.has(cell.day)) byDay.set(cell.day, []);
    byDay.get(cell.day).push(cell);
  });
  const days = [...byDay.keys()].sort();
  days.forEach((day) => {
    const list = byDay.get(day);
    const start = parseDayKey(day);
    const next = new Date(
      start.getFullYear(),
      start.getMonth(),
      start.getDate() + 1,
    );
    add({
      key: `day:${day}`,
      dimension: "day",
      day,
      where: `on ${dayText(start.getTime())}`,
      filter: { time_from: iso(start.getTime()), time_to: iso(next.getTime()) },
      ...timeGroup(list),
    });
    for (let from = 0; from <= 24 - WINDOW_HOURS; from += 1) {
      const inside = list.filter(
        (cell) => cell.hour >= from && cell.hour < from + WINDOW_HOURS,
      );
      const answered = inside.filter((cell) => cell.t.answered > 0);
      if (!answered.length) continue;
      // A stretch reads as the hours its answered rows fall in.
      const first = Math.min(...answered.map((cell) => cell.hour));
      const last = Math.max(...answered.map((cell) => cell.hour)) + 1;
      add({
        key: `window:${day}:${from}`,
        dimension: "window",
        day,
        from: first,
        to: last,
        where: `on ${dayText(start.getTime())}, ${hoursText(first, last)}`,
        filter: {
          time_from: iso(hourStart(day, first)),
          time_to: iso(hourStart(day, last)),
        },
        ...timeGroup(inside),
      });
    }
  });
  const weekdayOf = (day) => parseDayKey(day).getDay() || 7;
  const weekdays = [1, 2, 3, 4, 5, 6, 7].filter((w) =>
    days.some((day) => weekdayOf(day) === w),
  );
  weekdays.forEach((w) =>
    add({
      key: `weekday:${w}`,
      dimension: "weekday",
      where: `on ${PLURAL_WEEKDAY[w]}`,
      filter: { group: `weekday:${w}` },
      ...timeGroup(cells.filter((cell) => weekdayOf(cell.day) === w)),
    }),
  );
  [
    ["weekend", "at weekends", [6, 7]],
    ["weekdays", "on weekdays", [1, 2, 3, 4, 5]],
  ].forEach(([key, where, set]) => {
    const list = cells.filter((cell) => set.includes(weekdayOf(cell.day)));
    if (!list.length) return;
    add({
      key: `weekpart:${key}`,
      dimension: "weekpart",
      where,
      filter: { group: set.map((w) => `weekday:${w}`) },
      ...timeGroup(list),
    });
  });
  for (let from = 0; from < 24; from += WINDOW_HOURS) {
    const list = cells.filter(
      (cell) => cell.hour >= from && cell.hour < from + WINDOW_HOURS,
    );
    if (!list.length) continue;
    const blocks = [
      ...new Set(
        list.map(
          (cell) => `hour3:${cell.day}|${String(from).padStart(2, "0")}`,
        ),
      ),
    ].sort();
    add({
      key: `tod:${from}`,
      dimension: "tod",
      from,
      to: from + WINDOW_HOURS,
      where: `between ${hoursText(from, from + WINDOW_HOURS)}`,
      filter: { group: blocks },
      ...timeGroup(list),
    });
  }

  // Facets.
  const facet = (dimension, keep, where, partition = dimension) =>
    groupEntries(summary, dimension)
      .filter((entry) => keep(entry))
      .forEach((entry) => {
        const t = tally(entry, configId, labels);
        add({
          key: `${dimension}:${entry.key}`,
          dimension,
          partition,
          entryKey: entry.key,
          name: entry.label || entry.key,
          where: where(entry),
          filter: { group: `${dimension}:${entry.key}` },
          n: t.n,
          answered: t.answered,
          errors: t.errors,
          byLabel: t.byLabel,
        });
      });
  // Users: the 50 busiest, and for each answer the users with the most of
  // it (`user_by_answer`), who may have fewer rows (a cohort rarely helped).
  // A user picked for having the most of an answer is `picked`: it is
  // checked as if every user had been.
  if (unit !== "users") {
    const seen = new Set();
    const addUser = (entry, picked) => {
      if (!entry || entry.key === OTHER_KEY || entry.key === NONE_KEY) return;
      if (seen.has(entry.key)) return;
      seen.add(entry.key);
      const t = tally(entry, configId, labels);
      const name = entry.label || entry.key;
      add({
        key: `user:${entry.key}`,
        dimension: "user",
        entryKey: entry.key,
        name,
        where: `for ${name}`,
        filter: { group: `user:${entry.key}` },
        n: t.n,
        answered: t.answered,
        errors: t.errors,
        byLabel: t.byLabel,
        picked,
      });
    };
    groupEntries(summary, "user").forEach((entry) => addUser(entry, false));
    Object.values(summary?.groups?.user_by_answer || {}).forEach((lists) =>
      Object.values(lists || {}).forEach((list) =>
        (Array.isArray(list) ? list : []).forEach((entry) =>
          addUser(entry, true),
        ),
      ),
    );
  }
  facet(
    "model",
    (entry) => entry.key !== NONE_KEY,
    (entry) => `with ${entry.label || entry.key}`,
  );
  // A user question's user is one row: their turns are their sessions',
  // and they errored when any of their sessions did.
  const byUser = unit === "users";
  facet(
    "turns",
    () => true,
    (entry) =>
      `${byUser ? "for" : "in"} ${unitNoun(unit)} with ${
        TURN_BUCKETS.find((b) => b.key === entry.key)?.text || entry.label
      }`,
  );
  facet(
    "error",
    () => true,
    (entry) => {
      if (byUser)
        return entry.key === "error"
          ? "for users with a trace error"
          : "for users with no trace error";
      return entry.key === "error"
        ? "when the trace had an error"
        : "when the trace had no error";
    },
  );

  // Each model since each day, against the same model before it: a change
  // that starts on a day (a deploy) rather than one spread over the run.
  const byModel = new Map();
  groupEntries(summary, "model_day").forEach((entry) => {
    const at = String(entry.key).lastIndexOf("|");
    const model = String(entry.key).slice(0, at);
    const day = String(entry.key).slice(at + 1);
    if (!model || model === NONE_KEY || !day) return;
    if (!byModel.has(model)) byModel.set(model, []);
    byModel
      .get(model)
      .push({ day, key: entry.key, t: tally(entry, configId, labels) });
  });
  // The model's days before each split, summed once (a running total).
  const less = (all, part) => ({
    n: all.n - part.n,
    answered: all.answered - part.answered,
    errors: all.errors - part.errors,
    byLabel: new Map(
      labels.map((label) => [
        label,
        (all.byLabel.get(label) || 0) - (part.byLabel.get(label) || 0),
      ]),
    ),
  });
  byModel.forEach((list, model) => {
    list.sort((a, b) => (a.day < b.day ? -1 : 1));
    const all = sumTallies(
      list.map((d) => d.t),
      labels,
    );
    const running = [sumTallies([], labels)];
    list.forEach((d, i) => running.push(sumTallies([running[i], d.t], labels)));
    for (let i = MIN_SPLIT_DAYS; i <= list.length - MIN_SPLIT_DAYS; i += 1) {
      const before = running[i];
      const after = less(all, before);
      const since = dayText(parseDayKey(list[i].day).getTime());
      add({
        key: `modelsince:${model}:${list[i].day}`,
        dimension: "modelsince",
        model,
        since: list[i].day,
        where: `for ${model} since ${since}`,
        restText: "before",
        filter: { group: list.slice(i).map((d) => `model_day:${d.key}`) },
        n: after.n,
        answered: after.answered,
        errors: after.errors,
        byLabel: after.byLabel,
        baseline: before,
      });
    }
  });

  // Another question's answers, from the crossed counts.
  questions.forEach((other) => {
    if (other.configId === configId) return;
    const forward = summary?.cross?.[configId]?.[other.configId];
    const backward = summary?.cross?.[other.configId]?.[configId];
    if (!forward && !backward) return;
    const count = (mine, theirs) =>
      forward ? forward[mine]?.[theirs] || 0 : backward?.[theirs]?.[mine] || 0;
    questionLabels(other).forEach((theirs) => {
      const byLabel = new Map(
        labels.map((mine) => [mine, count(mine, theirs)]),
      );
      const answered = [...byLabel.values()].reduce((s, v) => s + v, 0);
      if (!answered) return;
      add({
        key: `q:${other.configId}:${theirs}`,
        dimension: "question",
        partition: `q:${other.configId}`,
        where: `when “${other.question}” was “${theirs}”`,
        filter: { config_id: other.configId, answer: theirs },
        n: answered,
        answered,
        errors: 0,
        byLabel,
      });
    });
  });

  // The error group is the same rows as a time group whose rows all errored
  // and that holds every error.
  const errorGroup = groups.find((g) => g.key === "error:error");
  const errorTotal = cells.reduce((sum, cell) => sum + cell.t.errors, 0);
  if (errorGroup && errorGroup.n === errorTotal)
    groups.forEach((g) => {
      if (g.cells !== undefined && g.n === errorTotal && g.errors === g.n)
        g.sameAs = errorGroup.key;
    });
  return { groups, cells };
}

// "all 8 on Oct 13, 7–10 AM" when every errored row of the error group falls
// in one day (and within four hours, for the hours).
function errorConcentration(group, cells) {
  const errored = cells.filter((cell) => cell.t.errors > 0);
  const total = errored.reduce((sum, cell) => sum + cell.t.errors, 0);
  if (!errored.length || total !== group.n || group.answered !== group.n)
    return "";
  const dayKeys = new Set(errored.map((cell) => cell.day));
  if (dayKeys.size !== 1) return "";
  const from = Math.min(...errored.map((cell) => cell.hour));
  const to = Math.max(...errored.map((cell) => cell.hour)) + 1;
  const day = parseDayKey([...dayKeys][0]).getTime();
  const hours = to - from <= 4 ? `, ${hoursText(from, to)}` : "";
  return `all ${total} on ${dayText(day)}${hours}`;
}

// Partitions in two where one side is the exception (errors, the weekend):
// what stands out on the other side, for another answer, is the same
// finding, and folds under the exception's.
const FOLDED = new Set(["error", "weekpart"]);

// Callouts are grouped into families; at most PER_FAMILY of one are shown.
const FAMILY = {
  day: "time",
  window: "time",
  weekday: "time",
  weekpart: "time",
  tod: "time",
  model: "model",
  modelsince: "model",
  user: "user",
  users: "user",
  error: "error",
  turns: "turns",
  question: "question",
};
const PER_FAMILY = 2;
// A callout with this share of its rows inside a stronger one's group is
// that one seen again.
const NESTED = 0.7;

// A nested callout says the same as its parent unless its share is further
// from the rest than the parent's and more than this far from the parent's.
const SHARPER = 0.15;
const sayingTheSame = (child, parent) =>
  Math.abs(child.delta) <= Math.abs(parent.delta) ||
  Math.abs(child.rate - parent.rate) < SHARPER;

const errorsIn = (g) => g.list.reduce((sum, cell) => sum + cell.t.errors, 0);

/**
 * The share of group `a`'s rows that lie in group `b`, where the counts can
 * tell (0 where they can't): time groups by their Day × Hour cells, errors
 * by each cell's error count, a model since a day inside the model. A
 * stretch of time wholly on or after the day a model changed counts as
 * inside that change.
 */
export function insideShare(a, b) {
  if (a === b || !a.n) return 0;
  const isTime = (g) => g.list !== undefined;
  if (isTime(a) && isTime(b)) {
    const keys = new Set(b.list.map((cell) => cell.key));
    const shared = a.list.reduce(
      (sum, cell) => sum + (keys.has(cell.key) ? cell.t.n : 0),
      0,
    );
    return shared / a.n;
  }
  if (isTime(a) && b.dimension === "error")
    return (b.entryKey === "error" ? errorsIn(a) : a.n - errorsIn(a)) / a.n;
  if (a.dimension === "error" && isTime(b))
    return (a.entryKey === "error" ? errorsIn(b) : b.n - errorsIn(b)) / a.n;
  if (isTime(a) && b.dimension === "modelsince")
    return a.list.every((cell) => cell.day >= b.since) ? 1 : 0;
  if (a.dimension === "modelsince" && b.dimension === "model")
    return a.model === b.entryKey ? 1 : 0;
  if (a.dimension === "model" && b.dimension === "modelsince")
    return a.entryKey === b.model ? b.n / a.n : 0;
  return 0;
}

const passesEffect = (t) => {
  const gap = Math.abs(t.delta);
  if (gap >= MIN_DELTA) return true;
  const high = Math.max(t.rate, t.restRate);
  const low = Math.min(t.rate, t.restRate);
  return gap >= MIN_DELTA_RARE && high >= MIN_RATIO * low;
};

/**
 * What a callout's "Show" selects: the rows that make it (the group's rows
 * with the answer) when the answer is more common there, else the whole
 * group, where it is missing. {filter, count, label}.
 */
function selection(t, question, unit) {
  const answer = { config_id: question.configId, answer: t.label };
  const group = t.group.filter;
  if (t.delta > 0 && t.k > 0) {
    const filter = group.config_id
      ? {
          answers: [
            { config_id: group.config_id, answer: group.answer },
            answer,
          ],
        }
      : { ...group, ...answer };
    return { filter, count: t.k, label: `“${t.label}” ${t.group.where}` };
  }
  return {
    filter: group,
    count: t.n,
    label: `All ${unitNoun(unit)} ${t.group.where}`,
  };
}

/** The answer to look at for a yes/no question, or one label in focus. */
function focusOf(focus, questions) {
  const question =
    questions.find((q) => q.configId === focus?.configId) || questions[0];
  if (!question) return { question: null, answer: null, explicit: false };
  const labels = question.labels;
  const explicit = Boolean(focus?.answer) && labels.includes(focus.answer);
  if (explicit) return { question, answer: focus.answer, explicit };
  if (isYesNo(question)) return { question, answer: "No", explicit };
  // A category question with nothing in focus tests every label.
  return { question, answer: null, explicit };
}

/**
 * Patterns for the question in focus.
 *   {question, answer, labels, answered, matches, rate, tested, counted,
 *    patterns, isSample}
 * `answer` is the label tested, or null when every label of a category
 * question was (`labels`). `tested` is how many distinct comparisons were made
 * and `counted` how many the correction counted. Each pattern:
 * {key, label, dimension, text, where, when, filter, n, k, rate, restRate,
 *  delta, p, q}; `filter` selects the group's rows on the rows endpoint.
 * Pass the server's `summary`, or `records` to build one.
 */
export function findPatterns({
  summary: given,
  records,
  questions: questionList = [],
  focus,
  unit = "sessions",
  window,
  max = MAX_PATTERNS,
}) {
  const summary =
    given ||
    summarizeRecords({ records, questions: questionList, window, unit });
  const questions = chartQuestions(questionList, summary);
  const { question, answer } = focusOf(focus, questions);
  const { isSample } = sampleOf(summary);
  const empty = {
    question,
    answer,
    labels: answer ? [answer] : question?.labels || [],
    answered: 0,
    matches: 0,
    rate: null,
    tested: 0,
    counted: 0,
    patterns: [],
    more: [],
    isSample,
  };
  if (!question) return empty;
  const run = runTally(summary, question);
  const total = run.answered;
  const testLabels = answer ? [answer] : question.labels;
  const matches = answer ? run.byLabel.get(answer) || 0 : 0;
  const base = {
    ...empty,
    answered: total,
    matches,
    rate: answer && total ? matches / total : null,
  };
  if (total < 2 * MIN_ROWS) return base;

  const { groups: all, cells } = buildGroups(
    summary,
    questions,
    question,
    unit,
  );
  // Time groups holding the same answered cells are one group: keep the
  // clearest description.
  const byCells = new Map();
  all
    .filter((g) => g.cells !== undefined && !g.sameAs)
    .sort(
      (a, b) =>
        PRIORITY.indexOf(a.dimension) - PRIORITY.indexOf(b.dimension) ||
        (a.from ?? 0) - (b.from ?? 0),
    )
    .forEach((g) => {
      if (!byCells.has(g.cells)) byCells.set(g.cells, g);
    });
  const kept = new Set(byCells.values());
  const groups = all.filter(
    (g) =>
      !g.sameAs &&
      (g.cells === undefined || kept.has(g)) &&
      g.answered >= MIN_ROWS &&
      g.answered < total &&
      (!g.baseline || g.baseline.answered >= MIN_ROWS),
  );

  // The rarer side of each such partition split in two.
  const rarer = new Map();
  FOLDED.forEach((dimension) => {
    const sides = groups.filter((g) => g.dimension === dimension);
    if (
      sides.length === 2 &&
      sides[0].answered + sides[1].answered === total &&
      sides[0].answered !== sides[1].answered
    )
      rarer.set(
        dimension,
        sides[0].answered < sides[1].answered ? sides[0].key : sides[1].key,
      );
  });

  const everyUser = Math.max(1, Math.floor((run.n || total) / MIN_USER_ROWS));
  const minP = new Map();
  const minimumOf = (n, hits) => {
    const key = `${n}|${hits}`;
    if (!minP.has(key)) minP.set(key, minimumP(n, total, hits));
    return minP.get(key);
  };
  const tests = [];
  testLabels.forEach((label) => {
    const hits = run.byLabel.get(label) || 0;
    if (hits === 0 || hits === total) return;
    let rows = groups.map((g) => {
      const n = g.answered;
      const k = g.byLabel.get(label) || 0;
      // A group with its own baseline (a model since a day) is compared with
      // that baseline (the same model before), not with the rest of the run.
      const restN = g.baseline ? g.baseline.answered : total - n;
      const restK = g.baseline ? g.baseline.byLabel.get(label) || 0 : hits - k;
      return {
        group: g,
        label,
        n,
        k,
        restN,
        restK,
        rate: k / n,
        restRate: restN ? restK / restN : 0,
        delta: k / n - (restN ? restK / restN : 0),
      };
    });
    // A partition split in two is one comparison: keep the side where the
    // answer is more common ("Order status when the trace had an error",
    // "Product advice when it had none"), the smaller one on a tie.
    const partitions = new Map();
    rows.forEach((t) => {
      if (!PARTITIONS.has(t.group.dimension)) return;
      const list = partitions.get(t.group.partition) || [];
      list.push(t);
      partitions.set(t.group.partition, list);
    });
    const complements = new Set();
    partitions.forEach((list) => {
      if (list.length === 2 && list[0].n + list[1].n === total) {
        const [a, b] = list;
        const keepA = a.delta > b.delta || (a.delta === b.delta && a.n <= b.n);
        complements.add(keepA ? b : a);
      }
    });
    rows = rows.filter((t) => !complements.has(t));
    rows.forEach((t) => {
      // A user picked from every user for having the most of an answer is
      // checked as if every user with enough rows had been (at most one per
      // MIN_USER_ROWS rows), so picking the extremes adds no surprise.
      const screened = t.group.picked ? everyUser : 1;
      tests.push({
        ...t,
        hits,
        p: Math.min(
          1,
          screened * fisherExact(t.k, t.n - t.k, t.restK, t.restN - t.restK),
        ),
        minP: Math.min(
          1,
          screened *
            (t.group.baseline
              ? minimumP(t.n, t.n + t.restN, t.k + t.restK)
              : minimumOf(t.n, hits)),
        ),
      });
    });
  });

  const counted = taroneCount(tests.map((t) => t.minP));
  const testable = tests.filter((t) => t.minP <= Q_MAX / counted);
  const qs = benjaminiHochberg(
    testable.map((t) => t.p),
    counted,
  );
  tests.forEach((t) => {
    t.q = 1;
  });
  testable.forEach((t, i) => {
    t.q = qs[i];
  });
  let passing = tests.filter((t) => t.q < Q_MAX && passesEffect(t));

  const sameLabel = (a, b) => a.label === b.label;
  // Within a partition, groups where the answer is more common imply the
  // ones where it is less common (1 turn and 4–6 turns imply 2–3 turns).
  const risers = new Set(
    passing
      .filter((t) => PARTITIONS.has(t.group.dimension) && t.delta > 0)
      .map((t) => `${t.label}|${t.group.partition}`),
  );
  passing = passing.filter(
    (t) =>
      !PARTITIONS.has(t.group.dimension) ||
      t.delta > 0 ||
      !risers.has(`${t.label}|${t.group.partition}`),
  );
  // The weekend standing out says it for its Saturdays and Sundays.
  const weekparts = passing.filter((t) => t.group.dimension === "weekpart");
  passing = passing.filter(
    (t) =>
      t.group.dimension !== "weekday" ||
      !weekparts.some(
        (w) =>
          sameLabel(w, t) &&
          Math.sign(w.delta) === Math.sign(t.delta) &&
          w.group.filter.group.includes(t.group.filter.group),
      ),
  );

  // Overlapping stretches of a day are one finding: keep the strongest, drop
  // any that overlap it, and drop the whole day when a stretch of it stands
  // out.
  const windows = passing
    .filter((t) => t.group.dimension === "window")
    .sort(
      (a, b) => a.q - b.q || Math.abs(b.delta) - Math.abs(a.delta) || b.n - a.n,
    );
  const keptWindows = [];
  windows.forEach((t) => {
    const overlaps = keptWindows.some(
      (w) =>
        sameLabel(w, t) &&
        w.group.day === t.group.day &&
        w.group.from < t.group.to &&
        t.group.from < w.group.to,
    );
    if (!overlaps) keptWindows.push(t);
  });
  passing = passing.filter((t) =>
    t.group.dimension === "window"
      ? keptWindows.includes(t)
      : t.group.dimension === "day"
        ? !keptWindows.some(
            (w) => sameLabel(w, t) && w.group.day === t.group.day,
          )
        : true,
  );

  // A model changing on several days is one finding: its clearest split.
  const splits = new Map();
  passing
    .filter((t) => t.group.dimension === "modelsince")
    .forEach((t) => {
      const key = `${t.label}|${t.group.model}`;
      const best = splits.get(key);
      if (
        !best ||
        t.q < best.q ||
        (t.q === best.q && Math.abs(t.delta) > Math.abs(best.delta))
      )
        splits.set(key, t);
    });
  const keptSplits = new Set(splits.values());
  passing = passing.filter(
    (t) => t.group.dimension !== "modelsince" || keptSplits.has(t),
  );

  // Users that stand out the same way read as one group; where their shares
  // fall into two clear bunches (a cohort at 85% and a few busy users at
  // 52%), as two, split at the widest gap when it is SHARPER or more.
  const users = passing.filter((t) => t.group.dimension === "user");
  const merged = [];
  const bunches = (side) => {
    if (side.length < 2) return [side];
    const byRate = [...side].sort(
      (a, b) => a.rate - b.rate || a.group.key.localeCompare(b.group.key),
    );
    let at = 0;
    let widest = 0;
    for (let i = 1; i < byRate.length; i += 1) {
      const gap = byRate[i].rate - byRate[i - 1].rate;
      if (gap > widest) {
        widest = gap;
        at = i;
      }
    }
    return widest >= SHARPER ? [byRate.slice(0, at), byRate.slice(at)] : [side];
  };
  testLabels.forEach((label) =>
    [true, false].forEach((higher) => {
      const side = users.filter(
        (t) => t.label === label && t.delta > 0 === higher,
      );
      bunches(side).forEach((bunch, part) => {
        if (bunch.length < 2) {
          merged.push(...bunch);
          return;
        }
        bunch.sort(
          (a, b) => a.q - b.q || a.group.key.localeCompare(b.group.key),
        );
        const n = bunch.reduce((sum, t) => sum + t.n, 0);
        const k = bunch.reduce((sum, t) => sum + t.k, 0);
        const { hits } = bunch[0];
        const restN = total - n;
        const restK = hits - k;
        const names = bunch.map((t) => t.group.name);
        const listed =
          names.length <= 2
            ? names.join(" and ")
            : `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
        merged.push({
          label,
          group: {
            key: `users:${label}:${higher}:${part}`,
            dimension: "users",
            where: `for ${names.length} users (${listed})`,
            filter: { group: bunch.map((t) => t.group.filter.group) },
          },
          n,
          k,
          restN,
          restK,
          rate: k / n,
          restRate: restN ? restK / restN : 0,
          delta: k / n - (restN ? restK / restN : 0),
          p: Math.min(...bunch.map((t) => t.p)),
          q: Math.min(...bunch.map((t) => t.q)),
        });
      });
    }),
  );
  passing = [...passing.filter((t) => t.group.dimension !== "user"), ...merged];

  // Strongest evidence first. A time group gives way to an explanation
  // (a model, users, errors, turns, another answer) of comparable strength,
  // within 10× in q, and an answer being less common gives way to one being
  // more common; among the near-certain ones, the finding that accounts for
  // the most rows (a whole weekend before one weekend morning).
  const excess = (t) => Math.abs(t.k - t.n * t.restRate);
  const strength = (t) =>
    Math.log10(Math.max(t.q, Q_FLOOR)) +
    (FAMILY[t.group.dimension] === "time" ? 1 : 0) +
    (t.delta < 0 ? 1 : 0);
  passing.forEach((t) => {
    t.strength = strength(t);
  });
  passing.sort(
    (a, b) =>
      a.strength - b.strength ||
      excess(b) - excess(a) ||
      Math.abs(b.delta) - Math.abs(a.delta) ||
      (a.group.key < b.group.key ? -1 : 1),
  );

  // A callout on the larger side of a partition in two (no error, weekdays)
  // folds under the strongest callout on its rarer side, whatever its
  // answer: "Product advice when the trace had no error" is "Order status
  // when it had one" seen from the other side. The rarer side leads.
  const lead = new Map();
  passing.forEach((t) => {
    const dimension = t.group.dimension;
    if (rarer.get(dimension) === t.group.key && !lead.has(dimension))
      lead.set(dimension, t);
  });
  const foldUnder = new Map();
  passing.forEach((t) => {
    const side = rarer.get(t.group.dimension);
    const parent = side && side !== t.group.key && lead.get(t.group.dimension);
    if (parent) foldUnder.set(t, parent);
  });
  if (foldUnder.size) {
    const children = new Map();
    foldUnder.forEach((parent, t) =>
      children.set(parent, [...(children.get(parent) || []), t]),
    );
    passing = passing
      .filter((t) => !foldUnder.has(t))
      .flatMap((t) => [t, ...(children.get(t) || [])]);
  }

  // A callout most of whose rows (70%) lie inside a stronger one's group,
  // with the same answer going the same way, is that callout seen again (a
  // Saturday afternoon inside "at weekends", Oct 13 inside "gpt-4.1-mini
  // since Oct 11"): it moves under "Show all", naming the one it sits in.
  // Unless it is much sharper: an afternoon at 98% inside errors at 44% is
  // a finding of its own.
  // At most two callouts of a family (time, model, users, errors, turns,
  // another question) are shown before "Show all".
  const shown = [];
  const familyCount = new Map();
  const rest = [];
  passing.forEach((t) => {
    const parent =
      foldUnder.get(t) ||
      shown
        .concat(rest.filter((r) => !r.within))
        .find(
          (s) =>
            s.label === t.label &&
            Math.sign(s.delta) === Math.sign(t.delta) &&
            sayingTheSame(t, s) &&
            insideShare(t.group, s.group) >= NESTED,
        );
    if (parent) {
      rest.push({ ...t, within: parent });
      return;
    }
    const family = FAMILY[t.group.dimension] || t.group.dimension;
    const count = familyCount.get(family) || 0;
    if (shown.length < max && count < PER_FAMILY) {
      shown.push(t);
      familyCount.set(family, count + 1);
    } else rest.push(t);
  });

  const inSample = isSample ? " in the sample" : "";
  const toPattern = (t) => {
    const when =
      t.group.dimension === "error" && t.group.entryKey === "error"
        ? errorConcentration(t.group, cells)
        : "";
    const restText = t.group.restText || "elsewhere";
    return {
      key: `${t.label}|${t.group.key}`,
      label: t.label,
      dimension: t.group.dimension,
      family: FAMILY[t.group.dimension] || t.group.dimension,
      text: `“${t.label}” ${t.group.where}: ${t.k} of ${t.n} (${formatShare(t.rate)}) vs ${formatShare(t.restRate)} ${restText}${inSample}${when ? `, ${when}` : ""}`,
      where: t.group.where,
      restText,
      when,
      within: t.within
        ? {
            key: `${t.within.label}|${t.within.group.key}`,
            label: t.within.label,
            where: t.within.group.where,
          }
        : null,
      filter: t.group.filter,
      select: selection(t, question, unit),
      n: t.n,
      k: t.k,
      rate: t.rate,
      restRate: t.restRate,
      delta: t.delta,
      p: t.p,
      q: t.q,
    };
  };
  const patterns = shown.map(toPattern);
  // Everything else that passed, strongest first.
  const more = rest.map(toPattern);
  return { ...base, tested: tests.length, counted, patterns, more };
}

/**
 * The cells of a label × label table that stand out: for each cell, the rows
 * of its row against the other rows, Fisher + Benjamini–Hochberg across every
 * cell, at least MIN_DELTA_TABLE above the other rows. `rows` are [{key, counts: Map(col -> n)}]; returns Map("row|col" ->
 * {n, k, p, q, rate, restRate, delta}) for the cells that pass, where the row
 * has more of the answer than the other rows. Rows `skip` returns true for
 * (the rest of the users, say) count in the totals but are not tested.
 */
export function standOutCells(rows, columns, { skip = () => false } = {}) {
  const colTotals = new Map(
    columns.map((col) => [
      col,
      rows.reduce((sum, row) => sum + (row.counts.get(col) || 0), 0),
    ]),
  );
  const total = [...colTotals.values()].reduce((s, v) => s + v, 0);
  const tests = [];
  rows.forEach((row) => {
    const n = columns.reduce((sum, col) => sum + (row.counts.get(col) || 0), 0);
    if (n < MIN_ROWS || n >= total || skip(row)) return;
    columns.forEach((col) => {
      const hits = colTotals.get(col);
      if (!hits || hits === total) return;
      const k = row.counts.get(col) || 0;
      const restN = total - n;
      const restK = hits - k;
      tests.push({
        key: `${row.key}|${col}`,
        n,
        k,
        rate: k / n,
        restRate: restK / restN,
        delta: k / n - restK / restN,
        p: fisherExact(k, n - k, restK, restN - restK),
        minP: minimumP(n, total, hits),
      });
    });
  });
  const counted = taroneCount(tests.map((t) => t.minP));
  const testable = tests.filter((t) => t.minP <= Q_MAX / counted);
  const qs = benjaminiHochberg(
    testable.map((t) => t.p),
    counted,
  );
  const out = new Map();
  testable.forEach((t, i) => {
    const q = qs[i];
    // Only where the row has more of the answer: with several answers, the
    // ones it has less of follow from it.
    if (q < Q_MAX && t.delta >= MIN_DELTA_TABLE) out.set(t.key, { ...t, q });
  });
  return out;
}

// A user question reads each user's latest sessions, a few at most: how many
// users had k of n sessions get an answer shows the ones it keeps finding.
export const MIN_CONSISTENT_SESSIONS = 3;
// The most tiers one line names.
const MAX_TIERS = 3;
// The most answers a category question lists users for.
export const MAX_CONSISTENCY = 3;

const binomial = (k, n, p) =>
  Math.exp(logChoose(n, k) + k * Math.log(p) + (n - k) * Math.log(1 - p));

/** P(X ≥ x) for X ~ Poisson(mean): how surprising x users are. */
export function poissonTail(x, mean) {
  if (x <= 0) return 1;
  if (!(mean > 0)) return 0;
  const term = (i) => Math.exp(-mean + i * Math.log(mean) - logFactorial(i));
  if (x <= mean) {
    // At or below the mean the tail is half or more: 1 − P(X < x).
    let below = 0;
    for (let i = 0; i < x; i += 1) below += term(i);
    return Math.min(1, Math.max(0, 1 - below));
  }
  let tail = 0;
  for (let i = x; ; i += 1) {
    const t = term(i);
    tail += t;
    if (t <= tail * 1e-12) break;
  }
  return Math.min(1, tail);
}

/**
 * A user question's users who most consistently got an answer, from the
 * summary's `groups.user_consistency`. For each answer: the tiers of users
 * k of whose n sessions got it (n of 3 or more) that surely beat the run's
 * share of it (the lower end of k/n's 95% Wilson interval is above the
 * sessions' share), at the most sessions read any such tier has, k
 * falling. Each tier says how many users chance alone would put there at
 * that share (each session's answer independent of who asked), and an
 * answer is listed only when its users are clearly more than that: the
 * Poisson tail of their count, corrected across the answers checked
 * (Benjamini–Hochberg, q < Q_MAX). Most above chance first.
 *   [{label, n, share, users, expected, p, q, tiers: [{k, n, users,
 *     expected}], text, chance, filter, select}]
 */
export function findConsistency({ summary, question, answer = null }) {
  const data = summary?.groups?.user_consistency?.[question?.configId];
  if (!data) return [];
  const settled = Object.values(data.sessions || {}).reduce((s, v) => s + v, 0);
  if (!settled) return [];
  const labels = answer ? [answer] : questionLabels(question);
  const lines = [];
  labels.forEach((label) => {
    const share = (data.sessions?.[label] || 0) / settled;
    if (share <= 0 || share >= 1) return;
    const passing = (data.tiers?.[label] || []).filter(
      ([n, k, users]) =>
        n >= MIN_CONSISTENT_SESSIONS && users > 0 && wilsonLower(k, n) > share,
    );
    if (!passing.length) return;
    const most = Math.max(...passing.map(([n]) => n));
    const tiers = passing
      .filter(([n]) => n === most)
      .sort((a, b) => b[1] - a[1])
      .slice(0, MAX_TIERS)
      .map(([n, k, users]) => ({
        n,
        k,
        users,
        expected: (data.users?.[String(n)] || 0) * binomial(k, n, share),
      }));
    const users = tiers.reduce((sum, t) => sum + t.users, 0);
    const expected = tiers.reduce((sum, t) => sum + t.expected, 0);
    const [first, ...others] = tiers;
    const head =
      first.k === first.n
        ? `${formatCount(first.users)} ${first.users === 1 ? "user" : "users"} answered “${label}” in every session read (${first.k} of ${first.n})`
        : `${formatCount(first.users)} ${first.users === 1 ? "user" : "users"} answered “${label}” in ${first.k} of ${first.n} sessions read`;
    const text = [
      head,
      ...others.map((t) => `${formatCount(t.users)} in ${t.k} of ${t.n}`),
    ].join(" · ");
    const around = (x) => formatCount(Math.round(x));
    const chance = `At the run's ${formatShare(share)} of sessions, chance alone would put about ${
      tiers.length === 1
        ? around(first.expected)
        : `${tiers
            .slice(0, -1)
            .map((t) => around(t.expected))
            .join(", ")} and ${around(tiers[tiers.length - 1].expected)}`
    } there.`;
    const filter = {
      group: tiers.map(
        (t) => `consistent:${question.configId}|${label}|${t.k}|${t.n}`,
      ),
    };
    const of = tiers.map((t) => `${t.k} of ${t.n}`).join(" or ");
    lines.push({
      key: `consistent|${label}`,
      label,
      n: most,
      share,
      users,
      expected,
      p: poissonTail(users, expected),
      tiers,
      text,
      chance,
      filter,
      select: {
        filter,
        count: users,
        label: `Users who answered “${label}” in ${of} sessions read`,
      },
    });
  });
  const qs = benjaminiHochberg(
    lines.map((line) => line.p),
    labels.length,
  );
  return lines
    .map((line, i) => ({ ...line, q: qs[i] }))
    .filter((line) => line.q < Q_MAX && line.users > line.expected)
    .sort(
      (a, b) =>
        b.users - b.expected - (a.users - a.expected) ||
        (a.label < b.label ? -1 : 1),
    )
    .slice(0, MAX_CONSISTENCY);
}

export { WEEKDAYS };
