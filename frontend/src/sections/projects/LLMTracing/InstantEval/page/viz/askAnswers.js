import {
  ERROR_ANSWER,
  LOW_CONFIDENCE,
  SKIPPED_ANSWER,
} from "../../instantEvalAnswers";
import { ANSWER_TYPES } from "../../instantEvalPayloads";

// What one row's answer to one question is right now, for the charts:
//   "waiting"   no answer yet (the key is missing)
//   "reading"   Jev is reading it (status pending or running)
//   "failed"    Jev could not answer
//   "skipped"   nothing to read
//   "answered"  a label, with `unsure` when confidence < 0.6
const IN_FLIGHT = new Set(["pending", "running", "queued"]);
const FAILED = new Set(["error", "errored", "failed"]);

export function answerState(record, configId) {
  const entry = record?.answers?.[configId];
  if (!entry) return { state: "waiting", answer: null, unsure: false };
  const answer =
    typeof entry.answer === "string" && entry.answer ? entry.answer : null;
  const status = typeof entry.status === "string" ? entry.status : "";
  if (answer === ERROR_ANSWER || FAILED.has(status))
    return { state: "failed", answer: ERROR_ANSWER, unsure: false };
  if (answer === SKIPPED_ANSWER)
    return { state: "skipped", answer: SKIPPED_ANSWER, unsure: false };
  if (!answer || IN_FLIGHT.has(status))
    return { state: "reading", answer: null, unsure: false };
  const confidence =
    typeof entry.confidence === "number" ? entry.confidence : null;
  return {
    state: "answered",
    answer,
    unsure: confidence !== null && confidence < LOW_CONFIDENCE,
    confidence,
  };
}

/** The labels a question can answer with, in display order. */
export const questionLabels = (question) =>
  Array.isArray(question?.labels) && question.labels.length
    ? question.labels
    : ["Yes", "No"];

export const isYesNo = (question) =>
  question?.answerType === ANSWER_TYPES.YES_NO ||
  (questionLabels(question).length === 2 &&
    questionLabels(question).includes("Yes") &&
    questionLabels(question).includes("No"));

/**
 * The answer to look at when none is focused: "No" for a yes/no question,
 * otherwise the least common label among the answers so far (ties go to the
 * later label), or the last label before any answer.
 */
export function defaultAnswer(question, records = []) {
  const labels = questionLabels(question);
  if (isYesNo(question)) return "No";
  const counts = new Map(labels.map((label) => [label, 0]));
  records.forEach((record) => {
    const { state, answer } = answerState(record, question?.configId);
    if (state === "answered" && counts.has(answer))
      counts.set(answer, counts.get(answer) + 1);
  });
  const answered = [...counts.values()].some((count) => count > 0);
  if (!answered) return labels[labels.length - 1];
  let best = null;
  counts.forEach((count, label) => {
    if (count === 0) return;
    if (best === null || count <= counts.get(best)) best = label;
  });
  return best;
}

/** The question being coloured, and the answer in focus (or the default). */
export function resolveFocus(focus, questions, records) {
  const question =
    questions.find((q) => q.configId === focus?.configId) || questions[0];
  if (!question) return { question: null, answer: null, explicit: false };
  const labels = questionLabels(question);
  const explicit = Boolean(focus?.answer) && labels.includes(focus.answer);
  return {
    question,
    answer: explicit ? focus.answer : defaultAnswer(question, records),
    explicit,
  };
}

/**
 * Stacking order inside a column, bottom first: each label in order, then
 * failures and skips, then rows being read, then rows not reached yet.
 */
export function stackRank(state, answer, labels) {
  if (state === "answered") {
    const index = labels.indexOf(answer);
    return index >= 0 ? index : labels.length;
  }
  if (state === "failed") return labels.length + 1;
  if (state === "skipped") return labels.length + 2;
  if (state === "reading") return labels.length + 3;
  return labels.length + 4;
}

/** Counts per answer for the legend: {labels: [{answer, count}], failed, skipped, reading, waiting, unsure}. */
export function answerCounts(records, question) {
  const labels = questionLabels(question);
  const counts = {
    labels: labels.map((answer) => ({ answer, count: 0, unsure: 0 })),
    failed: 0,
    skipped: 0,
    reading: 0,
    waiting: 0,
  };
  const byLabel = new Map(counts.labels.map((entry) => [entry.answer, entry]));
  records.forEach((record) => {
    const { state, answer, unsure } = answerState(record, question?.configId);
    if (state === "answered") {
      const entry = byLabel.get(answer);
      if (entry) {
        entry.count += 1;
        if (unsure) entry.unsure += 1;
      }
    } else counts[state] += 1;
  });
  return counts;
}

const NUMBER = new Intl.NumberFormat("en-US");
export const formatCount = (value) =>
  Number.isFinite(value) ? NUMBER.format(value) : "–";

export function formatShare(value) {
  if (!Number.isFinite(value)) return "–";
  const pct = value * 100;
  if (pct > 0 && pct < 0.5) return "<1%";
  if (pct < 100 && pct > 99.5) return ">99%";
  return `${Math.round(pct)}%`;
}
