import { describe, expect, it } from "vitest";
import {
  buildPromptComparisonCompletedPayload,
  buildPromptCreatedHref,
  buildPromptEditorHref,
  buildPromptFirstQualityLoopCompletedPayload,
  getPromptOnboardingRouteParams,
  isPromptFailureCaptureOnboarding,
  PROMPT_ONBOARDING_MODES,
  shouldAdvancePromptCompareOnboarding,
  shouldAdvancePromptRunOnboarding,
  shouldAdvancePromptSaveOnboarding,
} from "./promptOnboardingRoute";

describe("promptOnboardingRoute", () => {
  it("parses supported prompt onboarding route params", () => {
    expect(
      getPromptOnboardingRouteParams(
        "?source=onboarding&action=create-prompt&onboarding=save-version",
      ),
    ).toEqual({
      action: "create-prompt",
      isOnboarding: true,
      mode: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
    });
  });

  it("parses prompt journey-step params from Home CTAs", () => {
    [
      ["start_prompt", PROMPT_ONBOARDING_MODES.CREATE_PROMPT],
      ["create_prompt", PROMPT_ONBOARDING_MODES.CREATE_PROMPT],
      ["run_prompt_test", PROMPT_ONBOARDING_MODES.RUN_TEST],
      ["save_prompt_version", PROMPT_ONBOARDING_MODES.SAVE_VERSION],
      ["compare_prompt_versions", PROMPT_ONBOARDING_MODES.COMPARE],
      ["prompt_next_loop", PROMPT_ONBOARDING_MODES.ADD_FAILURE],
    ].forEach(([journeyStep, mode]) => {
      expect(
        getPromptOnboardingRouteParams(
          `?tour_anchor=prompt_focus&journey_step=${journeyStep}`,
        ),
      ).toEqual({
        action: mode,
        isOnboarding: true,
        mode,
      });
    });
  });

  it("drops unsupported prompt onboarding modes", () => {
    expect(
      getPromptOnboardingRouteParams(
        "?source=onboarding&onboarding=unsupported",
      ),
    ).toEqual({
      action: null,
      isOnboarding: true,
      mode: null,
    });
  });

  it("builds a prompt editor onboarding route", () => {
    expect(
      buildPromptEditorHref({
        promptId: "prompt-1",
        mode: PROMPT_ONBOARDING_MODES.RUN_TEST,
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test",
    );
  });

  it("moves a created onboarding prompt to the run-test route", () => {
    expect(
      buildPromptCreatedHref({
        promptId: "prompt-1",
        search: "?source=onboarding&action=create-prompt",
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test",
    );
  });

  it("moves a journey-created prompt to the run-test route", () => {
    expect(
      buildPromptCreatedHref({
        promptId: "prompt-1",
        search: "?tour_anchor=prompt_create_button&journey_step=start_prompt",
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test",
    );
  });

  it("keeps normal prompt creation routes clean", () => {
    expect(
      buildPromptCreatedHref({
        promptId: "prompt-1",
        search: "?folder=all",
      }),
    ).toBe("/dashboard/workbench/create/prompt-1");
  });

  it("advances run-test onboarding after a completed run result", () => {
    expect(
      shouldAdvancePromptRunOnboarding({
        isContentEmpty: false,
        isGenerating: false,
        loadingPrompt: false,
        mode: PROMPT_ONBOARDING_MODES.RUN_TEST,
        source: "onboarding",
      }),
    ).toBe(true);
  });

  it("does not advance run-test onboarding while the result is incomplete", () => {
    expect(
      shouldAdvancePromptRunOnboarding({
        isContentEmpty: true,
        isGenerating: false,
        loadingPrompt: false,
        mode: PROMPT_ONBOARDING_MODES.RUN_TEST,
        source: "onboarding",
      }),
    ).toBe(false);

    expect(
      shouldAdvancePromptRunOnboarding({
        isContentEmpty: false,
        isGenerating: true,
        loadingPrompt: false,
        mode: PROMPT_ONBOARDING_MODES.RUN_TEST,
        source: "onboarding",
      }),
    ).toBe(false);
  });

  it("advances save-version onboarding only from the guided save route", () => {
    expect(
      shouldAdvancePromptSaveOnboarding({
        mode: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
        source: "onboarding",
      }),
    ).toBe(true);

    expect(
      shouldAdvancePromptSaveOnboarding({
        mode: PROMPT_ONBOARDING_MODES.RUN_TEST,
        source: "onboarding",
      }),
    ).toBe(false);

    expect(
      shouldAdvancePromptSaveOnboarding({
        mode: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
        source: "workspace",
      }),
    ).toBe(false);
  });

  it("advances compare onboarding only after multiple versions are selected", () => {
    expect(
      shouldAdvancePromptCompareOnboarding({
        mode: PROMPT_ONBOARDING_MODES.COMPARE,
        selectedVersionCount: 2,
        source: "onboarding",
      }),
    ).toBe(true);

    expect(
      shouldAdvancePromptCompareOnboarding({
        mode: PROMPT_ONBOARDING_MODES.COMPARE,
        selectedVersionCount: 1,
        source: "onboarding",
      }),
    ).toBe(false);
  });

  it("builds a safe prompt comparison completion payload", () => {
    expect(
      buildPromptComparisonCompletedPayload({
        promptId: "prompt-1",
        versions: ["v1", "v2"],
      }),
    ).toEqual({
      eventName: "prompt_comparison_completed",
      primaryPath: "prompt",
      stage: "compare_prompt_versions",
      source: "prompt_template",
      metadata: {
        step: PROMPT_ONBOARDING_MODES.COMPARE,
        template_id: "prompt-1",
        version_count: 2,
      },
      idempotencyKey:
        "prompt_onboarding:prompt_comparison_completed:prompt-1:v1-v2",
    });
  });

  it("builds a safe prompt first quality loop completion payload", () => {
    expect(
      buildPromptFirstQualityLoopCompletedPayload({
        promptId: "prompt-1",
      }),
    ).toEqual({
      eventName: "first_quality_loop_completed",
      primaryPath: "prompt",
      stage: "activated",
      source: "prompt_metrics",
      metadata: {
        step: PROMPT_ONBOARDING_MODES.METRICS,
        template_id: "prompt-1",
      },
      idempotencyKey: "prompt_onboarding:first_quality_loop_completed:prompt-1",
    });
  });

  it("identifies only guided prompt failure capture routes", () => {
    expect(
      isPromptFailureCaptureOnboarding({
        mode: PROMPT_ONBOARDING_MODES.ADD_FAILURE,
        source: "onboarding",
      }),
    ).toBe(true);

    expect(
      isPromptFailureCaptureOnboarding({
        mode: PROMPT_ONBOARDING_MODES.ADD_FAILURE,
        source: "workspace",
      }),
    ).toBe(false);

    expect(
      isPromptFailureCaptureOnboarding({
        mode: PROMPT_ONBOARDING_MODES.METRICS,
        source: "onboarding",
      }),
    ).toBe(false);
  });
});
