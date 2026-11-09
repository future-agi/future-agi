// Harness recipe model (R4 lane Rb MUST 1; options-truth L1 G3 / C4, C14).
// Pure: defaults from the task suite's Environment, the per-harness CLI
// mapping (permissions, MCP config, helper model, version pin, built-in
// tools, instructions file, collect), the command preview, the summary
// lines, the capture words and the permission widening notice. The UI
// (HarnessRecipe.jsx), the script generator (snippets/harness.js), the
// catalog (cliCommandFor) and the run config (harnessConfigOf) all read it,
// so a flag on screen, in the script and on the run is written once.
import { catalogEntry } from "../catalog";
import {
  CAPTURE_LABELS,
  PERMISSION_LABELS,
} from "src/sections/common/Runs/provenanceModel";
import {
  needsLocalConfirm,
  recipeWidens,
} from "src/sections/common/Connect/connectModel";
import { fmtCost } from "src/sections/common/format/fmt";
import { plural } from "src/sections/common/vocab";

export const PERMISSION_ORDER = Object.freeze([
  "read_only",
  "edit",
  "full_auto",
]);
export { PERMISSION_LABELS };

/**
 * How each permission level is applied, per harness: `args` go on the CLI,
 * `env` into its environment, `wrapper` = run_harness_and_upload.py
 * enforces the level (`--permissions read_only|edit|full_auto`: read only
 * runs the harness in a read-only copy of the workdir and discards writes;
 * edit runs it in a writable copy and diffs the changes back; full auto lets
 * it write and use the network).
 * Docs: the coding harness Code https://docs.anthropic.com/en/docs/claude-code/cli-reference
 * · the coding harness https://github.com/openai/codex/blob/main/docs/sandbox.md
 * · Gemini CLI https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/configuration.md
 * · Aider https://aider.chat/docs/config/options.html
 * · Goose GOOSE_MODE https://goose-docs.ai/docs/guides/goose-permissions
 * · mini-SWE-agent --yolo https://mini-swe-agent.com/latest/usage/mini/
 * · OpenHands 0.x headless (python -m openhands.core.main) https://github.com/OpenHands/OpenHands/blob/0.62.0/openhands/core/main.py
 * · OpenHands CLI 1.x headless https://docs.openhands.dev/openhands/usage/cli/headless
 */
const PERMISSIONS = {
  claude_code: {
    read_only: { args: ["--permission-mode", "plan"] },
    edit: { args: ["--permission-mode", "acceptEdits"] },
    full_auto: { args: ["--dangerously-skip-permissions"] },
  },
  codex_cli: {
    read_only: { args: ["--sandbox", "read-only"] },
    edit: { args: ["--sandbox", "workspace-write"] },
    full_auto: { args: ["--dangerously-bypass-approvals-and-sandbox"] },
  },
  gemini_cli: {
    read_only: { args: ["--approval-mode", "default"] },
    edit: { args: ["--approval-mode", "auto_edit"] },
    full_auto: { args: ["--yolo"] },
  },
  aider: {
    read_only: { args: ["--dry-run"] },
    edit: { args: ["--yes-always"] },
    full_auto: { args: ["--yes-always", "--auto-test"] },
  },
  goose: {
    read_only: { env: { GOOSE_MODE: "chat" } },
    edit: { env: { GOOSE_MODE: "smart_approve" } },
    full_auto: { env: { GOOSE_MODE: "auto" } },
  },
  mini_swe_agent: {
    // Without --yolo mini asks before each command; the wrapper closes
    // stdin, so nothing runs unasked and writes land in a read-only copy.
    // --exit-immediately (always passed) skips its confirm-on-exit prompt.
    read_only: { args: [], wrapper: true },
    edit: { args: ["--yolo"], wrapper: true },
    full_auto: { args: ["--yolo"], wrapper: true },
  },
  openhands: {
    read_only: { wrapper: true },
    edit: { wrapper: true },
    full_auto: { wrapper: true },
  },
  custom_command: {
    read_only: { wrapper: true },
    edit: { wrapper: true },
    full_auto: { wrapper: true },
  },
};

/** How the wrapper enforces a level (the row hint and the script comment). */
export const WRAPPER_WORDS = Object.freeze({
  read_only:
    "Enforced by the wrapper: a read-only copy of the workdir; writes are discarded",
  edit: "Enforced by the wrapper: a writable copy of the workdir; the diff comes back",
  full_auto:
    "Enforced by the wrapper: a writable copy with network; the diff comes back",
});

/** `{args, env, wrapper}` of one harness and level. */
export function permissionOf(entryId, permission = "edit") {
  const table = PERMISSIONS[entryId] ?? PERMISSIONS.custom_command;
  const p = table[permission] ?? table.edit;
  return { args: p.args ?? [], env: p.env ?? {}, wrapper: Boolean(p.wrapper) };
}

/**
 * Per-harness facts the recipe needs. `stream` = the transcript format the
 * runner script parses (null = no transcript: final message only).
 * `cliName` is the harness's short CLI name. `mcp` = where the
 * harness reads MCP servers (null = no MCP support). `instructionsAt` =
 * the file the harness reads its instructions from (`--read` for Aider,
 * `task` = the wrapper puts the file before the task). `install` = how the
 * version pins the install line (`npm` / `pip` package, `goose` script).
 */
