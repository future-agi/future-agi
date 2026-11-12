import { describe, expect, it } from "vitest";
import {
  allRunsExternal,
  draftSummary,
  executionSummary,
  flattenRuns,
  lettersByConfig,
  runCount,
  runDisplayName,
  targetTypeOf,
} from "../runPlan";

const agent = {
  targetType: "agent",
  agentId: "g1",
  agentVersion: "gv3",
  name: "Support Agent",
  versionLabel: "v3",
};
const prompt = {
  targetType: "prompt",
  promptId: "p1",
  promptVersion: "pv5",
  name: "Support Reply",
  versionLabel: "V5",
  model: [{ value: "gpt-4o" }, { value: "claude-sonnet-4-5" }],
};

describe("runPlan", () => {
  it("expands prompts per model and keeps card order for letters", () => {
    const runs = flattenRuns([agent, prompt]);
    expect(runs.map((r) => `${r.letter} ${r.name}`)).toEqual([
      "A Support Agent v3",
      "B Support Reply V5 × gpt-4o",
      "C Support Reply V5 × claude-sonnet-4-5",
    ]);
    expect(lettersByConfig([agent, prompt])).toEqual({
      0: ["A"],
      1: ["B", "C"],
    });
  });

  it("gives a prompt with no model no run", () => {
    expect(flattenRuns([{ ...prompt, model: [] }, agent])).toHaveLength(1);
    expect(flattenRuns([{ ...prompt, model: [] }, agent])[0].letter).toBe("A");
  });

  it("names external and http runs", () => {
    const runs = flattenRuns([
      { targetType: "external", runName: "ci@main" },
      {
        targetType: "http",
        http: { url: "https://api.acme.ai/v1/reply", method: "POST" },
      },
      { targetType: "external", runName: "" },
    ]);
    expect(runs.map((r) => r.name)).toEqual([
      "ci@main",
      "POST api.acme.ai/v1/reply",
      "From code",
    ]);
  });

  it("derives the target of legacy cards", () => {
    expect(targetTypeOf({ agentId: "x" })).toBe("agent");
    expect(targetTypeOf({ promptId: "x" })).toBe("prompt");
  });

  it("counts non-LLM runs per model and voice", () => {
    expect(
      runCount("tts", [
        { model: [{ value: "a", voices: [{ voice: "x" }, { voice: "y" }] }] },
      ]),
    ).toBe(2);
    expect(runCount("llm", [agent, prompt])).toBe(3);
  });

  it("detects an all-external experiment", () => {
    expect(allRunsExternal([{ targetType: "external" }])).toBe(true);
    expect(allRunsExternal([{ targetType: "external" }, agent])).toBe(false);
    expect(allRunsExternal([])).toBe(false);
    // R4: Trace + upload waits for the script; Connect live runs right away.
    expect(
      allRunsExternal([{ targetType: "code", r4: { form: "upload" } }]),
    ).toBe(true);
    expect(
      allRunsExternal([{ targetType: "code", r4: { form: "connect" } }]),
    ).toBe(false);
    // Remote endpoint: Future AGI calls the URL, nothing waits on a machine.
    expect(
      allRunsExternal([
        { targetType: "code", r4: { form: "upload", mode: "remote" } },
      ]),
    ).toBe(false);
  });

  it("writes the execution line", () => {
    expect(executionSummary({ examples: 48, trials: 3, runs: 2 })).toBe(
      "48 rows × 3 tries × 2 runs = 288 tries",
    );
    expect(executionSummary({ examples: 1, trials: 1, runs: 1 })).toBe(
      "1 row × 1 try × 1 run = 1 try",
    );
    expect(executionSummary({ examples: null, runs: 1 })).toBeNull();
  });
});

describe("draftSummary (CR#26)", () => {
  it("names runs by type, mapped variables and evaluations", () => {
    expect(
      draftSummary({
        promptConfig: [
          { ...prompt, variableMapping: { a: "c1", b: "c2", c: "" } },
          { targetType: "http", http: { url: "https://x.ai" } },
        ],
        userEvalMetrics: [{}, {}],
      }),
    ).toBe(
      "3 runs (prompt, HTTP endpoint) · 2 mapped variables · 2 evaluations",
    );
  });
  it("is empty for an empty draft and singular for one", () => {
    expect(draftSummary({})).toBe("");
    expect(draftSummary({ promptConfig: [agent] })).toBe("1 run (agent)");
  });
});

describe("runDisplayName · HTTP (CR#34)", () => {
  it("names from the host only when the URL parses", () => {
    const http = (url) => ({
      targetType: "http",
      http: { method: "POST", url },
    });
    expect(runDisplayName(http("not a url"), null)).toBe("HTTP endpoint");
    expect(runDisplayName(http("https://api.acme.ai/reply"), null)).toBe(
      "POST api.acme.ai/reply",
    );
  });
});
