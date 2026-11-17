// W6 A2: Open in Prompts / Agent Playground keep the run's version.
import { describe, expect, it } from "vitest";
import { agentLinkOf, promptLinkOf } from "../targetLinks";

describe("targetLinks", () => {
  it("opens the run's prompt version", () => {
    expect(promptLinkOf({ prompt_id: "p1", prompt_version_name: "V3" })).toBe(
      "/dashboard/workbench/create/p1?tab=Playground&version=V3",
    );
    expect(
      promptLinkOf({}, { config: { prompt: { id: "p2", version: "V 5" } } }),
    ).toBe("/dashboard/workbench/create/p2?tab=Playground&version=V%205");
    expect(promptLinkOf({ prompt_id: "p1" })).toBe(
      "/dashboard/workbench/create/p1?tab=Playground",
    );
    expect(promptLinkOf({})).toBeNull();
  });
  it("opens the run's graph version by id", () => {
    expect(
      agentLinkOf({ graph_id: "g1", version_id: "v-3", version_name: "v3" }),
    ).toBe("/dashboard/agents/playground/g1/build?version=v-3");
    expect(
      agentLinkOf({}, { config: { agent: { id: "g2", version_id: "x" } } }),
    ).toBe("/dashboard/agents/playground/g2/build?version=x");
    expect(agentLinkOf({ graph_id: "g1" })).toBe(
      "/dashboard/agents/playground/g1/build",
    );
    expect(agentLinkOf(null)).toBeNull();
  });
});
