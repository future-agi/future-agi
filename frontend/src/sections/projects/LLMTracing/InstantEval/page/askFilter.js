import { answerText, summarizeCounts } from "../instantEvalAnswers";
import { ANSWER_TYPES } from "../instantEvalPayloads";

// What the charts select, as the filter the rows and export endpoints read:
//   {time_from, time_to}                   a time range (ISO or epoch ms)
//   {config_id, answer}                    one answer to one question
//   {answers: [{config_id, answer}, ...]}  several answers, all of them
//   {group: "dim:key" | ["dim:key", ...]}  any of these groups
//   {unsure: true}                         answers Jev was unsure of
// Every part narrows the rows; an empty filter is every row.

const toIso = (value) => {
  if (value === null || value === undefined || value === "") return undefined;
  const date = typeof value === "number" ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
};

export function isEmptyFilter(filter) {
  if (!filter) return true;
  return !Object.keys(filterParams(filter)).length;
}

/** The query parameters of a filter, for the rows and export endpoints. */
export function filterParams(filter = {}) {
  if (!filter) return {};
  const params = {};
  const from = toIso(filter.time_from);
  const to = toIso(filter.time_to);
  if (from) params.time_from = from;
  if (to) params.time_to = to;
  if (filter.config_id && filter.answer) {
    params.config_id = filter.config_id;
    params.answer = filter.answer;
  }
  const answers = (filter.answers || []).filter(
    (item) => item?.config_id && item?.answer,
  );
  if (answers.length) params.answers = JSON.stringify(answers);
  const groups = (
    Array.isArray(filter.group) ? filter.group : [filter.group]
  ).filter(Boolean);
  // Each group its own `group` parameter: one joined value outgrew the
  // server's per-item cap over long ranges, and split keys holding a comma.
  if (groups.length) params.group = groups;
  if (filter.unsure) {
    params.unsure = true;
    if (filter.config_id && !params.config_id)
      params.config_id = filter.config_id;
  }
  if (filter.user) params.user = filter.user;
  if (filter.model) params.model = filter.model;
  if (typeof filter.error === "boolean") params.error = filter.error;
  if (filter.q) params.q = filter.q;
  return params;
}

/** Successful question answers, not settled rows or fully answered rows. */
export function savedAnswerCount(counts = {}) {
  return Object.values(counts).reduce(
    (total, answers) =>
      total +
      Object.entries(answers || {})
        .filter(([label]) => !label.startsWith("__"))
        .reduce((sum, [, count]) => sum + count, 0),
    0,
  );
}

/** The filter of a focused answer on the legend. */
export const answerFilter = (configId, answer) =>
  configId && answer ? { config_id: configId, answer } : null;

/** Two filters at once: every part of both. */
export function mergeFilters(a, b) {
  if (!a) return b || null;
  if (!b) return a;
  const answers = [
    ...(a.answers || []),
    ...(a.config_id && a.answer
      ? [{ config_id: a.config_id, answer: a.answer }]
      : []),
    ...(b.answers || []),
    ...(b.config_id && b.answer
      ? [{ config_id: b.config_id, answer: b.answer }]
      : []),
  ];
  const merged = { ...a, ...b };
  delete merged.config_id;
  delete merged.answer;
  if (answers.length) merged.answers = answers;
  return merged;
}

const timeWords = (from, to) => {
  const start = from ? new Date(from) : null;
  const end = to ? new Date(to) : null;
  const day = (date) =>
    date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const hour = (date) =>
    date
      .toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
      .replace(":00", "");
  if (start && end) {
    if (day(start) === day(new Date(end.getTime() - 1)))
      return `on ${day(start)}, ${hour(start)}–${hour(end)}`;
    return `from ${day(start)} to ${day(new Date(end.getTime() - 1))}`;
  }
  if (start) return `since ${day(start)}`;
  if (end) return `before ${day(end)}`;
  return "";
};

