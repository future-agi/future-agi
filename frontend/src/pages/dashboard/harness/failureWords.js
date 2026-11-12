/**
 * What a run's failure means, in the product's words. The code and the raw text the run
 * recorded stay on the record, behind "Details", for whoever is debugging.
 *
 * A failure that names its cause (`failure.cause`, from a closed set) reads as that cause. One
 * our infrastructure caused reads as ours, not the agent's. Any other failure reads as it did.
 */
export const CAUSE_WORDS = {
  agent_exited_before_ready:
    "Didn't start — the agent exited before it was ready. Fix the crash on startup, then push a new agent version.",
  agent_not_ready_in_time:
    "Didn't start — the agent wasn't listening in time. Make it start faster or listen sooner, then push a new agent version.",
  chat_target_timeout:
    "Stopped — the agent didn't reply to a message in time. Run it again. If it repeats, make the agent reply faster.",
  chat_target_refused:
    "Stopped — the agent refused the connection. Check that it listens on the port it was given, then push a new agent version.",
  unknown: "Stopped — the reason wasn't recorded. Run it again.",
};

export const INFRASTRUCTURE_LINE =
  "Our infrastructure failed, not the agent. Counts neither for nor against it.";

/** The plain headline of a failure, or null when it has none and reads as recorded. */
export function failureHeadline(failure) {
  if (!failure) return null;
  if (failure.cause) return CAUSE_WORDS[failure.cause] || CAUSE_WORDS.unknown;
  if (failure.domain === "infrastructure") return INFRASTRUCTURE_LINE;
  return null;
}
