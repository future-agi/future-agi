import { beforeEach, describe, expect, it } from "vitest";
import {
  appendSetupQuickStartAttributionToHref,
  normalizeSetupQuickStartAttribution,
  persistSetupQuickStartAttribution,
  readPersistedSetupQuickStartAttribution,
  SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY,
  SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS,
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
      [
        "sample_preview",
        "explore_sample_data",
        "Explore with sample data",
        "sample",
      ],
      [
        "observe",
        "monitor_production_ai_app",
        "Monitor a production AI app",
        "observe",
      ],
      ["prompt", "improve_prompts", "Test and improve prompts", "prompt"],
      ["agent", "build_ai_agent", "Build or prototype an AI agent", "agent"],
      [
        "gateway",
        "control_model_traffic",
        "Route LLM traffic safely",
        "gateway",
      ],
      [
        "evals",
        "evaluate_quality",
        "Evaluate quality on data or traces",
        "evals",
      ],
      ["voice", "connect_voice_ai_agent", "Connect a voice AI agent", "voice"],
    ]);
  });

  it("keeps sample preview as the only featured fast Aha quick start", () => {
    const featuredQuickStarts = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.filter(
      (option) => option.featured,
    );
    const observeQuickStart = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.find(
      (option) => option.id === "observe",
    );

    expect(featuredQuickStarts.map((option) => option.id)).toEqual([
      "sample_preview",
    ]);
    expect(featuredQuickStarts[0]).toMatchObject({
      goal: "explore_sample_data",
      primaryPath: "sample",
      buttonLabel: "Preview sample trace first",
    });
    expect(observeQuickStart).toMatchObject({
      buttonLabel: "Connect real observability",
      primaryPath: "observe",
    });
    expect(observeQuickStart.featured).toBeUndefined();
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
