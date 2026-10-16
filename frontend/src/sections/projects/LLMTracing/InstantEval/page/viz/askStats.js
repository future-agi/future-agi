import { UNITS } from "src/sections/projects/observePopulation";
import { answerState, formatShare, resolveFocus } from "./askAnswers";
import { dayText } from "./timelineBins";

// "What stands out": for the answer in focus, every group of rows (a day, a
// 3-hour stretch of a day, a user, a model, a turn count, errors, another
// question's answer) is compared with the rest using Fisher's exact test, and
// the p-values are corrected across the comparisons (Benjamini–Hochberg).
// Every number in a callout is a count; nothing is generated.
//
// Two things keep the correction from drowning a small run:
//   - a group that is the exact complement of another (error / no error) or
//     holds exactly the same rows as another is the same comparison, so it
//     is counted once;
//   - a group too small to ever reach significance whatever its answers are
//     is not counted against the others (Tarone's rule for discrete tests,
//     as adapted to Benjamini–Hochberg by Gilbert, 2005).

export const Q_MAX = 0.1;
export const MIN_DELTA = 0.1;
export const MIN_ROWS = 3;
export const MAX_PATTERNS = 5;
const WINDOW_HOURS = 3;

// ln(n!) by summing, cached; the population is a few thousand at most.
const LOG_FACTORIAL = [0];
function logFactorial(n) {
  for (let i = LOG_FACTORIAL.length; i <= n; i += 1)
    LOG_FACTORIAL[i] = LOG_FACTORIAL[i - 1] + Math.log(i);
  return LOG_FACTORIAL[n];
}

const logChoose = (n, k) =>
  logFactorial(n) - logFactorial(k) - logFactorial(n - k);

/**
 * Two-sided Fisher exact p for the 2×2 table [[a, b], [c, d]]: the sum of
 * the probabilities of every table with the same margins that is no more
 * likely than the one observed.
 */
