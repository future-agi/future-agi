const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

export const REVIEW_STATUS = {
  AWAITING: "awaiting_approval",
};

export const PARK_AFTER_MS = 15 * 60 * 1000;

export const SOURCE_LABEL = {
  agent_reading: "From the agent's reading",
  agent_code: "From the agent's code",
  agent_settings: "From the agent's settings",
  agent_prompt: "From the agent's prompt",
  your_change: "From your change",
};

export const AGENT_TOOL_SOURCES = new Set(["agent_code", "agent_settings"]);

export const sourceWords = (source) =>
  source ? source.charAt(0).toLowerCase() + source.slice(1) : "";

export const REVIEW_COPY = {
  title: "Review the design",
  summary: ({ tables, tools, rules, useCases }) => {
    const found = [
      [tables, "record"],
      [tools, "tool"],
      [rules, "rule"],
      [useCases, "use case"],
    ]
      .filter(([count]) => count > 0)
      .map(([count, noun]) => plural(count, noun));
    const gate = "Nothing is built until you approve.";
    return found.length ? `${found.join(" · ")}. ${gate}` : gate;
  },
  approve: "Approve and build",
  continue: "Continue",
  approved: "Approved. The build is running from this design.",
  finishEditing: "Save or cancel the rule you're changing first.",
  emptyWorldTip:
    "Tell the builder what the agent works with first. There is nothing to build yet.",
  emptyWorld:
    "There's nothing to build yet. Say what the agent does and works with, for example “customers and their orders; it can look up an order and refund up to $200”.",
  agentChanged:
    "The agent changed after this design was made. Build again from the agent to use the new version.",
  parked: (since) =>
    `Waiting on you since ${new Date(since).toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    })}. Continue picks up the same design.`,
  chatBlocked: "The builder takes changes once you approve the design.",
  runBlocked: "Approve the design first",
  viewerLocked: "You don't have permission to change or approve this design.",
  loadError: "Couldn't load the design",
  brief: "Brief",
  briefSubtitle: "What the design is built from",
  briefRows: {
    description: "Description",
    sources: "Sources",
    channel: "Channel",
    agent: "Agent",
  },
  descriptionFallback: "Proposed from the agent's prompt, code and tools",
  sourcesValue: "The agent alone",
  channel: { chat: "Chat", voice: "Voice" },
  noAgent: "None yet. Connect one after you save",
  unsupported: "Can't be built here",
  unsupportedWhy: (reaches) =>
    reaches.length
      ? `It reaches ${reaches.join(", ")}, which the test world has no copy of.`
      : "It reaches a system the test world has no copy of.",
  leaveOut: "Leave out",
  leftOut: (tool) => `${tool} can't be built here, left out`,
  recordsAndTools: "Records and tools",
  recordsAndToolsCount: (tables, tools) =>
    `${plural(tables, "table")} · ${plural(tools, "tool")}`,
  records: "Records",
  tools: "Tools",
  rules: "Rules",
  people: "People",
  useCases: "Use cases",
  writtenAfterApproval: (scenarios, useCases) =>
    `${plural(scenarios, "scenario")} ${scenarios === 1 ? "is" : "are"} written after you approve${
      scenarios === useCases
        ? ", one per use case"
        : ", spread across these use cases"
    }.`,
  columns: (columns) => plural(columns.length, "column"),
  noTable: "No table",
  none: "None",
  tableHeader: {
    table: "Table",
    columns: "Columns",
    changedBy: "Changed by",
    readBy: "Read by",
  },
  noTableWhy: {
    in_process: "runs inside the agent",
    simulated: "touches no table",
    unsupported: "can't be built here",
    unknown: "where it runs is not known",
  },
  caller: "Caller",
  callerRole: "the person the agent serves",
  proposedForScenarios: "Proposed for the scenarios",
  noScenarios: "Written once it knows what the agent does",
  edit: "Edit",
  remove: "Remove",
  removeNamed: (name) => `Remove ${name}`,
  save: "Save",
  cancel: "Cancel",
  ruleLabel: "Rule",
  theRule: "The rule",
  theUseCase: "The use case",
  keepOneUseCase: "A design needs at least one use case",
  removedFromPlan: (label) => `${label} removed from the plan.`,
  agentStillCalls:
    "Your agent still calls it, so the world won't answer and those calls fail. Keep it unless you're testing that.",
  undo: "Undo",
  removeTitle: (name) => `Remove ${name}?`,
  removeContent: ({ tools, rules }) =>
    [
      tools.length
        ? `${tools.map((tool) => `${tool.name} ${tool.reason}`).join("; ")}.`
        : "",
      rules.length
        ? `${plural(rules.length, "rule")} ${rules.length === 1 ? "names" : "name"} it.`
        : "",
      "The scenarios that use these won't work well once it's removed.",
    ]
      .filter(Boolean)
      .join(" "),
  removeAnyway: "Remove anyway",
};

export const BUILD_COPY = {
  line: (name, phase, step) =>
    [
      name ? `Building environment ${name}` : "Building environment",
      phase,
      step,
    ]
      .filter(Boolean)
      .join(" · "),
  count: (no, total) => ` · ${no} of ${total}`,
  after: (elapsed) => `Runs start once it's ready · ${elapsed} so far`,
  afterNoClock: "Runs start once it's ready",
};

export const elapsedWords = (ms) => {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
};