export const HARNESS_PROFILES = Object.freeze({
  claude_code: {
    cliName: "claude-code",
    bin: "claude",
    version: "2.x",
    models: ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
    subCall: { model: "claude-haiku-4-5", env: "ANTHROPIC_SMALL_FAST_MODEL" },
    instructions: "CLAUDE.md",
    instructionsAt: "CLAUDE.md",
    stream: "stream-json",
    keyEnv: "ANTHROPIC_API_KEY",
    baseUrlEnv: "ANTHROPIC_BASE_URL",
    mcp: "claude",
    install: { npm: "@anthropic-ai/claude-code" },
  },
  codex_cli: {
    cliName: "codex",
    bin: "codex",
    version: "0.x",
    models: ["gpt-5-codex", "gpt-5", "gpt-4.1-mini"],
    subCall: null,
    instructions: "AGENTS.md",
    instructionsAt: "AGENTS.md",
    stream: "json",
    keyEnv: "OPENAI_API_KEY",
    baseUrlEnv: "OPENAI_BASE_URL",
    mcp: "codex",
    install: { npm: "@openai/codex" },
  },
  gemini_cli: {
    cliName: "gemini",
    bin: "gemini",
    version: "0.x",
    models: ["gemini-2.5-pro", "gemini-2.5-flash"],
    subCall: null,
    instructions: "GEMINI.md",
    instructionsAt: "GEMINI.md",
    stream: "stream-json",
    keyEnv: "GEMINI_API_KEY",
    baseUrlEnv: "GOOGLE_GEMINI_BASE_URL",
    mcp: "gemini",
    install: { npm: "@google/gemini-cli" },
  },
  openhands: {
    cliName: "openhands",
    bin: "python",
    version: "0.x",
    models: ["claude-sonnet-4-5", "gpt-5-codex", "gemini-2.5-pro"],
    subCall: null,
    instructions: "AGENTS.md",
    instructionsAt: ".openhands/microagents/repo.md",
    stream: "trajectory",
    keyEnv: "LLM_API_KEY",
    baseUrlEnv: "LLM_BASE_URL",
    mcp: "openhands",
    install: { pip: "openhands-ai" },
  },
  aider: {
    cliName: "aider",
    bin: "aider",
    version: "0.x",
    models: ["gpt-4.1", "claude-sonnet-4-5", "gemini-2.5-pro"],
    subCall: null,
    instructions: "CONVENTIONS.md",
    instructionsAt: "--read",
    stream: "llm-history",
    keyEnv: "OPENAI_API_KEY",
    baseUrlEnv: "OPENAI_API_BASE",
    mcp: null,
    install: { pip: "aider-chat" },
  },
  goose: {
    cliName: "goose",
    bin: "goose",
    version: "1.x",
    models: ["claude-sonnet-4-5", "gpt-4.1"],
    subCall: null,
    instructions: ".goosehints",
    instructionsAt: ".goosehints",
    stream: "stream-json",
    keyEnv: "ANTHROPIC_API_KEY",
    baseUrlEnv: "ANTHROPIC_HOST",
    mcp: "goose",
    install: { goose: true },
  },
  mini_swe_agent: {
    cliName: "mini-swe-agent",
    bin: "mini",
    version: "1.x",
    models: ["claude-sonnet-4-5", "gpt-5"],
    subCall: null,
    instructions: "AGENTS.md",
    instructionsAt: "task",
    stream: "trajectory",
    keyEnv: "ANTHROPIC_API_KEY",
    baseUrlEnv: "ANTHROPIC_BASE_URL",
    mcp: null,
    install: { pip: "mini-swe-agent" },
  },
  custom_command: {
    cliName: "custom",
    bin: "your-agent",
    version: "",
    models: ["claude-sonnet-4-5", "gpt-5-codex", "gemini-2.5-pro"],
    subCall: null,
    instructions: "AGENTS.md",
    instructionsAt: "env",
    stream: "trace-file",
    keyEnv: "ANTHROPIC_API_KEY",
    baseUrlEnv: "ANTHROPIC_BASE_URL",
    mcp: "custom",
    install: null,
  },
});

export const profileOf = (entryOrId) => {
  const id = typeof entryOrId === "string" ? entryOrId : entryOrId?.id;
  return HARNESS_PROFILES[id] ?? HARNESS_PROFILES.custom_command;
};

/**
 * The models a recipe's Model select offers: the harness's list, plus the
 * recipe's own model when a copied run uses one the list lacks (so the
 * select is never blank and the copy keeps its model).
 */
export function modelOptions(recipe, entryOrId) {
  const list = [...profileOf(entryOrId ?? recipe?.harness).models];
  const m = recipe?.model;
  return m && !list.includes(m) ? [...list, m] : list;
}

/** Does this harness read MCP servers? (Aider and mini-SWE-agent do not.) */
export const supportsMcp = (entryOrId) => Boolean(profileOf(entryOrId).mcp);

/** The info line in place of the MCP row for a harness without MCP. */
export function noMcpLine(entryOrId) {
  const id = typeof entryOrId === "string" ? entryOrId : entryOrId?.id;
  const name = catalogEntry(id)?.name ?? "This harness";
  return `${name} has no MCP support: it edits files and runs commands itself`;
}

/** Where a harness reads MCP servers, in words (the row hint + preview note). */
export const MCP_CONFIG_WORDS = Object.freeze({
  claude: ".fi/mcp.json, passed with --mcp-config",
  codex: "CODEX_HOME=.fi/codex · config.toml [mcp_servers.<name>]",
  gemini: ".gemini/settings.json · mcpServers",
  openhands: ".fi/openhands.toml · [mcp] stdio_servers",
  goose: "one --with-extension per server",
  custom: "FI_MCP_CONFIG=.fi/mcp.json in your command's environment",
});

/** Built-in tools (D: provider-side tools are off by default, Inspect §4). */
export const BUILTIN_TOOLS = Object.freeze([
  { id: "shell", label: "Shell", default: true },
  { id: "edit", label: "Edit files", default: true },
  { id: "web_fetch", label: "Web fetch", default: false },
  { id: "web_search", label: "Web search", default: false },
]);
export const PROVIDER_TOOLS_OFF =
  "Off by default: provider-side tools skip the sandbox's network policy.";

/** Built-in tool → the coding harness Code tool names (`--allowedTools`). */
const CLAUDE_TOOLS = {
  shell: ["Bash"],
  edit: ["Edit", "Write"],
  web_fetch: ["WebFetch"],
  web_search: ["WebSearch"],
};
/** Built-in tool → Gemini CLI core tools (`.gemini/settings.json` coreTools). */
const GEMINI_TOOLS = {
  shell: ["run_shell_command"],
  edit: ["replace", "write_file"],
  web_fetch: ["web_fetch"],
  web_search: ["google_web_search"],
};
const GEMINI_READ_TOOLS = ["read_file", "list_directory", "glob"];

/** How the built-in tools reach the harness, in words (the row hint). */
export function builtinToolsWords(entryId) {
  if (entryId === "claude_code")
    return "Passed as --allowedTools / --disallowedTools";
  if (entryId === "gemini_cli")
    return "Written to .gemini/settings.json coreTools";
  return "Passed to the harness as FI_ALLOWED_TOOLS (the wrapper's allowlist)";
}

