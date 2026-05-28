import { describe, expect, it } from "vitest";
import {
  buildAgentEvalCoveragePayload,
  isEvalOnboardingMode,
  TEST_ONBOARDING_MODES,
} from "./testOnboardingModes";

describe("testOnboardingModes", () => {
  it("identifies eval route modes", () => {
    expect(isEvalOnboardingMode(TEST_ONBOARDING_MODES.CREATE_EVAL)).toBe(true);
    expect(isEvalOnboardingMode(TEST_ONBOARDING_MODES.SAVE_EVAL)).toBe(true);
    expect(isEvalOnboardingMode("review-run")).toBe(false);
  });

  it("builds an agent save-eval coverage payload with safe metadata", () => {
    expect(
      buildAgentEvalCoveragePayload({
        mode: TEST_ONBOARDING_MODES.SAVE_EVAL,
        testId: "test-1",
        executionIds: ["exec-1", "exec-2"],
        evalConfig: {
          id: "eval-1",
          template_id: "template-1",
        },
      }),
    ).toEqual({
      eventName: "agent_scenario_saved_as_eval",
      primaryPath: "agent",
      stage: "save_agent_eval",
      source: "simulate",
      artifactType: "eval",
      artifactId: "eval-1",
      metadata: {
        step: TEST_ONBOARDING_MODES.SAVE_EVAL,
        test_id: "test-1",
        eval_config_id: "eval-1",
        eval_template_id: "template-1",
        execution_count: 2,
      },
      idempotencyKey:
        "agent_onboarding:agent_scenario_saved_as_eval:test-1:eval-1",
      isSample: false,
    });
  });

  it("builds an agent create-eval coverage payload without eval contents", () => {
    expect(
      buildAgentEvalCoveragePayload({
        mode: TEST_ONBOARDING_MODES.CREATE_EVAL,
        testId: "test 1",
        evalConfig: {
          template_id: "template 1",
        },
      }),
    ).toEqual({
      eventName: "agent_eval_created",
      primaryPath: "agent",
      stage: "agent_create_eval",
      source: "simulate",
      metadata: {
        step: TEST_ONBOARDING_MODES.CREATE_EVAL,
        test_id: "test 1",
        eval_template_id: "template 1",
        execution_count: 0,
      },
      idempotencyKey: "agent_onboarding:agent_eval_created:test-1:template-1",
      isSample: false,
    });
  });

  it("does not build a payload outside eval onboarding routes", () => {
    expect(
      buildAgentEvalCoveragePayload({
        mode: "review-run",
        testId: "test-1",
      }),
    ).toBeNull();
  });
});
