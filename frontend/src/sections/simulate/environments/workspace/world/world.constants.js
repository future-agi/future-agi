export const MONO = "ui-monospace, Menlo, monospace";

export const TOOLS_CARD_COPY = {
  title: (n) => `Tools · ${n}`,
  subtitle: "The tools the agent declares, and where each one runs",
  draft: "Draft: not used by runs until saved",
  groupCount: (n) => (n === 1 ? "1 tool" : `${n} tools`),
  expand: (name) => `Show the contract of ${name}`,
};

export const CODE_BLOCK_COPY = {
  copyCode: "Copy code",
  copied: "Copied to clipboard",
  copyFailed: "Couldn't copy it",
};

export const WORLD_FACTS_COPY = {
  title: "Data store",
  subtitle: "Where the world's records live, as the build read it",
  refusal: "When nothing matches, the agent hears",
  runtime: "Runtime",
  runtimeFile: "world.runtime",
  showRuntime: "Show the runtime",
  hideRuntime: "Hide the runtime",
  masked: "••••••",
};

export const TOOL_STATUS_ORDER = [
  "simulated",
  "in_process",
  "unsupported",
  "unknown",
];

const TOOL_STATUS_GROUP = {
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

  noWorld: {
    title: "No world to reset",
    subtitle:
      "This agent calls its own systems, so there is no seeded data to restore between scenarios.",
    tools: (n) => `Tools · ${n}`,
    toolsLead: "In the contract only: ",
    toolsTail: ". Your systems answer them during a run.",
  },
  writing: {
    title: "Writing the world",
    description:
      "The tables and their seeded rows show here once the build writes them.",
  },
  empty: {
    title: "No tools or data yet",
    description:
      "The tools your agent calls and the data they answer from show here once the environment is built.",
  },
};

export const isWritingWorld = (env) => env?.buildStatus === "building";

export const isConnectOnly = (env) => env?.agentMode === "connect_only";

export function worldTableCount(env) {
  if (isConnectOnly(env)) return 0;
  if (Array.isArray(env?.worldTables) && env.worldTables.length)
    return env.worldTables.length;
  return (Array.isArray(env?.stores) ? env.stores : [])
    .flatMap((store) => (Array.isArray(store?.tables) ? store.tables : []))
    .filter((table) => table?.name && !table.internal).length;
}