/** `id` of a collect choice → its C4 name. */
export const COLLECT_OPTIONS = Object.freeze([
  { id: "git_diff", label: "Git diff", default: true, c4: "git_diff" },
  {
    id: "final_message",
    label: "Final message",
    default: true,
    c4: "final_message",
  },
  { id: "terminal_log", label: "Terminal log", default: false, c4: "log" },
]);

/** The flags label: `Flags from the Gemini CLI docs (headless mode)`. */
export function flagsLabel(entryId) {
  const name = catalogEntry(entryId)?.name ?? "harness";
  if (entryId === "custom_command") return "Your command, as typed";
  return `Flags from the ${name} docs (headless mode)`;
}
export const CUSTOM_COMMAND_HELP =
  "Any CLI that takes a task and works in a folder. To record its steps, write a trace to $FI_TRACE_FILE (ATIF, a mini-SWE-agent trajectory or OpenAI chat messages); $TRACEPARENT nests its own OpenTelemetry spans under the try";
/** Where a custom command writes its steps (FI_TRACE_FILE, in the try folder). */
export const CUSTOM_TRACE_FILE = ".fi/trace.json";
/** Is this the OpenHands CLI 1.x (`openhands --headless --json`)? Else 0.x. */
export const isOpenHandsV1 = (recipe) =>
  recipe?.harness === "openhands" &&
  /^\s*v?1\b/.test(String(recipe?.version ?? ""));
export const API_KEY_NOTE =
  "Needs an API key from Workspace secrets: a subscription login can't be redirected through the gateway.";

/** The API key env a model needs, by vendor (OpenHands reads LLM_API_KEY). */
export function keyEnvOf(model, entryId) {
  if (entryId === "openhands") return "LLM_API_KEY";
  const m = String(model ?? "");
  if (/^claude/.test(m)) return "ANTHROPIC_API_KEY";
  if (/^gemini/.test(m)) return "GEMINI_API_KEY";
  if (/^(gpt|o\d|codex)/.test(m)) return "OPENAI_API_KEY";
  return profileOf(entryId).keyEnv;
}

/**
 * The MCP server a task suite's Environment provides (the sandbox's tool
 * server; the server is named after the image without `-sandbox`, like the
 * seeded #3E recipe). Null without an Environment MCP.
 */
export function environmentMcp(environment) {
  const m = environment?.mcp;
  if (!m?.name) return null;
  return {
    name: String(m.name).replace(/-sandbox$/, ""),
    transport: m.url ? "http" : "stdio",
    command: m.command ?? null,
    url: m.url ?? null,
    tools: Number(m.tools) || (m.tool_names ?? []).length || 0,
    tool_names: [...(m.tool_names ?? [])],
    env: [],
    from: "environment",
  };
}

/**
 * A recipe with the Environment's MCP server merged in (pure): the
 * environment may load after the first edit, and its server is never
 * dropped (it cannot be removed on screen). Unchanged when the harness
 * reads no MCP or the server is already there.
 */
export function withEnvironmentMcp(recipe, environment) {
  const env = environmentMcp(environment);
  if (!recipe || !env || !profileOf(recipe.harness).mcp) return recipe;
  const list = recipe.mcp_servers ?? [];
  const same = list.find((s) => s.name === env.name);
  if (!same) return { ...recipe, mcp_servers: [env, ...list] };
  // A copied run names the Environment's server with only a count: it is
  // that server, so it lists the Environment's tools (never invented ones)
  // and keeps the copy's unticked tools.
  if (same.tool_names?.length || !env.tool_names.length) return recipe;
  return {
    ...recipe,
    mcp_servers: list.map((s) =>
      s === same
        ? {
            ...env,
            ...s,
            tools: env.tool_names.length,
            tool_names: [...env.tool_names],
            disabled_tools: (s.disabled_tools ?? []).filter((t) =>
              env.tool_names.includes(t),
            ),
            from: "environment",
          }
        : s,
    ),
  };
}

/**
 * The run name after a model change (pure): a name that was the automatic
 * `<Harness> × <old model>` follows the model; any other name (typed by
 * the user, or one without a model) stays. Null when it does not change.
 */
export function nameAfterModelChange(name, harnessName, prevModel, nextModel) {
  if (!harnessName || !prevModel || !nextModel || prevModel === nextModel)
    return null;
  return String(name ?? "").trim() === `${harnessName} × ${prevModel}`
    ? `${harnessName} × ${nextModel}`
    : null;
}

/**
 * The default recipe of a harness (defaults from the task suite's
 * Environment). Every field the UI edits lives here. The model is the
 * harness's first listed model; harnesses without MCP get no servers.
 * @param {object} entry   catalog entry (kind harness)
 * @param {object} [environment]  dataset.environment (task suites)
 */
export function defaultRecipe(entry, environment) {
  const p = profileOf(entry);
  const mcp = p.mcp ? environmentMcp(environment) : null;
  const budget = environment?.resources ?? {};
  return {
    harness: entry?.id ?? "custom_command",
    version: p.version,
    model: p.models[0],
    subCallModel: null,
    mcp_servers: mcp ? [mcp] : [],
    permissions: "edit",
    builtin_tools: Object.fromEntries(
      BUILTIN_TOOLS.map((t) => [t.id, t.default]),
    ),
    instructions: p.instructions,
    budget: {
      max_turns: 40,
      max_cost_usd: 1,
      timeout_s: Number(budget.timeout_s) || 600,
    },
    collect: Object.fromEntries(COLLECT_OPTIONS.map((c) => [c.id, c.default])),
    extra_files: [],
    command: entry?.id === "custom_command" ? "your-agent --task {task}" : null,
    // custom command: does it write its steps to $FI_TRACE_FILE?
    trace_file: false,
  };
}

/** The flag (or env, or wrapper) a permission maps to: `{flag, approximate, wrapper}`. */
export function permissionFlag(entryId, permission) {
  const p = permissionOf(entryId, permission);
  const env = Object.entries(p.env).map(([k, v]) => `${k}=${v}`);
  const parts = [...env, ...p.args];
  if (p.wrapper) parts.push(`--permissions ${permission ?? "edit"}`);
  return {
    flag: parts.join(" ") || "(no flag)",
    approximate: !["claude_code", "codex_cli"].includes(entryId),
    wrapper: p.wrapper,
  };
}

