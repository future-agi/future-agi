// options-truth L3 G6: version pins, the environment merge, the model list
// of a copied run, Max turns per CLI and per-server tool picks. Pure.
import { describe, expect, it } from "vitest";
import { catalogEntry } from "../../catalog";
import {
  asRecipe,
  commandPreview,
  configFiles,
  defaultRecipe,
  geminiTurnsKey,
  harnessConfigOf,
  installLine,
  modelOptions,
  moreSettingsSummary,
  nameAfterModelChange,
  recipeSummary,
  turnLimitWords,
  versionPin,
  withEnvironmentMcp,
} from "../recipeModel";
import { toggleServerTool, toolCountWords } from "../McpServers";
import { harnessScript } from "../../snippets/harness";

const ENV = {
  mcp: {
    name: "acme-support-sandbox",
    command: "npx -y @acme/support-mcp",
    tool_names: ["lookup_order", "issue_refund", "create_ticket"],
  },
};

describe("versionPin (fix 4)", () => {
  it("pins N.N, N.N.N and N.x; anything else is unpinned", () => {
    expect(versionPin("2.1")).toEqual({
      npm: "@2.1",
      pip: "==2.1.*",
      exact: null,
    });
    expect(versionPin("v2.0.14").npm).toBe("@2.0.14");
    expect(versionPin("2.0.14").pip).toBe("==2.0.14");
    expect(versionPin("2.x").npm).toBe("@2");
    expect(versionPin("latest")).toEqual({ npm: "", pip: "", exact: null });
  });
  it("the install line follows the version", () => {
    const claude = catalogEntry("claude_code");
    const r = (version) => ({ ...defaultRecipe(claude), version });
    expect(installLine(r("2.1"))).toBe(
      "npm i -g @anthropic-ai/claude-code@2.1",
    );
    expect(installLine(r("2.0.14"))).toBe(
      "npm i -g @anthropic-ai/claude-code@2.0.14",
    );
    const gem = {
      ...defaultRecipe(catalogEntry("gemini_cli")),
      version: "0.9.0",
    };
    expect(installLine(gem)).toBe("npm i -g @google/gemini-cli@0.9.0");
  });
});

describe("withEnvironmentMcp (fix 2)", () => {
  const e = catalogEntry("claude_code");
  it("adds the environment's server to a recipe made before it loaded", () => {
    const early = { ...defaultRecipe(e, null), model: "claude-haiku-4-5" };
    expect(early.mcp_servers.some((s) => s.name === "acme-support")).toBe(
      false,
    );
    const merged = withEnvironmentMcp(early, ENV);
    expect(merged.mcp_servers[0].name).toBe("acme-support");
    expect(merged.mcp_servers[0].from).toBe("environment");
    // the edit made before the environment arrived is kept
    expect(merged.model).toBe("claude-haiku-4-5");
    expect(commandPreview(merged).text).toContain("mcp__acme-support");
  });
  it("never adds it twice, and leaves harnesses without MCP alone", () => {
    const withEnv = defaultRecipe(e, ENV);
    expect(withEnvironmentMcp(withEnv, ENV)).toBe(withEnv);
    const aider = defaultRecipe(catalogEntry("aider"), null);
    expect(withEnvironmentMcp(aider, ENV)).toBe(aider);
  });
});

describe("modelOptions (fix 8)", () => {
  it("OpenHands offers gemini-2.5-pro; a copied run's unknown model is kept", () => {
    const oh = catalogEntry("openhands");
    expect(modelOptions(defaultRecipe(oh), oh)).toContain("gemini-2.5-pro");
    const copied = { ...defaultRecipe(oh), model: "gpt-4.1" };
    const list = modelOptions(copied, oh);
    expect(list).toContain("gpt-4.1");
    expect(list.filter((m) => m === "gpt-4.1")).toHaveLength(1);
  });
});

describe("Max turns per CLI (fix 5)", () => {
  const recipe = (id, extra = {}) => {
    const base = defaultRecipe(catalogEntry(id), ENV);
    return { ...base, ...extra, budget: { ...base.budget, max_turns: 6 } };
  };
  it("Gemini writes maxSessionTurns where its version reads it", () => {
    const old = recipe("gemini_cli", { version: "0.9.0" });
    expect(geminiTurnsKey(old)).toBe("maxSessionTurns");
    expect(geminiTurnsKey(recipe("gemini_cli", { version: "1.2.0" }))).toBe(
      "model.maxSessionTurns",
    );
    const gem = configFiles(old).find(
      (f) => f.path === ".gemini/settings.json",
    );
    expect(gem.what).toContain("maxSessionTurns 6");
    const script = harnessScript({
      entry: catalogEntry("gemini_cli"),
      ids: {},
      recipe: old,
    });
    expect(script).toContain('TURNS_KEY = "maxSessionTurns"');
    expect(script).toContain('settings["maxSessionTurns"] = MAX_TURNS');
  });
  it("Codex stops at N from its own events; mini takes agent.step_limit", () => {
    const codex = harnessScript({
      entry: catalogEntry("codex_cli"),
      ids: {},
      recipe: recipe("codex_cli"),
    });
    expect(codex).toContain("proc.send_signal(signal.SIGINT)");
    expect(codex).toContain("len(turns) >= MAX_TURNS");
    expect(turnLimitWords(recipe("codex_cli"))).toMatch(/SIGINT\) at 6$/);
    expect(commandPreview(recipe("mini_swe_agent")).text).toContain(
      "-c agent.step_limit=6",
    );
  });
  it("Aider has no turn limit: no control, no turns in the summary", () => {
    const aider = recipe("aider");
    expect(turnLimitWords(aider)).toBeNull();
    expect(moreSettingsSummary(aider)).not.toMatch(/\bturns?\b/);
    expect(turnLimitWords(recipe("claude_code"))).toBe(
      "--max-turns 6 on the command line",
    );
  });
});

