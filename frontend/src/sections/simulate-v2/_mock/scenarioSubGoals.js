/**
 * Sub-goals on the scenario (PRD v6, Story 18), as the Scenarios tab, the
 * editor and the run results read them.
 *
 * 2–7 per scenario, each critical or supporting. Sub-goals decide pass or
 * fail (PRD v7, runs pinned `gradingRule: 2`): a conversation passes when
 * every critical sub-goal holds; an inconclusive critical one makes it
 * inconclusive, never a pass. A supporting sub-goal is reported — shown, and
 * it lowers the score — but doesn't fail it. A sub-goal someone adds is
 * critical unless they mark it supporting. Each points at a shared
 * definition in the catalogue (`subGoalsOfScenario` in envModel.js).
 */
import {
  subGoalsOfScenario,
  rollupOf,
  subGoalCatalogOf,
  isConnectOnly,
} from "./envModel";
import { hashSeed } from "./runStream";
import { joinedIdsOf, NOT_APPLICABLE } from "./subActors";

export const SUBGOAL_MIN = 2;
export const SUBGOAL_MAX = 7;

/** The quiet warning outside 2–7, or null inside it. */
export const subGoalCountNote = (n) => {
  if (n < SUBGOAL_MIN)
    return `Add ${SUBGOAL_MIN - n} more — a scenario needs at least ${SUBGOAL_MIN} sub-goals to be graded.`;
  if (n > SUBGOAL_MAX)
    return `${n - SUBGOAL_MAX} over — keep it to ${SUBGOAL_MAX}; split the rest into another scenario.`;
  return null;
};

/** Mark one sub-goal; the scenario can't be graded until it is re-proved. */
export const withSubGoalMark = (row, id, mark) => ({
  ...row,
  subGoalMarks: { ...(row.subGoalMarks || {}), [id]: mark },
  subGoalsUnproved: true,
});

/** How many scenarios use each catalogue entry, and how many mark it critical. */
export const subGoalUsageOf = (env, envState) => {
  const out = new Map();
  subGoalCatalogOf(env, envState).forEach((g) =>
    out.set(g.id, { usedBy: g.usedBy.length, critical: 0, supporting: 0 }),
  );
  (envState?.scenarios || []).forEach((row) => {
    if (row.retired || row.archived) return;
    subGoalsOfScenario(row, env, envState).forEach((x) => {
      const u = out.get(x.id);
      if (u) u[x.mark === "critical" ? "critical" : "supporting"] += 1;
    });
  });
  return out;
};

/*
  A generated scenario's sub-goals, derived from the scenario rather than the
  same three on every row: 2–7, seeded on its id, drawn from what this
  environment's tools and the focus actually ask for.
*/
export const generatedSubGoals = (seed, env, focus) => {
  const h = hashSeed(String(seed));
  const n = SUBGOAL_MIN + (h % (SUBGOAL_MAX - SUBGOAL_MIN + 1));
  const tools = (env?.tools || []).map((t) => t.name).filter(Boolean);
  const middle = [
    "Verify who is asking, before touching any account state",
    ...tools
      .slice(0, 3)
      .map(
        (_, i) =>
          `Call ${tools[(h + i) % tools.length]} with the arguments the task actually needs`,
      ),
    focus
      ? `Handle the ${focus.trim().toLowerCase()} part explicitly, not by falling through`
      : "Check the request against the policy before acting",
    "Take the requested action",
    "Offer a specific next step, not a vague promise",
  ];
  const picked = [];
  for (
    let i = 0;
    picked.length < Math.max(0, n - 2) && i < middle.length * 2;
    i += 1
  ) {
    const g = middle[(h + i) % middle.length];
    if (!picked.includes(g)) picked.push(g);
  }
  return [
    "Understand what the caller is asking for",
    ...picked,
    "Confirm the outcome with the caller",
  ].slice(0, n);
};

/* ── run results ─────────────────────────────────────────────────────────── */