/** The row hint: how the level is enforced for this harness. */
export function permissionHint(entryId, permission) {
  const p = permissionOf(entryId, permission);
  if (p.wrapper) {
    const flag = p.args.length ? ` · ${p.args.join(" ")} on the CLI` : "";
    return `${WRAPPER_WORDS[permission] ?? WRAPPER_WORDS.edit}${flag}`;
  }
  const env = Object.entries(p.env).map(([k, v]) => `${k}=${v}`);
  if (env.length)
    return `Set in the harness's environment: ${env.join(" ")} (${flagsLabel(
      entryId,
    )
      .replace(/^Flags from the /, "")
      .replace(/ docs \(headless mode\)$/, "")} docs)`;
  return flagsLabel(entryId);
}

/** `Edit files → --permission-mode acceptEdits` (tooltip text). */
export function permissionTooltip(entryId, permission) {
  const { flag } = permissionFlag(entryId, permission);
  return `${PERMISSION_LABELS[permission]} → ${flag}`;
}

const shQuote = (s) => `"${String(s).replace(/(["\\$`])/g, "\\$1")}"`;

/**
 * `2.x` → npm `@2` / pip `==2.*`; `2.1` → `@2.1` / `==2.1.*`; `1.0.72` →
 * `@1.0.72` / `==1.0.72`; else no pin.
 */
export function versionPin(version) {
  const v = String(version ?? "")
    .trim()
    .replace(/^v/, "");
  const major = /^(\d+)\.x$/.exec(v);
  if (major)
    return { npm: `@${major[1]}`, pip: `==${major[1]}.*`, exact: null };
  const minor = /^(\d+\.\d+)(\.x)?$/.exec(v);
  if (minor)
    return { npm: `@${minor[1]}`, pip: `==${minor[1]}.*`, exact: null };
  if (/^\d+\.\d+\.\d+([-.][\w.]+)?$/.test(v))
    return { npm: `@${v}`, pip: `==${v}`, exact: v };
  return { npm: "", pip: "", exact: null };
}

/**
 * The install line of a harness at the recipe's version (null for a
 * custom command). Goose's script takes an exact version only
 * (GOOSE_VERSION=v1.2.3); a major like `1.x` installs the stable release.
 */
export function installLine(recipe) {
  const r = asRecipe(recipe);
  const p = profileOf(r.harness);
  const pin = versionPin(r.version);
  if (!p.install) return null;
  if (p.install.npm) return `npm i -g ${p.install.npm}${pin.npm}`;
  // OpenHands 1.x is the `openhands` CLI package; 0.x is `openhands-ai`.
  if (isOpenHandsV1(r)) return `pip install "openhands${pin.pip}"`;
  if (p.install.pip) return `pip install "${p.install.pip}${pin.pip}"`;
  const env = pin.exact ? `GOOSE_VERSION=v${pin.exact} ` : "";
  return `curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | ${env}CONFIGURE=false bash`;
}

/** The note under the version field (Goose pins exact versions only). */
export function versionNote(recipe) {
  const r = asRecipe(recipe);
  if (r.harness === "goose" && !versionPin(r.version).exact)
    return "Goose installs an exact version (e.g. 1.55.0); this installs the latest stable release";
  if (r.harness === "custom_command") return "Recorded on the run";
  return null;
}

/** Checked built-in tools as ids. */
const toolsOn = (r) =>
  BUILTIN_TOOLS.filter((t) => r.builtin_tools?.[t.id]).map((t) => t.id);

/** The Gemini CLI coreTools allowlist of a recipe. */
export function geminiCoreTools(recipe) {
  const r = asRecipe(recipe);
  return [...GEMINI_READ_TOOLS, ...toolsOn(r).flatMap((t) => GEMINI_TOOLS[t])];
}

/** `{allowed, disallowed}` the coding harness Code tool names of a recipe (MCP servers allowed). */
export function claudeToolLists(recipe) {
  const r = asRecipe(recipe);
  const on = new Set(toolsOn(r));
  // L3 G6 (fix 12): a server with unticked tools allows only its ticked
  // ones (`mcp__<server>__<tool>`) and denies the rest, so the command runs
  // the same subset the run calls; a whole server stays `mcp__<server>`.
  const servers = (r.mcp_servers ?? []).map((s) => {
    const off = new Set(s.disabled_tools ?? []);
    const names = s.tool_names ?? [];
    const subset = off.size > 0 && names.length > 0;
    return {
      allowed: subset
        ? names.filter((t) => !off.has(t)).map((t) => `mcp__${s.name}__${t}`)
        : [`mcp__${s.name}`],
      denied: subset
        ? names.filter((t) => off.has(t)).map((t) => `mcp__${s.name}__${t}`)
        : [],
    };
  });
  const allowed = [
    ...BUILTIN_TOOLS.filter((t) => on.has(t.id)).flatMap(
      (t) => CLAUDE_TOOLS[t.id],
    ),
    ...servers.flatMap((s) => s.allowed),
  ];
  const disallowed = [
    ...BUILTIN_TOOLS.filter((t) => !on.has(t.id)).flatMap(
      (t) => CLAUDE_TOOLS[t.id],
    ),
    ...servers.flatMap((s) => s.denied),
  ];
  return { allowed, disallowed };
}

/** The env the harness process gets from the recipe (preview prefix + script). */
export function harnessEnv(recipe) {
  const r = asRecipe(recipe);
  const id = r.harness;
  const p = profileOf(id);
  const perm = permissionOf(id, r.permissions);
  const env = { ...perm.env };
  if (perm.wrapper) env.FI_PERMISSIONS = r.permissions;
  if (p.subCall && r.subCallModel) env[p.subCall.env] = r.subCallModel;
  const mcp = p.mcp ? r.mcp_servers ?? [] : [];
  if (mcp.length && p.mcp === "codex") env.CODEX_HOME = ".fi/codex";
  if (mcp.length && p.mcp === "custom") env.FI_MCP_CONFIG = ".fi/mcp.json";
  // Gemini CLI drops workspace settings (MCP, coreTools) and the approval
  // mode in an untrusted folder; each try's fresh folder is trusted.
  if (id === "gemini_cli") env.GEMINI_CLI_TRUST_WORKSPACE = "true";
  if (id === "openhands") env.LLM_MODEL = r.model;
  if (isOpenHandsV1(r)) env.OPENHANDS_PERSISTENCE_DIR = ".fi/openhands";
  if (id === "custom_command") {
    env.FI_MODEL = r.model;
    env.FI_TRACE_FILE = CUSTOM_TRACE_FILE;
  }
  if (!["claude_code", "gemini_cli"].includes(id))
    env.FI_ALLOWED_TOOLS = toolsOn(r).join(",") || "none";
  if (p.instructionsAt === "env" && r.instructions)
    env.FI_INSTRUCTIONS_FILE = r.instructions;
  return env;
}

