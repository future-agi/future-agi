import { describe, it, expect } from "vitest";
import { forkEnvironment } from "../forkEnvironment";

const NOW = "2026-09-17T10:00:00.000Z";

const env = {
  id: "env-1",
  name: "Voice support",
  surface: "voice",
  adoptedAt: "2026-09-01T00:00:00.000Z",
  buildProgress: { done: 14, total: 14 },
  platform: {
    runTestId: "rt-parent",
    testExecutionId: "ex-parent",
    simulationUrl: "https://example.test/run/ex-parent",
  },
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

  it("carries none of the parent's platform run links", () => {
    // `platform` is how the Runs tab and the header's Run action find an
    // environment's executions. Copied over, a brand-new fork opens showing the
    // parent's runs and "Run again" re-runs the parent.
    expect(fork.env.platform).toBeUndefined();
    expect(fork.env.buildProgress).toBeUndefined();
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
});
