/*
  Instant eval on a run (evals.md, Run → Evaluate selected evidence): ask a
  plain question of each saved trial — Yes / No, or one of a few categories —
  without running the agent again. Answers are saved beside the run, pinned
  to the evidence they read; the run's own task checks are never rewritten.

  The same flow as Observe's Instant eval (ask → answers per item → counts,
  a square per item, the answers table); here an item is a trial, not a trace.

  Saved on the environment: envState.trialEvals[runId] = [ask, …], newest first.
*/
import { scenarioTitle } from "./scenarios";
import {
  FIELDS_OF_READS,
  GRADING_ERROR,
  GRADING_ERROR_REASON,
  evidenceOf,
  gradingErrorKeys,
  missingOf,
  newResultId,
  skippedMapOf,
} from "./evalEvidence";

export const ANSWER_TYPES = { YES_NO: "yes_no", CATEGORY: "category" };
export const MAX_QUESTIONS = 5;
export const MAX_CATEGORIES = 8;

export const READS = [
  {
    value: "conversation",
    label: "Conversation",
    about: "What the caller and the agent said",
  },
  {
    value: "conversation_tools",
    label: "Conversation and tool calls",
    about: "Adds each tool call and what it returned",
  },
  {
    value: "tools",
    label: "Tool calls only",
    about: "What the agent did in the world",
  },
];
export const readsLabelOf = (v) => READS.find((r) => r.value === v)?.label || v;

export const EXAMPLES = [
  {
    text: "Did the agent explain its decision clearly?",
    type: ANSWER_TYPES.YES_NO,
  },
  {
    text: "Did the agent check the record before acting on it?",
    type: ANSWER_TYPES.YES_NO,
  },
  {
    text: "Did the caller have to repeat themselves?",
    type: ANSWER_TYPES.YES_NO,
  },
  {
    text: "How did the conversation end?",
    type: ANSWER_TYPES.CATEGORY,
    labels: ["Resolved", "Handed to a person", "Caller gave up", "Other"],
  },
];

/* The task checks' outcome, in the four buckets this screen counts by. */
export const outcomeOf = (t) =>
  t.status === "passed"
    ? "passed"
    : t.status === "error"
      ? "error"
      : t.status === "unmeasured"
        ? "unmeasured"
        : "failed";
export const OUTCOME_LABEL = {
  passed: "Passed",
  failed: "Failed",
  unmeasured: "Not counted",
  error: "Error",
};

export const SCOPES = [
  { value: "all", label: "All trials" },
  { value: "failed", label: "Failed trials" },
  { value: "passed", label: "Passed trials" },
];
export const scopeLabelOf = (v) =>
  SCOPES.find((s) => s.value === v)?.label || v;
/* A saved ask's scope, as it was named when it ran. */
export const askScopeLabelOf = (ask) =>
  ask.scopeLabel || scopeLabelOf(ask.scope);
export const scopeTasksOf = (scope, tasks) =>
  scope === "all" ? tasks : tasks.filter((t) => outcomeOf(t) === scope);

export const labelsOf = (q) =>
  q.type === ANSWER_TYPES.YES_NO ? ["Yes", "No"] : q.labels;

/* Platform judge rate for one short answer over saved evidence. */
const CREDITS_PER_ANSWER = 0.0035;
export const estimateOf = (trials, questions) =>
  trials * questions * CREDITS_PER_ANSWER;
export const aboutCredits = (c) =>
  c < 0.01
    ? "under 0.01 credits"
    : `about ${c < 1 ? c.toFixed(2) : c.toFixed(1)} credits`;

const hash = (s) =>
  [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);

const YES_WHY = [
  "said what it would do and why before doing it",
  "named the policy it was following",
  "confirmed the details back to the caller",
  "checked the record first, then answered",
];
const NO_WHY = [
  "acted without saying why",
  "skipped the check and answered from memory",
  "gave a reason that doesn't match the tool result",
  "left the caller to work out what happened",
];

const CATEGORY_WHY = [
  [/^(other|something else)$/i, "it ended without a clear outcome"],
  [
    /resolv|solved|done/i,
    "the agent confirmed the outcome and the caller agreed",
  ],
  [/person|human|handed|escalat/i, "the agent offered a transfer to a person"],
  [
    /gave up|abandon|left|hung/i,
    "the caller stopped replying before it was settled",
  ],
  [/refund|money/i, "the caller asked for their money back"],
];
const categoryWhy = (label) =>
  CATEGORY_WHY.find(([re]) => re.test(label))?.[1] ||
  `closest to “${label.toLowerCase()}”`;