/** A recipe's turn limit (Max turns). */
export const turnsOf = (recipe) =>
  Number(asRecipe(recipe)?.budget?.max_turns) || 40;

/**
 * Where Gemini CLI reads its turn limit in .gemini/settings.json: the
 * top-level `maxSessionTurns` in 0.x, `model.maxSessionTurns` from 1.0.
 */
export function geminiTurnsKey(recipe) {
  const v = String(asRecipe(recipe)?.version ?? "").replace(/^v/, "");
  const major = Number(/^(\d+)/.exec(v)?.[1]);
  return Number.isFinite(major) && major >= 1
    ? "model.maxSessionTurns"
    : "maxSessionTurns";
}

/**
 * How a harness applies Max turns (a fact line under the field), or null
 * when the CLI has no turn limit (Aider answers one message per try).
 */
export function turnLimitWords(recipe) {
  const r = asRecipe(recipe);
  const n = turnsOf(r);
  switch (r.harness) {
    case "claude_code":
    case "goose":
      return `--max-turns ${n} on the command line`;
    case "gemini_cli":
      return `${geminiTurnsKey(r)}: ${n} in .gemini/settings.json`;
    case "codex_cli":
      return `The runner counts codex's steps (commands, MCP calls, edits) and stops it (SIGINT) at ${n}`;
    case "openhands":
      return isOpenHandsV1(r)
        ? `MAX_ITERATIONS=${n} in its environment (--override-with-envs)`
        : `max_iterations = ${n} in .fi/openhands.toml`;
    case "mini_swe_agent":
      return `-c agent.step_limit=${n} on the command line`;
    case "custom_command":
      return `FI_MAX_TURNS=${n} in the command's environment`;
    default:
      return null;
  }
}

/** Aider's CLI takes one message per try and has no turn limit. */
export const NO_TURN_LIMIT =
  "Aider answers one message per try; it has no turn limit";

/** The files the script writes in each fresh folder, in words. */
export function configFiles(recipe) {
  const r = asRecipe(recipe);
  const p = profileOf(r.harness);
  const servers = p.mcp ? r.mcp_servers ?? [] : [];
  const names = servers.map((s) => s.name).join(", ");
  const out = [];
  if (servers.length && p.mcp === "claude")
    out.push({ path: ".fi/mcp.json", what: `mcpServers: ${names}` });
  if (servers.length && p.mcp === "custom")
    out.push({ path: ".fi/mcp.json", what: `mcpServers: ${names}` });
  if (servers.length && p.mcp === "codex")
    out.push({ path: ".fi/codex/config.toml", what: `[mcp_servers] ${names}` });
  if (p.mcp === "gemini")
    out.push({
      path: ".gemini/settings.json",
      what: [
        servers.length ? `mcpServers: ${names}` : null,
        "coreTools",
        `${geminiTurnsKey(r)} ${turnsOf(r)}`,
      ]
        .filter(Boolean)
        .join(" · "),
    });
  if (r.harness === "openhands" && !isOpenHandsV1(r))
    out.push({
      path: ".fi/openhands.toml",
      what: [
        "[core] max_iterations, runtime local (this folder), save_trajectory_path, max_budget_per_task",
        servers.length ? `[mcp] stdio_servers: ${names}` : null,
      ]
        .filter(Boolean)
        .join(" · "),
    });
  const at = p.instructionsAt;
  if (
    r.instructions &&
    at &&
    !["--read", "task", "env"].includes(at) &&
    at !== r.instructions
  )
    out.push({ path: at, what: `copied from ${r.instructions}` });
  return out;
}

/** `argv` of one MCP server's command (`npx -y pkg` → `["npx","-y","pkg"]`). */
export const mcpArgv = (s) =>
  String(s?.command || s?.name || "")
    .split(/\s+/)
    .filter(Boolean);

/**
 * The command the runner starts per try, with `"<task>"` in place of the
 * task text, plus the env it sets (`env`, shown as a prefix in `text`).
 * Flags come from each harness's docs (headless mode).
 * @returns {{text: string, argv: string[], env: object, files: object[],
 *   approximate: boolean, label: string}}
 */