describe("per-server tool picks (fix 12)", () => {
  const e = catalogEntry("openhands");
  it("an unticked tool leaves the run's server list and the counts say so", () => {
    const r = defaultRecipe(e, ENV);
    const server = r.mcp_servers.find((s) => s.name === "acme-support");
    const off1 = toggleServerTool(server, "issue_refund");
    const off2 = toggleServerTool(off1, "create_ticket");
    expect(off2.disabled_tools).toEqual(["issue_refund", "create_ticket"]);
    expect(toolCountWords(off2)).toBe("1 of 3 tools");
    // ticking it again brings it back
    expect(toggleServerTool(off2, "create_ticket").disabled_tools).toEqual([
      "issue_refund",
    ]);
    const next = {
      ...r,
      mcp_servers: r.mcp_servers.map((s) =>
        s.name === server.name ? off2 : s,
      ),
    };
    const cfg = harnessConfigOf(next, e);
    const s = cfg.mcp_servers.find((x) => x.name === "acme-support");
    expect(s.tools).toEqual(["lookup_order"]);
    expect(s.disabled_tools).toEqual(["issue_refund", "create_ticket"]);
    expect(recipeSummary(next)).toContain("acme-support (1 of 3 tools)");
    // a stored config reopens with the same picks
    const back = asRecipe(cfg, e).mcp_servers.find(
      (x) => x.name === "acme-support",
    );
    expect(back.tool_names).toEqual([
      "lookup_order",
      "issue_refund",
      "create_ticket",
    ]);
    expect(back.disabled_tools).toEqual(["issue_refund", "create_ticket"]);
  });
  it("the simulated customer switch reaches the run config", () => {
    const r = { ...defaultRecipe(e, ENV), user_sim: null };
    expect(harnessConfigOf(r, e).user_sim).toBeNull();
    expect("user_sim" in harnessConfigOf(defaultRecipe(e, ENV), e)).toBe(false);
  });
});

describe("a copied run's MCP server (Start from)", () => {
  const e = catalogEntry("openhands");
  // a seeded run stores the server with a count only
  const copied = asRecipe(
    {
      id: "openhands",
      model: "gemini-2.5-pro",
      mcp_servers: [{ name: "acme-support", tools: 12 }],
    },
    e,
  );
  it("without names keeps its count and never invents tools", () => {
    const s = harnessConfigOf(copied, e).mcp_servers[0];
    expect(s.tool_count).toBe(12);
    expect(s.tools).toEqual([]);
    expect(recipeSummary(copied)).toContain("acme-support (12 tools)");
  });
  it("is the Environment's server: it lists the Environment's tools", () => {
    const merged = withEnvironmentMcp(copied, ENV);
    expect(merged.mcp_servers).toHaveLength(1);
    const s = harnessConfigOf(merged, e).mcp_servers[0];
    expect(s.tools).toEqual(["lookup_order", "issue_refund", "create_ticket"]);
    expect(s.tool_count).toBe(3);
    expect(s.from).toBe("environment");
    // a server that already lists its tools is left alone
    expect(withEnvironmentMcp(merged, ENV)).toBe(merged);
  });
});

describe("nameAfterModelChange (fix 8)", () => {
  it("an automatic `<Harness> × <model>` name follows the model", () => {
    expect(
      nameAfterModelChange(
        "OpenHands × gemini-2.5-pro",
        "OpenHands",
        "gemini-2.5-pro",
        "claude-sonnet-4-5",
      ),
    ).toBe("OpenHands × claude-sonnet-4-5");
  });
  it("a typed name, a name without the model, or no change stays", () => {
    expect(
      nameAfterModelChange(
        "My run",
        "OpenHands",
        "gemini-2.5-pro",
        "gpt-5-codex",
      ),
    ).toBeNull();
    expect(
      nameAfterModelChange(
        "OpenHands",
        "OpenHands",
        "gemini-2.5-pro",
        "gpt-5-codex",
      ),
    ).toBeNull();
    expect(
      nameAfterModelChange(
        "OpenHands × gemini-2.5-pro",
        "OpenHands",
        "gemini-2.5-pro",
        "gemini-2.5-pro",
      ),
    ).toBeNull();
  });
});
