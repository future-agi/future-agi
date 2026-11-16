// A mode missing here shows without a hint.
export const TEST_CONDITION_HINTS = {
  timeout_before_commit:
    "The call waits, then times out. Nothing changes in the world.",
  timeout_after_commit:
    "The world makes the change, then the agent hears a timeout. A retry makes it twice.",
  unavailable: "Unavailable: the service answers that it is down.",
  error: "The service answers with the status and message you set.",
};

export const TIMEOUT_MODES = ["timeout_before_commit", "timeout_after_commit"];

export const TEST_CONDITIONS_COPY = {
  heading: "Hidden · test conditions",
  empty: "Tools behave normally",
  add: "Add a test condition",
  tool: "Tool",
  whatHappens: "What happens",
  onCall: "On which call",
  delay: "Delay in seconds",
  status: "Status",
  message: "Message",
  remove: "Remove",
  inProcess: "This tool runs inside the agent, so it cannot be held",
};

export const testConditionRoute = (fault) =>
  fault.tool !== fault.action ? fault.action : "";

export function testConditionLine(fault) {
  return [
    `${fault.tool}: ${fault.mode_label}`,
    fault.on_call != null && ` on call ${fault.on_call}`,
    fault.delay_seconds != null && ` after ${fault.delay_seconds} s`,
    fault.status != null &&
      ` (${fault.status}${fault.message ? `: ${fault.message}` : ""})`,
  ]
    .filter(Boolean)
    .join("");
}

// Only the fields the mode reads; numbers go as they are so the server can refuse a null.
export function fileFault(fault) {
  const stored = {
    action: fault.action,
    mode: fault.mode,
    on_call: fault.on_call,
  };
  if (TIMEOUT_MODES.includes(fault.mode))
    stored.delay_seconds = fault.delay_seconds;
  if (fault.mode === "error") {
    stored.status = fault.status;
    if (fault.message) stored.message = fault.message;
  }
  return stored;
}
