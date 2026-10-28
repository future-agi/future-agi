/*
  Mid-run clarifying questions (PRD v6 · AC-15.13).

  Sometimes the harness can't go on without a fact nobody wrote down — which
  time zone a booking tool should assume, which currency a refund is in. On a
  real long run the scenario waits for an answer rather than guessing (a guess
  would grade the agent on our assumption), the user is notified, and the
  scenario resumes once answered. Everything else keeps running.

  Here: at most once per run, only on runs long enough to leave unattended,
  and chosen from the run's own seed so the same run always asks the same
  question at the same turn. Answering changes nothing about the verdict a
  scenario was always going to reach — finished runs read back unchanged.
*/
import { hashSeed } from "./runStream";

/* Runs shorter than this finish before anyone would have walked away. */
export const CLARIFY_MIN_TASKS = 6;

const TEMPLATES = [
  {
    match: /avail|book|slot|calendar|schedul|reserv|appoint|resched/i,
    ask: (tool) => `Which time zone should ${tool} assume?`,
    why: "The scenario gives times (\"tomorrow at 7\") but no time zone, and the world's clock and the caller's disagree.",
    options: ["The caller's local time", "The business's time zone", "UTC"],
  },
  {
    match: /refund|payment|charge|invoice|compensat|credit/i,
    ask: (tool) =>
      `Which currency should ${tool} use when the order doesn't say?`,
    why: "The order record has an amount but no currency, so the refund can't be checked against the limit.",
    options: [
      "The order's original currency",
      "USD",
      "The caller's billing currency",
    ],
  },
  {
    match: /order|lookup|account|customer|search|find|get_/i,
    ask: (tool) =>
      `Two records match the caller. Which one should ${tool} return?`,
    why: "The seed has two customers with the same name and postcode, and the scenario doesn't say which one is calling.",
    options: [
      "The most recent order",
      "The account with an open ticket",
      "Ask the caller to confirm",
    ],
  },
];
const FALLBACK = {
  ask: () =>
    'The scenario says "next week" but the world has no date. Which day should the simulated caller treat as today?',
  why: "Relative dates can't be graded until the world has a fixed today.",
  options: ["The run's start date", "Monday of next week"],
};

/**
 * The one question a run asks, if it asks one.
 * { taskId, step, question, why, options, tool } | null
 */
export const clarifyPlanOf = ({ seed, tasks = [], concurrency = 4 }) => {
  if (tasks.length < CLARIFY_MIN_TASKS) return null;
  /* From the first wave, so it lands while someone is still watching — but
     never the scenario the stage opens on. */
  const askable = (t) =>
    t.verdict !== "unmeasured" && (t.steps || []).length >= 4;
  const first = tasks.slice(1, Math.max(2, concurrency + 1)).filter(askable);
  /* Else the next scenario that can ask — still early in the run. */
  const pool = first.length
    ? first
    : tasks.slice(1).filter(askable).slice(0, 1);
  if (!pool.length) return null;
  const h = hashSeed(`clarify:${seed}`);
  const task = pool[h % pool.length];
  const tools = (task.callLog?.calls || []).map((c) => c.name).filter(Boolean);
  const hit = tools
    .map((tool) => [tool, TEMPLATES.find((tp) => tp.match.test(tool))])
    .find(([, tp]) => tp);
  const tpl = hit ? hit[1] : FALLBACK;
  const tool = hit ? hit[0] : null;
  /* Mid-call, and never past the turn a failing scenario stops at. */
  const mid = Math.max(1, Math.floor(task.steps.length / 2));
  const step =
    task.failStep != null ? Math.min(mid, Math.max(0, task.failStep)) : mid;
  return {
    taskId: task.id,
    step,
    tool,
    question: tpl.ask(tool),
    why: tpl.why,
    options: tpl.options,
  };
};

/*
  A fix's first run never waits on a person: the scenario that would have
  asked is left unscored instead — never graded on our guess. Drawn the same
  way live and when the run is read back (a fixed wave size, not the run's
  parallelism), so both mark the same scenario.
*/
export const SKIPPED_ASK =
  "Not enough evidence — it needed an answer nobody wrote down, and a fix's first run skips the question rather than grade a guess.";

export const withAskSkipped = (tasks = [], seed) => {
  const plan = clarifyPlanOf({ seed, tasks, concurrency: 4 });
  if (!plan) return tasks;
  return tasks.map((t) =>
    t.id === plan.taskId
      ? {
          ...t,
          verdict: "unmeasured",
          failStep: null,
          fault: { ...(t.fault || {}), grading: SKIPPED_ASK },
          askSkipped: true,
        }
      : t,
  );
};
