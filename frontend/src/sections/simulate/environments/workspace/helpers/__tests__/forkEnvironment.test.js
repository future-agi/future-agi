import { describe, it, expect } from "vitest";
import { forkEnvironment } from "../forkEnvironment";

const NOW = "2026-09-17T10:00:00.000Z";

const env = {
  id: "env-1",
  name: "Voice support",
  surface: "voice",
  agentType: "voice",
  domain: "ecommerce",
  tagline: "Inbound phone support",
  description: "A returns line.",
  difficulty: "Starter",
  tools: [{ name: "lookup_order" }],
  rules: ["Refunds over $200 need approval"],
  seed: { tables: [{ name: "orders" }] },
  evalPreset: ["task_success"],
  adoptedAt: "2026-09-01T00:00:00.000Z",
  // The parent's bridge into the product run: a fork must not inherit any of it.
  platform: { runTestId: "rt-parent", testExecutionId: "ex-parent" },
  buildStatus: "building",
  buildProgress: { done: 3, total: 7 },
  stageOutputs: [{ kind: "contract", data: {} }],
};

const envState = {
  agent: { via: "endpoint", typeId: "voice" },
  additionalAgents: [{ id: "agent-extra", typeId: "text" }],
  activeAgentId: "agent-extra",
  agentVersions: [{ id: "agent-v1", label: "v1" }],
  scenarios: [{ id: "s1" }, { id: "s2" }],
  evals: [{ id: "task_success" }],
  scenarioSource: "templates",
  seededFromTemplate: true,
  runs: [{ id: "run-1" }],
};

describe("forkEnvironment", () => {
  const fork = forkEnvironment(env, envState, NOW);

  it("mints a fork id suffixed off the source id", () => {
    expect(fork.env.id).toMatch(/^env-1-fork-[a-z0-9]+$/);
  });

  it("appends the fork marker to the name", () => {
    expect(fork.env.name).toBe("Voice support · fork");
  });

  it("stamps adoptedAt with the passed time", () => {
    expect(fork.env.adoptedAt).toBe(NOW);
  });

  it("peels off the template sticker", () => {
    expect(fork.envState.seededFromTemplate).toBe(false);
  });

  it("restarts the env version lineage at v1 noting the source", () => {
    expect(fork.envState.envVersions[0].label).toBe("v1");
    expect(fork.envState.envVersions[0].note).toBe("Forked from Voice support.");
  });

  it("carries no run history", () => {
    expect(fork.envState.runs).toEqual([]);
  });

  it("copies scenarios and evals into fresh arrays", () => {
    expect(fork.envState.scenarios).toEqual(envState.scenarios);
    expect(fork.envState.scenarios).not.toBe(envState.scenarios);
    expect(fork.envState.evals).toEqual(envState.evals);
    expect(fork.envState.evals).not.toBe(envState.evals);
  });

  it("copies the agent and its version history", () => {
    expect(fork.envState.agent).toEqual(envState.agent);
    expect(fork.envState.agentVersions[0].label).toBe("v1");
  });

  it("carries the additional agents into a fresh array and keeps the active pointer", () => {
    expect(fork.envState.additionalAgents).toEqual(envState.additionalAgents);
    expect(fork.envState.additionalAgents).not.toBe(envState.additionalAgents);
    expect(fork.envState.activeAgentId).toBe("agent-extra");
  });

  it("carries the world over to the fork", () => {
    expect(fork.env.tools).toEqual(env.tools);
    expect(fork.env.rules).toEqual(env.rules);
    expect(fork.env.seed).toEqual(env.seed);
    expect(fork.env.evalPreset).toEqual(env.evalPreset);
    expect(fork.env.surface).toBe("voice");
    expect(fork.env.agentType).toBe("voice");
    expect(fork.env.domain).toBe("ecommerce");
    expect(fork.env.tagline).toBe("Inbound phone support");
    expect(fork.env.description).toBe("A returns line.");
    expect(fork.env.difficulty).toBe("Starter");
  });

  it("does not inherit the parent's run bridge or build state", () => {
    expect(fork.env.platform).toBeUndefined();
    expect(fork.env.buildStatus).toBeUndefined();
    expect(fork.env.buildProgress).toBeUndefined();
    expect(fork.env.stageOutputs).toBeUndefined();
  });

  it("carries no unexpected field over from the parent", () => {
    expect(Object.keys(fork.env).sort()).toEqual(
      [
        "adoptedAt", "agentType", "custom", "description", "difficulty", "domain",
        "evalPreset", "forkedFrom", "id", "name", "rules", "seed", "surface",
        "tagline", "tools",
      ].sort(),
    );
  });
});
