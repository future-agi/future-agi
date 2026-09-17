import { describe, it, expect } from "vitest";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { MOCK_READING } from "src/api/simulate-environments/_fixtures/preflightFails";
import { AGENT_TYPES } from "src/sections/agents/constants";
import {
  seedFromTemplate,
  seedAgentBuilt,
  envFromDraft,
} from "../seedEnvState";

const NOW = "2026-09-17T10:00:00.000Z";
const template = MOCK_WORLD;

describe("seedFromTemplate", () => {
  const state = seedFromTemplate(template, NOW);

  it("seeds a template-baseline agent reached via seed", () => {
    expect(state.agent.via).toBe("seed");
    expect(state.agent.seeded).toBe(true);
    expect(state.agent.name).toBe("Template baseline");
  });

  it("stamps the template sticker and starts with no added evals", () => {
    expect(state.seededFromTemplate).toBe(true);
    expect(state.evals).toEqual([]);
  });

  it("records v1 agent and env versions with the template notes", () => {
    expect(state.envVersions[0].note).toBe("First build from the template.");
    expect(state.agentVersions[0].note).toBe("Shipped with the template.");
    expect(state.envVersions[0].label).toBe("v1");
  });

  it("primes the full generated scenario pool from the templates source", () => {
    expect(state.scenarios.length).toBe(generatedPool(template).length);
    expect(state.scenarioSource).toBe("templates");
  });
});

describe("seedAgentBuilt", () => {
  const draft = { kind: "repo", value: "github.com/acme/support-agent" };
  const state = seedAgentBuilt(draft, null, NOW);

  it("connects the agent via the endpoint", () => {
    expect(state.agent.via).toBe("endpoint");
  });

  it("records v1 agent and env versions with the agent-build notes", () => {
    expect(state.agentVersions[0].note).toBe(
      "First version connected to this environment.",
    );
    expect(state.envVersions[0].note).toBe("First build from the agent.");
  });

  it("primes scenarios and starts with empty evals and runs", () => {
    expect(state.scenarios.length).toBe(generatedPool(MOCK_WORLD).length);
    expect(state.evals).toEqual([]);
    expect(state.runs).toEqual([]);
  });
});

describe("envFromDraft", () => {
  it("names the environment from the passed name", () => {
    const env = envFromDraft({ kind: "repo", value: "x" }, "support-agent", null);
    expect(env.name).toBe("support-agent");
  });

  it("surfaces voice for a voice provider draft", () => {
    const env = envFromDraft(
      { kind: "platform", provider: "vapi", agentId: "a1" },
      "voice-agent",
      null,
    );
    expect(env.surface).toBe("voice");
    expect(env.agentType).toBe(AGENT_TYPES.VOICE);
  });

  it("surfaces chat for a code draft", () => {
    const env = envFromDraft({ kind: "repo", value: "x" }, "code-agent", null);
    expect(env.surface).toBe("chat");
    expect(env.agentType).toBe(AGENT_TYPES.CHAT);
  });

  it("draws the world from MOCK_WORLD and carries the redacted draft", () => {
    const draft = { kind: "repo", value: "x" };
    const env = envFromDraft(draft, "code-agent", null);
    expect(env.tools).toEqual(MOCK_WORLD.tools);
    expect(env.rules).toEqual(MOCK_WORLD.rules);
    expect(env.seed).toEqual(MOCK_WORLD.seed);
    expect(env.builtFrom).toBe(draft);
    expect(env.id).toBeUndefined();
  });

  it("falls back to MOCK_WORLD when the read-audit is only a display projection", () => {
    const env = envFromDraft({ kind: "repo", value: "x" }, "code-agent", {
      reading: MOCK_READING,
    });
    expect(env.tools).toEqual(MOCK_WORLD.tools);
  });
});
