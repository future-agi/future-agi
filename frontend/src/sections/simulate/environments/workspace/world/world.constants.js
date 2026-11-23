export const MONO = "ui-monospace, Menlo, monospace";

export const TOOLS_CARD_COPY = {
  title: (n) => `Tools · ${n}`,
  subtitle: "The tools the agent declares, and where each one runs",
  arguments: "Arguments",
  noInputs: "Takes no inputs",
  returns: "Returns",
  returnsNotDeclared: "Not recorded for this environment version",
  changes: "Changes",
  effect: {
    read: "Reads only",
    write: "Can change records",
    unknown: "Not declared",
  },
  refusesWhen: "Refuses when",
  needsFirst: "Needs first",
  resetLabel: "Reset",
  reset: "Reset to the seeded records before each trial",
  notReset: "Not reset by the test world",
  groupCount: (n) => (n === 1 ? "1 tool" : `${n} tools`),
  expand: (name) => `Show the contract of ${name}`,
};

export const TOOL_STATUS_ORDER = [
  "simulated",
  "in_process",
  "unsupported",
  "unknown",
];

export const TOOL_STATUS_GROUP = {
  simulated: {
    label: "Simulated",
    note: "The test world answers these calls during a run.",
  },
  in_process: {
    label: "Runs inside the agent",
    note: "These run inside your agent, so the test world doesn't answer them.",
  },
  unsupported: {
    label: "Unsupported",
    note: "These reach a system the test world has no copy of, so a test never calls them.",
  },
  unknown: {
    label: "Where it runs is not known",
    note: "The contract doesn't say where these run.",
  },
};

export const WORLD_COPY = {
  summary: (total, answered) => {
    if (!total) return "Your agent declares no tools.";
    const tools = total === 1 ? "1 tool" : `${total} tools`;
    if (answered === total)
      return total === 1
        ? "Your agent calls 1 tool, and the test world answers it."
        : `Your agent calls ${tools}, and the test world answers every one.`;
    if (!answered)
      return `Your agent calls ${tools}. The test world answers none of them.`;
    return `Your agent calls ${tools}. The test world answers ${answered} of them.`;
  },
  originTip: (repo, ref) =>
    ref
      ? `Read from ${repo} at ${ref}. A commit there that changes the world makes a new environment version.`
      : `Read from ${repo}.`,
  map: {
    title: (tables, tools) =>
      tools
        ? `World · ${tables} ${tables === 1 ? "table" : "tables"} · ${tools} ${tools === 1 ? "tool" : "tools"}`
        : `World · ${tables} ${tables === 1 ? "table" : "tables"}`,
    subtitle: (rows, withTools) =>
      withTools
        ? `${rows.toLocaleString()} ${rows === 1 ? "row" : "rows"}. Each table with the tools that use it.`
        : `${rows.toLocaleString()} ${rows === 1 ? "row" : "rows"} seeded before each run.`,
    table: "Table",
    rows: "Rows",
    columns: "Columns",
    usedBy: "Tools that use it",
    noTool: "No tool uses it",
    reads: "Reads this table",
    changes: "Changes this table",
    expand: (name) => `Show the columns of ${name}`,
  },
  noWorld: {
    title: "No world to reset",
    subtitle:
      "This agent calls its own systems, so there is no seeded data to restore between scenarios.",
    tools: (n) => `Tools · ${n}`,
    toolsLead: "In the contract only: ",
    toolsTail: ". Your systems answer them during a run.",
    noTools: "None read from your agent.",
    data: "Data",
    dataValue: "No tables or stores are seeded for this environment.",
  },
  empty: {
    title: "No tools or data yet",
    description:
      "The tools your agent calls and the data they answer from show here once the environment is built.",
  },
};

export function inputLine(name, type, unit) {
  return [`${name}: ${type || "any"}`, unit].filter(Boolean).join(", ");
}

export function returnLine(name, kind) {
  return kind ? `${name}: ${kind}` : name;
}

export function effectLabel(effect) {
  return TOOLS_CARD_COPY.effect[effect] || TOOLS_CARD_COPY.effect.unknown;
}

export function hasNoWorld(env) {
  return (
    Boolean(env?.detailReady) &&
    !(env?.worldTables?.length > 0) &&
    !(env?.stores?.length > 0)
  );
}

export function groupToolsByStatus(tools = []) {
  return TOOL_STATUS_ORDER.map((status) => ({
    status,
    label: TOOL_STATUS_GROUP[status].label,
    note: TOOL_STATUS_GROUP[status].note,
    tools: tools.filter((t) => (t.status || "unknown") === status),
  })).filter((group) => group.tools.length > 0);
}

export function toolsUsingTable(tools = [], table) {
  return tools
    .filter((t) => t.tables?.recorded)
    .map((t) => {
      if (t.tables.changed?.includes(table))
        return { tool: t.tool, changes: true };
      if (t.tables.read?.includes(table))
        return { tool: t.tool, changes: false };
      return null;
    })
    .filter(Boolean);
}

export function repoOriginOf(env) {
  const repository = env?.source?.repository;
  if (!repository) return null;
  const repo = String(repository)
    .replace(/^https?:\/\/(www\.)?github\.com\//, "")
    .replace(/\.git$/, "")
    .replace(/\/$/, "");
  const ref = env.source.ref || null;
  const short = repo.split("/").pop() || repo;
  return { repo, ref, label: ref ? `${short} @ ${ref}` : short };
}

export function toolNamesOf(env) {
  const status = env?.toolStatus;
  if (Array.isArray(status) && status.length) return status.map((t) => t.tool);
  return (env?.tools || []).map((t) => t.name).filter(Boolean);
}
