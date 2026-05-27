import { describe, expect, it } from "vitest";
import {
  activationStateFixtureList,
  getActivationStateFixture,
} from "../fixtures/activation-state.fixtures";
import {
  hasOnePrimaryAction,
  hasSampleRoute,
  isInternalHref,
  isSampleHidden,
  makeActivationStateErrorFallback,
  canOpenSample,
  normalizeActivationState,
  normalizeProductPath,
  validateActivationStateFixture,
} from "../activation-state-utils";

describe("activation-state utilities", () => {
  it.each(activationStateFixtureList)(
    "normalizes the $name fixture",
    ({ state }) => {
      const normalized = normalizeActivationState(state);

      expect(normalized.schemaVersion).toBe("activation-state-2026-05-26.v1");
      expect(normalized.stage).toBeTruthy();
      expect(normalized.fallbackAction.href).toMatch(/^\//);
      expect(hasOnePrimaryAction(normalized)).toBe(true);
      expect(validateActivationStateFixture(state)).toBe(true);
    },
  );

  it("rejects an unknown stage", () => {
    const fixture = getActivationStateFixture("observeNoSetup");

    expect(() =>
      normalizeActivationState({ ...fixture, stage: "unknown_stage" }),
    ).toThrow(/Unsupported activation stage/);
  });

  it("rejects a missing fallback action", () => {
    const fixture = getActivationStateFixture("observeNoSetup");

    expect(() =>
      normalizeActivationState({ ...fixture, fallback_action: null }),
    ).toThrow(/fallback action/);
  });

  it("rejects external action hrefs", () => {
    const fixture = getActivationStateFixture("observeNoSetup");

    expect(() =>
      normalizeActivationState({
        ...fixture,
        recommended_action: {
          ...fixture.recommended_action,
          href: "https://example.com/phishing",
        },
      }),
    ).toThrow(/external href/);
    expect(isInternalHref("/dashboard/get-started")).toBe(true);
    expect(isInternalHref("https://example.com")).toBe(false);
  });

  it("preserves sample action markers", () => {
    const normalized = normalizeActivationState(
      getActivationStateFixture("sampleTraceReady"),
    );

    expect(normalized.recommendedAction.isSample).toBe(true);
    expect(normalized.recommendedAction.completionEvent).toBe(
      "sample_signal_viewed",
    );
  });

  it("normalizes prompt onboarding state and route modes", () => {
    const normalized = normalizeActivationState(
      getActivationStateFixture("promptCreatedNoRun"),
    );

    expect(normalized.primaryPath).toBe("prompt");
    expect(normalized.stage).toBe("run_prompt_test");
    expect(normalized.prompt.promptId).toBe("prompt-1");
    expect(normalized.prompt.hasRealPrompt).toBe(true);
    expect(normalized.prompt.hasTestRun).toBe(false);
    expect(normalized.signals.latestPromptId).toBe("prompt-1");
    expect(normalized.recommendedAction.href).toContain("onboarding=run-test");
  });

  it("keeps sample prompt activity out of real activation", () => {
    const normalized = normalizeActivationState(
      getActivationStateFixture("samplePromptOnly"),
    );

    expect(normalized.isActivated).toBe(false);
    expect(normalized.prompt.hasRealPrompt).toBe(false);
    expect(normalized.prompt.samplePromptCount).toBe(1);
    expect(normalized.signals.promptSampleTemplates).toBe(1);

    const fixture = getActivationStateFixture("promptCreatedNoRun");
    expect(() =>
      normalizeActivationState({
        ...fixture,
        prompt: {
          ...fixture.prompt,
          is_sample: true,
          has_real_prompt: true,
        },
      }),
    ).toThrow(/Sample prompt state/);
  });

  it("normalizes agent onboarding state and route modes", () => {
    const normalized = normalizeActivationState(
      getActivationStateFixture("agentRunReadyForReview"),
    );

    expect(normalized.primaryPath).toBe("agent");
    expect(normalized.stage).toBe("review_agent_trace");
    expect(normalized.agent.agentId).toBe("agent-1");
    expect(normalized.agent.hasRun).toBe(true);
    expect(normalized.signals.agentGraphExecutionId).toBe("graph-execution-1");
    expect(normalized.recommendedAction.href).toContain(
      "onboarding=review-run",
    );
  });

  it("keeps sample agent activity out of real activation", () => {
    const normalized = normalizeActivationState(
      getActivationStateFixture("sampleAgentScenarioReady"),
    );

    expect(normalized.isActivated).toBe(false);
    expect(normalized.agent.hasAgent).toBe(false);
    expect(normalized.agent.sampleAgentCount).toBe(1);
    expect(normalized.signals.agentSampleCount).toBe(1);

    const fixture = getActivationStateFixture("agentCreatedNoRun");
    expect(() =>
      normalizeActivationState({
        ...fixture,
        agent: {
          ...fixture.agent,
          is_sample: true,
          has_agent: true,
        },
      }),
    ).toThrow(/Sample agent state/);
  });

  it("normalizes daily quality state and rejects sample signals", () => {
    const normalized = normalizeActivationState(
      getActivationStateFixture("dailyQualityObserveNewSignal"),
    );

    expect(normalized.dailyQuality.mode).toBe("new_signal");
    expect(normalized.dailyQuality.topSignal.type).toBe("trace_failure");
    expect(normalized.dailyQuality.primaryAction.id).toBe(
      "review_failed_trace",
    );

    const fixture = getActivationStateFixture("dailyQualityObserveNewSignal");
    expect(() =>
      normalizeActivationState({
        ...fixture,
        daily_quality: {
          ...fixture.daily_quality,
          top_signal: {
            ...fixture.daily_quality.top_signal,
            is_sample: true,
          },
        },
      }),
    ).toThrow(/sample data/);
  });

  it("derives sample project route helpers", () => {
    const normalized = normalizeActivationState({
      ...getActivationStateFixture("sampleTraceReady"),
      sample_project: {
        ...getActivationStateFixture("sampleTraceReady").sample_project,
        status: "ready_for_observe",
        entry_route:
          "/dashboard/observe/observe-1/trace/trace-1?sample=true&from=onboarding",
      },
    });

    expect(hasSampleRoute(normalized.sampleProject)).toBe(true);
    expect(canOpenSample(normalized.sampleProject)).toBe(true);
    expect(isSampleHidden(normalized.sampleProject)).toBe(false);
  });

  it("normalizes accepted product path aliases", () => {
    expect(normalizeProductPath("observability")).toBe("observe");
    expect(normalizeProductPath("sample_project")).toBe("sample");
  });

  it("preserves configured stage copy and goal options", () => {
    const normalized = normalizeActivationState({
      ...getActivationStateFixture("newWorkspaceNoGoal"),
      stage_copy: {
        eyebrow: "Configured",
        title: "Configured title",
        description: "Configured description",
      },
    });

    expect(normalized.stageCopy.title).toBe("Configured title");
    expect(normalized.availableGoals[0]).toEqual(
      expect.objectContaining({
        goal: "monitor_production_ai_app",
        primaryPath: "observe",
      }),
    );
  });

  it("creates a renderable local fallback for hard API failures", () => {
    const fallback = makeActivationStateErrorFallback({
      message: "Network error",
    });

    expect(fallback.stage).toBe("feature_disabled");
    expect(fallback.fallbackAction.href).toBe("/dashboard/get-started");
    expect(fallback.warnings).toContain("activation_state_request_failed");
  });

  it("requires a route match or blocked reason for the primary action", () => {
    const fixture = getActivationStateFixture("observeNoSetup");

    expect(() =>
      normalizeActivationState({
        ...fixture,
        recommended_action: {
          ...fixture.recommended_action,
          href: "/dashboard/not-in-route-map",
        },
      }),
    ).toThrow(/route availability/);

    const blocked = normalizeActivationState({
      ...fixture,
      recommended_action: {
        ...fixture.recommended_action,
        href: null,
        blocked: true,
        blocked_reason: "route_not_implemented",
        route_available: false,
      },
    });
    expect(blocked.recommendedAction.blockedReason).toBe(
      "route_not_implemented",
    );
  });
});
