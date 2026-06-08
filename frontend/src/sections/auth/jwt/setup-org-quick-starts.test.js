import { beforeEach, describe, expect, it } from "vitest";
import {
  appendSetupQuickStartAttributionToHref,
  normalizeSetupQuickStartAttribution,
  persistSetupQuickStartAttribution,
  readPersistedSetupQuickStartAttribution,
  SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY,
  SETUP_ORG_FIRST_SETUP_QUICK_START_IDS,
  SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS,
  isSetupOrgFirstSetupQuickStart,
  setupQuickStartAttributionFromId,
} from "./setup-org-quick-starts";

describe("setup org product-loop quick starts", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("covers each first-run product path with a canonical goal", () => {
    expect(
      SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.map((option) => [
        option.id,
        option.goal,
        option.goalLabel,
        option.primaryPath,
      ]),
    ).toEqual([
      ["observe", "monitor_production_ai_app", "Connect your agent", "observe"],
      ["prompt", "improve_prompts", "Prove a prompt edit is better", "prompt"],
      [
        "agent",
        "build_ai_agent",
        "Watch your agent handle a hard call",
        "agent",
      ],
      [
        "gateway",
        "control_model_traffic",
        "Route LLM traffic safely",
        "gateway",
      ],
      [
        "evals",
        "evaluate_quality",
        "Catch failing responses before users do",
        "evals",
      ],
      ["voice", "connect_voice_ai_agent", "Test a voice agent", "voice"],
      [
        "sample_preview",
        "explore_sample_data",
        "Preview sample trace",
        "sample",
      ],
    ]);
  });

  it("keeps product setup paths primary and sample data secondary", () => {
    const sampleQuickStarts = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.filter(
      (option) => option.sample,
    );
    const observeQuickStart = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.find(
      (option) => option.id === "observe",
    );

    expect(sampleQuickStarts.map((option) => option.id)).toEqual([
      "sample_preview",
    ]);
    expect(sampleQuickStarts[0]).toMatchObject({
      goal: "explore_sample_data",
      primaryPath: "sample",
      buttonLabel: "Preview sample trace",
    });
    expect(observeQuickStart).toMatchObject({
      buttonLabel: "Connect your agent",
      featured: true,
      firstActionLabel: "Choose package",
      pathPreview:
        "Choose package, copy setup code, send trace, review trace, create quality check.",
      primaryPath: "observe",
      sequencePreview: [
        "Choose package",
        "Copy setup code",
        "Send trace",
        "Review trace",
        "Create quality check",
      ],
    });
    expect(observeQuickStart.sample).toBeUndefined();
  });

  it("shows the full prompt versioning loop before comparison", () => {
    const promptQuickStart = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.find(
      (option) => option.id === "prompt",
    );

    expect(promptQuickStart).toMatchObject({
      buttonLabel: "Prove a prompt edit is better",
      firstActionLabel: "Write the prompt you want to improve",
      pathPreview:
        "Write the prompt, see how it scores, lock a baseline, try an edit, see which wins.",
      sequencePreview: [
        "Write the prompt you want to improve",
        "See how it scores on real cases",
        "Lock a baseline",
        "Try an edit and rerun",
        "See which edit wins",
      ],
    });
  });

  it("shows the full agent prototype loop through eval coverage", () => {
    const agentQuickStart = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.find(
      (option) => option.id === "agent",
    );

    expect(agentQuickStart).toMatchObject({
      buttonLabel: "Watch your agent handle a hard call",
      firstActionLabel: "Stand up an agent you can run",
      pathPreview:
        "Stand up the agent, give it a prompt, run a scenario, see where it failed, add coverage.",
      sequencePreview: [
        "Stand up an agent you can run",
        "Give it a prompt and a model",
        "Watch your agent handle a real scenario",
        "See where it failed and why",
        "Catch that failure automatically",
      ],
    });
  });

  it("shows the full gateway request review and control loop", () => {
    const gatewayQuickStart = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.find(
      (option) => option.id === "gateway",
    );

    expect(gatewayQuickStart).toMatchObject({
      buttonLabel: "Route LLM traffic safely",
      firstActionLabel: "Route your first request",
      pathPreview:
        "Route the first request, get a key, see cost + latency, trace the log, add guardrails.",
      sequencePreview: [
        "Route your first request",
        "Get a key to route through",
        "See cost + latency per call",
        "Trace where time and spend went",
        "Put guardrails on future traffic",
      ],
    });
  });

  it("shows the full voice test-call loop through monitoring", () => {
    const voiceQuickStart = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.find(
      (option) => option.id === "voice",
    );

    expect(voiceQuickStart).toMatchObject({
      buttonLabel: "Test a voice agent",
      firstActionLabel: "Bring in a voice agent to test",
      pathPreview:
        "Bring in the agent, hear a call, see timing + interruptions, define good, keep watching.",
      sequencePreview: [
        "Bring in a voice agent to test",
        "Hear how a call goes",
        "See timing, interruptions, and outcome",
        "Define what a good call sounds like",
        "Keep watching live calls",
      ],
    });
  });

  it("gives every first setup path a visible sequence preview", () => {
    const firstSetupQuickStarts = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.filter(
      isSetupOrgFirstSetupQuickStart,
    );

    expect(firstSetupQuickStarts).toHaveLength(
      SETUP_ORG_FIRST_SETUP_QUICK_START_IDS.length,
    );
    firstSetupQuickStarts.forEach((option) => {
      expect(option.sequencePreview).toEqual(
        expect.arrayContaining([option.firstActionLabel]),
      );
      expect(option.sequencePreview.length).toBeGreaterThanOrEqual(4);
    });
  });

  it("gives every first setup path a concrete outcome preview", () => {
    const firstSetupQuickStarts = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.filter(
      isSetupOrgFirstSetupQuickStart,
    );

    firstSetupQuickStarts.forEach((option) => {
      expect(option.outcomePreview).toEqual(expect.any(String));
      expect(option.outcomePreview.length).toBeGreaterThan(20);
    });
    expect(
      firstSetupQuickStarts.find((option) => option.id === "observe")
        ?.outcomePreview,
    ).toBe("A real trace reviewed and a quality check ready to add.");
    expect(
      firstSetupQuickStarts.find((option) => option.id === "voice")
        ?.outcomePreview,
    ).toBe("A test call transcript with success criteria to add.");
  });

  it("keeps the signup picker to the first setup paths", () => {
    expect(SETUP_ORG_FIRST_SETUP_QUICK_START_IDS).toEqual([
      "observe",
      "prompt",
      "agent",
      "gateway",
      "evals",
      "voice",
    ]);
    expect(
      SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.filter(
        isSetupOrgFirstSetupQuickStart,
      ).map((option) => option.buttonLabel),
    ).toEqual([
      "Connect your agent",
      "Prove a prompt edit is better",
      "Watch your agent handle a hard call",
      "Route LLM traffic safely",
      "Catch failing responses before users do",
      "Test a voice agent",
    ]);
  });

  it("normalizes quick-start attribution from known quick-start ids", () => {
    expect(setupQuickStartAttributionFromId("observe")).toEqual({
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
    });
    expect(
      normalizeSetupQuickStartAttribution({
        quickStartGoal: "monitor_production_ai_app",
        quickStartId: "observe",
        quickStartPrimaryPath: "observe",
      }),
    ).toEqual({
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
    });
  });

  it("drops unknown or mismatched quick-start attribution", () => {
    expect(setupQuickStartAttributionFromId("unknown")).toBeNull();
    expect(
      normalizeSetupQuickStartAttribution({
        quickStartGoal: "monitor_production_ai_app",
        quickStartId: "unknown",
        quickStartPrimaryPath: "observe",
      }),
    ).toEqual({});
    expect(
      normalizeSetupQuickStartAttribution({
        quickStartGoal: "connect_voice_ai_agent",
        quickStartId: "observe",
        quickStartPrimaryPath: "observe",
      }),
    ).toEqual({});
    expect(
      normalizeSetupQuickStartAttribution({
        quickStartGoal: "monitor_production_ai_app",
        quickStartId: "observe",
        quickStartPrimaryPath: "voice",
      }),
    ).toEqual({});
  });

  it("adds sanitized quick-start attribution to internal routes", () => {
    expect(
      appendSetupQuickStartAttributionToHref(
        "/dashboard/observe/observe-1/trace/trace-1?sample=true#details",
        {
          quickStartGoal: "explore_sample_data",
          quickStartId: "sample_preview",
          quickStartPrimaryPath: "sample",
        },
      ),
    ).toBe(
      "/dashboard/observe/observe-1/trace/trace-1?sample=true&quick_start_goal=explore_sample_data&quick_start_id=sample_preview&quick_start_primary_path=sample#details",
    );

    expect(
      appendSetupQuickStartAttributionToHref(
        "/dashboard/home?quick_start_id=observe",
        {
          quick_start_goal: "explore_sample_data",
          quick_start_id: "sample_preview",
          quick_start_primary_path: "sample",
        },
      ),
    ).toBe(
      "/dashboard/home?quick_start_id=sample_preview&quick_start_goal=explore_sample_data&quick_start_primary_path=sample",
    );
  });

  it("does not add invalid quick-start attribution to routes", () => {
    expect(
      appendSetupQuickStartAttributionToHref("/dashboard/home?sample=true", {
        quickStartGoal: "secret",
        quickStartId: "user@example.com",
        quickStartPrimaryPath: "observe",
      }),
    ).toBe("/dashboard/home?sample=true");
    expect(
      appendSetupQuickStartAttributionToHref("//example.com/path", {
        quickStartGoal: "explore_sample_data",
        quickStartId: "sample_preview",
        quickStartPrimaryPath: "sample",
      }),
    ).toBe("//example.com/path");
  });

  it("persists only normalized quick-start attribution", () => {
    expect(
      persistSetupQuickStartAttribution({
        quickStartGoal: "monitor_production_ai_app",
        quickStartId: "observe",
        quickStartPrimaryPath: "observe",
      }),
    ).toEqual({
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
    });
    expect(readPersistedSetupQuickStartAttribution()).toEqual({
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
    });

    expect(
      persistSetupQuickStartAttribution({
        quickStartGoal: "secret",
        quickStartId: "user@example.com",
        quickStartPrimaryPath: "observe",
      }),
    ).toEqual({});
    expect(readPersistedSetupQuickStartAttribution()).toEqual({});
  });

  it("drops corrupted persisted attribution", () => {
    window.sessionStorage.setItem(
      SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY,
      "{",
    );

    expect(readPersistedSetupQuickStartAttribution()).toEqual({});
    expect(
      window.sessionStorage.getItem(SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY),
    ).toBeNull();
  });
});