/*
  Which critical sub-goal a failed run missed — the one the failure itself
  names, so a run's issues spread the way real failures do instead of piling
  onto whichever sub-goal happens to come first:
    · a rule scenario fails by breaking its rule
    · "said it was done, never called X" misses X's tool sub-goal
    · otherwise, one of the scenario's critical sub-goals, seeded per scenario
*/
const cruxOf = (task, crit, h) => {
  if (!crit.length) return null;
  const rule = crit.find((g) => g.id.startsWith("SG-rule-"));
  if (rule) return rule;
  const missed =
    JSON.stringify(task.steps || []).match(/never called ([a-z0-9_]+)/i)?.[1] ||
    String(task.reason || task.summary || task.failure || "").match(
      /never called ([a-z0-9_]+)/i,
    )?.[1];
  const tool = missed && crit.find((g) => g.id === `SG-tool-${missed}`);
  if (tool) return tool;
  return crit[h % crit.length];
};

/**
 * Each sub-goal's outcome on one run of a scenario, and the roll-up. The mock
 * has one crux per failed run: a critical sub-goal, placed where the run
 * failed. A flaky run leaves that crux inconclusive; some runs also miss a
 * supporting one, which costs score but not the verdict.
 */
export const subGoalResultsOf = (task, env, envState) => {
  if (!task || task.status === "unmeasured") return { items: [], rollup: null };
  const all = subGoalsOfScenario(task, env, envState);
  if (!all.length) return { items: [], rollup: null };
  /* A sub-goal about a sub-actor who didn't come in on this trial isn't
     applicable (TH-8651): it is listed, and left out of every count, rate
     and the roll-up. It is not Unmeasured — nothing went missing. */
  const joined = all.some((g) => g.subActorId)
    ? joinedIdsOf(task, env, envState)
    : null;
  const notApplicable = all
    .filter((g) => g.subActorId && !joined.has(g.subActorId))
    .map((g) => ({ ...g, status: NOT_APPLICABLE }));
  const goals = all.filter((g) => !(g.subActorId && !joined.has(g.subActorId)));
  if (!goals.length) return { items: [], rollup: null, notApplicable };
  const crit = goals.filter((g) => g.mark === "critical");
  const sup = goals.filter((g) => g.mark !== "critical");
  const h = hashSeed(String(task.id || ""));
  /* A required evaluation can fail a conversation whose critical sub-goals
     all held (sub-goal rule) — then no sub-goal is marked missed. */
  const crux =
    task.status === "passed" ||
    task.failedBy === "required-evaluation" ||
    task.duplicateWrite
      ? null
      : cruxOf(task, crit, h);
  const missedSupport = sup.length && h % 4 === 0 ? sup[h % sup.length] : null;
  const items = goals.map((g) => ({
    ...g,
    status:
      crux && g.key === crux.key
        ? task.status === "flaky"
          ? "inconclusive"
          : "failed"
        : missedSupport && g.key === missedSupport.key
          ? "failed"
          : "met",
  }));
  const rollup = rollupOf(items);
  /* What failed it when the sub-goals didn't (sub-goal rule): a required
     evaluation, or the turn limit. The line says so instead of reading as a pass. */
  if (task.failedBy === "required-evaluation" && task.status !== "passed") {
    return {
      items,
      notApplicable,
      rollup: {
        ...rollup,
        verdict: "failed",
        requiredFail: task.requiredFail?.name || "a required evaluation",
      },
    };
  }
  /* world.md WO-1: a money write committed twice fails a trial whose sub-goals held. */
  if (task.duplicateWrite && task.status !== "passed") {
    return {
      items,
      notApplicable,
      rollup: {
        ...rollup,
        verdict: "failed",
        duplicateWrite: task.duplicateWrite,
      },
    };
  }
  if (task.turnLimit && task.status !== "passed") {
    return {
      items,
      notApplicable,
      rollup: { ...rollup, verdict: "failed", turnLimit: task.turnLimit.limit },
    };
  }
  return { items, notApplicable, rollup };
};

