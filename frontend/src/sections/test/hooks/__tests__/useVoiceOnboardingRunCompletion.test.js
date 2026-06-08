import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import useVoiceOnboardingRunCompletion from "../useVoiceOnboardingRunCompletion";
import { VOICE_ONBOARDING_MODES } from "../../onboardingVoiceRouteEvents";

const mockNavigate = vi.fn();
const mockRecordActivationEvent = vi.fn();

vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mockRecordActivationEvent,
  }),
}));

const quickStartAttribution = {
  quick_start_goal: "connect_voice_ai_agent",
  quick_start_id: "voice",
  quick_start_primary_path: "voice",
};

const voiceParams = {
  agentDefinitionId: "agent-1",
  mode: VOICE_ONBOARDING_MODES.RUN_TEST_CALL,
};

describe("useVoiceOnboardingRunCompletion", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("records the first terminal voice run and moves to review", () => {
    renderHook(() =>
      useVoiceOnboardingRunCompletion({
        agentType: "voice",
        executions: [
          { id: "execution-1", status: "Running" },
          {
            call_execution_id: "call-1",
            id: "execution-2",
            status: "Completed",
          },
        ],
        quickStartAttribution,
        testId: "test-1",
        voiceParams,
      }),
    );

    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      {
        eventName: "voice_test_call_completed",
        primaryPath: "voice",
        stage: "run_voice_test_call",
        source: "voice_simulation_runs",
        artifactType: "voice_call",
        artifactId: "call-1",
        metadata: {
          test_id: "test-1",
          execution_id: "execution-2",
          call_execution_id: "call-1",
          agent_definition_id: "agent-1",
          status: "Completed",
        },
        idempotencyKey: "voice_test_call_completed:test-1:execution-2:call-1",
        isSample: false,
        quick_start_goal: "connect_voice_ai_agent",
        quick_start_id: "voice",
        quick_start_primary_path: "voice",
      },
      expect.objectContaining({
        onError: expect.any(Function),
      }),
    );
    expect(mockNavigate).toHaveBeenCalledWith(
      "/dashboard/simulate/test/test-1/execution-2/call-details?from=onboarding&onboarding=review-voice-call&agent_definition_id=agent-1&call_id=call-1&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
      { replace: true },
    );
  });

  it("does not record non-terminal or non-voice runs", () => {
    renderHook(() =>
      useVoiceOnboardingRunCompletion({
        agentType: "voice",
        executions: [{ id: "execution-1", status: "Running" }],
        quickStartAttribution,
        testId: "test-1",
        voiceParams,
      }),
    );
    renderHook(() =>
      useVoiceOnboardingRunCompletion({
        agentType: "text",
        executions: [{ id: "execution-2", status: "Completed" }],
        quickStartAttribution,
        testId: "test-1",
        voiceParams,
      }),
    );

    expect(mockRecordActivationEvent).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("records each terminal execution once", () => {
    const { rerender } = renderHook(
      ({ executions }) =>
        useVoiceOnboardingRunCompletion({
          agentType: "voice",
          executions,
          quickStartAttribution,
          testId: "test-1",
          voiceParams,
        }),
      {
        initialProps: {
          executions: [{ id: "execution-1", status: "Completed" }],
        },
      },
    );

    rerender({ executions: [{ id: "execution-1", status: "Completed" }] });

    expect(mockRecordActivationEvent).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledTimes(1);
  });

  it("treats terminal statuses as case-insensitive", () => {
    renderHook(() =>
      useVoiceOnboardingRunCompletion({
        agentType: "voice",
        executions: [{ id: "execution-1", status: "completed" }],
        quickStartAttribution,
        testId: "test-1",
        voiceParams,
      }),
    );

    expect(mockRecordActivationEvent).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledTimes(1);
  });
});
