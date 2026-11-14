/**
 * What a build's failure means, in the product's words. The code and the raw text the run
 * recorded stay on the job's record for whoever is debugging; the page shows these words.
 *
 * A failure that names its cause (`failure.cause`, from a closed set) reads as that cause. One
 * the run itself caused reads as a run issue that doesn't count against the agent. A start
 * timeout recorded without a cause (before causes existed, or by an older image) reads as the
 * slow-start cause. Any other failure reads as recorded.
 */
export const CAUSE_WORDS = {
  agent_exited_before_ready:
    "Didn't start — the agent exited before it was ready. Fix the crash on startup, then push a new agent version.",
  agent_not_ready_in_time:
    "Didn't start in time — the agent wasn't ready within the limit. Run it again; if it repeats, make it start or listen sooner.",
  chat_target_timeout:
    "Stopped — the agent didn't reply to a message in time. Run it again. If it repeats, make the agent reply faster.",
  chat_target_refused:
    "Stopped — the agent refused the connection. Check that it listens on the port it was given, then push a new agent version.",
  unknown:
    "The agent stopped before it was ready and didn't say why. Start it on your own machine to see the error it prints.",
};

export const INFRASTRUCTURE_LINE =
  "Stopped — an issue occurred while running this. Run it again; it doesn't count against the agent.";

// A start timeout recorded without a cause names this code in the failure.
const START_TIMEOUT = /depends_on_timeout/;

/** The plain headline of a failure, or null when it has none and reads as recorded. */
export function failureHeadline(failure) {
  if (!failure) return null;
  const cause =
    failure.cause && (CAUSE_WORDS[failure.cause] ? failure.cause : "unknown");
  if (cause && cause !== "unknown") return CAUSE_WORDS[cause];
  if (START_TIMEOUT.test(`${failure.code || ""} ${failure.message || ""}`))
    return CAUSE_WORDS.agent_not_ready_in_time;
  if (cause) return CAUSE_WORDS.unknown;
  if (failure.domain === "infrastructure") return INFRASTRUCTURE_LINE;
  return null;
}
