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
      ["prompt", "improve_prompts", "Test prompts or agent prompts", "prompt"],
      ["agent", "build_ai_agent", "Prototype agent", "agent"],
      ["gateway", "control_model_traffic", "Set up gateway", "gateway"],
      ["evals", "evaluate_quality", "Test AI with Simulation / Evals", "evals"],
      ["voice", "connect_voice_ai_agent", "Connect a voice AI agent", "voice"],
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
        "Choose package, copy setup code, send trace, review trace, create evaluator.",
      primaryPath: "observe",
      sequencePreview: [
        "Choose package",
        "Copy setup code",
        "Send trace",
        "Review trace",
        "Create evaluator",
      ],
    });
    expect(observeQuickStart.sample).toBeUndefined();
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
    ).toBe("A real trace reviewed and an evaluator ready to create.");
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
      "Test prompts or agent prompts",
      "Prototype agent",
      "Set up gateway",
      "Test AI with Simulation / Evals",
      "Connect a voice AI agent",
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
