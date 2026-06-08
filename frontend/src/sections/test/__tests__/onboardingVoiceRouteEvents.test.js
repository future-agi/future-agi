import { describe, expect, it } from "vitest";
import {
  appendVoiceOnboardingAttributionToHref,
  buildVoiceAgentCreatedPayload,
  buildVoiceCallReviewedPayload,
  buildVoiceCreateTestHref,
  buildVoiceMonitorOpenedPayload,
  buildVoiceOnboardingReturnHref,
  buildVoiceReviewCallHref,
  buildVoiceRouteFocusPayload,
  buildVoiceRunTestHref,
  buildVoiceSuccessCriteriaHref,
  buildVoiceSuccessCriteriaAddedPayload,
  buildVoiceTestCallCompletedPayload,
  getVoiceOnboardingParams,
  voiceCallIdFromExecution,
  voiceSetupQuickStartAttributionFromSearch,
  VOICE_ONBOARDING_MODES,
} from "../onboardingVoiceRouteEvents";

const VOICE_QUICK_START_SEARCH =
  "?quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice";

describe("onboardingVoiceRouteEvents", () => {
  it("reads voice onboarding route params", () => {
    expect(
      getVoiceOnboardingParams(
        "?from=onboarding&onboarding=review-voice-call&call_id=call-1&agent_definition_id=agent-1",
      ),
    ).toEqual({
      mode: "review-voice-call",
      from: "onboarding",
      callId: "call-1",
      agentDefinitionId: "agent-1",
      tourAnchor: null,
    });
  });

  it("reads voice journey-step params from Home CTAs", () => {
    expect(
      getVoiceOnboardingParams(
        "?tour_anchor=voice_agent_button&journey_step=create_voice_agent",
      ),
    ).toMatchObject({
      mode: VOICE_ONBOARDING_MODES.CREATE_AGENT,
      from: "onboarding",
      tourAnchor: "voice_agent_button",
    });

    expect(
      getVoiceOnboardingParams(
        "?tour_anchor=voice_test_call_button&journey_step=run_voice_test_call",
      ),
    ).toMatchObject({
      mode: VOICE_ONBOARDING_MODES.RUN_TEST_CALL,
      from: "onboarding",
      tourAnchor: "voice_test_call_button",
    });

    expect(
      getVoiceOnboardingParams(
        "?tour_anchor=voice_call_review_link&journey_step=review_voice_call",
      ),
    ).toEqual({
      mode: VOICE_ONBOARDING_MODES.REVIEW_CALL,
      from: "onboarding",
      callId: "",
      agentDefinitionId: "",
      tourAnchor: "voice_call_review_link",
    });

    expect(
      getVoiceOnboardingParams(
        "?tour_anchor=voice_success_criteria_button&journey_step=add_voice_success_criteria",
      ),
    ).toMatchObject({
      mode: VOICE_ONBOARDING_MODES.SUCCESS_CRITERIA,
      from: "onboarding",
      tourAnchor: "voice_success_criteria_button",
    });

    expect(
      getVoiceOnboardingParams(
        "?tour_anchor=voice_monitor_button&journey_step=voice_monitor_calls",
      ),
    ).toMatchObject({
      mode: VOICE_ONBOARDING_MODES.MONITOR_CALLS,
      from: "onboarding",
      tourAnchor: "voice_monitor_button",
    });
  });

  it("builds a route focus event with only safe identifiers", () => {
    expect(
      buildVoiceRouteFocusPayload({
        mode: VOICE_ONBOARDING_MODES.RUN_TEST_CALL,
        source: "voice_simulation_runs",
        testId: "test-1",
        agentDefinitionId: "agent-1",
      }),
    ).toMatchObject({
      eventName: "onboarding_voice_route_focus_viewed",
      primaryPath: "voice",
      stage: "run_voice_test_call",
      source: "voice_simulation_runs",
      artifactType: "voice_test",
      artifactId: "test-1",
      metadata: {
        route_mode: "run-test-call",
        test_id: "test-1",
        agent_definition_id: "agent-1",
      },
      isSample: false,
    });
  });

  it("keeps setup quick-start attribution across voice onboarding routes", () => {
    const attribution = voiceSetupQuickStartAttributionFromSearch(
      VOICE_QUICK_START_SEARCH,
    );

    expect(attribution).toEqual({
      quick_start_goal: "connect_voice_ai_agent",
      quick_start_id: "voice",
      quick_start_primary_path: "voice",
    });
    expect(
      appendVoiceOnboardingAttributionToHref(
        "/dashboard/simulate/test?from=onboarding",
        attribution,
      ),
    ).toBe(
      "/dashboard/simulate/test?from=onboarding&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
    );
    expect(
      buildVoiceCreateTestHref({
        agentDefinitionId: "agent-1",
        quickStartAttribution: attribution,
      }),
    ).toBe(
      "/dashboard/simulate/test?from=onboarding&onboarding=create-test-call&agent_definition_id=agent-1&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
    );
    expect(
      buildVoiceRunTestHref({
        agentDefinitionId: "agent-1",
        search: VOICE_QUICK_START_SEARCH,
        testId: "test-1",
      }),
    ).toBe(
      "/dashboard/simulate/test/test-1/runs?from=onboarding&onboarding=run-test-call&agent_definition_id=agent-1&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
    );
    expect(
      buildVoiceReviewCallHref({
        agentDefinitionId: "agent-1",
        executionId: "execution-1",
        quickStartAttribution: attribution,
        testId: "test-1",
      }),
    ).toBe(
      "/dashboard/simulate/test/test-1/execution-1/call-details?from=onboarding&onboarding=review-voice-call&agent_definition_id=agent-1&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
    );
    expect(
      buildVoiceSuccessCriteriaHref({
        agentDefinitionId: "agent-1",
        callId: "call-1",
        search: VOICE_QUICK_START_SEARCH,
        testId: "test-1",
      }),
    ).toBe(
      "/dashboard/simulate/test/test-1/runs?from=onboarding&onboarding=success-criteria&agent_definition_id=agent-1&call_id=call-1&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
    );
  });

  it("adds setup quick-start attribution to voice activation payloads", () => {
    const attribution = voiceSetupQuickStartAttributionFromSearch(
      VOICE_QUICK_START_SEARCH,
    );

    expect(
      buildVoiceAgentCreatedPayload({
        agentDefinitionId: "agent-1",
        provider: "livekit",
        quickStartAttribution: attribution,
      }),
    ).toMatchObject({
      eventName: "voice_agent_created",
      quick_start_goal: "connect_voice_ai_agent",
      quick_start_id: "voice",
      quick_start_primary_path: "voice",
    });

    expect(
      buildVoiceRouteFocusPayload({
        mode: VOICE_ONBOARDING_MODES.RUN_TEST_CALL,
        quickStartAttribution: attribution,
        source: "voice_simulation_runs",
        testId: "test-1",
      }),
    ).toMatchObject({
      eventName: "onboarding_voice_route_focus_viewed",
      quick_start_goal: "connect_voice_ai_agent",
      quick_start_id: "voice",
      quick_start_primary_path: "voice",
    });

    expect(
      buildVoiceCallReviewedPayload({
        testId: "test-1",
        executionId: "execution-1",
        quickStartAttribution: attribution,
      }),
    ).toMatchObject({
      eventName: "voice_call_reviewed",
      quick_start_goal: "connect_voice_ai_agent",
      quick_start_id: "voice",
      quick_start_primary_path: "voice",
    });

    expect(
      buildVoiceTestCallCompletedPayload({
        agentDefinitionId: "agent-1",
        testId: "test-1",
        executionId: "execution-1",
        callId: "call-1",
        status: "Completed",
        quickStartAttribution: attribution,
      }),
    ).toMatchObject({
      eventName: "voice_test_call_completed",
      quick_start_goal: "connect_voice_ai_agent",
      quick_start_id: "voice",
      quick_start_primary_path: "voice",
    });
  });

  it("builds the canonical voice review event", () => {
    expect(
      buildVoiceCallReviewedPayload({
        testId: "test-1",
        executionId: "execution-1",
        callId: "call-1",
      }),
    ).toEqual({
      eventName: "voice_call_reviewed",
      primaryPath: "voice",
      stage: "review_voice_call",
      source: "voice_call_detail",
      artifactType: "voice_call",
      artifactId: "call-1",
      metadata: {
        test_id: "test-1",
        execution_id: "execution-1",
        call_execution_id: "call-1",
      },
      idempotencyKey: "voice_call_reviewed:test-1:execution-1:call-1",
      isSample: false,
    });
  });

  it("builds the canonical voice test-call completion event", () => {
    expect(
      buildVoiceTestCallCompletedPayload({
        agentDefinitionId: "agent-1",
        testId: "test-1",
        executionId: "execution-1",
        callId: "call-1",
        status: "Completed",
      }),
    ).toEqual({
      eventName: "voice_test_call_completed",
      primaryPath: "voice",
      stage: "run_voice_test_call",
      source: "voice_simulation_runs",
      artifactType: "voice_call",
      artifactId: "call-1",
      metadata: {
        test_id: "test-1",
        execution_id: "execution-1",
        call_execution_id: "call-1",
        agent_definition_id: "agent-1",
        status: "Completed",
      },
      idempotencyKey: "voice_test_call_completed:test-1:execution-1:call-1",
      isSample: false,
    });
  });

  it("extracts voice call IDs from supported execution shapes", () => {
    expect(voiceCallIdFromExecution({ call_id: "call-1" })).toBe("call-1");
    expect(voiceCallIdFromExecution({ callExecutionId: "call-2" })).toBe(
      "call-2",
    );
    expect(voiceCallIdFromExecution({ traceId: "trace-1" })).toBe("trace-1");
    expect(voiceCallIdFromExecution({ id: "execution-1" })).toBe("execution-1");
  });

  it("builds the success criteria event after an eval is saved", () => {
    expect(
      buildVoiceSuccessCriteriaAddedPayload({
        testId: "test-1",
        callId: "call-1",
        evalConfig: {
          template_id: "template-1",
          name: "call_quality",
        },
      }),
    ).toMatchObject({
      eventName: "voice_success_criteria_added",
      primaryPath: "voice",
      stage: "add_voice_success_criteria",
      source: "simulation_eval_drawer",
      artifactType: "voice_test",
      artifactId: "test-1",
      metadata: {
        test_id: "test-1",
        call_execution_id: "call-1",
        template_id: "template-1",
        eval_name: "call_quality",
      },
      idempotencyKey: "voice_success_criteria_added:test-1:call-1:template-1",
      isSample: false,
    });
  });

  it("builds the monitor event for activated voice tests", () => {
    expect(
      buildVoiceMonitorOpenedPayload({
        testId: "test-1",
        source: "voice_call_logs",
      }),
    ).toMatchObject({
      eventName: "voice_call_monitor_opened",
      primaryPath: "voice",
      stage: "voice_monitor_calls",
      source: "voice_call_logs",
      artifactType: "voice_test",
      artifactId: "test-1",
      metadata: {
        test_id: "test-1",
      },
      idempotencyKey: "voice_call_monitor_opened:test-1",
      isSample: false,
    });
  });

  it("builds the voice onboarding home return destination", () => {
    expect(buildVoiceOnboardingReturnHref()).toBe(
      "/dashboard/home?source=onboarding&target_event=voice_success_criteria_added",
    );
    expect(
      buildVoiceOnboardingReturnHref({
        search: VOICE_QUICK_START_SEARCH,
      }),
    ).toBe(
      "/dashboard/home?source=onboarding&target_event=voice_success_criteria_added&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
    );
    expect(
      buildVoiceOnboardingReturnHref(
        buildVoiceSuccessCriteriaAddedPayload({
          testId: "test-1",
          callId: "call-1",
          evalConfig: { name: "call_quality" },
          quickStartAttribution: voiceSetupQuickStartAttributionFromSearch(
            VOICE_QUICK_START_SEARCH,
          ),
        }),
      ),
    ).toBe(
      "/dashboard/home?source=onboarding&target_event=voice_success_criteria_added&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
    );
  });
});
