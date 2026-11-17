// options-truth L5 G2: Rerun with changes prefills the model the run really
// called, prices a try at most at its cap, and a param the run never set
// reads `default` in What changed.
import { describe, expect, it } from "vitest";
import { promptChangesOf, runModelOf } from "../RerunDialogParts";
import { rerunEstimate } from "src/sections/common/Runs/rerunEstimate";
import { perTryCapOf } from "src/sections/experiment-detail/ExperimentBarRightSection/rerunPlan";
import { configDiff } from "src/sections/common/Runs/configDiff";

const agent = {
  kind: "agent",
  agent: {
    graph_id: "g",
    version: "v3",
    nodes: [
      { id: "classify_intent", type: "llm", model: "gpt-4o-mini" },
      { id: "lookup_order", type: "tool" },
      { id: "respond", type: "llm", model: "gpt-4o" },
    ],
  },
};

describe("Rerun with changes · the run's real model", () => {
  it("reads the prompt's model, the agent's own, else its reply node's", () => {
    expect(runModelOf({ model: { id: "gpt-4.1" } })).toBe("gpt-4.1");
    expect(runModelOf(agent)).toBe("gpt-4o");
    expect(
      runModelOf({ ...agent, agent: { ...agent.agent, model: "o3" } }),
    ).toBe("o3");
    expect(runModelOf({})).toBeNull();
  });
  it("an agent keeping its model sends no model change; a new one does", () => {
    expect(promptChangesOf(agent, { model: "gpt-4o" })).toEqual([]);
    expect(promptChangesOf(agent, { model: "claude-sonnet-4-5" })).toEqual([
      { axis: "model", field: "id", value: "claude-sonnet-4-5" },
    ]);
    expect(promptChangesOf(agent, { temperature: "0.9" })).toEqual([
      { axis: "params", field: "temperature", value: 0.9 },
    ]);
  });
  it("What changed says `default` for a param the parent never set", () => {
    const child = {
      ...agent,
      model: { id: "gpt-4o", params: { temperature: 0.9 } },
    };
    const text = JSON.stringify(configDiff(agent, child));
    expect(text).toMatch(/temperature default → 0\.9/);
    expect(text).not.toMatch(/temperature none/);
  });
});

describe("rerunEstimate · per try = min(model estimate, per-try cap)", () => {
  it("caps each try before multiplying by tries", () => {
    const e = rerunEstimate({
      rows: 10,
      trials: 3,
      costPerTry: 0.08,
      maxCostPerTry: 0.05,
    });
    expect(e.tries).toBe(30);
    expect(e.cost).toBeCloseTo(1.5, 6);
    const cheap = rerunEstimate({
      rows: 10,
      trials: 3,
      costPerTry: 0.02,
      maxCostPerTry: 0.05,
    });
    expect(cheap.cost).toBeCloseTo(0.6, 6);
    const none = rerunEstimate({ rows: 10, trials: 3, costPerTry: 0.08 });
    expect(none.cost).toBeCloseTo(2.4, 6);
  });
  it("reads the cap from the run's limits or its harness budget", () => {
    expect(perTryCapOf({ limits: { max_cost_per_try_usd: 0.05 } })).toBe(0.05);
    expect(perTryCapOf({ harness: { max_cost_usd: 0.5 } })).toBe(0.5);
    expect(perTryCapOf({})).toBeNull();
    expect(perTryCapOf({ limits: { max_cost_per_try_usd: "" } })).toBeNull();
  });
});
