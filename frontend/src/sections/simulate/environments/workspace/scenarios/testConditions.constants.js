// A scenario's test conditions: the faults the world gives the agent's calls in it, as the scenario file's
// `faults` carries them ({ action, mode, on_call, delay_seconds, status, message }).

export const TEST_CONDITION_MODES = {
  timeout_before_commit: "Times out before it runs",
  timeout_after_commit: "Runs, then the answer is lost",
  unavailable: "Unavailable",
  error: "Answers with an error",
};

// What each mode means for the agent, under the choice.
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
  // The agent never sees them: they shape how its tools answer in this scenario.
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
  refused: "The test world cannot apply this test condition.",
};

// The modes a tool can be given: "Runs, then the answer is lost" only for a tool that changes records, since a read
// has nothing to lose.
export const modesFor = (target) =>
  Object.keys(TEST_CONDITION_MODES).filter(
    (mode) => mode !== "timeout_after_commit" || Boolean(target?.writes),
  );

// The contract tool a fault names, from the environment's fault targets.
const targetOf = (fault, env) =>
  env?.faultTargets?.find((one) => one.action === fault?.action);

// The route a fault holds, shown under its line, when it reads differently from the tool's name.
export function testConditionRoute(fault, env) {
  const target = targetOf(fault, env);
  return target && target.tool !== target.action ? target.action : "";
}

// One line per fault, from the fields it holds: "{tool}: {mode label} on call {n}", with the delay for a timeout and
// the status for an error.
export function testConditionLine(fault, env) {
  const target = targetOf(fault, env);
  const label = TEST_CONDITION_MODES[fault.mode] || fault.mode;
  return [
    `${target ? target.tool : fault.action}: ${label}`,
    fault.on_call != null && ` on call ${fault.on_call}`,
    fault.delay_seconds != null && ` after ${fault.delay_seconds} s`,
    fault.status != null &&
      ` (${fault.status}${fault.message ? `: ${fault.message}` : ""})`,
  ]
    .filter(Boolean)
    .join("");
}
