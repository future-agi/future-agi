// options-truth L1 G3: every knob the harness recipe shows reaches the
// preview, the script and the run config (C4); the catalog shows the same
// command (C14).
import { describe, expect, it } from "vitest";
import { catalogEntry } from "../../catalog";
import {
  PERMISSION_ORDER,
  asRecipe,
  cliCommandFor,
  commandPreview,
  defaultRecipe,
  harnessConfigOf,
  installLine,
  noMcpLine,
  permissionFlag,
  permissionHint,
  recipeSummary,
  recordKeyOf,
  supportsMcp,
} from "../recipeModel";
import { harnessScript } from "../../snippets/harness";

const ENV = {
  mcp: {
    name: "acme-support-sandbox",
    command: "acme-sandbox mcp --stdio",
    tools: 12,
  },
};
const ALL = [
  "claude_code",
  "codex_cli",
  "gemini_cli",
  "openhands",
  "aider",
  "goose",
  "mini_swe_agent",
  "custom_command",
];
const GITHUB = {
  name: "github-extra",
  transport: "stdio",
  command: "npx -y @modelcontextprotocol/server-github",
  env: ["GITHUB_TOKEN"],
  tools: 4,
  tool_names: ["search_code", "get_file", "create_issue", "list_pull_requests"],
  from: "recipe",
};
const recipeOf = (id, patch = {}) => {
  const e = catalogEntry(id);
  const r = defaultRecipe(e, ENV);
  return {
    ...r,
    mcp_servers: supportsMcp(id) ? [...r.mcp_servers, GITHUB] : [],
    ...patch,
  };
};
const ids = {
  runId: "a6",
  runLabel: "#2A",
  datasetName: "TS",
  inputColumn: "instruction",
};

describe("permissions: three real levels for all 8 harnesses", () => {
  it.each(ALL)("%s: no placeholder, levels differ, the hint says how", (id) => {
    const flags = PERMISSION_ORDER.map((l) => permissionFlag(id, l).flag);
    flags.forEach((f) => expect(f).not.toMatch(/your CLI|\(no flag\)/));
    expect(new Set(flags).size).toBe(3);
    PERMISSION_ORDER.forEach((l) => expect(permissionHint(id, l)).toBeTruthy());
  });
  it("mini-SWE-agent never keeps --yolo with Read only", () => {
    const ro = commandPreview(
      recipeOf("mini_swe_agent", { permissions: "read_only" }),
    );
    expect(ro.argv).not.toContain("--yolo");
    expect(ro.env.FI_PERMISSIONS).toBe("read_only");
    const fa = commandPreview(
      recipeOf("mini_swe_agent", { permissions: "full_auto" }),
    );
    expect(fa.argv).toContain("--yolo");
  });
  it("Goose uses its documented GOOSE_MODE", () => {
    const at = (p) =>
      commandPreview(recipeOf("goose", { permissions: p })).env.GOOSE_MODE;
    expect(at("read_only")).toBe("chat");
    expect(at("edit")).toBe("smart_approve");
    expect(at("full_auto")).toBe("auto");
  });
  it("the script carries the level and enforces wrapper levels", () => {
    const f = harnessScript({
      entry: catalogEntry("openhands"),
      ids,
      recipe: recipeOf("openhands", { permissions: "read_only" }),
    });
    expect(f).toContain('PERMISSIONS = "read_only"');
    expect(f).toContain("WRAPPER_ENFORCED = True");
    expect(f).toContain('add("--permissions", default=PERMISSIONS');
  });
});

