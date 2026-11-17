import { describe, expect, it } from "vitest";
import { NO_BASELINE, runMatches, sameTarget } from "../runMatch";

const agent = (id, graph = "GRAPH_SUPPORT") => ({
  run_id: id,
  label: `#${id.slice(4)}`,
  name: "Support Agent v3",
  experiment_name: "Support agent v3 · 3 trials",
  target_type: "agent",
  target: { graph_id: graph, graph_name: "Support Agent" },
});
const prompt = (id, promptId = "PROMPT_REPLY", model = "gpt-4o-mini") => ({
  run_id: id,
  label: `#${id.slice(4)}`,
  name: `Support Reply V4 × ${model}`,
  target_type: "prompt",
  target: { prompt_id: promptId, prompt_name: "Support Reply", model },
});

describe("runMatches", () => {
  it("matches label with or without #, name, experiment and model", () => {
    const r = agent("RUN_4A");
    expect(runMatches(r, "")).toBe(true);
    expect(runMatches(r, "#4")).toBe(true);
    expect(runMatches(r, "4a")).toBe(true);
    expect(runMatches(r, "support agent")).toBe(true);
    expect(runMatches(r, "3 trials")).toBe(true);
    expect(runMatches(prompt("RUN_2B", "P", "claude-sonnet"), "sonnet")).toBe(
      true,
    );
    expect(runMatches(r, "#7")).toBe(false);
    expect(runMatches(null, "x")).toBe(false);
  });
});

describe("runMatches · frameworks, harnesses and models (IT-R2 G2 P1)", () => {
  it("finds a run by its framework, harness or any model it called", () => {
    const crew = {
      run_id: "R3C",
      label: "#3C",
      name: "CrewAI support crew",
      target: { framework: "CrewAI" },
      config: {
        framework: { id: "crewai", name: "CrewAI" },
        models: [{ id: "gpt-4o" }],
      },
    };
    const cc = {
      run_id: "R3E",
      label: "#3E",
      name: "Support run",
      target: { harness: "Claude Code", model: "claude-sonnet-4-5" },
      config: { harness: { name: "Claude Code" } },
    };
    expect(runMatches({ ...crew, name: "Support crew" }, "crewai")).toBe(true);
    expect(runMatches(crew, "gpt-4o")).toBe(true);
    expect(runMatches(cc, "claude code")).toBe(true);
    expect(runMatches(cc, "crewai")).toBe(false);
  });
});

describe("sameTarget", () => {
  it("compares graphs, prompts, URLs and external runs", () => {
    expect(sameTarget(agent("RUN_3A"), agent("RUN_4A"))).toBe(true);
    expect(sameTarget(agent("RUN_3A"), agent("RUN_9A", "GRAPH_OTHER"))).toBe(
      false,
    );
    expect(sameTarget(prompt("RUN_1A"), prompt("RUN_2B"))).toBe(true);
    expect(sameTarget(prompt("RUN_1A"), prompt("RUN_9A", "OTHER"))).toBe(false);
    expect(sameTarget(agent("RUN_3A"), prompt("RUN_1A"))).toBe(false);
    expect(
      sameTarget(
        { target_type: "http", target: { url: "api.acme.ai/v1/reply" } },
        { target_type: "http", target: { url: "api.acme.ai/v1/reply" } },
      ),
    ).toBe(true);
    expect(
      sameTarget(
        { target_type: "external", target: { branch: "main" } },
        { target_type: "external", target: { branch: "pr-412" } },
      ),
    ).toBe(true);
    expect(sameTarget(null, agent("RUN_3A"))).toBe(false);
  });

  it("names the no-baseline value", () => {
    expect(NO_BASELINE).toBe("none");
  });
});
