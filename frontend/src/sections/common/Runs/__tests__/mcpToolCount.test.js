// A harness config (C4) lists an MCP server's tool names in `tools` with
// `tool_count` beside them; older configs and the recipe editor carry the
// count in `tools`. Every "N tools" line reads the count either way.
import { describe, expect, it } from "vitest";
import { catalogEntry } from "src/sections/common/RunSources/catalog";
import {
  defaultRecipe,
  harnessConfigOf,
} from "src/sections/common/RunSources/recipe/recipeModel";
import { r4CardSummary } from "src/sections/develop-detail/Experiment/R4RunCard";
import { usesOf } from "src/sections/develop-detail/Experiment/reviewModel";
import { mcpToolCount, provenanceOf } from "../provenanceModel";

const ENVIRONMENT = {
  mcp: {
    name: "acme-support-sandbox",
    command: "acme-sandbox mcp --stdio",
    tool_names: ["lookup_order", "issue_refund", "search_kb"],
  },
};
const entry = catalogEntry("claude_code");
const recipe = defaultRecipe(entry, ENVIRONMENT);
const config = harnessConfigOf(recipe, entry, { mode: "local" });

describe("mcpToolCount", () => {
  it("reads the count from names, tool_count or a number", () => {
    expect(mcpToolCount({ tools: ["a", "b"] })).toBe(2);
    expect(mcpToolCount({ tools: [], tool_count: 12 })).toBe(12);
    expect(mcpToolCount({ tools: 7 })).toBe(7);
    expect(mcpToolCount({ name: "x" })).toBeNull();
    expect(mcpToolCount(null)).toBeNull();
  });

  it("a harness config carries names and the same count", () => {
    const [server] = config.mcp_servers;
    expect(server.tools).toEqual(["lookup_order", "issue_refund", "search_kb"]);
    expect(mcpToolCount(server)).toBe(3);
    expect(mcpToolCount(recipe.mcp_servers[0])).toBe(3);
  });
});

describe("tool counts from a harness config", () => {
  it("the run's provenance counts the server's tools", () => {
    const p = provenanceOf({ config: { harness: config } });
    expect(p.mcp).toEqual([
      expect.objectContaining({ name: "acme-support", tools: 3 }),
    ]);
  });

  it("the run card says the count, not the names", () => {
    const text = r4CardSummary({
      entryId: "claude_code",
      mode: "local",
      slot: { harness: config },
    });
    expect(text).toContain("MCP acme-support (3 tools)");
    expect(text).not.toContain("lookup_order");
  });

  it("Review & run says the count for the config and the editor recipe", () => {
    expect(usesOf("harness", config).mcp).toBe("acme-support (3 tools)");
    expect(usesOf("harness", recipe).mcp).toBe("acme-support (3 tools)");
  });
});
