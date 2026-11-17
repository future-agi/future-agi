// Tools of a Prompt run card (R2-SPEC §5.3.2). Pure: the card's tools (the
// prompt version's own + any added with `+ Add tool`), how each one answers
// (Mock · Recorded · Live), the create-body keys and the step checks.
//
// Card field `toolSetup` (form only):
//   versionTools  [{name, description, params, required, schema, schema_hash}]
//                 mirrored from the selected prompt version
//   versionLabel  `Support Ops Reply V1` (where the tools came from)
//   fixtures      [{tool, cases}] auto fixtures of the version (read-only)
//   recorded      {available, reason, sessions} recordings of the dataset
//   mode          "mock" | "recorded" | "per_tool"
//   perTool       {[name]: "mock" | "recorded" | "live"}   (mode per_tool)
//   endpoints     {[name]: "https://…"}                     (Live tools)
//   maxToolTurns  8
//   onUnrecorded  "mock" | "fail" | "live"

export const TOOL_MODES = Object.freeze({
  mock: {
    value: "mock",
    label: "Mock",
    icon: "mdi:test-tube",
    help: "Returns a test answer. Fast and repeatable. Nothing real is called.",
  },
  recorded: {
    value: "recorded",
    label: "Recorded",
    icon: "mdi:history",
    help: "Returns what this tool returned in production for the row's source session. Rows without a recording use Mock.",
  },
  live: {
    value: "live",
    label: "Live",
    icon: "mdi:lightning-bolt",
    help: "Calls {url}. Real calls can have side effects.",
  },
});

export const BLOCK_HELP =
  "Each tool returns a test answer we generate from its schema and the row. Nothing real is called.";

/**
 * IT-R1 G1 P12: one line under each choice of the tools menu, and the
 * block's subtitle after a pick (pure copy).
 */
export const MENU_HELP = Object.freeze({
  mock: "Test answers made from each tool's schema and the row. Nothing real is called.",
  recorded:
    "Each tool replays what it returned in production for the row. No recording: Mock.",
  per_tool: "Choose Mock, Recorded or Live for each tool.",
  advanced: "Tool turns per row, unrecorded calls, add or edit tools.",
});
export const AUTO_FIXTURES_HELP =
  "auto fixtures = the test answers Mock returns, built from each tool's schema and the task's start state";

/**
 * What a call to a Recorded tool does on a row with no recording: the
 * card's `onUnrecorded` (Advanced > Unrecorded calls; the run's engine rule,
 * sources/tools.js). Only a call to that tool on such a row is affected;
 * a row that never calls it runs as usual.
 */
export const UNRECORDED_OUTCOME = Object.freeze({
  mock: "uses Mock",
  fail: "fails the row",
  live: "is made live",
});
const outcomeOf = (onUnrecorded) =>
  UNRECORDED_OUTCOME[onUnrecorded] ?? UNRECORDED_OUTCOME.mock;

/** The Recorded legend line, true to the card's Unrecorded calls choice. */
export function recordedHelpFor(onUnrecorded) {
  if (!onUnrecorded || onUnrecorded === "mock") return TOOL_MODES.recorded.help;
  return `Returns what this tool returned in production for the row's source session. A call on a row without a recording ${outcomeOf(onUnrecorded)}.`;
}

/** The line under a tools-menu choice (`MENU_HELP`, Recorded follows onUnrecorded). */
export function menuHelpFor(choice, onUnrecorded) {
  if (choice !== "recorded" || !onUnrecorded || onUnrecorded === "mock")
    return MENU_HELP[choice];
  return `Each tool replays what it returned in production for the row. No recording: the call ${outcomeOf(onUnrecorded)}.`;
}

/** The subtitle under `Tools (6): …` for the chosen mode. */
export function blockHelpFor(mode, onUnrecorded) {
  if (mode === "recorded")
    return !onUnrecorded || onUnrecorded === "mock"
      ? "Each tool returns what it returned in production for the row; rows without a recording use Mock."
      : `Each tool returns what it returned in production for the row; on a row without a recording a call ${outcomeOf(onUnrecorded)}.`;
  if (mode === "per_tool")
    return "Each tool uses its own mode (Advanced lists them). Live tools call their URL for real.";
  return BLOCK_HELP;
}

export const ON_UNRECORDED = Object.freeze([
  { value: "fail", label: "Fail the row" },
  { value: "mock", label: "Use mock" },
  { value: "live", label: "Call live" },
]);

export const DEFAULT_MAX_TOOL_TURNS = 8;
export const MAX_TOOL_TURNS_RANGE = Object.freeze({ min: 1, max: 30 });

/** Sandbox URL a Live tool calls when none is set (placeholder only). */
export const liveUrlHint = (name) =>
  `https://sandbox.acme.ai/tools/${name || "tool"}`;

