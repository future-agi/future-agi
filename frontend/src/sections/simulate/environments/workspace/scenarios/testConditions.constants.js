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
  heading: "Test conditions",
  empty: "No test conditions. The tools answer normally.",
  add: "Add a test condition",
  tool: "Tool",
  whatHappens: "What happens",
  onCall: "On which call",
  delay: "Delay in seconds",
  status: "Status",
  message: "Message",
  remove: "Remove",
  inProcess: "This tool runs inside the agent, so it cannot be held",
  noDoor:
    "Actors cannot act and faults cannot be set here: this agent's tools run inside it. Serve the tools from a tools service to test this.",
  refused: "The test world cannot apply this test condition.",
};

// The world action a fault names for a tool: a service tool's route with `/` as `.`, else the tool's own name.
const actionOf = (entry) => {
  const route = String(entry?.endpoint || "").replace(/^\/+|\/+$/g, "");
  return route ? route.replace(/\//g, ".") : String(entry?.tool || "");
};

// Each contract tool a fault can name, with whether the world's door stands in front of it.
export function faultTargets(env) {
  const reach = env?.doorReach || {};
  const entries = Array.isArray(env?.toolEntrypoints)
    ? env.toolEntrypoints
    : [];
  const named = entries
    .filter((entry) => entry?.tool)
    .map((entry) => ({
      tool: String(entry.tool),
      action: actionOf(entry),
      reachable: Boolean(reach[entry.tool]),
    }));
  if (named.length) return named;
  return (env?.tools || [])
    .filter((tool) => tool?.name)
    .map((tool) => ({
      tool: tool.name,
      action: tool.name,
      reachable: Boolean(reach[tool.name]),
    }));
}

// The contract tool a fault names: its action is a service route (`orders.refund`) or the tool's own name.
function faultTool(fault, env) {
  const action = String(fault?.action || "");
  const target = faultTargets(env).find((one) => one.action === action);
  return target?.tool || action;
}

// The route a fault holds, shown under its line, when it reads differently from the tool's name.
export function testConditionRoute(fault, env) {
  const action = String(fault?.action || "");
  return faultTool(fault, env) === action ? "" : action;
}

// One line per fault: "{tool}: {mode label} on call {n}", with the delay for a timeout and the status for an error.
export function testConditionLine(fault, env) {
  const label = TEST_CONDITION_MODES[fault?.mode] || fault?.mode || "";
  const onCall = fault?.on_call ?? 1;
  let line = `${faultTool(fault, env)}: ${label} on call ${onCall}`;
  if (TIMEOUT_MODES.includes(fault?.mode)) {
    line += ` after ${fault?.delay_seconds ?? 30} s`;
  }
  if (fault?.mode === "error") {
    line += ` (${fault?.status ?? 500}${fault?.message ? `: ${fault.message}` : ""})`;
  }
  return line;
}

// Whether a scenario needs the world's door somewhere it does not stand: an acting actor (actions, or brought in by
// one of the agent's tool calls) on an agent whose tools run inside it, or a fault on a tool the door cannot hold.
export function needsAMissingDoor(row, env) {
  const reach = env?.doorReach;
  if (!reach || !Object.keys(reach).length) return false;
  const targets = faultTargets(env);
  const byAction = Object.fromEntries(targets.map((one) => [one.action, one]));
  const byTool = Object.fromEntries(targets.map((one) => [one.tool, one]));
  const faultOff = (row?.faults || []).some((fault) => {
    const target = byAction[fault?.action] || byTool[fault?.action];
    return target ? !target.reachable : false;
  });
  const acts = (row?.callActors || []).some(
    (one) => one?.actions?.length || one?.trigger?.kind === "on_tool",
  );
  return (
    faultOff ||
    (acts && targets.length > 0 && targets.every((one) => !one.reachable))
  );
}
