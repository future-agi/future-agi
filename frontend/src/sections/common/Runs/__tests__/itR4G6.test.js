// IT-R4 G6 P2 / P4: Add run presets and the run header's how-it-ran card.
import { describe, expect, it } from "vitest";
import { pickerPresets } from "../pickerPresets";
import { runHowItRan, STEPS_NOTE } from "../runHowItRan";

const run = (id, kind, extra = {}) => ({
  run_id: id,
  label: id,
  created_at: extra.created_at ?? "2026-10-01",
  dataset_version: { number: extra.v ?? 2 },
  config: { kind, ...(extra.config ?? {}) },
});

describe("Add run presets", () => {
  const runs = [
    run("#2B", "agent"),
    run("#2D", "harness", { config: { harness: { name: "Claude Code" } } }),
    run("#2C", "code", { config: { code: { framework: "LangGraph 0.2" } } }),
    run("#3B", "code", {
      created_at: "2026-10-03",
      config: { code: { framework: "OpenAI Agents SDK 0.1" } },
    }),
    run("#3C", "code", {
      created_at: "2026-10-03",
      config: { code: { framework: "CrewAI 0.8" } },
    }),
    run("#1C", "code", {
      v: 1,
      created_at: "2026-09-01",
      config: { code: { framework: "LangGraph 0.1" } },
    }),
  ];
  it("adds every harness / your-code run on the latest version, and one per framework", () => {
    const p = Object.fromEntries(
      pickerPresets(runs, ["#2B", "#2D", "#2C"]).map((x) => [x.id, x.ids]),
    );
    expect(p["harness-vs-code"].sort()).toEqual(["#3B", "#3C"]);
    // LangGraph's newest run (#2C) is in view: not added again.
    expect(p.frameworks.sort()).toEqual(["#3B", "#3C"]);
  });
});

describe("how a run ran", () => {
  it("lists model, tools, MCP and where it ran, with the steps note", () => {
    const how = runHowItRan({
      run_id: "d",
      label: "#2D",
      config: {
        kind: "harness",
        harness: { name: "Claude Code" },
        models: [{ id: "claude-sonnet-4-5" }],
        tools: ["a", "b", "c", "d", "e", "f", "g"].map((name) => ({ name })),
        mcp_servers: [{ name: "acme-support-sandbox", tools: 12 }],
        execution: { mode: "local", label: "Docker" },
      },
    });
    const byK = Object.fromEntries(how.lines.map((l) => [l.k, l.v]));
    expect(byK.Model).toBe("claude-sonnet-4-5");
    expect(byK.Tools).toBe("7: a, b, c, d, e, f +1 more");
    expect(byK.MCP).toBe("acme-support-sandbox (12 tools)");
    expect(byK["Ran on"]).toBe("Docker");
    expect(how.note).toBe(STEPS_NOTE);
  });
});
