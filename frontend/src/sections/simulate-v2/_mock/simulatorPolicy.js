/**
 * How the simulated caller is allowed to behave.
 *
 * Prompt wording is not a control system. A caller told to "be an impatient
 * customer chasing a refund" will, across ten runs, volunteer the OTP before
 * being asked, refuse the thing the scenario needs it to accept, answer a
 * question it already answered, or keep talking after the task is done — and
 * every one of those turns a test of the agent into a test of nothing.
 *
 * So the caller is a policy, not a paragraph: facts it holds, facts it will not
 * volunteer, what unlocks each of them, how hard it pushes back, when it
 * interrupts, when it hangs up, and what it must never do. Structured, so it
 * can be read before the run rather than reconstructed from a transcript
 * afterwards — and so a caller that breaks it is a *simulator* failure rather
 * than an agent one.
 *
 * One rule sits above the rest: the caller is never tuned to make the agent
 * pass. It follows the scenario, and if that exposes the agent, that is the
 * product working.
 */

import { hashSeed } from "./runStream";

const pick = (arr, n) => arr[n % arr.length];

/* Traits are the persona's; these are what each one means for behaviour. */
const STYLE_FROM_TRAIT = {
  impatient: { patience: "low", verbosity: "terse", pushbacks: 3 },
  interrupts: { interruption: "cuts in once the agent repeats itself" },
  "well-informed": { expertise: "high" },
  cooperative: { patience: "high", pushbacks: 1 },
  "vague on detail": { precision: "low" },
  hesitant: { patience: "high", verbosity: "halting" },
  "repeats themselves": { precision: "low" },
  terse: { verbosity: "terse" },
  "skips pleasantries": { verbosity: "terse" },
  "corrects the agent": { pushbacks: 3 },
  procedural: { precision: "high", verbosity: "measured" },
  "slow to answer": { patience: "high" },
  brisk: { patience: "low", verbosity: "terse" },
};

/**
 * Facts the caller holds back until something specific unlocks them.
 *
 * This is the single most valuable line in the policy. An agent that never asks
 * for the second factor and still gets told it is an agent that passed a check
 * it should have failed — so the caller does not offer it, ever, until asked.
 */
const guarded = (env) => {
  const tools = (env?.tools || []).map((t) => t.name).join(" ");
  const out = [
    { fact: "One-time passcode", trigger: "Only after the agent asks for it by name" },
    { fact: "Full card number", trigger: "Never — the caller reads the last four digits only" },
  ];
  if (/identity|verify|kyc/i.test(tools)) {
    out.unshift({ fact: "Second identity factor", trigger: "Only after the agent asks a second, different question" });
  }
  if (/refund|payment|charge/i.test(tools)) {
    out.push({ fact: "Willingness to accept a partial refund", trigger: "Only after the agent has quoted an amount" });
  }
  return out;
};

/**
 * The policy for one scenario's caller.
 *
 * Deterministic in the scenario id, so the same scenario always gets the same
 * caller — a policy that changed between runs would make every comparison
 * meaningless in exactly the way this file exists to prevent.
 */
export const simulatorPolicy = (scenario, env) => {
  if (!scenario) return null;
  const h = hashSeed(scenario.id || "s");
  const traits = scenario.persona?.traits || [];

  const style = traits.reduce((acc, t) => ({ ...acc, ...(STYLE_FROM_TRAIT[t] || {}) }), {
    patience: "medium",
    verbosity: "normal",
    precision: "medium",
    expertise: "medium",
    pushbacks: 2,
    interruption: "none",
  });

  const goal = scenario.task || "Get the request resolved in one call.";

  return {
    persona: scenario.persona,
    goal,
    /* What it will state plainly when asked anything relevant. */
    facts: [
      `Name and contact as held in ${env?.name || "the environment"}`,
      scenario.critical
        ? "A specific, verifiable reason for the request"
        : "The reference number it is calling about",
      pick([
        "Availability for a callback later today",
        "That it has called about this before",
        "That it is calling from a noisy place",
      ], h),
    ],
    private: guarded(env),
    style: {
      patience: style.patience,
      verbosity: style.verbosity,
      precision: style.precision,
      expertise: style.expertise,
    },
    objections: {
      max: style.pushbacks,
      /* Blockers are the scenarios where the agent must hold a line, so the
         caller has to actually push on it — politely, and a bounded number of
         times, so a refusal loop terminates instead of running the clock. */
      style: scenario.critical
        ? "Pushes back on a refusal and asks for the reason, then accepts it"
        : "Accepts a clear answer the first time",
    },
    interruption: style.interruption === "none"
      ? { allowed: false, when: "Waits for the agent to finish" }
      : { allowed: true, when: style.interruption },
    /* When the call is over — the condition that stops a caller talking past
       the end of its own task. */
    termination: [
      "The goal is satisfied and the agent has confirmed it",
      "The agent states a rule that blocks the goal and the caller has pushed back its allowed number of times",
      "The agent transfers or escalates",
      "Six turns with no progress",
    ],
    prohibited: [
      "Volunteering a guarded fact before it is asked for",
      "Contradicting a fact it has already stated",
      "Answering a question with the goal instead of the answer",
      "Accepting an outcome the scenario says it must refuse",
      "Helping the agent past a check the scenario is testing",
    ],
  };
};

/** A one-line read of the policy, for places with no room for the whole thing. */
export const policySummary = (policy) => {
  if (!policy) return "";
  return `${policy.style.verbosity}, ${policy.style.patience} patience · ${policy.objections.max} pushbacks · ${policy.private.length} facts held back`;
};