/* What a check rests on: the end state and tool calls, or the conversation. */
const evidenceOfGoal = (g, env) =>
  isConnectOnly(env) || !CHECKABLE.test(String(g?.text || ""))
    ? "the conversation"
    : "end state and tool calls";

/**
 * Each check with the evidence it rests on (scenarios.md "Check evidence").
 * A trial that couldn't be measured still lists its checks — each one
 * Unmeasured, naming what's missing (SC-3), never dropped or counted.
 */
export const checkEvidenceOf = (task, env, envState) => {
  if (!task) return { items: [], rollup: null };
  if (task.status !== "unmeasured") {
    const r = subGoalResultsOf(task, env, envState);
    return {
      ...r,
      items: r.items.map((g) => ({ ...g, evidence: evidenceOfGoal(g, env) })),
    };
  }
  const goals = subGoalsOfScenario(task, env, envState);
  const f = task.fault || {};
  const missing = f.transport
    ? "the rest of the conversation — the connection dropped"
    : f.grading
      ? "a verdict — the judge didn't return one"
      : f.simulator
        ? "a valid test — the simulated caller left its goal"
        : "the end-state snapshot — it wasn't saved";
  return {
    items: goals.map((g) => ({
      ...g,
      status: "unmeasured",
      evidence: evidenceOfGoal(g, env),
      missing,
    })),
    rollup: goals.length ? { verdict: "unmeasured", missing } : null,
  };
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** "Failed — 1 critical sub-goal failed" and the like. */
export const rollupLine = (r) => {
  if (!r) return "";
  if (r.requiredFail)
    return `Failed — required evaluation ${r.requiredFail} failed; every critical sub-goal held`;
  if (r.duplicateWrite)
    return `Failed — money written twice: ${r.duplicateWrite.summary} went through ${r.duplicateWrite.times} times; every critical sub-goal held`;
  if (r.turnLimit)
    return `Failed — hit the turn limit (${r.turnLimit})${r.criticalFailed ? ` · ${plural(r.criticalFailed, "critical sub-goal")} not reached` : ""}`;
  if (r.verdict === "failed")
    return `Failed — ${plural(r.criticalFailed, "critical sub-goal")} failed`;
  if (r.verdict === "unmeasured")
    return `Unmeasured — missing ${r.missing}; counts neither for nor against the agent`;
  if (r.verdict === "inconclusive")
    return `Unmeasured — ${plural(r.criticalUnsure, "critical sub-goal")} unsure`;
  return r.supportingFailed
    ? `Passed — ${plural(r.supportingFailed, "supporting sub-goal")} missed (reported, doesn't fail) · score ${r.score}`
    : "Passed — every critical sub-goal held";
};

/** What a mark means, in the words every screen uses. */
export const MARK_NOTE = {
  critical: "Critical — the conversation fails if it doesn't hold",
  supporting: "Supporting — reported, doesn't fail the conversation",
};

/*
  What one sub-goal was checked against. A sealed run checks the world's end
  state and the agent's tool calls for the sub-goals that can be checked that
  way (a tool call, a verification, a rule held); a sub-goal about what was
  said is judged from the conversation. A live or prompt-only run — and a
  connect-only environment, which has no world — judges every one from the
  conversation. `basis` is `gradingOf(run).basis` (runRollup.js).
*/
const CHECKABLE = /^Call [a-z0-9_]+|^Holds the line|verify|confirm|check/i;

export const subGoalBasisOf = (goal, basis, env) => {
  if (!basis) return null;
  const text =
    typeof goal === "string" ? goal : goal?.text || goal?.label || "";
  const judged = basis.judged || isConnectOnly(env) || !CHECKABLE.test(text);
  return judged
    ? {
        judged: true,
        icon: "solar:chat-round-line-linear",
        label: "Judged from the conversation",
      }
    : {
        judged: false,
        icon: "solar:database-linear",
        label: "Checked against end state and tool calls",
      };
};
