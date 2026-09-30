import { AGENT_TYPES } from "src/sections/agents/constants";
import { environmentNameFor } from "src/api/simulate-environments/preflightPayload";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { SEED_COPY } from "./seedEnvState.constants";

// Transports that make an agent a voice agent. A build draft that names one of
// these providers surfaces the environment as voice; anything else is chat.
const VOICE_PROVIDERS = ["livekit", "vapi", "retell", "twilio", "pipecat"];

const surfaceFromDraft = (draft) =>
  draft?.provider && VOICE_PROVIDERS.includes(draft.provider) ? "voice" : "chat";

// The world (tools/rules/seed/description) an agent build derives against. The
// accepted read-audit is the real seam, but today its `reading` is a display
// projection (truncated rule strings, no seed tables), not a runnable world —
// so a reading without a `seed` block falls back to the v1 MOCK_WORLD overlay.
// A future read-audit that carries a full world satisfies the guard and is used
// verbatim.
const worldFor = (audit) =>
  audit?.reading?.seed ? audit.reading : MOCK_WORLD;

// Build the env record adopted for an agent-built environment. The id is minted
// by the build step and spread in by the caller, so it is intentionally absent
// here. Fields are picked explicitly so the world's own id/name never leak in.
export function envFromDraft(draft, name, audit) {
  const world = worldFor(audit);
  const surface = surfaceFromDraft(draft);
  return {
    name,
    surface,
    agentType: surface === "voice" ? AGENT_TYPES.VOICE : AGENT_TYPES.CHAT,
    tools: world.tools || [],
    rules: world.rules || [],
    seed: world.seed,
    description: world.description,
    evalPreset: world.evalPreset,
    domain: world.domain,
    tagline: world.tagline,
    difficulty: world.difficulty,
    builtFrom: draft,
  };
}

// Per-env state for a template-adopted environment: the template's baseline
// agent (a seeded v1), the full generated scenario pool from the templates
// source, no added evals (the preset surfaces as Suggested), and the sticker
// that locks version editing until the env is forked.
export function seedFromTemplate(template, now) {
  const scenarios = generatedPool(template);
  return {
    agent: {
      typeId: template.agentType,
      values: {},
      via: "seed",
      seeded: true,
      name: SEED_COPY.templateBaselineName,
      connectedAt: now,
    },
    agentVersions: [
      {
        id: "agent-v1",
        label: "v1",
        note: SEED_COPY.templateAgentNote,
        reach: "seed",
        createdAt: now,
      },
    ],
    envVersions: [
      {
        id: `${template.id}-v1`,
        label: "v1",
        createdAt: now,
        note: SEED_COPY.templateEnvNote,
        scenarios: scenarios.length,
        changed: ["contract", "seed"],
      },
    ],
    envDerivedForAgent: "v1",
    activeAgentVersion: "v1",
    scenarios,
    scenarioSource: "templates",
    evals: [],
    runs: [],
    seededFromTemplate: true,
  };
}

// Per-env state for an agent-built environment: a fresh v1 for both the agent
// and the environment, the scenarios derived from the build's world, and empty
// evals/runs. `seededFromTemplate` stays false so version editing is unlocked.
export function seedAgentBuilt(draft, audit, now) {
  const world = worldFor(audit);
  const scenarios = generatedPool(world);
  return {
    agent: {
      typeId: draft?.provider || "auto",
      values: {},
      via: "endpoint",
      connectedAt: now,
    },
    agentVersions: [
      {
        id: "agent-v1",
        label: "v1",
        note: SEED_COPY.agentVersionNote,
        reach: "endpoint",
        createdAt: now,
      },
    ],
    activeAgentVersion: "v1",
    envVersions: [
      {
        id: `${environmentNameFor(draft)}-v1`,
        label: "v1",
        createdAt: now,
        note: SEED_COPY.agentEnvNote,
        scenarios: scenarios.length,
        changed: ["contract", "seed"],
      },
    ],
    envDerivedForAgent: "v1",
    scenarios,
    evals: [],
    runs: [],
    seededFromTemplate: false,
  };
}