/* One trial's answer — steady for the same question, trial and ask. */
export function answerOf(q, t, salt) {
  const h = hash(`${q.text}|${t.id}|${salt}`);
  const turn = 2 + (h % 7);
  const passed = outcomeOf(t) === "passed";
  if (q.type === ANSWER_TYPES.YES_NO) {
    const yes = h % 100 < (passed ? 78 : 34);
    const why = (yes ? YES_WHY : NO_WHY)[h % 4];
    return {
      answer: yes ? "Yes" : "No",
      reason: `Turn ${turn}: the agent ${why}.`,
    };
  }
  const labels = q.labels;
  /* Passing trials lean to the first category, failing ones spread out. */
  const r = h % 100;
  const i = passed
    ? r < 62
      ? 0
      : 1 + (r % (labels.length - 1))
    : r % labels.length;
  return {
    answer: labels[i],
    reason: `Turn ${turn}: ${categoryWhy(labels[i])}.`,
  };
}

/*
  Runs one ask over the chosen trials: answers, what couldn't be read, and
  the trials the judge didn't answer — each kept apart. `trials` is the
  exact selection the person reviewed (see scopeTrialsOf).
*/
export function runAsk({ questions, reads, scope, scopeLabel, trials, ctx }) {
  const id = newResultId();
  const fields = FIELDS_OF_READS[reads] || ["transcript"];
  const missing = missingOf(trials, fields, ctx);
  const lacking = new Set(missing.map((m) => m.key));
  const readable = trials.filter((t) => !lacking.has(t.id));
  const qs = questions.map((q, i) => ({
    id: `q${i + 1}`,
    text: q.text.trim(),
    type: q.type,
    labels: labelsOf(q),
  }));
  const answers = {};
  qs.forEach((q) => {
    const errors = gradingErrorKeys(
      readable.map((t) => t.id),
      `${id}|${q.id}`,
    );
    answers[q.id] = {};
    readable.forEach((t) => {
      answers[q.id][t.id] = errors.has(t.id)
        ? { error: true, answer: null, reason: GRADING_ERROR_REASON }
        : answerOf(q, t, id);
    });
  });
  return {
    id,
    at: new Date().toISOString(),
    by: "You",
    /* Instant eval is the evaluator; the question is its criterion. */
    evaluator: { name: "Instant eval", version: "v1" },
    reads,
    scope,
    scopeLabel,
    taskIds: trials.map((t) => t.id),
    evidence: evidenceOf({ fields, trials, thisRunId: ctx.runId }),
    questions: qs,
    answers,
    skipped: skippedMapOf(missing),
    credits: estimateOf(readable.length, qs.length),
    durationS: Math.max(6, Math.round(readable.length * 0.6)),
  };
}

/* Counts for one question, in the question's label order. */
export const countsOf = (ask, q) => {
  const by = Object.values(ask.answers[q.id] || {}).reduce((m, a) => {
    if (a.error) return m;
    m[a.answer] = (m[a.answer] || 0) + 1;
    return m;
  }, {});
  return q.labels.map((label) => ({ label, n: by[label] || 0 }));
};

/*
  Answer colours come from the design system's eight data colours, so an
  answer never reads as a status. Yes and No judge neither way: purple and
  orange, never green and red ("No" isn't always bad). A catch-all category
  ("Other") is the neutral last colour.
*/
const CATCH_ALL = /^(other|others|something else|none|unknown|not sure)$/i;
const CATEGORY_ORDER = [1, 4, 6, 2, 3];
export function answerColorOf(q, label, theme) {
  const data = theme.palette.data || [];
  if (q.type === ANSWER_TYPES.YES_NO)
    return label === "Yes" ? data[0] : data[5];
  if (CATCH_ALL.test(String(label).trim())) return data[7];
  const i = q.labels.indexOf(label);
  return data[CATEGORY_ORDER[i]] ?? data[7];
}

/* Trials grouped by scenario, in the run's own order. */
export const byScenario = (tasks) => {
  const groups = new Map();
  tasks.forEach((t) => {
    const name = scenarioTitle(t.title || t.name) || "Scenario";
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(t);
  });
  return [...groups.entries()].map(([name, rows]) => ({ name, rows }));
};

const csvCell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
export function csvOf(ask, tasks) {
  const head = [
    "run",
    "trial",
    "scenario",
    "persona",
    "task checks",
    ...ask.questions.flatMap((q) => [q.text, `${q.text} — why`]),
  ];
  const rows = tasks.map((t) => [
    t.runLabel || "",
    t.trialNo ?? t.id,
    scenarioTitle(t.title || t.name),
    t.persona?.name || "",
    OUTCOME_LABEL[outcomeOf(t)],
    ...ask.questions.flatMap((q) => {
      const a = ask.answers[q.id]?.[t.id];
      if (a?.error) return [GRADING_ERROR, a.reason];
      return a ? [a.answer, a.reason] : ["Not read", ask.skipped[t.id] || ""];
    }),
  ]);
  return [head, ...rows].map((r) => r.map(csvCell).join(",")).join("\n");
}

export const whenOf = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
