import { describe, expect, it } from "vitest";
import {
  buildPromptCreatedHref,
  buildPromptEditorHref,
  getPromptOnboardingRouteParams,
  PROMPT_ONBOARDING_MODES,
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

  it("keeps normal prompt creation routes clean", () => {
    expect(
      buildPromptCreatedHref({
        promptId: "prompt-1",
        search: "?folder=all",
      }),
    ).toBe("/dashboard/workbench/create/prompt-1");
  });
});