export function commandPreview(
  recipeIn,
  { task = "<task>", mcpConfig = ".fi/mcp.json" } = {},
) {
  const recipe = asRecipe(recipeIn);
  const id = recipe?.harness;
  const entry = catalogEntry(id);
  const p = profileOf(id);
  const perm = permissionOf(id, recipe?.permissions ?? "edit");
  const turns = recipe?.budget?.max_turns ?? 40;
  const servers = p.mcp ? recipe?.mcp_servers ?? [] : [];
  const mcp = servers.length > 0;
  let argv;
  if (id === "claude_code") {
    const tools = claudeToolLists(recipe);
    argv = [
      "claude",
      "-p",
      task,
      "--model",
      recipe.model,
      "--output-format",
      "stream-json",
      "--verbose",
      ...perm.args,
      ...(mcp ? ["--mcp-config", mcpConfig] : []),
      ...(tools.allowed.length
        ? ["--allowedTools", tools.allowed.join(",")]
        : []),
      ...(tools.disallowed.length
        ? ["--disallowedTools", tools.disallowed.join(",")]
        : []),
      "--max-turns",
      String(turns),
    ];
  } else if (id === "codex_cli") {
    argv = [
      "codex",
      "exec",
      "--json",
      "--model",
      recipe.model,
      ...perm.args,
      task,
    ];
  } else if (id === "gemini_cli") {
    argv = [
      "gemini",
      "-p",
      task,
      "--model",
      recipe.model,
      "--output-format",
      "stream-json",
      ...perm.args,
    ];
  } else if (id === "openhands" && isOpenHandsV1(recipe)) {
    argv = [
      "openhands",
      "--headless",
      "--json",
      "--override-with-envs",
      "-t",
      task,
    ];
  } else if (id === "openhands") {
    argv = [
      "python",
      "-m",
      "openhands.core.main",
      "--config-file",
      ".fi/openhands.toml",
      "-t",
      task,
    ];
  } else if (id === "aider") {
    argv = [
      "aider",
      "--message",
      task,
      "--model",
      recipe.model,
      ...perm.args,
      "--no-auto-commits",
      ...(recipe.instructions ? ["--read", recipe.instructions] : []),
      // its record: each model call, its tokens and cost, each edit; the
      // history files stay out of the try's git diff
      "--llm-history-file",
      ".fi/aider.llm.history",
      "--analytics-log",
      ".fi/aider.events.jsonl",
      "--no-analytics",
      "--chat-history-file",
      ".fi/aider.chat.history.md",
      "--input-history-file",
      ".fi/aider.input.history",
      "--no-gitignore",
    ];
  } else if (id === "goose") {
    argv = [
      "goose",
      "run",
      "-t",
      task,
      "--model",
      recipe.model,
      "--output-format",
      "stream-json",
      "--max-turns",
      String(turns),
      ...servers.flatMap((s) => ["--with-extension", mcpArgv(s).join(" ")]),
    ];
  } else if (id === "mini_swe_agent") {
    const cap = Number(recipe?.budget?.max_cost_usd);
    argv = [
      "mini",
      "-t",
      task,
      "--model",
      recipe.model,
      ...perm.args,
      "-o",
      ".fi/mini.traj.json",
      "--exit-immediately",
      // its turn limit: the agent stops after N steps
      "-c",
      `agent.step_limit=${turns}`,
      ...(Number.isFinite(cap) && cap > 0 ? ["-l", String(cap)] : []),
    ];
  } else {
    const cmd = String(recipe?.command || "your-agent --task {task}");
    argv = cmd.split(/\s+/).map((part) => (part === "{task}" ? task : part));
  }
  const env = harnessEnv(recipe);
  const quote = (a) => (a === task || /[\s,]/.test(a) ? shQuote(a) : a);
  const prefix = Object.entries(env).map(
    ([k, v]) => `${k}=${/[\s,]/.test(v) ? shQuote(v) : v}`,
  );
  const text = [...prefix, ...argv.map(quote)].join(" ");
  return {
    text,
    argv,
    env,
    files: configFiles(recipe),
    approximate: !["claude_code", "codex_cli"].includes(id),
    label: flagsLabel(id),
    wrapper: perm.wrapper,
    entryName: entry?.name ?? null,
  };
}

/** C14: the catalog tile's command = the default recipe's preview. */
export function cliCommandFor(entryId) {
  const entry = catalogEntry(entryId);
  return commandPreview(defaultRecipe(entry ?? { id: entryId }, null)).text;
}

/** C4 collect names of a recipe (`git_diff`, `final_message`, `log`, `files`). */
export function collectOf(recipe) {
  const r = asRecipe(recipe);
  const out = COLLECT_OPTIONS.filter((c) => r.collect?.[c.id]).map((c) => c.c4);
  if ((r.extra_files ?? []).length) out.push("files");
  return out;
}

