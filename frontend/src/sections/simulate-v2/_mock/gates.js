/**
 * The gates, and what they threw away.
 *
 * Generation is a filter, not a list. Scenarios are drafted, each one is put
 * through three gates that are code rather than a model, and the drafts that
 * fail are discarded — not kept with a warning on them.
 *
 * Showing only the survivors makes "32 scenarios" an arbitrary number. Showing
 * what was rejected makes it a yield, and the reasons are the evidence that the
 * generator has judgement: a scenario the world cannot stage, one only a
 * disobedient agent could pass, and one that passes when nothing happens at all
 * are three different kinds of worthless.
 *
 * The gates are the same three a kept scenario is proved against
 * (`VALIDATION_CHECKS` in contract.js) — a reject is simply where one said no.
 */

/*
 * Each gate is named twice: what it asks of a scenario, and what a scenario
 * that failed it is. Rejects are counted, so it is the failure name that goes
 * on the badge — "3 world is ready" beside a reject count reads as three
 * scenarios that are fine.
 */
export const GATES = [
  { id: "world", asks: "world is ready", failed: "not ready" },
  { id: "solution", asks: "solution passes", failed: "unsolvable" },
  { id: "checks", asks: "checks can fail", failed: "vacuous" },
  /*
    The suite-level gate. The first three judge a scenario on its own; this one
    judges it against the ones already kept, because ten scenarios that differ
    only in the name of the caller are one scenario written ten times — and a
    suite like that reports coverage it does not have.
  */
  { id: "diversity", asks: "adds something new", failed: "duplicate" },
];

const singular = (s = "") => s.replace(/ies$/, "y").replace(/s$/, "");
const article = (s = "") => (/^[aeiou]/i.test(s) ? "an" : "a");
const lower = (s = "") => s.charAt(0).toLowerCase() + s.slice(1);

/**
 * What this environment's generation run discarded.
 *
 * Derived from the environment's own tables, rules and tools, so the rejects
 * are about this world rather than a hand-written example from another one.
 */
export const rejectsFor = (env) => {
  if (!env) return [];
  const tables = env.seed?.tables || [];
  const rules = env.rules || [];
  const tools = env.tools || [];

  /* Gate 1 — the world cannot stage what the draft presumes. */
  const ready = tables.slice(0, 3).map((t, i) => ({
    id: `${env.id}-rej-ready-${i}`,
    gate: "world",
    title: `Asks for ${article(singular(t.name))} ${singular(t.name)} the world does not hold`,
    reason: t.note
      ? `setup.py could not build the state this draft presumes — the seeded ${t.name} are ${t.note}, and the draft asked for something outside that. A scenario the world cannot stage fails for the wrong reason.`
      : `setup.py could not build the state this draft presumes — nothing in ${t.name} matches it. A scenario the world cannot stage fails for the wrong reason.`,
  }));

  /* Gate 2 — no permitted run passes it. */
  const solvable = rules.slice(0, 2).map((r, i) => ({
    id: `${env.id}-rej-solvable-${i}`,
    gate: "solution",
    title: i === 0
      ? "Only passable by breaking a rule"
      : "Asks for an exception the rules do not allow",
    reason: `The reference solution could not satisfy the draft's own checks: finishing the task requires breaking "${lower(r)}". A scenario only a disobedient agent can pass grades the rule, not the agent.`,
  }));

  if (tools.length) {
    solvable.push({
      id: `${env.id}-rej-solvable-tool`,
      gate: "solution",
      title: "Needs a tool this agent does not have",
      reason: `The draft's solution reaches for a capability that is not in the contract. The agent has ${tools.length} tools — ${tools.map((t) => t.name).join(", ")} — and none of them do this, so the reference run never finished.`,
    });
  }

  /* Gate 3 — the check is true of a run that did nothing. */
  const vacuous = [
    {
      id: `${env.id}-rej-vacuous-0`,
      gate: "checks",
      title: "Check passes when nothing happens",
      reason: rules[0]
        ? `Its only check asserts that "${lower(rules[0])}" was not violated, which is trivially true of a run in which the agent did nothing. A check that cannot fail grades nothing while reporting a result.`
        : "Its only check asserts that no violation occurred, which is trivially true of a run in which the agent did nothing.",
    },
    tables[0] && {
      id: `${env.id}-rej-vacuous-1`,
      gate: "checks",
      title: "Passing condition is the world's default",
      reason: `The check asserts ${tables[0].name} is unchanged at the end of the task, and nothing in the task ever changes it. An agent that hangs up immediately passes.`,
    },
  ].filter(Boolean);

  /* Gate 4 — it is a rewording of a scenario already in the suite. */
  const personas = ["Frustrated repeat caller", "Guest with no account", "Power user, terse"];
  const diversity = [
    {
      id: `${env.id}-rej-div-0`,
      gate: "diversity",
      title: `Third draft in a row with the same caller`,
      reason: `The draft reuses ${personas[0]} with the same account state and the same expected tool path as one already kept. Renaming the caller does not make it a different test — the suite would report ten scenarios and cover eight.`,
    },
    {
      id: `${env.id}-rej-div-1`,
      gate: "diversity",
      title: "Placeholder values a real world would never hold",
      reason: "One-time passcode 123456 and a card ending 4242. Fixtures like these are the ones an agent can pass by pattern-matching, so the draft was rejected and the values regenerated as distinct ones.",
    },
    tools[0] && {
      id: `${env.id}-rej-div-2`,
      gate: "diversity",
      title: `Same expected path as an accepted draft`,
      reason: `Ends in ${tools[0].name} with the same preconditions and the same order as a scenario already kept. Only the entity names differ, so it adds no behavioural coverage.`,
    },
  ].filter(Boolean);

  return [...ready, ...solvable, ...vacuous, ...diversity];
};

/**
 * Drafted / kept / rejected for a scenario list.
 *
 * Counted rather than stored: `kept` is however many scenarios actually
 * survived into the environment, so the tally can never drift from the list
 * sitting above it.
 */
export const generationTally = (env, kept = 0) => {
  const rejects = rejectsFor(env);
  return {
    drafted: kept + rejects.length,
    kept,
    rejected: rejects.length,
    byGate: GATES.map((g) => ({ ...g, count: rejects.filter((r) => r.gate === g.id).length }))
      .filter((g) => g.count > 0),
  };
};