export function fisherExact(a, b, c, d) {
  const row1 = a + b;
  const col1 = a + c;
  const n = a + b + c + d;
  if (n === 0) return 1;
  const lo = Math.max(0, row1 + col1 - n);
  const hi = Math.min(row1, col1);
  const logDenominator = logChoose(n, row1);
  const prob = (x) =>
    Math.exp(
      logChoose(col1, x) + logChoose(n - col1, row1 - x) - logDenominator,
    );
  const observed = prob(a);
  let p = 0;
  for (let x = lo; x <= hi; x += 1) {
    const px = prob(x);
    if (px <= observed * (1 + 1e-7)) p += px;
  }
  return Math.min(1, p);
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
  for (let k = 1; k <= minimumPs.length; k += 1)
    if (minimumPs.filter((p) => p <= alpha / k).length <= k) return k;
  return minimumPs.length;
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

export const TURN_BUCKETS = [
  { key: "1", min: 1, max: 1, text: "1 turn" },
  { key: "2-3", min: 2, max: 3, text: "2–3 turns" },
  { key: "4-6", min: 4, max: 6, text: "4–6 turns" },
  { key: "7+", min: 7, max: Infinity, text: "7+ turns" },
];

const localDay = (date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

const unitNoun = (unit) => (UNITS[unit] || UNITS.sessions).plural;

// Which groups split the same rows between them: each partition is one way of
// slicing the run, so complements and implied groups can be found.
const PARTITIONS = new Set(["error", "model", "turns", "question"]);

// When two groups hold exactly the same rows, the one kept says it best.
const PRIORITY = [
  "error",
  "model",
  "turns",
  "question",
  "user",
  "window",
  "day",
];

/**
 * Every group to test: {key, dimension, partition, where, members: Set<id>}.
 * `where` completes the sentence after the answer ("on Oct 13, 2–5 PM").
 */
function buildGroups(rows, questions, question, unit) {
  const groups = new Map();
  const add = (key, dimension, where, id, extra = {}) => {
    if (!groups.has(key))
      groups.set(key, {
        key,
        dimension,
        partition: dimension,
        where,
        members: new Set(),
        ...extra,
      });
    groups.get(key).members.add(id);
  };
  rows.forEach(({ record }) => {
    const id = record.id;
    const time = new Date(record.time);
    if (!Number.isNaN(time.getTime())) {
      const day = localDay(time);
      add(`day:${day}`, "day", `on ${dayText(day)}`, id, { day });
      const hour = time.getHours();
      for (
        let start = Math.max(0, hour - WINDOW_HOURS + 1);
        start <= Math.min(hour, 24 - WINDOW_HOURS);
        start += 1
      )
        add(`window:${day}:${start}`, "window", "", id, { day, start });
    }
    const user = record.user_id || record.end_user_id;
    if (user && unit !== "users")
      add(`user:${user}`, "user", `for ${user}`, id, { user });
    const facets = record.facets || {};
    if (facets.model)
      add(`model:${facets.model}`, "model", `with ${facets.model}`, id);
    if (Number.isFinite(facets.turns) && facets.turns > 0) {
      const bucket = TURN_BUCKETS.find(
        (b) => facets.turns >= b.min && facets.turns <= b.max,
      );
      add(
        `turns:${bucket.key}`,
        "turns",
        `in ${unitNoun(unit)} with ${bucket.text}`,
        id,
      );
    }
    if (typeof facets.error === "boolean")
      add(
        `error:${facets.error}`,
        "error",
        facets.error
          ? "when the run hit an error"
          : "when the run had no error",
        id,
      );
    questions.forEach((other) => {
      if (other.configId === question.configId) return;
      const { state, answer } = answerState(record, other.configId);
      if (state !== "answered") return;
      add(
        `q:${other.configId}:${answer}`,
        "question",
        `when “${other.question}” was “${answer}”`,
        id,
        { partition: `q:${other.configId}` },
      );
    });
  });
  return [...groups.values()];
}

// The local day and hours the rows of a group fall in.
function spanOf(members, timeOf) {
  let first = Infinity;
  let last = -Infinity;
  const days = new Set();
  members.forEach((id) => {
    const ms = timeOf.get(id);
    if (!Number.isFinite(ms)) return;
    first = Math.min(first, ms);
    last = Math.max(last, ms);
    days.add(localDay(new Date(ms)));
  });
  if (!Number.isFinite(first)) return null;
  return {
    day: days.size === 1 ? [...days][0] : null,
    from: new Date(first).getHours(),
    to: new Date(last).getHours() + 1,
  };
}

// "all 8 on Oct 13, 7–10 AM" when every row of a group is on one day.
function concentration(members, timeOf) {
  const span = spanOf(members, timeOf);
  if (!span?.day) return "";
  const hours =
    span.to - span.from <= 4 ? `, ${hoursText(span.from, span.to)}` : "";
  return `all ${members.size} on ${dayText(span.day)}${hours}`;
}

/**
 * Patterns for the focused question and answer.
 *   {question, answer, answered, matches, rate, tested, counted, patterns}
 * `tested` is how many distinct groups were compared and `counted` how many
 * of them the correction counted (the rest are too small to ever stand out).
 * Each pattern: {key, dimension, text, where, when, ids, n, k, rate,
 * restRate, delta, p, q}, where `ids` are every row of the group (what
 * "Show n" selects) and `when` notes a group whose rows all fall on one day.
 */
export function findPatterns({
  records = [],
  questions = [],
  focus,
  unit = "sessions",
  max = MAX_PATTERNS,
}) {
  const { question, answer } = resolveFocus(focus, questions, records);
  const empty = {
    question,
    answer,
    answered: 0,
    matches: 0,
    rate: null,
    tested: 0,
    counted: 0,
    patterns: [],
  };
  if (!question) return empty;
  const rows = records
    .map((record) => ({ record, ...answerState(record, question.configId) }))
    .filter((row) => row.state === "answered");
  const total = rows.length;
  const hits = new Set(
    rows.filter((row) => row.answer === answer).map((row) => row.record.id),
  );
  const totalHits = hits.size;
  const base = {
    ...empty,
    answered: total,
    matches: totalHits,
    rate: total ? totalHits / total : null,
  };
  if (total < 2 * MIN_ROWS || totalHits === 0 || totalHits === total)
    return base;
  const timeOf = new Map(
    rows.map(({ record }) => [record.id, new Date(record.time).getTime()]),
  );

  let groups = buildGroups(rows, questions, question, unit)
    .map((group) => {
      const n = group.members.size;
      let k = 0;
      group.members.forEach((id) => {
        if (hits.has(id)) k += 1;
      });
      const restN = total - n;
      const restK = totalHits - k;
      return {
        ...group,
        n,
        k,
        restN,
        restK,
        rate: k / n,
        restRate: restN ? restK / restN : 0,
        delta: k / n - (restN ? restK / restN : 0),
      };
    })
    .filter((group) => group.n >= MIN_ROWS && group.n < total);

  // The same rows are the same comparison: keep the clearest description.
  const bySet = new Map();
  groups
    .slice()
    .sort(
      (a, b) =>
        PRIORITY.indexOf(a.dimension) - PRIORITY.indexOf(b.dimension) ||
        (a.start ?? 0) - (b.start ?? 0),
    )
    .forEach((group) => {
      const id = [...group.members].join("\u0000");
      if (!bySet.has(id)) bySet.set(id, group);
    });
  const unique = new Set(bySet.values());
  groups = groups.filter((group) => unique.has(group));

  // A partition split in two (error / no error) is one comparison: keep the
  // side where the answer is more common.
  const partitions = new Map();
  groups.forEach((group) => {
    if (!PARTITIONS.has(group.dimension)) return;
    const list = partitions.get(group.partition) || [];
    list.push(group);
    partitions.set(group.partition, list);
  });
  const complements = new Set();
  partitions.forEach((list) => {
    if (list.length === 2 && list[0].n + list[1].n === total)
      complements.add(list[0].delta >= list[1].delta ? list[1] : list[0]);
  });
  groups = groups.filter((group) => !complements.has(group));

  // A window reads as the hours its rows actually fall in.
  groups.forEach((group) => {
    if (group.dimension !== "window") return;
    const span = spanOf(group.members, timeOf);
    group.from = span.from;
    group.to = span.to;
    group.where = `on ${dayText(group.day)}, ${hoursText(span.from, span.to)}`;
  });

  const tests = groups.map((group) => ({
    ...group,
    p: fisherExact(
      group.k,
      group.n - group.k,
      group.restK,
      group.restN - group.restK,
    ),
    minP: minimumP(group.n, total, totalHits),
  }));
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
  let passing = tests.filter(
    (t) => t.q < Q_MAX && Math.abs(t.delta) >= MIN_DELTA && t.n >= MIN_ROWS,
  );

  // Within a partition, groups where the answer is more common imply the
  // ones where it is less common (1 turn and 4–6 turns imply 2–3 turns).
  const risers = new Set(
    passing
      .filter((t) => PARTITIONS.has(t.dimension) && t.delta > 0)
      .map((t) => t.partition),
  );
  passing = passing.filter(
    (t) =>
      !PARTITIONS.has(t.dimension) || t.delta > 0 || !risers.has(t.partition),
  );

  // Overlapping stretches of a day are one finding: keep the strongest, drop
  // any that overlap it, and drop the whole day when a stretch of it stands
  // out.
  const windows = passing
    .filter((t) => t.dimension === "window")
    .sort(
      (a, b) => a.q - b.q || Math.abs(b.delta) - Math.abs(a.delta) || b.n - a.n,
    );
  const keptWindows = [];
  windows.forEach((t) => {
    const overlaps = keptWindows.some(
      (kept) => kept.day === t.day && kept.from < t.to && t.from < kept.to,
    );
    if (!overlaps) keptWindows.push(t);
  });
  const windowDays = new Set(keptWindows.map((t) => t.day));
  passing = passing.filter((t) =>
    t.dimension === "window"
      ? keptWindows.includes(t)
      : t.dimension === "day"
        ? !windowDays.has(t.day)
        : true,
  );

  // Users that stand out the same way read as one group.
  const users = passing.filter((t) => t.dimension === "user");
  const merged = [];
  [true, false].forEach((higher) => {
    const side = users.filter((t) => t.delta > 0 === higher);
    if (side.length < 2) {
      merged.push(...side);
      return;
    }
    side.sort((a, b) => a.q - b.q);
    const members = new Set(side.flatMap((t) => [...t.members]));
    const n = members.size;
    let k = 0;
    members.forEach((id) => {
      if (hits.has(id)) k += 1;
    });
    const restN = total - n;
    const restK = totalHits - k;
    const names = side.map((t) => t.user);
    const listed =
      names.length <= 2
        ? names.join(" and ")
        : `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
    merged.push({
      key: `users:${higher}`,
      dimension: "users",
      where: `for ${names.length} users (${listed})`,
      members,
      n,
      k,
      restN,
      restK,
      rate: k / n,
      restRate: restN ? restK / restN : 0,
      delta: k / n - (restN ? restK / restN : 0),
      p: Math.min(...side.map((t) => t.p)),
      q: Math.min(...side.map((t) => t.q)),
    });
  });
  passing = [...passing.filter((t) => t.dimension !== "user"), ...merged];

  const patterns = passing
    .sort((a, b) => a.q - b.q || Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, max)
    .map((t) => {
      const when =
        t.dimension === "day" || t.dimension === "window"
          ? ""
          : concentration(t.members, timeOf);
      return {
        key: t.key,
        dimension: t.dimension,
        text: `“${answer}” ${t.where}: ${t.k} of ${t.n} (${formatShare(t.rate)}) vs ${formatShare(t.restRate)} elsewhere${when ? `, ${when}` : ""}`,
        where: t.where,
        when,
        ids: [...t.members],
        n: t.n,
        k: t.k,
        rate: t.rate,
        restRate: t.restRate,
        delta: t.delta,
        p: t.p,
        q: t.q,
      };
    });
  return { ...base, tested: tests.length, counted, patterns };
}