/** The `More settings` summary line: `v2.x · Shell, Edit files · CLAUDE.md · 40 turns · $1.00 per try · Git diff + final message`. */
export function moreSettingsSummary(recipeIn) {
  const recipe = asRecipe(recipeIn);
  const tools = BUILTIN_TOOLS.filter((t) => recipe?.builtin_tools?.[t.id]).map(
    (t) => t.label,
  );
  const b = recipe?.budget ?? {};
  const collect = COLLECT_OPTIONS.filter((c) => recipe?.collect?.[c.id]).map(
    (c, i) => (i === 0 ? c.label : c.label.toLowerCase()),
  );
  const extra = (recipe?.extra_files ?? []).length;
  return [
    recipe?.version ? `v${String(recipe.version).replace(/^v/, "")}` : null,
    tools.length ? tools.join(", ") : "No built-in tools",
    recipe?.instructions || "No instructions file",
    b.max_turns && recipe?.harness !== "aider"
      ? plural(b.max_turns, "turn")
      : null,
    b.max_cost_usd != null ? `${fmtCost(b.max_cost_usd)} per try` : null,
    collect.length || extra
      ? `${collect.join(" + ")}${extra ? `${collect.length ? " + " : ""}${plural(extra, "file")}` : ""}`
      : "Nothing collected",
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * What a run of this harness captures (§C9). `gateway` = when Future AGI
 * starts the try (Connect live, sandbox, CI): model calls through a per-try
 * gateway URL, plus the transcript where the CLI has one. `upload` = what
 * the Trace + upload script reads from the CLI's own output.
 */
export function captureOf(entryOrId) {
  const entry =
    typeof entryOrId === "string" ? catalogEntry(entryOrId) : entryOrId;
  const full = entry?.capture?.steps === "transcript";
  const upload = entry?.capture_upload === "transcript";
  const record = recordOfEntry(entry);
  return {
    gateway: {
      key: full ? "transcript" : "gateway",
      label: full ? CAPTURE_LABELS.transcript : CAPTURE_LABELS.gateway,
      detail: RECORD_SENTENCES[record],
    },
    upload: {
      key: upload ? "transcript" : "final_only",
      label: upload ? CAPTURE_LABELS.transcript : CAPTURE_LABELS.final_only,
      detail: RECORD_SENTENCES[record],
    },
  };
}

/**
 * Does this recipe need a confirmation on the machine (R4-SPEC D21)?
 * Widening = `Full auto`, a new MCP server, or a custom command; the owner
 * confirms unless it is your machine and it runs with
 * `--allow-recipe-changes`.
 * @param {object} recipe  the edited recipe
 * @param {object} base    the recipe the worker declared (or the defaults)
 * @param {object|null} machine  ConnectedMachine
 * @param {{userId?: string}} [o]
 * @returns {{confirm: boolean, reasons: string[], line: string|null}}
 */
export function wideningNotice(recipe, base, machine, { userId } = {}) {
  if (!machine) return { confirm: false, reasons: [], line: null };
  const widening = recipeWidens(
    {
      permissions: recipe?.permissions,
      mcp_servers: recipe?.mcp_servers,
      command: recipe?.harness === "custom_command" ? recipe?.command : null,
    },
    {
      permissions: base?.permissions,
      mcp_servers: base?.mcp_servers,
      command: base?.command ?? null,
    },
  );
  const custom = recipe?.harness === "custom_command";
  const reasons = [...widening.reasons];
  if (custom && !reasons.includes("custom_command"))
    reasons.push("custom_command");
  const isOwner =
    machine.you === true || (userId != null && machine.owner?.id === userId);
  const widens = reasons.length > 0;
  const confirm =
    widens && (!isOwner || needsLocalConfirm(machine, { widens, reasons }));
  const owner = machine.owner?.name ?? "The owner";
  return {
    confirm,
    reasons,
    line: confirm
      ? `${isOwner ? "You" : owner} will be asked to confirm on ${machine.name} before the run starts.`
      : null,
  };
}

const REASON_TEXT = {
  full_auto: "Full auto lets the harness run any command in its folder",
  permissions: "wider permissions",
  mcp_server: "a new MCP server",
  custom_command: "a custom command",
};
export const wideningReasonText = (reasons) =>
  (reasons ?? []).map((r) => REASON_TEXT[r] ?? r).join(" · ");

/** Fields that differ from `base` (the compact Rerun view): `[{field, from, to}]`. */
export function changedFields(recipe, base) {
  const out = [];
  const add = (field, from, to) => {
    if (String(from ?? "") !== String(to ?? "")) out.push({ field, from, to });
  };
  add("Model", base?.model, recipe?.model);
  add(
    "Permissions",
    PERMISSION_LABELS[base?.permissions] ?? base?.permissions,
    PERMISSION_LABELS[recipe?.permissions] ?? recipe?.permissions,
  );
  add("Max turns", base?.budget?.max_turns, recipe?.budget?.max_turns);
  add(
    "MCP servers",
    (base?.mcp_servers ?? []).map((s) => s.name).join(", ") || "none",
    (recipe?.mcp_servers ?? []).map((s) => s.name).join(", ") || "none",
  );
  return out;
}

/**
 * Any harness value → the editable recipe shape: a recipe passes through; a
 * C4 run config (`config.harness`, `slot.harness`) is converted back (its
 * `builtin_tools` / `collect` are name lists, MCP `tools` a name list).
 */
export function asRecipe(value, entry) {
  if (!value || typeof value !== "object") return defaultRecipe(entry, null);
  const c4 =
    Array.isArray(value.builtin_tools) ||
    Array.isArray(value.collect) ||
    (value.id && !value.harness);
  if (!c4) return value;
  const id = value.harness ?? value.id ?? entry?.id;
  const base = defaultRecipe(catalogEntry(id) ?? entry ?? { id }, null);
  const names = (list) => (Array.isArray(list) ? list : null);
  const tools = names(value.builtin_tools);
  const collect = names(value.collect);
  return {
    ...base,
    harness: id,
    version: value.version ?? base.version,
    model: value.model ?? base.model,
    subCallModel: value.subcall_model ?? value.subCallModel ?? null,
    permissions: value.permissions ?? base.permissions,
    mcp_servers: (value.mcp_servers ?? []).map((m) => {
      const on = Array.isArray(m.tools) ? m.tools : m.tool_names ?? [];
      const off = [...(m.disabled_tools ?? [])];
      // every tool of the server: the enabled ones plus the unticked ones
      const list = [...on, ...off.filter((t) => !on.includes(t))];
      return {
        transport: "stdio",
        env: m.env_secret ? [m.env_secret] : [],
        ...m,
        tools: list.length
          ? list.length
          : Array.isArray(m.tools)
            ? m.tool_count ?? 0
            : m.tools ?? 0,
        tool_names: list,
        disabled_tools: off,
      };
    }),
    builtin_tools: tools
      ? Object.fromEntries(
          BUILTIN_TOOLS.map((t) => [t.id, tools.includes(t.id)]),
        )
      : value.builtin_tools ?? base.builtin_tools,
    instructions:
      value.instructions_file ?? value.instructions ?? base.instructions,
    budget: {
      ...base.budget,
      ...(value.budget ?? {}),
      ...(value.max_turns != null ? { max_turns: value.max_turns } : {}),
      ...(value.max_cost_usd != null
        ? { max_cost_usd: value.max_cost_usd }
        : {}),
      ...(value.timeout_s != null ? { timeout_s: value.timeout_s } : {}),
    },
    collect: collect
      ? Object.fromEntries(
          COLLECT_OPTIONS.map((c) => [c.id, collect.includes(c.c4)]),
        )
      : value.collect ?? base.collect,
    extra_files: value.extra_files ?? base.extra_files,
    command: value.command ?? base.command,
    trace_file: Boolean(value.trace_file ?? base.trace_file),
    ...("user_sim" in value ? { user_sim: value.user_sim ?? null } : {}),
  };
}

/** A run's stored recipe (`config.harness`) → the editable recipe shape. */
export function recipeFromConfig(harness, entry) {
  if (!harness) return defaultRecipe(entry, null);
  return asRecipe(harness, entry);
}

/** The run name of a custom command: its first token (`my-agent`). */
export function commandName(command) {
  const first =
    String(command ?? "")
      .trim()
      .split(/\s+/)[0] ?? "";
  return first && first !== "{task}" ? first.split("/").pop() : null;
}

/**
 * C4: the run config of a recipe (`config.harness`, set through
 * `slot.harness`). Keeps `budget` for the readers of the editable shape.
 * @param {object} recipe  the edited recipe
 * @param {object} entry   catalog entry
 * @param {{mode?: string, form?: string, sandbox?: object|null,
 *   connection?: object|null}} [o]
 */
export function harnessConfigOf(recipeIn, entry, o = {}) {
  const recipe = asRecipe(recipeIn, entry);
  const id = entry?.id ?? recipe.harness;
  const p = profileOf(id);
  const servers = p.mcp ? recipe.mcp_servers ?? [] : [];
  const key = recordKeyOf(entry ?? id, o);
  const b = recipe.budget ?? {};
  return {
    id,
    name: entry?.name ?? catalogEntry(id)?.name ?? null,
    version: recipe.version || null,
    model: recipe.model,
    subcall_model: p.subCall ? recipe.subCallModel ?? null : null,
    permissions: recipe.permissions,
    mcp_servers: servers.map((s) => {
      // a server known only by its count (a copied run) keeps that count;
      // only a server added here without a Test lists the Test's tools
      const count = Number(s.tools) || 0;
      const names = s.tool_names?.length
        ? s.tool_names
        : s.from === "environment" || count > 0
          ? []
          : mcpTestTools(s);
      // unticked tools: the run may call only the rest (the engine filters)
      const off = (s.disabled_tools ?? []).filter((t) => names.includes(t));
      const on = names.filter((t) => !off.includes(t));
      return {
        name: s.name,
        transport: s.url ? "http" : s.transport ?? "stdio",
        command: s.command ?? null,
        url: s.url ?? null,
        env_secret: (s.env ?? [])[0] ?? null,
        tools: [...on],
        tool_count: names.length ? on.length : Math.max(0, count - off.length),
        disabled_tools: [...off],
        from: s.from ?? "recipe",
      };
    }),
    builtin_tools: BUILTIN_TOOLS.filter(
      (t) => recipe.builtin_tools?.[t.id],
    ).map((t) => t.id),
    instructions_file: recipe.instructions || null,
    collect: collectOf(recipe),
    extra_files: [...(recipe.extra_files ?? [])],
    max_turns: Number(b.max_turns) || null,
    timeout_s: Number(b.timeout_s) || null,
    max_cost_usd: Number(b.max_cost_usd) || null,
    // the cap stops each try over it (the run engine and the script)
    max_cost_per_try: Number(b.max_cost_usd) > 0,
    budget: { ...b },
    command: id === "custom_command" ? recipe.command ?? null : null,
    command_name: id === "custom_command" ? commandName(recipe.command) : null,
    // C4 capture key (transcript|gateway|final) with the model_calls/steps
    // shape the trace readers expect.
    capture: {
      key,
      model_calls: "gateway",
      // a custom command records steps only when it writes its trace file
      steps:
        key !== "trace_file" || recipe.trace_file ? "transcript" : "final_only",
    },
    ...(id === "custom_command"
      ? { trace_file: recipe.trace_file ? CUSTOM_TRACE_FILE : null }
      : {}),
    // the simulated customer (task suites): off → no customer turns
    ...("user_sim" in recipe ? { user_sim: recipe.user_sim ?? null } : {}),
    install: installLine(recipe),
    cli_command: commandPreview(recipe).text,
    ...(o.sandbox ? { sandbox: o.sandbox } : {}),
    ...(o.connection ? { connection: o.connection } : {}),
  };
}

/**
 * Tools a `Test` of an MCP server lists, derived from its name and command
 * (the stand-in for starting it and calling tools/list).
 */
export function mcpTestTools(server) {
  const name =
    `${server?.name ?? ""} ${server?.command ?? ""} ${server?.url ?? ""}`.toLowerCase();
  if (server?.tool_names?.length) return server.tool_names;
  if (/github/.test(name))
    return ["search_code", "get_file", "create_issue", "list_pull_requests"];
  if (/postgres|sql|db/.test(name)) return ["query", "list_tables", "describe"];
  if (/browser|playwright/.test(name))
    return ["navigate", "click", "screenshot", "read_page"];
  if (/slack/.test(name))
    return ["post_message", "list_channels", "search_messages"];
  if (/filesystem|files?\b/.test(name))
    return ["read_file", "write_file", "list_directory"];
  if (/jira|linear|issue/.test(name))
    return ["search_issues", "create_issue", "add_comment"];
  return ["search", "fetch"];
}

/**
 * What each try of a harness records, in plain words (chip, script, live
 * panel): one wording per harness, whatever the mode (options-truth L2 G6).
 * Every harness CLI but Aider and a custom command writes a step record the
 * script parses (stream-json, --json, a trajectory); Aider logs its model
 * calls and edits; a custom command hands over a trace file if it writes one.
 */
export const RECORD_WORDS = Object.freeze({
  transcript: "every message and tool call, as a trace",
  model_calls_edits: "every model call and each file it edits",
  trace_file:
    "your trace file (FI_TRACE_FILE) if the command writes one, else the final output",
});
/** The sentence next to the chip (same key). */
export const RECORD_SENTENCES = Object.freeze({
  transcript:
    "Each model turn, tool call (with its result) and sub-agent becomes a span, timed from the CLI's own record.",
  model_calls_edits:
    "Each model call (with its tokens and cost) and each file Aider edits becomes a span; Aider makes no tool calls.",
  trace_file:
    "Steps come from the file the command writes at $FI_TRACE_FILE (ATIF, a mini-SWE-agent trajectory or OpenAI chat messages); without one, one span with the end of its output.",
});

/** The record key of a catalog entry (`capture.record`, else transcript). */
function recordOfEntry(entry) {
  if (entry?.capture?.record) return entry.capture.record;
  return entry?.id === "custom_command" ? "trace_file" : "transcript";
}

/**
 * What a try of this harness records: the same on every path (the script
 * parses the CLI's own record locally, in CI and in the sandbox; a live
 * connection runs the same parser) → `transcript` | `model_calls_edits` |
 * `trace_file`.
 */
export function recordKeyOf(entryOrId) {
  const entry =
    typeof entryOrId === "string" ? catalogEntry(entryOrId) : entryOrId;
  return recordOfEntry(entry ?? { id: entryOrId });
}

/**
 * IT-R1 G1 P7: the sticky one-line summary of a harness recipe:
 * `claude-sonnet-4-5 · MCP acme-support (12 tools) · permissions: edit
 * files · $1 per try`. Every MCP server it names is in the script's
 * MCP_SERVERS; harnesses without MCP name none. A helper model shows as
 * `helper claude-haiku-4-5`; a custom command leads with its command.
 */
export function recipeSummary(recipeIn) {
  if (!recipeIn) return "";
  const recipe = asRecipe(recipeIn);
  const p = profileOf(recipe.harness);
  const servers = recipe.mcp_servers ?? [];
  const mcp = !p.mcp
    ? null
    : servers.length
      ? `MCP ${servers
          .map((m) => {
            const off = (m.disabled_tools ?? []).length;
            if (!m.tools) return m.name;
            return off
              ? `${m.name} (${m.tools - off} of ${m.tools} tools)`
              : `${m.name} (${m.tools} tools)`;
          })
          .join(", ")}`
      : "no MCP servers";
  const perm = PERMISSION_LABELS[recipe.permissions] ?? recipe.permissions;
  const cap = Number(recipe.budget?.max_cost_usd);
  return [
    recipe.harness === "custom_command" && recipe.command
      ? commandName(recipe.command)
      : null,
    recipe.model,
    p.subCall && recipe.subCallModel ? `helper ${recipe.subCallModel}` : null,
    mcp,
    perm ? `permissions: ${String(perm).toLowerCase()}` : null,
    Number.isFinite(cap) && cap > 0 ? `${fmtCost(cap)} per try` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}
