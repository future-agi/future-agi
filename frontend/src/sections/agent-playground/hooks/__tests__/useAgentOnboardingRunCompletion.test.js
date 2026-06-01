import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import useAgentOnboardingRunCompletion from "../useAgentOnboardingRunCompletion";

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
  quick_start_goal: "build_ai_agent",
  quick_start_id: "agent",
  quick_start_primary_path: "agent",
};

describe("useAgentOnboardingRunCompletion", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("records terminal onboarding runs and moves to review", () => {
    renderHook(() =>
      useAgentOnboardingRunCompletion({
        agentId: "agent-1",
        executionData: { status: "success" },
        executionId: "execution-1",
        onboardingMode: "run-scenario",
        quickStartAttribution,
        versionId: "version-1",
      }),
    );

    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      {
        eventName: "agent_prototype_run_completed",
        primaryPath: "agent",
        stage: "run_agent_scenario",
        source: "agent_playground",
        artifactType: "graph_execution",
        artifactId: "execution-1",
        metadata: {
          agent_id: "agent-1",
          graph_execution_id: "execution-1",
          status: "success",
          version_id: "version-1",
        },
        idempotencyKey: "agent_prototype_run_completed:execution-1",
        isSample: false,
        quick_start_goal: "build_ai_agent",
        quick_start_id: "agent",
        quick_start_primary_path: "agent",
      },
      expect.objectContaining({
        onError: expect.any(Function),
      }),
    );
    expect(mockNavigate).toHaveBeenCalledWith(
      "/dashboard/agents/playground/agent-1/executions?version=version-1&onboarding=review-run&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
      { replace: true },
    );
  });

  it("does not record non-terminal or non-onboarding runs", () => {
    renderHook(() =>
      useAgentOnboardingRunCompletion({
        agentId: "agent-1",
        executionData: { status: "running" },
        executionId: "execution-1",
        onboardingMode: "run-scenario",
        quickStartAttribution,
      }),
    );
    renderHook(() =>
      useAgentOnboardingRunCompletion({
        agentId: "agent-1",
        executionData: { status: "success" },
        executionId: "execution-2",
        onboardingMode: null,
        quickStartAttribution,
      }),
    );

    expect(mockRecordActivationEvent).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("records terminal eval coverage runs and returns home", () => {
    renderHook(() =>
      useAgentOnboardingRunCompletion({
        agentId: "agent-1",
        executionData: { status: "success" },
        executionId: "execution-2",
        onboardingMode: "add-eval",
        quickStartAttribution,
        versionId: "version-1",
      }),
    );

    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      {
        eventName: "agent_eval_created",
        primaryPath: "agent",
        stage: "agent_create_eval",
        source: "agent_playground",
        artifactType: "agent_eval",
        artifactId: "execution-2",
        metadata: {
          agent_id: "agent-1",
          graph_execution_id: "execution-2",
          status: "success",
          version_id: "version-1",
        },
        idempotencyKey: "agent_eval_created:execution-2",
        isSample: false,
        quick_start_goal: "build_ai_agent",
        quick_start_id: "agent",
        quick_start_primary_path: "agent",
      },
      expect.objectContaining({
        onError: expect.any(Function),
      }),
    );
    expect(mockNavigate).toHaveBeenCalledWith(
      "/dashboard/home?mode=daily-quality&source=onboarding&target_event=agent_eval_created&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
      { replace: true },
    );
  });

  it("records each terminal execution once", () => {
    const { rerender } = renderHook(
      ({ executionData }) =>
        useAgentOnboardingRunCompletion({
          agentId: "agent-1",
          executionData,
          executionId: "execution-1",
          onboardingMode: "run-scenario",
          quickStartAttribution,
        }),
      { initialProps: { executionData: { status: "success" } } },
    );

    rerender({ executionData: { status: "success" } });

    expect(mockRecordActivationEvent).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledTimes(1);
  });
});
