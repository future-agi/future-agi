// L7c g2 · picker:search (skeptic must-fix): model names match with or
// without separators, and the Agent graph card is found by the models its
// versions' nodes call (the graph list carries none).
import { describe, expect, it } from "vitest";
import { matcherOf, searchCatalog, searchStepCards } from "../catalog";
import { modelsOfVersion, withGraphModels } from "../graphModels-g2";

const names = {
  prompts: [
    { name: "Support Reply", model: "gpt-4o-mini" },
    { name: "Policy Answer", model: "claude-sonnet-4-5" },
  ],
  graphs: [{ name: "Support Agent", models: ["gpt-4o-mini", "gpt-4o"] }],
};

describe("picker:search · separator-free model names", () => {
  it("gpt4o, GPT4o-mini and 4omini find gpt-4o-mini", () => {
    ["gpt4o", "GPT4o-mini", "4omini", "gpt 4o mini", "gpt-4o-mini"].forEach(
      (q) => {
        const hits = searchStepCards(q, names);
        expect(hits.map((h) => h.card)).toContain("prompt");
        expect(hits.find((h) => h.card === "prompt").why).toBe(
          "model gpt-4o-mini (Support Reply)",
        );
      },
    );
  });
  it("matcherOf is null for an empty query and matches either form", () => {
    expect(matcherOf("  ")).toBeNull();
    const has = matcherOf("sonnet45");
    expect(has("claude-sonnet-4-5")).toBe(true);
    expect(has("gpt-4o")).toBe(false);
    expect(has(null)).toBe(false);
  });
  it("junk still names nothing", () => {
    expect(searchStepCards("zzqx", names)).toEqual([]);
  });
  it("the catalog entries match separator-free too", () => {
    expect(searchCatalog("mcpserver").map((e) => e.name)).toContain(
      "MCP server tool",
    );
    expect(searchCatalog("a2a").map((e) => e.name)).toContain("A2A agent card");
  });
});

describe("picker:search · graph version models are indexed", () => {
  it("a model only the graph's nodes call finds Agent graph", () => {
    const only = {
      prompts: [],
      graphs: [{ name: "Support Agent", models: ["gpt-4o-mini", "gpt-4o"] }],
    };
    expect(searchStepCards("gpt4o", only)).toEqual([
      { card: "agent", why: "model gpt-4o-mini (Support Agent)" },
    ]);
    expect(searchStepCards("gpt-4o-mini", only)).toEqual([
      { card: "agent", why: "model gpt-4o-mini (Support Agent)" },
    ]);
    // A graph's own name still wins over its models.
    expect(searchStepCards("support agent", only)).toEqual([
      { card: "agent", why: "graph Support Agent" },
    ]);
  });
  it("modelsOfVersion reads the nodes' prompt templates once each", () => {
    const detail = {
      nodes: [
        { prompt_template: { model: "gpt-4o-mini" } },
        { prompt_template: null },
        { prompt_template: { model: "gpt-4o" } },
        { prompt_template: { model: "gpt-4o-mini" } },
      ],
    };
    expect(modelsOfVersion(detail)).toEqual(["gpt-4o-mini", "gpt-4o"]);
    expect(modelsOfVersion(null)).toEqual([]);
    expect(
      withGraphModels(
        [
          { id: "g1", name: "A" },
          { id: "g2", name: "B" },
        ],
        {
          g1: ["gpt-4o"],
        },
      ),
    ).toEqual([
      { id: "g1", name: "A", models: ["gpt-4o"] },
      { id: "g2", name: "B", models: [] },
    ]);
  });
});