export function hasNoWorld(env) {
  if (isConnectOnly(env)) return true;
  return (
    Boolean(env?.detailReady) &&
    env?.buildStatus === "ready" &&
    worldTableCount(env) === 0
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

export function repoOriginOf(env) {
  const repository = env?.source?.repository;
  if (!repository) return null;
  const repo = String(repository)
    .replace(/^https?:\/\/(www\.)?github\.com\//, "")
    .replace(/\.git$/, "")
    .replace(/\/$/, "");
  const sha = env.source.commit_sha
    ? String(env.source.commit_sha).slice(0, 7)
    : null;
  const ref = env.source.ref || sha;
  const short = repo.split("/").pop() || repo;
  return { repo, ref, label: ref ? `${short} @ ${ref}` : short };
}

export function toolNamesOf(env) {
  const status = env?.toolStatus;
  if (Array.isArray(status) && status.length) return status.map((t) => t.tool);
  return (env?.tools || []).map((t) => t.name).filter(Boolean);
}

const SECRET_KEY = /(password|secret|token|credential|api[_-]?key)/i;
const EMBEDDED_STORE_KINDS = new Set(["in_process", "in_memory", "none"]);

export function storeFacts(dataStore) {
  if (!dataStore || typeof dataStore !== "object") return [];
  const embedded = EMBEDDED_STORE_KINDS.has(dataStore.kind);
  return Object.entries(dataStore)
    .filter(
      ([key, value]) =>
        value !== "" && value != null && !(embedded && SECRET_KEY.test(key)),
    )
    .map(([key, value]) => ({
      key,
      label: key.replaceAll("_", " "),
      value: SECRET_KEY.test(key)
        ? WORLD_FACTS_COPY.masked
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value),
    }));
}

export function withToolEvidence(toolStatus = [], env) {
  const declared = Object.fromEntries(
    (env?.tools || []).filter((t) => t?.name).map((t) => [t.name, t]),
  );
  const entrypoints = Object.fromEntries(
    (env?.toolEntrypoints || []).filter((e) => e?.tool).map((e) => [e.tool, e]),
  );
  return toolStatus.map((t) => ({
    ...t,
    declared: declared[t.tool] || null,
    entrypoint: entrypoints[t.tool] || null,
  }));
}

function withTypedColumns(tables = [], columns) {
  if (!columns || typeof columns !== "object") return tables;
  return tables.map((table) => {
    const typed = columns[table.name];
    return Array.isArray(typed) && typed.length
      ? {
          ...table,
          fields: typed.map((c) => ({
            name: c.name,
            type: c.type,
            pk: Boolean(c.pk),
          })),
        }
      : table;
  });
}

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export const WORLD_STRIP_COPY = {
  summary: (agent, total, answered) => {
    const who = agent ? `Agent ${agent}` : "Your agent";
    if (!total) return `${who} calls no tools.`;
    if (answered === total)
      return `${who} calls ${plural(total, "tool")}; the world answers every one.`;
    if (!answered)
      return `${who} calls ${plural(total, "tool")}; the world answers none of them.`;
    return `${who} calls ${plural(total, "tool")}; the world answers ${answered} of them.`;
  },
  kind: "Simulated",
  kindTip:
    "Simulated: tool calls are answered by this world, inside our sandbox.",
};

export const WORLD_CARD_COPY = {
  title: (tables, tools) =>
    `World · ${plural(tables, "table")} · ${plural(tools, "tool")}`,
  subtitle: (rows) =>
    rows
      ? `${rows.toLocaleString()} ${rows === 1 ? "row" : "rows"}. Each table with the tools that use it; a tool opens its details.`
      : "Each table with the tools that use it; a tool opens its details.",
  head: ["", "Table", "Rows", "Columns", "Tools that use it"],
  noTable: "No table",
  noToolUsesIt: "No tool uses it",
  primaryKey: "Primary key",
  changesThis: (desc) =>
    [desc, "changes this table"].filter(Boolean).join(" · "),
  readsThis: (desc) => [desc, "reads this table"].filter(Boolean).join(" · "),
  tier: {
    simulated: {
      word: "Simulated",
      note: "Answered by the world: reads and writes its seeded data, consistent across every call in a run.",
    },
    in_process: {
      word: "Runs inside the agent",
      note: "Runs inside your agent, so the world doesn't answer it.",
    },
    unsupported: {
      word: "Unsupported",
      note: "Reaches a system the world has no copy of, so a run never calls it.",
    },
    unknown: {
      word: "Not known",
      note: "The contract doesn't say where these run.",
    },
  },
  tierLabel: (count, total, word) =>
    `${count === total ? "All tools" : plural(count, "tool")}: ${word}`,
};

export const TOOL_DRAWER_COPY = {
  codePending: "The code shows here once the build writes this tool.",
  remove: "Remove from the world",
  removeNamed: (tool) => `Remove ${tool} from the world`,
  removeTitle: (name) => `Remove ${name}?`,
  removeContent:
    "This asks the builder to remove the tool from the environment. It lands as a draft that you save as a new environment version, and the scenarios that call it are checked again.",
  removeConfirm: "Remove tool",
  arguments: "Arguments",
  returns: "Returns",
  changes: "Changes",
  reads: "Reads",
  reset: "Reset",
  none: "None",
  noTables: "None of the world's tables",
  restored: "Restored to the seed before every trial",
  optional: "optional",
  allowed: (values) => `one of ${values.map(String).join(", ")}`,
  between: (min, max) =>
    min != null && max != null
      ? `${min} to ${max}`
      : min != null
        ? `at least ${min}`
        : `at most ${max}`,
  typeWord: {
    str: "text",
    string: "text",
    int: "whole number",
    integer: "whole number",
    float: "number",
    number: "number",
    bool: "yes / no",
    boolean: "yes / no",
    enum: "one of",
    date: "date",
    object: "object",
    dict: "object",
    list: "list",
    array: "list",
  },
};

export function worldTablesOf(env) {
  const tables = Array.isArray(env?.worldTables) ? env.worldTables : [];
  if (tables.length) return withTypedColumns(tables, env?.dataSchemaColumns);
  const columns = env?.dataSchemaColumns;
  if (!columns || typeof columns !== "object") return [];
  const base = env?.baseEnvironment;
  return Object.entries(columns)
    .filter(([, cols]) => Array.isArray(cols))
    .map(([name, cols]) => ({
      name,
      rows:
        base && typeof base === "object" && Array.isArray(base[name])
          ? base[name].length
          : null,
      fields: cols.map((c) => ({ name: c.name, type: c.type, pk: c.pk })),
    }));
}

export function placeTools(tables, tools) {
  const names = new Set(tables.map((t) => t.name));
  const byTable = new Map(tables.map((t) => [t.name, []]));
  const none = [];
  tools.forEach((tool) => {
    const changed = (tool.tables?.changed || []).find((n) => names.has(n));
    const read = (tool.tables?.read || []).find((n) => names.has(n));
    const at = changed || read;
    if (at) byTable.get(at).push({ tool, changes: Boolean(changed) });
    else none.push({ tool, changes: false });
  });
  return { byTable, none };
}
