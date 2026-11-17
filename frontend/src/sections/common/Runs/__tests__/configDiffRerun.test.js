// options-truth L3 G9 (fix 2): What changed for a Rerun with changes diffs
// MCP servers by name, prints `collect` as words and never twins a declared
// param with a `none →` chip.
import { describe, expect, it } from "vitest";
import { collectText, configDiff } from "../configDiff";

const harness = (over = {}) => ({
  kind: "harness",
  harness: {
    id: "claude_code",
    name: "Claude Code",
    version: "2.x",
    model: "claude-sonnet-4-5",
    collect: ["git_diff", "final_message"],
    mcp_servers: [{ name: "acme-support", tools: 12 }],
    ...over,
  },
  mcp_servers: over.mcp_servers ?? [
    { name: "acme-support", transport: "stdio", tools: 12 },
  ],
});

describe("configDiff · rerun recipe", () => {
  it("collect reads as words, from a list or from flags", () => {
    expect(collectText(["git_diff", "final_message"])).toBe(
      "git diff, final message",
    );
    expect(collectText({ git_diff: true, terminal_log: false })).toBe(
      "git diff",
    );
    expect(collectText(null)).toBe("none");
    const chips = configDiff(
      harness(),
      harness({ collect: { git_diff: true, terminal_log: true } }),
    );
    const c = chips.find((x) => x.detail?.field === "collect");
    expect(c.label).toBe(
      "collect git diff, final message → git diff, terminal log",
    );
    expect(JSON.stringify(chips)).not.toMatch(/object Object/);
    // The same setting in two spellings is no change.
    expect(
      configDiff(
        harness(),
        harness({ collect: { git_diff: true, final_message: true } }),
      ).some((x) => x.detail?.field === "collect"),
    ).toBe(false);
  });

  it("a removed MCP server is one chip by name", () => {
    const chips = configDiff(harness(), harness({ mcp_servers: [] }));
    const mcp = chips.filter((c) => c.axis === "mcp");
    expect(mcp).toHaveLength(1);
    expect(mcp[0].detail).toEqual({ removed: "acme-support" });
  });

  it("a declared param changed by a rerun shows once", () => {
    const schema = [{ name: "temperature", type: "float", default: 0.2 }];
    const a = {
      kind: "code",
      params: { schema, values: { temperature: 0.2 } },
      model: { id: "gpt-4.1-mini", params: {} },
    };
    const b = {
      kind: "code",
      params: { schema, values: { temperature: 0.9 } },
      model: { id: "gpt-4.1-mini", params: { temperature: 0.9 } },
    };
    const labels = configDiff(a, b)
      .filter((c) => c.axis === "params")
      .map((c) => c.label);
    expect(labels).toEqual(["temperature 0.2 → 0.9"]);
  });
});
