/*
  The account's active rate plan — the one place prices live. Run plans, the
  Cost tab, builds, improvements and re-grades all read it. Mock values; the
  real ones come from the account.
*/

/* Credits are the billing unit; 1 credit = $0.01. */
export const RATE_PLAN = {
  name: "Team plan",
  version: "Oct 2026",
  creditUsd: 0.01,
  rates: {
    /* Simulated caller and actors, per 1M tokens. */
    text_sim_tokens: { credits: 400, per: 1e6, unit: "tokens" },
    /* Simulated caller on a voice call, per minute. */
    voice_sim_minutes: { credits: 12, per: 1, unit: "minutes" },
    /* A managed LLM-judge eval, per call. */
    judge_calls: { credits: 2, per: 1, unit: "judge calls" },
    /* Failure analysis (Debug failures), per failing scenario it reads. */
    analysis_calls: { credits: 5, per: 1, unit: "failing scenarios" },
    /* Restoring a past call's end-state to grade it again (a re-grade). */
    restore_calls: { credits: 1, per: 1, unit: "restored calls" },
    /* Hosted sandbox has no rate yet: measured, not billed during the beta. */
    sandbox_minutes: null,
  },
};

/* The account's billing policy for work that didn't complete — what the
   Cost tab applies and says, line by line. */
export const BILLING_POLICY = {
  refund:
    "A call that fails on our side (simulator, world or connection) is run again; the failed attempt is refunded.",
  waive: "A scenario that can't be measured isn't charged.",
  notStarted: "Calls a limit or a stop kept from starting aren't charged.",
  once: "Each usage event is charged once — reopening an analysis or a retried delivery doesn't charge it again.",
};

/* Tokens the simulated caller and actors use per text trial, and a voice
   call's expected length — a range, since call length isn't known upfront. */
export const TOKENS_PER_TRIAL = 9000;
export const AGENT_TOKENS_PER_TRIAL = 6000;
export const CALL_MINUTES = [1.2, 2.2];
/** The call length a run plan estimates with, in minutes: [low, high]. */
export const CALL_MINUTES_RANGE = CALL_MINUTES;

/* What the plan includes each month before a simulation meter charges —
   the free tiers Settings → Usage shows on each product card. */
export const FREE_TIER = {
  text_sim_tokens: 1e6,
  voice_sim_minutes: 60,
  ai_credits: 2000,
};

const R = RATE_PLAN.rates;
const midMinutes = (CALL_MINUTES[0] + CALL_MINUTES[1]) / 2;

/* One simulated conversation at these rates, with one managed judge — what
   an improvement's search prices each trial at. In dollars, the unit
   improvement budgets are set in. */
export const PER_CONVERSATION_USD = {
  voice:
    (midMinutes * R.voice_sim_minutes.credits + R.judge_calls.credits) *
    RATE_PLAN.creditUsd,
  chat:
    ((TOKENS_PER_TRIAL / R.text_sim_tokens.per) * R.text_sim_tokens.credits +
      R.judge_calls.credits) *
    RATE_PLAN.creditUsd,
};
