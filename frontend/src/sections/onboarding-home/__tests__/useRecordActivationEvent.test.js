import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useRecordActivationEvent } from "../hooks/useRecordActivationEvent";
import { recordActivationEvent } from "../api/onboarding-home-api";
import { trackOnboardingHomeEvent } from "../analytics/onboarding-events";
import { persistSetupQuickStartAttribution } from "src/sections/auth/jwt/setup-org-quick-starts";

vi.mock("../api/onboarding-home-api", () => ({
  onboardingHomeQueryKeys: {
    all: ["onboarding-home"],
  },
  recordActivationEvent: vi.fn(),
}));

vi.mock("../analytics/onboarding-events", () => ({
  OnboardingHomeEvents: {
    activationEventRecorded: "onboarding_activation_event_recorded",
  },
  trackOnboardingHomeEvent: vi.fn(),
}));

const renderWithQueryClient = (hook) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
  const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
  const wrapper = ({ children }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children);

  return {
    ...renderHook(hook, { wrapper }),
    invalidateQueries,
  };
};

describe("useRecordActivationEvent", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
  });

  it("tracks successful activation events through onboarding analytics", async () => {
    recordActivationEvent.mockResolvedValueOnce({
      requestId: "req-next",
      stage: "review_eval_failures",
      primaryPath: "evals",
      isActivated: false,
      workspaceId: "wrk-1",
      organizationId: "org-1",
      userId: "usr-1",
    });

    const { result, invalidateQueries } = renderWithQueryClient(() =>
      useRecordActivationEvent(),
    );

    await act(async () => {
      await result.current.mutateAsync({
        eventName: "eval_run_completed",
        primaryPath: "evals",
        stage: "run_eval",
        source: "eval_create_page",
        campaignKey: "observe_waiting_for_first_trace",
        emailKey: "observe_waiting_v1",
        targetStage: "waiting_for_first_trace",
        targetEvent: "trace_received",
        sendLogId: "send-123",
        emailStatus: "stale",
        staleReason: "target_complete",
        quickStartGoal: "evaluate_quality",
        quickStartId: "evals",
        quickStartPrimaryPath: "evals",
        artifactType: "eval",
        artifactId: "eval-1",
        projectId: "project-1",
        isSample: false,
        metadata: {
          prompt: "do not forward",
        },
        idempotencyKey: "dedupe-key",
      });
    });

    expect(recordActivationEvent).toHaveBeenCalledWith({
      eventName: "eval_run_completed",
      primaryPath: "evals",
      stage: "run_eval",
      source: "eval_create_page",
      campaignKey: "observe_waiting_for_first_trace",
      emailKey: "observe_waiting_v1",
      targetStage: "waiting_for_first_trace",
      targetEvent: "trace_received",
      sendLogId: "send-123",
      emailStatus: "stale",
      staleReason: "target_complete",
      quickStartGoal: "evaluate_quality",
      quickStartId: "evals",
      quickStartPrimaryPath: "evals",
      artifactType: "eval",
      artifactId: "eval-1",
      projectId: "project-1",
      isSample: false,
      metadata: {
        prompt: "do not forward",
      },
      idempotencyKey: "dedupe-key",
    });
    expect(trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "onboarding_activation_event_recorded",
      {
        activation_event_name: "eval_run_completed",
        primary_path: "evals",
        activation_stage: "run_eval",
        source: "eval_create_page",
        campaign_key: "observe_waiting_for_first_trace",
        email_key: "observe_waiting_v1",
        target_stage: "waiting_for_first_trace",
        target_event: "trace_received",
        send_log_id: "send-123",
        email_status: "stale",
        stale_reason: "target_complete",
        quick_start_goal: "evaluate_quality",
        quick_start_id: "evals",
        quick_start_primary_path: "evals",
        artifact_type: "eval",
        artifact_id: "eval-1",
        project_id: "project-1",
        is_sample: false,
        next_stage: "review_eval_failures",
        next_primary_path: "evals",
        next_is_activated: false,
        next_request_id: "req-next",
        workspace_id: "wrk-1",
        organization_id: "org-1",
        user_id: "usr-1",
      },
    );
    expect(trackOnboardingHomeEvent.mock.calls[0][1]).not.toHaveProperty(
      "metadata",
    );
    expect(trackOnboardingHomeEvent.mock.calls[0][1]).not.toHaveProperty(
      "idempotency_key",
    );
    expect(trackOnboardingHomeEvent.mock.calls[0][1]).not.toHaveProperty(
      "idempotencyKey",
    );
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["onboarding-home"],
    });
  });

  it("preserves caller success callbacks and tracks snake_case payloads", async () => {
    const nextState = {
      requestId: "req-voice-next",
      stage: "run_voice_test_call",
      primaryPath: "voice",
      isActivated: false,
      workspaceId: "wrk-voice",
      organizationId: "org-voice",
      userId: "usr-voice",
    };
    const payload = {
      event_name: "voice_agent_created",
      primary_path: "voice",
      stage: "create_voice_agent",
      source: "voice_create_page",
      campaign_key: "voice_agent_create",
      email_key: "voice_agent_create_v1",
      target_stage: "create_voice_agent",
      target_event: "voice_agent_created",
      send_log_id: "send-voice",
      artifact_type: "voice_agent",
      artifact_id: "agent-1",
      project_id: "project-voice",
      is_sample: true,
      metadata: {
        message: "do not forward",
      },
      idempotency_key: "snake-dedupe-key",
    };
    const callerOnSuccess = vi.fn();
    recordActivationEvent.mockResolvedValueOnce(nextState);

    const { result } = renderWithQueryClient(() => useRecordActivationEvent());

    await act(async () => {
      result.current.mutate(payload, {
        onSuccess: callerOnSuccess,
      });
    });

    await waitFor(() => {
      expect(callerOnSuccess).toHaveBeenCalledTimes(1);
    });

    expect(callerOnSuccess.mock.calls[0][0]).toEqual(nextState);
    expect(trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "onboarding_activation_event_recorded",
      {
        activation_event_name: "voice_agent_created",
        primary_path: "voice",
        activation_stage: "create_voice_agent",
        source: "voice_create_page",
        campaign_key: "voice_agent_create",
        email_key: "voice_agent_create_v1",
        target_stage: "create_voice_agent",
        target_event: "voice_agent_created",
        send_log_id: "send-voice",
        artifact_type: "voice_agent",
        artifact_id: "agent-1",
        project_id: "project-voice",
        is_sample: true,
        next_stage: "run_voice_test_call",
        next_primary_path: "voice",
        next_is_activated: false,
        next_request_id: "req-voice-next",
        workspace_id: "wrk-voice",
        organization_id: "org-voice",
        user_id: "usr-voice",
      },
    );
    expect(trackOnboardingHomeEvent.mock.calls[0][1]).not.toHaveProperty(
      "metadata",
    );
    expect(trackOnboardingHomeEvent.mock.calls[0][1]).not.toHaveProperty(
      "idempotency_key",
    );
    expect(trackOnboardingHomeEvent.mock.calls[0][1]).not.toHaveProperty(
      "idempotencyKey",
    );
  });

  it("uses persisted quick-start attribution for downstream events", async () => {
    persistSetupQuickStartAttribution({
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
    });
    recordActivationEvent.mockResolvedValueOnce({
      requestId: "req-observe-next",
      stage: "create_trace_evaluator",
      primaryPath: "observe",
      isActivated: false,
      workspaceId: "wrk-observe",
      organizationId: "org-observe",
      userId: "usr-observe",
    });

    const { result } = renderWithQueryClient(() => useRecordActivationEvent());

    await act(async () => {
      await result.current.mutateAsync({
        eventName: "trace_detail_opened",
        primaryPath: "observe",
        stage: "review_first_trace",
        source: "trace_drawer",
      });
    });

    expect(recordActivationEvent).toHaveBeenCalledWith({
      eventName: "trace_detail_opened",
      primaryPath: "observe",
      stage: "review_first_trace",
      source: "trace_drawer",
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
    });
    expect(trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "onboarding_activation_event_recorded",
      expect.objectContaining({
        activation_event_name: "trace_detail_opened",
        quick_start_goal: "monitor_production_ai_app",
        quick_start_id: "observe",
        quick_start_primary_path: "observe",
      }),
    );
  });

  it("does not track failed activation event mutations", async () => {
    recordActivationEvent.mockRejectedValueOnce(new Error("failed"));

    const { result } = renderWithQueryClient(() => useRecordActivationEvent());

    await act(async () => {
      await expect(
        result.current.mutateAsync({
          event_name: "voice_agent_created",
          primary_path: "voice",
          stage: "create_voice_agent",
        }),
      ).rejects.toThrow("failed");
    });

    await waitFor(() => {
      expect(trackOnboardingHomeEvent).not.toHaveBeenCalled();
    });
  });
});