describe("MCP servers in each harness's own config", () => {
  it.each([
    ["claude_code", "--mcp-config"],
    ["codex_cli", "CODEX_HOME=.fi/codex"],
    ["goose", "--with-extension"],
    ["custom_command", "FI_MCP_CONFIG=.fi/mcp.json"],
  ])("%s: the preview shows %s", (id, bit) => {
    expect(commandPreview(recipeOf(id)).text).toContain(bit);
  });
  it.each([
    "claude_code",
    "codex_cli",
    "gemini_cli",
    "openhands",
    "goose",
    "custom_command",
  ])("%s: whatever the summary claims is in MCP_SERVERS", (id) => {
    const r = recipeOf(id);
    const sum = recipeSummary(r);
    const file = harnessScript({ entry: catalogEntry(id), ids, recipe: r });
    expect(sum).toContain("github-extra (4 tools)");
    expect(sum).toContain("acme-support (12 tools)");
    expect(file).toMatch(/"github-extra": \{/);
    expect(file).toMatch(/"acme-support": \{/);
  });
  it.each(["aider", "mini_swe_agent"])(
    "%s: no MCP, an info line, the summary omits MCP",
    (id) => {
      expect(supportsMcp(id)).toBe(false);
      expect(noMcpLine(id)).toMatch(
        /has no MCP support: it edits files and runs commands itself$/,
      );
      expect(recipeSummary(recipeOf(id))).not.toMatch(/MCP/);
      expect(
        harnessScript({ entry: catalogEntry(id), ids, recipe: recipeOf(id) }),
      ).toContain("MCP_SERVERS = {}");
    },
  );
  it("an added server reaches the run config with its tool names", () => {
    const c = harnessConfigOf(
      recipeOf("claude_code"),
      catalogEntry("claude_code"),
    );
    expect(c.mcp_servers.map((m) => m.name)).toEqual([
      "acme-support",
      "github-extra",
    ]);
    expect(c.mcp_servers[1].tools).toEqual(GITHUB.tool_names);
    expect(c.mcp_servers[1].env_secret).toBe("GITHUB_TOKEN");
  });
});

describe("helper model, version, tools, instructions, collect", () => {
  it("helper model: env prefix + script + config; absent when unset", () => {
    const r = recipeOf("claude_code", { subCallModel: "claude-haiku-4-5" });
    expect(
      commandPreview(r).text.startsWith(
        "ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4-5 claude",
      ),
    ).toBe(true);
    expect(
      harnessScript({ entry: catalogEntry("claude_code"), ids, recipe: r }),
    ).toContain('HELPER_MODEL = "claude-haiku-4-5"');
    expect(harnessConfigOf(r, catalogEntry("claude_code")).subcall_model).toBe(
      "claude-haiku-4-5",
    );
    const none = recipeOf("claude_code");
    expect(commandPreview(none).text).not.toContain(
      "ANTHROPIC_SMALL_FAST_MODEL",
    );
    expect(
      harnessConfigOf(none, catalogEntry("claude_code")).subcall_model,
    ).toBeNull();
  });
  it.each([
    ["claude_code", "npm i -g @anthropic-ai/claude-code@1"],
    ["codex_cli", "npm i -g @openai/codex@1"],
    ["gemini_cli", "npm i -g @google/gemini-cli@1"],
    ["aider", 'pip install "aider-chat==1.*"'],
    // OpenHands 1.x is the `openhands` CLI package (harness-logs F5)
    ["openhands", 'pip install "openhands==1.*"'],
    ["mini_swe_agent", 'pip install "mini-swe-agent==1.*"'],
  ])("%s: the version pins the install line", (id, line) => {
    expect(installLine(recipeOf(id, { version: "1.x" }))).toBe(line);
  });
  it("OpenHands 0.x stays openhands-ai (python -m openhands.core.main)", () => {
    expect(installLine(recipeOf("openhands", { version: "0.x" }))).toBe(
      'pip install "openhands-ai==0.*"',
    );
  });
  it("Goose pins exact versions through GOOSE_VERSION", () => {
    expect(installLine(recipeOf("goose", { version: "1.9.3" }))).toContain(
      "GOOSE_VERSION=v1.9.3",
    );
    expect(installLine(recipeOf("goose", { version: "1.x" }))).not.toContain(
      "GOOSE_VERSION",
    );
  });
  it("built-in tools: --allowedTools for Claude Code, coreTools for Gemini", () => {
    const r = recipeOf("claude_code", {
      builtin_tools: {
        shell: true,
        edit: false,
        web_fetch: true,
        web_search: false,
      },
    });
    const t = commandPreview(r).text;
    expect(t).toContain(
      '--allowedTools "Bash,WebFetch,mcp__acme-support,mcp__github-extra"',
    );
    expect(t).toContain('--disallowedTools "Edit,Write,WebSearch"');
    const g = harnessScript({
      entry: catalogEntry("gemini_cli"),
      ids,
      recipe: recipeOf("gemini_cli"),
    });
    expect(g).toContain('"run_shell_command",');
  });
  it("the instructions file goes where each harness reads it", () => {
    const aider = commandPreview(
      recipeOf("aider", { instructions: "CONVENTIONS.md" }),
    );
    const read = aider.argv.indexOf("--read");
    expect(aider.argv.slice(read, read + 2)).toEqual([
      "--read",
      "CONVENTIONS.md",
    ]);
    const cc = commandPreview(
      recipeOf("claude_code", { instructions: "CONVENTIONS.md" }),
    );
    expect(cc.files).toContainEqual({
      path: "CLAUDE.md",
      what: "copied from CONVENTIONS.md",
    });
    const goose = harnessScript({
      entry: catalogEntry("goose"),
      ids,
      recipe: recipeOf("goose", { instructions: "CONVENTIONS.md" }),
    });
    expect(goose).toContain('INSTRUCTIONS_AT = ".goosehints"');
  });
  it("collect sets COLLECT and only those upload", () => {
    const r = recipeOf("codex_cli", {
      collect: { git_diff: true, final_message: false, terminal_log: true },
      extra_files: ["reports/*.json"],
    });
    const f = harnessScript({
      entry: catalogEntry("codex_cli"),
      ids,
      recipe: r,
    });
    expect(f).toContain('COLLECT = ["git_diff", "log", "files"]');
    expect(harnessConfigOf(r, catalogEntry("codex_cli")).collect).toEqual([
      "git_diff",
      "log",
      "files",
    ]);
  });
});

describe("one command everywhere, custom command, defaults, capture", () => {
  it.each(ALL)("%s: preview = script cli argv = catalog (C14)", (id) => {
    const e = catalogEntry(id);
    const r = defaultRecipe(e, null);
    expect(cliCommandFor(id)).toBe(commandPreview(r).text);
    const file = harnessScript({ entry: e, ids, recipe: r });
    commandPreview(r, { task: "{task}" }).argv.forEach((a) =>
      expect(file).toContain(JSON.stringify(a)),
    );
  });
  it("custom command: name = first token, config holds the full command", () => {
    const r = recipeOf("custom_command", {
      command: "my-agent run --task {task} --fast",
    });
    const c = harnessConfigOf(r, catalogEntry("custom_command"));
    expect(c.command).toBe("my-agent run --task {task} --fast");
    expect(c.command_name).toBe("my-agent");
    expect(recipeSummary(r).startsWith("my-agent · ")).toBe(true);
  });
  it("the default model is each harness's first listed model (Aider: not Claude)", () => {
    expect(defaultRecipe(catalogEntry("aider"), null).model).toBe("gpt-4.1");
  });
  it("what we record: one key per path for the chip, sentence and script", () => {
    expect(recordKeyOf("claude_code", { mode: "local", form: "upload" })).toBe(
      "transcript",
    );
    // one key per harness whatever the mode (options-truth L2 G6)
    for (const where of [
      { mode: "local", form: "upload" },
      { mode: "local", form: "connect" },
      { mode: "ci" },
      { mode: "sandbox" },
    ]) {
      expect(recordKeyOf("aider", where)).toBe("model_calls_edits");
      expect(recordKeyOf("gemini_cli", where)).toBe("transcript");
      expect(recordKeyOf("custom_command", where)).toBe("trace_file");
    }
  });
  it("a C4 config round-trips into the editable recipe", () => {
    const r = recipeOf("claude_code", {
      permissions: "full_auto",
      subCallModel: "claude-haiku-4-5",
      version: "1.x",
    });
    const back = asRecipe(harnessConfigOf(r, catalogEntry("claude_code")));
    expect(back.permissions).toBe("full_auto");
    expect(back.subCallModel).toBe("claude-haiku-4-5");
    expect(back.version).toBe("1.x");
    expect(back.mcp_servers.map((m) => m.name)).toEqual([
      "acme-support",
      "github-extra",
    ]);
    expect(commandPreview(back).text).toBe(commandPreview(r).text);
  });
});