const fnOf = (t) => t?.function ?? t?.config?.function ?? t ?? {};

/**
 * One tool, normalised from an OpenAI-style schema (`{type:"function",
 * function:{name, description, parameters}}`) or a workspace tool
 * (`{name, config:{function}}`).
 */
export function normaliseTool(raw, { schemaHash } = {}) {
  const fn = fnOf(raw);
  const name = String(fn?.name ?? raw?.name ?? "").trim();
  if (!name) return null;
  const parameters = fn?.parameters ?? {};
  const props = parameters.properties ?? {};
  return {
    name,
    description: String(fn?.description ?? raw?.description ?? ""),
    params: Object.keys(props),
    required: Array.isArray(parameters.required) ? parameters.required : [],
    types: Object.fromEntries(
      Object.entries(props).map(([k, v]) => [k, v?.type ?? "any"]),
    ),
    schema: { type: "function", function: { ...fn, name } },
    schema_hash: raw?.schema_hash ?? schemaHash ?? null,
  };
}

/** The tools of a prompt version (`prompt_config_snapshot.configuration`). */
export function versionToolsOf(version) {
  const list = version?.prompt_config_snapshot?.configuration?.tools ?? [];
  return list.map((t) => normaliseTool(t)).filter(Boolean);
}

/** Tools picked with `+ Add tool` (ConfigTool rows `{id, tool:{tool}}`). */
export function addedToolsOf(configurationTools) {
  return (configurationTools ?? [])
    .map((row) => normaliseTool(row?.tool?.tool ?? row?.tool ?? row))
    .filter(Boolean);
}

/** Version tools first, then added ones not already there. */
export function cardTools(setup, configurationTools) {
  const out = [...(setup?.versionTools ?? [])];
  const seen = new Set(out.map((t) => t.name));
  addedToolsOf(configurationTools).forEach((t) => {
    if (seen.has(t.name)) return;
    seen.add(t.name);
    out.push({ ...t, added: true });
  });
  return out;
}

/** `{order_id}` · `{order_id, amount, reason}` (required first). */
export function paramSignature(tool) {
  const params = tool?.params ?? [];
  return `{${params.join(", ")}}`;
}

/** `lookup_order(order_id: string)` for the chip tooltip. */
export function fullSignature(tool) {
  const req = new Set(tool?.required ?? []);
  const args = (tool?.params ?? []).map(
    (p) => `${p}${req.has(p) ? "" : "?"}: ${tool?.types?.[p] ?? "any"}`,
  );
  return `${tool?.name ?? ""}(${args.join(", ")})`;
}

/** Whether recordings can answer (dataset rows have a source session). */
export const recordedAvailable = (setup) => Boolean(setup?.recorded?.available);

/** How one tool answers under the card's setup. */
export function effectiveMode(setup, name) {
  const mode = setup?.mode ?? "mock";
  const pick =
    mode === "per_tool" ? setup?.perTool?.[name] ?? "mock" : mode ?? "mock";
  if (pick === "recorded" && !recordedAvailable(setup)) return "mock";
  return TOOL_MODES[pick] ? pick : "mock";
}

/** `all Mock` · `all Recorded` · `4 Mock · 1 Recorded · 1 Live`. */
export function modeSummary(setup, tools) {
  const names = (tools ?? []).map((t) => t.name);
  if (!names.length) return "none";
  const counts = {};
  names.forEach((n) => {
    const m = effectiveMode(setup, n);
    counts[m] = (counts[m] ?? 0) + 1;
  });
  const keys = Object.keys(counts);
  if (keys.length === 1) return `all ${TOOL_MODES[keys[0]].label}`;
  return ["mock", "recorded", "live"]
    .filter((m) => counts[m])
    .map((m) => `${counts[m]} ${TOOL_MODES[m].label}`)
    .join(" · ");
}

/** Live tools of a card that have no endpoint yet. */
export function liveToolsWithoutEndpoint(setup, tools) {
  return (tools ?? [])
    .filter((t) => effectiveMode(setup, t.name) === "live")
    .filter((t) => !isHttpUrl(setup?.endpoints?.[t.name]))
    .map((t) => t.name);
}

export function isHttpUrl(url) {
  try {
    const u = new URL(String(url ?? "").trim());
    return (u.protocol === "https:" || u.protocol === "http:") && !!u.host;
  } catch {
    return false;
  }
}

