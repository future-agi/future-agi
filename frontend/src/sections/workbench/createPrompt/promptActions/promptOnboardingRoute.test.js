import { describe, expect, it } from "vitest";
import {
  buildPromptComparisonCompletedPayload,
  buildPromptCreatedHref,
  buildPromptCreatedPayload,
  buildPromptEditorHref,
  buildPromptFirstQualityLoopCompletedPayload,
  buildPromptTestRunCompletedPayload,
  buildPromptVersionCreatedPayload,
  countCommittedPromptVersions,
  getPromptOnboardingRouteParams,
  getSelectedPromptVersionsFromSearch,
  isPromptFailureCaptureOnboarding,
  PROMPT_ONBOARDING_JOURNEY_STEPS,
  PROMPT_ONBOARDING_MODES,
  resolvePromptPostSaveJourneyStep,
  resolvePromptSaveCommitTarget,
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
      journeyStep: null,
      mode: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
      tourAnchor: null,
    });
  });

  it("parses prompt journey-step params from Home CTAs", () => {
    [
      ["start_prompt", PROMPT_ONBOARDING_MODES.CREATE_PROMPT],
      ["create_prompt", PROMPT_ONBOARDING_MODES.CREATE_PROMPT],
      ["run_prompt_test", PROMPT_ONBOARDING_MODES.RUN_TEST],
      ["save_prompt_version", PROMPT_ONBOARDING_MODES.SAVE_VERSION],
      ["create_second_prompt_version", PROMPT_ONBOARDING_MODES.COMPARE],
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
        journeyStep,
        mode,
        tourAnchor: "prompt_focus",
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
      journeyStep: null,
      mode: null,
      tourAnchor: null,
    });
  });

  it("builds a prompt editor onboarding route", () => {
    expect(
      buildPromptEditorHref({
        promptId: "prompt-1",
        mode: PROMPT_ONBOARDING_MODES.RUN_TEST,
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test&tour_anchor=prompt_run_test_button&journey_step=run_prompt_test",
    );
  });

  it("preserves setup quick-start attribution on guided prompt editor routes", () => {
    [
      [
        PROMPT_ONBOARDING_MODES.RUN_TEST,
        {
          onboarding: PROMPT_ONBOARDING_MODES.RUN_TEST,
          tourAnchor: "prompt_run_test_button",
          journeyStep: "run_prompt_test",
        },
      ],
      [
        PROMPT_ONBOARDING_MODES.SAVE_VERSION,
        {
          onboarding: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
          tourAnchor: "prompt_save_version_button",
          journeyStep: "save_prompt_version",
        },
      ],
      [
        PROMPT_ONBOARDING_MODES.COMPARE,
        {
          onboarding: PROMPT_ONBOARDING_MODES.COMPARE,
          tourAnchor: "prompt_compare_versions_button",
          journeyStep: "compare_prompt_versions",
        },
      ],
      [
        PROMPT_ONBOARDING_MODES.ADD_FAILURE,
        {
          onboarding: PROMPT_ONBOARDING_MODES.ADD_FAILURE,
          tab: "Evaluation",
          tourAnchor: "prompt_add_example_button",
          journeyStep: "prompt_next_loop",
        },
      ],
      [
        PROMPT_ONBOARDING_MODES.METRICS,
        {
          onboarding: PROMPT_ONBOARDING_MODES.METRICS,
          tab: "Metrics",
        },
      ],
    ].forEach(([mode, expected]) => {
      const href = buildPromptEditorHref({
        promptId: "prompt-1",
        mode,
        search:
          "?source=onboarding&quick_start_goal=improve_prompts&quick_start_id=prompt&quick_start_primary_path=prompt",
      });
      const params = new URLSearchParams(href.split("?")[1]);

      expect(href).toContain("/dashboard/workbench/create/prompt-1?");
      expect(params.get("source")).toBe("onboarding");
      expect(params.get("onboarding")).toBe(expected.onboarding);
      expect(params.get("tab")).toBe(expected.tab || null);
      expect(params.get("tour_anchor")).toBe(expected.tourAnchor || null);
      expect(params.get("journey_step")).toBe(expected.journeyStep || null);
      expect(params.get("quick_start_goal")).toBe("improve_prompts");
      expect(params.get("quick_start_id")).toBe("prompt");
      expect(params.get("quick_start_primary_path")).toBe("prompt");
    });
  });

  it("preserves selected prompt versions on guided editor routes", () => {
    const selectedVersions = [
      { version: "v1", isDraft: false },
      { version: "v2", isDraft: true },
    ];
    const href = buildPromptEditorHref({
      promptId: "prompt-1",
      mode: PROMPT_ONBOARDING_MODES.RUN_TEST,
      selectedVersions,
    });
    const params = new URLSearchParams(href.split("?")[1]);

    expect(href).toContain("/dashboard/workbench/create/prompt-1?");
    expect(params.get("selected-versions")).toBe(
      JSON.stringify(selectedVersions),
    );
    expect(params.get("onboarding")).toBe(PROMPT_ONBOARDING_MODES.RUN_TEST);
  });

  it("parses selected prompt versions from guided route state", () => {
    const selectedVersions = [
      { version: "v1", templateVersion: "v1", isDraft: false },
      { version: "v2", templateVersion: "v2", isDraft: false },
    ];
    const params = new URLSearchParams();
    params.set("selected-versions", JSON.stringify(selectedVersions));

    expect(getSelectedPromptVersionsFromSearch(params)).toEqual(
      selectedVersions,
    );
    expect(
      getSelectedPromptVersionsFromSearch("?selected-versions=not-json"),
    ).toEqual([]);
  });

  it("adds destination tour anchors to each guided editor step", () => {
    expect(
      buildPromptEditorHref({
        promptId: "prompt-1",
        mode: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=save-version&tour_anchor=prompt_save_version_button&journey_step=save_prompt_version",
    );
    expect(
      buildPromptEditorHref({
        promptId: "prompt-1",
        mode: PROMPT_ONBOARDING_MODES.COMPARE,
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=compare&tour_anchor=prompt_compare_versions_button&journey_step=compare_prompt_versions",
    );
    expect(
      buildPromptEditorHref({
        journeyStep: PROMPT_ONBOARDING_JOURNEY_STEPS.CREATE_SECOND_VERSION,
        promptId: "prompt-1",
        mode: PROMPT_ONBOARDING_MODES.COMPARE,
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=compare&tour_anchor=prompt_create_second_version_button&journey_step=create_second_prompt_version",
    );
    expect(
      buildPromptEditorHref({
        promptId: "prompt-1",
        mode: PROMPT_ONBOARDING_MODES.ADD_FAILURE,
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=add-failure&tab=Evaluation&tour_anchor=prompt_add_example_button&journey_step=prompt_next_loop",
    );
    expect(
      buildPromptEditorHref({
        promptId: "prompt-1",
        mode: PROMPT_ONBOARDING_MODES.METRICS,
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=metrics&tab=Metrics",
    );
  });

  it("moves a created onboarding prompt to the run-test route", () => {
    expect(
      buildPromptCreatedHref({
        promptId: "prompt-1",
        search: "?source=onboarding&action=create-prompt",
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test&tour_anchor=prompt_run_test_button&journey_step=run_prompt_test",
    );
  });

  it("carries setup quick-start attribution after prompt creation", () => {
    expect(
      buildPromptCreatedHref({
        promptId: "prompt-1",
        search:
          "?source=onboarding&action=create-prompt&quick_start_goal=improve_prompts&quick_start_id=prompt&quick_start_primary_path=prompt",
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test&tour_anchor=prompt_run_test_button&journey_step=run_prompt_test&quick_start_goal=improve_prompts&quick_start_id=prompt&quick_start_primary_path=prompt",
    );
  });

  it("moves a journey-created prompt to the run-test route", () => {
    expect(
      buildPromptCreatedHref({
        promptId: "prompt-1",
        search: "?tour_anchor=prompt_create_button&journey_step=start_prompt",
      }),
    ).toBe(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test&tour_anchor=prompt_run_test_button&journey_step=run_prompt_test",
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

  it("counts committed prompt versions across route and API shapes", () => {
    expect(
      countCommittedPromptVersions([
        { templateVersion: "v1", isDraft: false, isDefault: true },
        {
          template_version: "v2",
          is_draft: false,
          commit_message: "Safer answer",
        },
        { template_version: "v3", is_draft: true, commit_message: "Draft" },
        null,
      ]),
    ).toBe(2);
  });

  it("targets the draft second version while saving the guided prompt loop", () => {
    const baseline = { version: "v1", isDraft: false };
    const secondDraft = { version: "v2", isDraft: true };

    expect(
      resolvePromptSaveCommitTarget({
        mode: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
        selectedVersions: [baseline, secondDraft],
        source: "onboarding",
      }),
    ).toBe(secondDraft);

    expect(
      resolvePromptSaveCommitTarget({
        mode: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
        selectedVersions: [baseline, secondDraft],
        source: "workspace",
      }),
    ).toBe(baseline);
  });

  it("routes post-save onboarding by the committed target version", () => {
    const baseline = { version: "v1", isDraft: false };
    const secondVersion = { version: "v2", isDraft: false };

    expect(
      resolvePromptPostSaveJourneyStep({
        baseVersion: baseline,
        commitTarget: baseline,
      }),
    ).toBe(PROMPT_ONBOARDING_JOURNEY_STEPS.CREATE_SECOND_VERSION);

    expect(
      resolvePromptPostSaveJourneyStep({
        baseVersion: baseline,
        commitTarget: secondVersion,
      }),
    ).toBe(PROMPT_ONBOARDING_JOURNEY_STEPS.COMPARE_VERSIONS);
  });

  it("advances compare onboarding only after multiple committed versions are selected", () => {
    expect(
      shouldAdvancePromptCompareOnboarding({
        committedVersionCount: 2,
        mode: PROMPT_ONBOARDING_MODES.COMPARE,
        selectedVersionCount: 2,
        source: "onboarding",
      }),
    ).toBe(true);

    expect(
      shouldAdvancePromptCompareOnboarding({
        committedVersionCount: 1,
        mode: PROMPT_ONBOARDING_MODES.COMPARE,
        selectedVersionCount: 2,
        source: "onboarding",
      }),
    ).toBe(false);

    expect(
      shouldAdvancePromptCompareOnboarding({
        committedVersionCount: 2,
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

  it("builds a safe prompt creation activation payload", () => {
    expect(
      buildPromptCreatedPayload({
        promptId: "prompt-1",
        search:
          "?quick_start_goal=improve_prompts&quick_start_id=prompt&quick_start_primary_path=prompt",
      }),
    ).toEqual({
      eventName: "prompt_created",
      primaryPath: "prompt",
      stage: "start_prompt",
      source: "prompt_template",
      metadata: {
        step: PROMPT_ONBOARDING_MODES.CREATE_PROMPT,
        template_id: "prompt-1",
      },
      quickStartGoal: "improve_prompts",
      quickStartId: "prompt",
      quickStartPrimaryPath: "prompt",
      idempotencyKey: "prompt_onboarding:prompt_created:prompt-1",
    });
  });

  it("builds a safe prompt test-run completion payload", () => {
    expect(
      buildPromptTestRunCompletedPayload({
        promptId: "prompt-1",
        versions: [{ version: "v1" }],
      }),
    ).toEqual({
      eventName: "prompt_test_run_completed",
      primaryPath: "prompt",
      stage: "run_prompt_test",
      source: "prompt_playground",
      metadata: {
        step: PROMPT_ONBOARDING_MODES.RUN_TEST,
        template_id: "prompt-1",
        version_count: 1,
      },
      idempotencyKey: "prompt_onboarding:prompt_test_run_completed:prompt-1:v1",
    });
  });

  it("builds a safe prompt version-created payload", () => {
    expect(
      buildPromptVersionCreatedPayload({
        promptId: "prompt-1",
        version: { version: "v1" },
      }),
    ).toEqual({
      eventName: "prompt_version_created",
      primaryPath: "prompt",
      stage: "save_prompt_version",
      source: "prompt_template",
      metadata: {
        step: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
        template_id: "prompt-1",
        version: "v1",
      },
      idempotencyKey: "prompt_onboarding:prompt_version_created:prompt-1:v1",
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
