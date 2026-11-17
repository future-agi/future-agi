// options-truth L3 G6 (fix 12, S3): the tools unticked in Choose tools reach
// the harness's own command and config, and every screen names a run's CI
// file from its YAML. Pure.
import { describe, expect, it } from "vitest";
import { catalogEntry } from "../../catalog";
import {
  claudeToolLists,
  defaultRecipe,
  withEnvironmentMcp,
} from "../recipeModel";
import { toggleServerTool } from "../McpServers";
import { excludedToolsOf, harnessScript } from "../../snippets/harness";
import { ciPathOfYaml, ciWorkflow, ciWorkflowPath } from "../../snippets/ci";

const ENV = {
  mcp: {
    name: "acme-support-sandbox",
    command: "npx -y @acme/support-mcp",
    tool_names: ["lookup_order", "issue_refund", "create_ticket"],
  },
};
const IDS = {
  datasetName: "Support ops tasks · sandbox",
  runLabel: "#4A",
  runId: "a6000000-0000-4000-8000-00000000004a",
};

/** The default recipe of `id` with the environment server and `off` unticked. */
function recipeWith(id, off = []) {
  const r = withEnvironmentMcp(defaultRecipe(catalogEntry(id), null), ENV);
  return {
    ...r,
    mcp_servers: r.mcp_servers.map((s) =>
      off.reduce((acc, t) => toggleServerTool(acc, t), s),
    ),
  };
}

describe("claudeToolLists with a tool subset", () => {
  it("allows the whole server when every tool is ticked", () => {
    const t = claudeToolLists(recipeWith("claude_code"));
    expect(t.allowed).toContain("mcp__acme-support");
    expect(t.disallowed.some((x) => x.startsWith("mcp__"))).toBe(false);
  });
  it("allows only the ticked tools and denies the unticked ones", () => {
    const t = claudeToolLists(
      recipeWith("claude_code", ["issue_refund", "create_ticket"]),
    );
    expect(t.allowed).toContain("mcp__acme-support__lookup_order");
    expect(t.allowed).not.toContain("mcp__acme-support");
    expect(t.allowed).not.toContain("mcp__acme-support__issue_refund");
    expect(t.disallowed).toEqual(
      expect.arrayContaining([
        "mcp__acme-support__issue_refund",
        "mcp__acme-support__create_ticket",
      ]),
    );
  });
  it("the script's command carries the same lists", () => {
    const script = harnessScript({
      entry: catalogEntry("claude_code"),
      ids: IDS,
      recipe: recipeWith("claude_code", ["issue_refund"]),
    });
    expect(script).toContain("mcp__acme-support__issue_refund");
    expect(script).toContain("mcp__acme-support__lookup_order");
  });
});

describe("excludedToolsOf and the Gemini / Codex configs", () => {
  it("lists unticked tools only for harnesses whose config can switch them off", () => {
    const s = recipeWith("gemini_cli", ["issue_refund"]).mcp_servers[0];
    expect(excludedToolsOf(s, "gemini")).toEqual(["issue_refund"]);
    expect(excludedToolsOf(s, "codex")).toEqual(["issue_refund"]);
    expect(excludedToolsOf(s, "openhands")).toEqual([]);
  });
  it("Gemini writes excludeTools; a full server writes none", () => {
    const sub = harnessScript({
      entry: catalogEntry("gemini_cli"),
      ids: IDS,
      recipe: recipeWith("gemini_cli", ["issue_refund"]),
    });
    expect(sub).toContain('"exclude_tools": ["issue_refund"]');
    expect(sub).toContain('"excludeTools": spec["exclude_tools"]');
    const full = harnessScript({
      entry: catalogEntry("gemini_cli"),
      ids: IDS,
      recipe: recipeWith("gemini_cli"),
    });
    expect(full).not.toContain("exclude_tools");
  });
  it("Codex writes disabled_tools in its config.toml", () => {
    const sub = harnessScript({
      entry: catalogEntry("codex_cli"),
      ids: IDS,
      recipe: recipeWith("codex_cli", ["create_ticket"]),
    });
    expect(sub).toContain('"exclude_tools": ["create_ticket"]');
    expect(sub).toContain("disabled_tools = {json.dumps(off)}");
  });
});

describe("ciPathOfYaml (S3)", () => {
  it("reads the per-run path off the workflow's own header", () => {
    const yaml = ciWorkflow({
      entry: catalogEntry("custom_python"),
      ids: { ...IDS, runLabel: "#4B" },
    });
    expect(ciPathOfYaml(yaml)).toBe(ciWorkflowPath("#4B"));
    expect(ciPathOfYaml(yaml)).toBe(".github/workflows/fi-experiment-4b.yml");
  });
  it("falls back when the text names no workflow file", () => {
    expect(ciPathOfYaml("on:\n  push:\n")).toBe(
      ".github/workflows/fi-experiment.yml",
    );
    expect(ciPathOfYaml("", "x.yml")).toBe("x.yml");
  });
});

describe("waitingOf CI (snippet:ci:workflow): the waiting YAML is the drawer's", () => {
  it("a harness CI run installs its pinned CLI and reads its model key", async () => {
    const { waitingOf } = await import(
      "src/sections/develop-detail/Experiment/waitingModel"
    );
    const { harnessConfigOf } = await import("../recipeModel");
    const entry = catalogEntry("gemini_cli");
    const recipe = {
      ...defaultRecipe(entry, null),
      version: "0.9.0",
      model: "gemini-2.5-flash",
    };
    const run = {
      label: "#4A",
      run_id: IDS.runId,
      config: {
        kind: "harness",
        harness: { id: "gemini_cli", ...harnessConfigOf(recipe, entry) },
        execution: { mode: "ci" },
        ci: { trigger: "pull_request", threshold: 0.85 },
        limits: { concurrency: 4, timeout_s: 600 },
      },
    };
    const w = waitingOf(run);
    expect(w.mode).toBe("ci");
    // dataset integration FE-0: the waiting surface prints no generated workflow.
    expect(w.command).toBeNull();
  });
});
