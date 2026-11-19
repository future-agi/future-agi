/**
 * What a build's failure means, in the product's words. The code and the raw text the run
 * recorded stay on the job's record for whoever is debugging; the page shows these words.
 *
 * A failure that names its cause (`failure.cause`, from a closed set) reads as that cause, and
 * any other value as `unknown`. A run's start timeout, recorded by its closed code without a
 * cause, reads as one. Another failure the run itself caused reads as a run issue that doesn't
 * count against the agent. Any other failure reads as recorded. The message never decides:
 * it can carry what the agent printed.
 */
export const CAUSE_WORDS = {
  agent_exited_before_ready:
    "Didn't start — the agent exited before it was ready. Fix the crash on startup, then push a new agent version.",
  agent_not_ready_in_time:
    "Didn't start in time — the agent wasn't ready within the limit. Run it again; if it repeats, make it start or listen sooner.",
  dependency_not_ready_in_time:
    "Didn't start in time — a service this run needs wasn't ready within the limit. Run it again.",
  chat_target_timeout:
    "Stopped — the agent didn't reply to a message in time. Run it again. If it repeats, make the agent reply faster.",
  chat_target_refused:
    "Stopped — the agent refused the connection. Check that it listens on the port it was given, then push a new agent version.",
  unknown:
    "We couldn't run this agent version in its environment, and the run recorded no cause. Run it again; if it repeats, start the agent on your own machine and check that it comes up.",
};

export const INFRASTRUCTURE_LINE =
  "Stopped — an issue occurred while running this. Run it again; it doesn't count against the agent.";

// A run that stopped because a process it needs didn't start in time records this code, with no
// cause: the run can't tell which process it was.
const START_TIMEOUT_CODE = "depends_on_timeout";
export const START_TIMEOUT_LINE =
  "Stopped — a service this run needs didn't start in time. Run it again.";

/** The plain headline of a failure, or null when it has none and reads as recorded. */
export function failureHeadline(failure) {
  if (!failure) return null;
  if (failure.cause != null)
    return Object.hasOwn(CAUSE_WORDS, failure.cause)
      ? CAUSE_WORDS[failure.cause]
      : CAUSE_WORDS.unknown;
  if (failure.code === START_TIMEOUT_CODE) return START_TIMEOUT_LINE;
  if (failure.domain === "infrastructure") return INFRASTRUCTURE_LINE;
  return null;
}