/** "“No” on Oct 13, 7–10 AM", for a filter the charts didn't label. */
export function describeFilter(filter, questions = []) {
  if (!filter) return "";
  const answers = [
    ...(filter.config_id && filter.answer
      ? [{ config_id: filter.config_id, answer: filter.answer }]
      : []),
    ...(filter.answers || []),
  ];
  const parts = answers.map((item) => {
    const index = questions.findIndex((q) => q.configId === item.config_id);
    const suffix = questions.length > 1 && index >= 0 ? ` (Q${index + 1})` : "";
    return `“${answerText(item.answer)}”${suffix}`;
  });
  if (filter.unsure) parts.push("unsure answers");
  const time = timeWords(filter.time_from, filter.time_to);
  if (time) parts.push(time);
  const groups = (
    Array.isArray(filter.group) ? filter.group : [filter.group]
  ).filter(Boolean);
  if (groups.length)
    parts.push(
      groups.length === 1
        ? groups[0].replace(":", " ")
        : `${groups.length} groups`,
    );
  if (filter.q) parts.push(`matching “${filter.q}”`);
  return parts.join(" ") || "Selected rows";
}

const Z = 1.96;

/**
 * A share read from a sample, with its 95% Wilson interval. With the whole
 * population size `N`, a finite-population correction narrows the interval
 * as the sample covers more of it (and closes it when it covers all). The
 * correction scales the sample size (n·(N−1)/(N−n)), not the half-width: a
 * shrunk half-width around Wilson's shifted centre can leave the share
 * outside its own interval (10 of 10 read "≈ 100% (95% CI 73–99%)").
 */
export function wilson(x, n, N) {
  if (!n) return null;
  const p = x / n;
  if (N && n >= N) return { p, lo: p, hi: p };
  const m = N && N > 1 ? (n * (N - 1)) / (N - n) : n;
  const z2 = Z * Z;
  const denom = 1 + z2 / m;
  const center = (p + z2 / (2 * m)) / denom;
  const half = (Z * Math.sqrt((p * (1 - p)) / m + z2 / (4 * m * m))) / denom;
  // The interval holds p; the clamp only keeps rounding (0.9999…) from
  // leaving it a hair outside.
  return {
    p,
    lo: Math.max(0, Math.min(p, center - half)),
    hi: Math.min(1, Math.max(p, center + half)),
  };
}

const pct = (value) => `${Math.round(value * 100)}%`;

/** "≈ 38% No (95% CI 35–41%)". */
export function estimateWords(label, interval) {
  if (!interval) return "";
  if (interval.lo === interval.hi) return `${pct(interval.p)} ${label}`;
  return `≈ ${pct(interval.p)} ${label} (95% CI ${Math.round(interval.lo * 100)}–${pct(interval.hi)})`;
}

/** "152 / min". */
export const rateWords = (rate) =>
  rate ? `${rate >= 10 ? Math.round(rate) : rate.toFixed(1)} / min` : "";

/** "about 4 min left". */
export const etaWords = (seconds) =>
  seconds ? `${durationWords(seconds)} left` : "";

/** Credits as people read them: whole from 10 up, else to a tenth ("0.7"). */
export const creditAmount = (credits) => {
  const value = Math.max(0, credits || 0);
  return value >= 10
    ? Math.round(value).toLocaleString()
    : String(Math.round(value * 10) / 10);
};

/** "120 credits", "0.7 credits", "1 credit". */
export const creditCount = (credits) => {
  const amount = creditAmount(credits);
  return `${amount} credit${amount === "1" ? "" : "s"}`;
};

/** "about 120 credits". */
export const creditWords = (credits) => `about ${creditCount(credits)}`;

/** "about 7 min", "about 3 h 20 min". */
export function durationWords(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  if (s < 60) return `about ${s} s`;
  if (s < 3600) return `about ${Math.round(s / 60)} min`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return `about ${h} h${m ? ` ${m} min` : ""}`;
}

