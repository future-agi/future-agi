import { describe, expect, it } from "vitest";
import {
  activationStateFixtureList,
  getActivationStateFixture,
} from "../fixtures/activation-state.fixtures";
import {
  DEFAULT_PRODUCT_SETUP_HREF,
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

  it("normalizes the prompt second-version bridge stage", () => {
    const normalized = normalizeActivationState(
      getActivationStateFixture("promptVersionNoComparison"),
    );

    expect(normalized.primaryPath).toBe("prompt");
    expect(normalized.stage).toBe("create_second_prompt_version");
    expect(normalized.recommendedAction.id).toBe(
      "create_second_prompt_version",
    );
    expect(normalized.recommendedAction.completionEvent).toBe(
      "prompt_comparable_version_created",
    );
  });

  it("normalizes a backend-driven journey plan", () => {
    const fixture = getActivationStateFixture("promptCreatedNoRun");
    const normalized = normalizeActivationState({
      ...fixture,
      journey_plan: {
        id: "prompt_first_run",
        primary_path: "prompt",
        eyebrow: "Prompt setup",
        title: "Test prompts and compare versions",
        description: "Create one prompt, test it, and compare changes.",
        chips: ["prompt"],
        current_step_id: "run_prompt_test",
        current_step_index: 1,
        steps: [
          {
            id: "create_prompt",
            stage: "start_prompt",
            action_id: "create_prompt",
            success_event: "prompt_created",
            tour_anchor: "prompt_create_button",
            label: "Create prompt",
            description: "Start with one prompt.",
            status: "complete",
            href: "/dashboard/workbench/all?source=onboarding",
            fallback_href: "/dashboard/get-started",
            route_available: true,
            blocked_reason: null,
            requires_permission: "prompt:write",
          },
          {
            id: "run_prompt_test",
            stage: "run_prompt_test",
            action_id: "run_prompt_test",
            success_event: "prompt_test_run_completed",
            tour_anchor: "prompt_run_test_button",
            label: "Run test",
            description: "Run one focused example.",
            status: "current",
            href: "/dashboard/workbench/create/prompt-1?onboarding=run-test",
            fallback_href: "/dashboard/get-started",
            route_available: true,
            blocked_reason: null,
            requires_permission: "prompt:write",
          },
        ],
      },
    });

    expect(normalized.journeyPlan.id).toBe("prompt_first_run");
    expect(normalized.journeyPlan.currentStepId).toBe("run_prompt_test");
    expect(normalized.journeyPlan.steps[1].status).toBe("current");
    expect(normalized.journeyPlan.steps[1].tourAnchor).toBe(
      "prompt_run_test_button",
    );
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

  it("normalizes gateway onboarding state and route modes", () => {
    const normalized = normalizeActivationState(
      getActivationStateFixture("gatewayRequestReady"),
    );

    expect(normalized.primaryPath).toBe("gateway");
    expect(normalized.stage).toBe("review_gateway_log");
    expect(normalized.gateway.hasRequest).toBe(true);
    expect(normalized.gateway.requestId).toBe("request-1");
    expect(normalized.signals.gatewayRequestId).toBe("request-1");
    expect(normalized.recommendedAction.href).toContain(
      "onboarding=review-request",
    );
  });

  it("keeps sample gateway requests out of real activation", () => {
    const normalized = normalizeActivationState(
      getActivationStateFixture("sampleGatewayRequestReady"),
    );

    expect(normalized.isActivated).toBe(false);
    expect(normalized.gateway.hasRequest).toBe(false);
    expect(normalized.gateway.sampleRequestCount).toBe(1);
    expect(normalized.signals.gatewaySampleRequestCount).toBe(1);

    const fixture = getActivationStateFixture("gatewayRequestReady");
    expect(() =>
      normalizeActivationState({
        ...fixture,
        gateway: {
          ...fixture.gateway,
          is_sample: true,
          has_request: true,
        },
      }),
    ).toThrow(/Sample gateway request state/);
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
    expect(normalized.signals.sampleProjectOpened).toBe(true);
    expect(normalized.signals.sampleTraceAvailable).toBe(true);
  });

  it("normalizes accepted product path aliases", () => {
    expect(normalizeProductPath("observability")).toBe("observe");
    expect(normalizeProductPath("sample_project")).toBe("sample");
  });

  it("preserves configured stage copy and goal options", () => {
    const normalized = normalizeActivationState({
      ...getActivationStateFixture("goalPickerFallback"),
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
        outcomePreview:
          "A real trace reviewed and a quality check ready to add.",
        primaryPath: "observe",
      }),
    );
  });

  it("normalizes lifecycle email context attribution", () => {
    const staleLink = normalizeActivationState(
      getActivationStateFixture("staleEmailLink"),
    );

    expect(staleLink.emailContext).toEqual({
      campaignKey: "observe_waiting_for_first_trace",
      emailKey: "observe_waiting_for_first_trace_1",
      sendLogId: "send-123",
      emailStatus: "stale",
      linkIssuedAt: "2026-05-26T15:00:00Z",
      targetStage: "waiting_for_first_trace",
      targetEvent: "trace_received",
      targetRoute: "/dashboard/observe/observe-1",
      contextStatus: "stale",
      staleReason: "stage_changed",
      resolvedHref: "/dashboard/observe/observe-1/trace/trace-1",
    });

    const camelCaseContext = normalizeActivationState({
      ...getActivationStateFixture("observeNoSetup"),
      emailContext: {
        campaignKey: "observe_waiting",
        emailKey: "observe_waiting_1",
        sendLogId: "send-456",
        emailStatus: "fresh",
        linkIssuedAt: "2026-05-27T12:00:00Z",
        targetStage: "waiting_for_first_trace",
        targetEvent: "trace_received",
        targetRoute: "/dashboard/observe/observe-1",
        contextStatus: "fresh",
        staleReason: null,
        resolvedHref: "/dashboard/observe/observe-1",
      },
    });

    expect(camelCaseContext.emailContext).toEqual({
      campaignKey: "observe_waiting",
      emailKey: "observe_waiting_1",
      sendLogId: "send-456",
      emailStatus: "fresh",
      linkIssuedAt: "2026-05-27T12:00:00Z",
      targetStage: "waiting_for_first_trace",
      targetEvent: "trace_received",
      targetRoute: "/dashboard/observe/observe-1",
      contextStatus: "fresh",
      staleReason: null,
      resolvedHref: "/dashboard/observe/observe-1",
    });
  });

  it("creates a renderable local fallback for hard API failures", () => {
    const fallback = makeActivationStateErrorFallback({
      message: "Network error",
    });

    expect(fallback.stage).toBe("feature_disabled");
    expect(fallback.fallbackAction.href).toBe(DEFAULT_PRODUCT_SETUP_HREF);
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