/** Blocking issues of the Tools block: `Live tool lookup_order has no endpoint`. */
export function toolIssues(card) {
  const setup = card?.toolSetup;
  if (!setup) return [];
  const tools = cardTools(setup, card?.configuration?.tools);
  const issues = liveToolsWithoutEndpoint(setup, tools).map(
    (n) => `Live tool ${n} has no endpoint`,
  );
  const turns = setup.maxToolTurns;
  if (
    tools.length &&
    turns !== undefined &&
    turns !== null &&
    turns !== "" &&
    !(
      Number.isInteger(Number(turns)) &&
      Number(turns) >= MAX_TOOL_TURNS_RANGE.min &&
      Number(turns) <= MAX_TOOL_TURNS_RANGE.max
    )
  )
    issues.push(
      `Max tool turns must be ${MAX_TOOL_TURNS_RANGE.min}–${MAX_TOOL_TURNS_RANGE.max}`,
    );
  return issues;
}

/**
 * Warnings (never block), when a tool answers from recordings and only
 * some rows have one: `24 rows have no recording; a call to issue_refund on
 * them fails the row`. Only a call to a Recorded tool on such a row follows
 * Unrecorded calls; a row that never calls one is not affected, so the line
 * names the tools and never says every such row fails.
 * @param {object} card
 * @param {{rowCount?: number|null}} [ctx]
 */
export function toolWarnings(card, { rowCount } = {}) {
  const setup = card?.toolSetup;
  if (!setup) return [];
  const tools = cardTools(setup, card?.configuration?.tools);
  const recorded = tools
    .filter((t) => effectiveMode(setup, t.name) === "recorded")
    .map((t) => t.name);
  if (!recorded.length || !Number.isFinite(Number(rowCount))) return [];
  const sessions = Math.max(0, Number(setup.recorded?.sessions ?? 0));
  const missing = Math.max(0, Number(rowCount) - sessions);
  if (!missing) return [];
  const which =
    recorded.length === 1
      ? recorded[0]
      : recorded.length === 2
        ? `${recorded[0]} or ${recorded[1]}`
        : `any of the ${recorded.length} Recorded tools`;
  // v2.1 (tools:recording-warning): the run engine's own rule
  // (toolFixtures.toolRecordingsOf): its recorded sessions answer the first
  // rows of the run in order, one session a row; the rest have none.
  const covered = sessions
    ? ` (the ${sessions.toLocaleString("en-US")} recorded ${sessions === 1 ? "session answers the first row" : "sessions answer the first rows"} in run order)`
    : "";
  return [
    `${missing.toLocaleString("en-US")} ${missing === 1 ? "row has" : "rows have"} no recording${covered}; a call to ${which} on ${missing === 1 ? "it" : "them"} ${outcomeOf(setup.onUnrecorded)}`,
  ];
}

/**
 * Create-body keys of a prompt entry (CONTRACT "Round 2"): `tools[{name,
 * schema, mode, endpoint}]` (fixtures are generated, not sent),
 * `max_tool_turns`, `on_unrecorded`. `{}` when the card has no tools.
 */
export function promptToolsPayload(card) {
  const setup = card?.toolSetup ?? {};
  const tools = cardTools(setup, card?.configuration?.tools);
  if (!tools.length) return {};
  return {
    tools: tools.map((t) => {
      const mode = effectiveMode(setup, t.name);
      return {
        name: t.name,
        schema: t.schema,
        mode,
        endpoint:
          mode === "live"
            ? String(setup.endpoints?.[t.name] ?? "").trim() || null
            : null,
      };
    }),
    max_tool_turns:
      Number(setup.maxToolTurns) > 0
        ? Number(setup.maxToolTurns)
        : DEFAULT_MAX_TOOL_TURNS,
    on_unrecorded: setup.onUnrecorded ?? "mock",
  };
}

/** `tools` of a run config (r2Shapes RunConfig): `[{name, schema_hash, mode}]`. */
export function configTools(card) {
  const setup = card?.toolSetup ?? {};
  return cardTools(setup, card?.configuration?.tools).map((t) => ({
    name: t.name,
    schema_hash: t.schema_hash ?? null,
    mode: effectiveMode(setup, t.name),
  }));
}

/** The match side of one fixture case: `order_id = "A-7781"` · `default`. */
export function fixtureMatchText(match) {
  if (match === "*" || match === null || match === undefined) return "default";
  if (typeof match === "string") return match;
  return Object.entries(match)
    .map(([k, v]) => `${k} = ${JSON.stringify(v)}`)
    .join(", ");
}

/** `66 auto fixtures (6 tools)`: every case, then how many tools hold them. */
export function fixtureCountText(fixtures) {
  const tools = (fixtures ?? []).length;
  const cases = (fixtures ?? []).reduce(
    (sum, f) => sum + (f?.cases?.length ?? 0),
    0,
  );
  return `${cases} auto ${cases === 1 ? "fixture" : "fixtures"} (${tools} ${tools === 1 ? "tool" : "tools"})`;
}

/** The answer side, compact JSON (`404 {...}` for error answers). */
export function fixtureResultText(result) {
  const code = result?.error?.code;
  const text = JSON.stringify(result ?? null);
  return code ? `${code} ${text}` : text;
}
