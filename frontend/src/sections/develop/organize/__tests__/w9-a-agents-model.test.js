// W9 A7 (W9-SCOPE §5): the Agents dialog model and the Agents column words.
import { describe, expect, it } from "vitest";
import {
  agentBuilderHref,
  agentChanges,
  agentChangesProblem,
  agentChipText,
  agentTooltip,
  agentsCopy,
  attachState,
  nextAgentIds,
  suggestedGraphs,
} from "../agentsModel";

const A = { id: "a", agent_ids: ["g1"] };
const B = { id: "b", agent_ids: [] };

describe("attach and detach", () => {
  it("states and next ids", () => {
    expect(attachState([A, B], "g1")).toBe("some");
    expect(attachState([A], "g1")).toBe("all");
    expect(attachState([B], "g1")).toBe("none");
    expect(nextAgentIds([A, B], { g1: "attach", g2: "attach" })).toEqual({
      a: ["g1", "g2"],
      b: ["g1", "g2"],
    });
    expect(nextAgentIds([A, B], { g1: "detach" })).toEqual({ a: [], b: [] });
  });

  it("changes only the datasets that differ, within the limit", () => {
    expect(agentChanges([A, B], { g1: "attach" })).toEqual([
      { ...B, next: ["g1"] },
    ]);
    const many = Array.from({ length: 21 }, (_, i) => `g${i}`);
    const choices = Object.fromEntries(many.map((g) => [g, "attach"]));
    expect(agentChangesProblem(agentChanges([B], choices), many)).toBe(
      "A dataset can be attached to up to 20 agents.",
    );
    expect(
      agentChangesProblem(agentChanges([B], { gx: "attach" }), ["g1"]),
    ).toBe("Agent gx doesn't exist in this workspace.");
  });

  it("suggests each graph once", () => {
    expect(
      suggestedGraphs([
        { suggested_agents: [{ id: "g1", name: "S" }] },
        {
          suggested_agents: [
            { id: "g1", name: "S" },
            { id: "g2", name: "T" },
          ],
        },
      ]).map((g) => g.id),
    ).toEqual(["g1", "g2"]);
  });
});

describe("words", () => {
  const agent = {
    id: "g1",
    name: "Support Agent",
    last_run: { label: "#4B", version: "v3", display: "86.6%" },
  };
  const row = {
    baseline: { label: "#2B", display: "79.4%", metric_name: "Success rate" },
  };

  it("chip and tooltip", () => {
    expect(agentChipText(agent)).toBe("Support Agent · #4B 86.6%");
    expect(agentChipText({ id: "g2", name: "Billing", last_run: null })).toBe(
      "Billing · no run yet",
    );
    expect(agentTooltip(agent, row)).toBe(
      "Latest run on Support Agent: #4B (v3), 86.6% Success rate. Baseline #2B 79.4%.",
    );
    expect(agentTooltip({ name: "Billing", last_run: null }, row)).toBe(
      "No finished run on Billing yet.",
    );
    expect(agentBuilderHref("g1")).toBe(
      "/dashboard/agents/playground/g1/build",
    );
    expect(agentsCopy(1)).toMatch(
      /^Attach this dataset to the agents it tests\./,
    );
    expect(agentsCopy(3)).toMatch(/^Attach these 3 datasets/);
  });
});
