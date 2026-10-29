/*
  A run's seed and limits (roadmap runs.md "Seed/data", "Timeout and
  limits"): the seed is the saved snapshot's, or new and fixed when the run
  starts; a per-trial timeout, a whole-run deadline, the environment's turn
  limit and a step limit. A limit the agent's platform can't honour is shown
  as not enforced, never offered as enforceable.
*/
import { envTurnLimitOf } from "./runStream";
import { takesSnapshot } from "./candidateSource";

export const TRIAL_TIMEOUTS = [5, 10, 15, 30];
export const RUN_DEADLINES = [0, 60, 120, 240, 480];
export const STEP_LIMITS = [10, 25, 50, 100];

export const minutesLabel = (m) => (!m ? "None" : m < 60 ? `${m} min` : `${m / 60} h`);

/** The defaults the run plan opens with. */
export const runLimitsOf = (env, envState) => ({
  trialTimeout: 10,
  runDeadline: 0,
  turnLimit: envTurnLimitOf(envState),
  /* Tool steps are counted only where the agent's code runs through us. */
  stepLimit: takesSnapshot(envState?.agent) ? 25 : null,
});

/** "seed new · 10 min a trial · no deadline · 12 turns · 25 steps" — the closed summary. */
export const limitsLine = (limits, { voice, seed }) =>
  [
    seed,
    `${minutesLabel(limits.trialTimeout)} a ${voice ? "call" : "trial"}`,
    limits.runDeadline ? `run ends by ${minutesLabel(limits.runDeadline)}` : "no run deadline",
    `${limits.turnLimit} turns`,
    limits.stepLimit ? `${limits.stepLimit} tool steps` : null,
  ]
    .filter(Boolean)
    .join(" · ");
