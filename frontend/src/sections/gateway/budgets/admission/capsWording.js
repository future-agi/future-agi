// What daily admission caps do in this release, in the same words wherever a
// cap shows. No gateway reports the spend ledger that switching caps on needs,
// so caps can't be turned on; caps already on stay enforced.
export const CAPS_OFF_MESSAGE =
  "Daily caps can't be turned on in this release. Budgets in Gateway > Budgets still limit spend.";

export const CAPS_ON_MESSAGE =
  "Daily caps are on. A key under a cap can make only non-streaming chat requests, and its requests over the day's cap are refused. Streaming and Gemini requests from capped keys are refused, and native message and response requests are refused for every key while daily caps are on.";

// Under the agent caps list, only while caps are on and an agent has one.
export const UNBOUND_KEY_MESSAGE =
  "While any agent has a daily cap, a key not bound to an agent is refused.";

// Switching caps on is refused in this release, so turning them off is said
// to be one-way before anyone confirms it.
export const DEACTIVATE_TITLE = "Turn off daily caps?";
export const DEACTIVATE_MESSAGE =
  "You can't turn them back on in this release.";

// Restoring a config version never switches daily caps on: over caps off, a
// version with caps on is restored as a new version with caps off.
export const RESTORE_CAPS_OFF_MESSAGE =
  "This version has daily caps on. Daily caps can't be turned on in this release. Restore it with daily caps off?";

export const RESTORE_STOPS_CAPS_MESSAGE =
  "This version has daily caps off. Restoring it stops daily cap enforcement.";