/** The share a sample reports for one answer, with its 95% interval. */
export function sampleEstimate(run, question, focusAnswer) {
  const sample = run.sample;
  if (!sample?.is_sample || !question) return null;
  const counts = run.summary?.counts?.[question.configId] || {};
  const answered = Object.entries(counts).filter(
    ([label]) => !label.startsWith("__"),
  );
  const n = answered.reduce((sum, [, count]) => sum + count, 0);
  if (!n) return null;
  const share = (label) => wilson(counts[label] || 0, n, sample.population);
  const focused =
    focusAnswer && !focusAnswer.startsWith("__") ? focusAnswer : null;
  const yesNo = question.answerType === ANSWER_TYPES.YES_NO;
  if (focused || yesNo) {
    const label = focused || ("No" in counts ? "No" : null);
    if (!label) return null;
    const words = estimateWords(answerText(label), share(label));
    return { label, labels: [label], words, parts: [words] };
  }
  // A category question: its three most common answers.
  const top = answered
    .sort((a, b) => b[1] - a[1])
    .slice(0, CATEGORY_ESTIMATES)
    .map(([label]) => label);
  if (!top.length) return null;
  const parts = top.map((label, index) =>
    shareWords(answerText(label), share(label), index === 0),
  );
  return { label: top[0], labels: top, words: parts.join(" · "), parts };
}

const CATEGORY_ESTIMATES = 3;

// Past this share of a sample left unanswered, the estimate says how far the
// unanswered rows could move it.
export const UNANSWERED_BOUND_SHARE = 0.02;

/**
 * What the rows Jev couldn't answer could do to an estimate: for each label,
 * its share if none of them had that answer (the low end) or all of them did
 * (the high end), each with its 95% interval. Failures aren't random (a big
 * session is likelier to fail), so the interval over answered rows alone
 * overstates how sure the estimate is.
 */
export function unansweredBounds(run, question, labels) {
  const counts = run.summary?.counts?.[question?.configId] || {};
  const answered = Object.entries(counts)
    .filter(([label]) => !label.startsWith("__"))
    .reduce((sum, [, count]) => sum + count, 0);
  const failed = (counts.__error__ || 0) + (counts.__skipped__ || 0);
  const n = answered + failed;
  if (!failed || !n) return null;
  const population = run.sample?.population;
  return {
    failed,
    n,
    share: failed / n,
    bounds: (labels || []).map((label) => {
      const k = counts[label] || 0;
      return {
        label,
        lo: wilson(k, n, population).lo,
        hi: wilson(k + failed, n, population).hi,
      };
    }),
  };
}

const range = ({ lo, hi }) => `${Math.round(lo * 100)}–${pct(hi)}`;

/** "107 of 1,000 couldn't be answered. If they were all Yes or all No, No would be 30–46%." */
export function boundWords(bounds, question) {
  if (!bounds?.bounds.length) return "";
  const head = `${bounds.failed.toLocaleString()} of ${bounds.n.toLocaleString()} couldn't be answered.`;
  if (question?.answerType === ANSWER_TYPES.YES_NO) {
    const [only] = bounds.bounds;
    return `${head} If they were all Yes or all No, ${answerText(only.label)} would be ${range(only)}.`;
  }
  const parts = bounds.bounds.map(
    (bound) => `${answerText(bound.label)} ${range(bound)}`,
  );
  return `${head} Counting them as all or none of each answer: ${parts.join(" · ")}.`;
}

/** "≈ 31% Order status (31–32%)", or "26% Return or refund (25–27%)". */
function shareWords(label, interval, first) {
  const lead = first ? "≈ " : "";
  if (interval.lo === interval.hi) return `${pct(interval.p)} ${label}`;
  return `${lead}${pct(interval.p)} ${label} (${Math.round(interval.lo * 100)}–${pct(interval.hi)})`;
}

/**
 * A question's split as the run's page shows it: each answer's share of the
 * rows Jev answered, and the rows it couldn't answer apart.
 */
export function answeredSplit(item, counts) {
  const summary = summarizeCounts(counts, item);
  const labels = summary.labels.filter((entry) => entry.count > 0);
  const answered = labels.reduce((sum, entry) => sum + entry.count, 0);
  const failed = summary.errors.count + summary.skipped.count;
  return {
    labels: labels.map((entry) => ({
      ...entry,
      share: answered ? entry.count / answered : 0,
    })),
    answered,
    failed,
  };
}
