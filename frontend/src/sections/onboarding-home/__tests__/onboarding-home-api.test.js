import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
  endpoints: {
    onboarding: {
      activationState: "/accounts/activation-state/",
      activationEvent: "/accounts/activation-events/",
      goal: "/accounts/onboarding/goal/",
      sampleProject: "/accounts/sample-project/",
      hideSampleProject: "/accounts/sample-project/hide/",
    },
  },
}));

import axios, { endpoints } from "src/utils/axios";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";
import { useActivationState } from "../hooks/useActivationState";
import {
  fetchActivationState,
  hideSampleProject,
  onboardingHomeQueryKeys,
  OnboardingEndpointUnavailableError,
  openSampleProject,
  recordActivationEvent,
  saveOnboardingGoal,
} from "../api/onboarding-home-api";

const renderWithQueryClient = (hook) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 0,
        gcTime: 0,
      },
    },
  });
  const wrapper = ({ children }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children);
  return renderHook(hook, { wrapper });
};

describe("onboarding home API", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches activation state from the onboarding endpoint", async () => {
    const fixture = getActivationStateFixture("observeNoSetup");
    axios.get.mockResolvedValueOnce({ data: { result: fixture } });

    const state = await fetchActivationState();

    expect(axios.get).toHaveBeenCalledWith("/accounts/activation-state/", {
      params: {},
    });
    expect(state.stage).toBe("connect_observability");
    expect(state.primaryPath).toBe("observe");
  });

  it("passes email and campaign query params", async () => {
    axios.get.mockResolvedValueOnce({
      data: { result: getActivationStateFixture("staleEmailLink") },
    });

    await fetchActivationState({
      source: "email",
      campaignKey: "observe_waiting",
      emailKey: "observe_waiting_1",
      sendLogId: "send-123",
      emailStatus: "stale",
      targetStage: "waiting_for_first_trace",
      targetEvent: "trace_received",
      targetRoute: "/dashboard/observe/observe-1",
      linkIssuedAt: "2026-05-26T15:00:00Z",
      staleReason: "target_complete",
      mode: "email",
    });

    expect(axios.get).toHaveBeenCalledWith("/accounts/activation-state/", {
      params: {
        source: "email",
        campaign_key: "observe_waiting",
        email_key: "observe_waiting_1",
        send_log_id: "send-123",
        email_status: "stale",
        target_stage: "waiting_for_first_trace",
        target_event: "trace_received",
        target_route: "/dashboard/observe/observe-1",
        link_issued_at: "2026-05-26T15:00:00Z",
        stale_reason: "target_complete",
        mode: "email",
      },
    });
  });

  it("normalizes direct fixture payloads in tests", async () => {
    axios.get.mockResolvedValueOnce({
      data: getActivationStateFixture("featureDisabled"),
    });

    const state = await fetchActivationState();

    expect(state.stage).toBe("feature_disabled");
    expect(state.fallbackAction.href).toBe("/dashboard/get-started");
  });

  it("saves the onboarding goal through the goal endpoint", async () => {
    axios.post.mockResolvedValueOnce({
      data: { result: getActivationStateFixture("observeNoSetup") },
    });

    const state = await saveOnboardingGoal({
      goal: "monitor_production_ai_app",
      primaryPath: "observability",
      source: "goal_picker",
      campaignKey: "welcome",
      expectedStage: "choose_goal",
      knownGoalId: "goal-1",
    });

    expect(axios.post).toHaveBeenCalledWith("/accounts/onboarding/goal/", {
      goal: "monitor_production_ai_app",
      primary_path: "observe",
      source: "goal_picker",
      campaign_key: "welcome",
      expected_stage: "choose_goal",
      known_goal_id: "goal-1",
    });
    expect(state.stage).toBe("connect_observability");
  });

  it("exposes goal conflict responses to the caller", async () => {
    const conflict = {
      statusCode: 409,
      result: {
        reason: "known_goal_mismatch",
        current_goal_id: "goal-current",
      },
    };
    axios.post.mockRejectedValueOnce(conflict);

    await expect(saveOnboardingGoal({ goal: "improve_prompts" })).rejects.toBe(
      conflict,
    );
  });

  it("records an activation event and returns the nested activation state", async () => {
    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          event_id: "event-1",
          event_name: "trace_reviewed",
          activation_state: getActivationStateFixture("observeNeedsEvaluator"),
        },
      },
    });

    const state = await recordActivationEvent({
      eventName: "trace_detail_opened",
      primaryPath: "observability",
      stage: "review_first_trace",
      source: "trace_full_page",
      artifactType: "trace",
      artifactId: "trace-1",
      projectId: "observe-1",
      campaignKey: "daily_quality_open_actions",
      emailKey: "daily_quality_open_actions_v1",
      sendLogId: "send-123",
      emailStatus: "current",
      targetStage: "daily_review",
      targetEvent: "daily_quality_item_reviewed",
      linkIssuedAt: "2026-05-29T08:00:00Z",
      contextStatus: "current",
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
      metadata: {
        entry: "trace_full_page",
        retry: 1,
        setup: true,
        empty: null,
        skipped: undefined,
      },
    });

    expect(axios.post).toHaveBeenCalledWith("/accounts/activation-events/", {
      event_name: "trace_detail_opened",
      primary_path: "observe",
      stage: "review_first_trace",
      source: "trace_full_page",
      artifact_type: "trace",
      artifact_id: "trace-1",
      project_id: "observe-1",
      campaign_key: "daily_quality_open_actions",
      email_key: "daily_quality_open_actions_v1",
      send_log_id: "send-123",
      email_status: "current",
      target_stage: "daily_review",
      target_event: "daily_quality_item_reviewed",
      link_issued_at: "2026-05-29T08:00:00Z",
      context_status: "current",
      metadata: {
        quick_start_goal: "monitor_production_ai_app",
        quick_start_id: "observe",
        quick_start_primary_path: "observe",
        entry: "trace_full_page",
        retry: "1",
        setup: "true",
        empty: null,
      },
    });
    expect(state.stage).toBe("create_trace_evaluator");
  });

  it("drops unknown quick-start attribution from activation event metadata", async () => {
    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          activation_state: getActivationStateFixture("observeNeedsEvaluator"),
        },
      },
    });

    await recordActivationEvent({
      eventName: "trace_detail_opened",
      primaryPath: "observe",
      quickStartGoal: "secret",
      quickStartId: "user@example.com",
      quickStartPrimaryPath: "observe",
      metadata: {
        quick_start_goal: "secret",
        quick_start_id: "user@example.com",
        quick_start_primary_path: "observe",
      },
    });

    expect(axios.post).toHaveBeenCalledWith("/accounts/activation-events/", {
      event_name: "trace_detail_opened",
      primary_path: "observe",
    });
  });

  it("lets normalized top-level quick-start attribution override metadata", async () => {
    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          activation_state: getActivationStateFixture("observeNeedsEvaluator"),
        },
      },
    });

    await recordActivationEvent({
      eventName: "trace_detail_opened",
      primaryPath: "observe",
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
      metadata: {
        quick_start_goal: "secret",
        quick_start_id: "user@example.com",
        quick_start_primary_path: "voice",
      },
    });

    expect(axios.post).toHaveBeenCalledWith("/accounts/activation-events/", {
      event_name: "trace_detail_opened",
      primary_path: "observe",
      metadata: {
        quick_start_goal: "monitor_production_ai_app",
        quick_start_id: "observe",
        quick_start_primary_path: "observe",
      },
    });
  });

  it("returns a renderable feature-disabled state", async () => {
    axios.get.mockResolvedValueOnce({
      data: { result: getActivationStateFixture("featureDisabled") },
    });

    const state = await fetchActivationState();

    expect(state.stage).toBe("feature_disabled");
    expect(state.recommendedAction.href).toBe("/dashboard/get-started");
  });

  it("uses stable activation-state query keys", () => {
    expect(
      onboardingHomeQueryKeys.activationState({
        organizationId: "org-1",
        workspaceId: "wrk-1",
        source: "email",
      }),
    ).toEqual([
      "onboarding-home",
      "activation-state",
      {
        organizationId: "org-1",
        workspaceId: "wrk-1",
        source: "email",
      },
    ]);
  });

  it("lets the hook render a local fallback on hard failures", async () => {
    axios.get.mockRejectedValueOnce({ message: "offline" });

    const { result } = renderWithQueryClient(() =>
      useActivationState({
        organizationId: "org-1",
        workspaceId: "wrk-1",
      }),
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.state.stage).toBe("feature_disabled");
    expect(result.current.state.fallbackAction.href).toBe(
      "/dashboard/get-started",
    );
  });

  it("opens and hides sample projects through onboarding endpoints", async () => {
    axios.post
      .mockResolvedValueOnce({
        data: {
          result: {
            sample_project: {
              available: true,
              created: true,
              status: "ready_for_observe",
              href: "/dashboard/observe/observe-1/trace/trace-1?sample=true&from=onboarding",
              version: "2026-05-26.1",
              is_hidden: false,
              hidden_reason: null,
              entry_routes: [
                "/dashboard/observe/observe-1/trace/trace-1?sample=true&from=onboarding",
              ],
              missing_artifacts: [],
              last_opened_at: null,
            },
            activation_state: getActivationStateFixture(
              "observeWaitingWithSample",
            ),
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            sample_project: {
              available: false,
              created: true,
              status: "hidden",
              href: null,
              version: "2026-05-26.1",
              is_hidden: true,
              hidden_reason: "user_hidden",
              entry_routes: [],
              missing_artifacts: [],
              last_opened_at: null,
            },
            activation_state: getActivationStateFixture("sampleUnavailable"),
          },
        },
      });

    const opened = await openSampleProject({
      path: "observability",
      source: "onboarding_home",
      reason: "waiting_for_first_trace",
      openAfterCreate: true,
      campaignKey: "observe_sample_bridge",
      emailKey: "observe_sample_bridge_v1",
      sendLogId: "send-sample",
      targetStage: "waiting_for_first_trace_sample_available",
      targetEvent: "onboarding_sample_project_opened",
    });
    const hidden = await hideSampleProject({
      source: "onboarding_home",
      reason: "user_dismissed",
    });

    expect(axios.post).toHaveBeenNthCalledWith(1, "/accounts/sample-project/", {
      path: "observe",
      source: "onboarding_home",
      reason: "waiting_for_first_trace",
      open_after_create: true,
      campaign_key: "observe_sample_bridge",
      email_key: "observe_sample_bridge_v1",
      send_log_id: "send-sample",
      target_stage: "waiting_for_first_trace_sample_available",
      target_event: "onboarding_sample_project_opened",
    });
    expect(axios.post).toHaveBeenNthCalledWith(
      2,
      "/accounts/sample-project/hide/",
      {
        source: "onboarding_home",
        reason: "user_dismissed",
      },
    );
    expect(opened.stage).toBe("waiting_for_first_trace_sample_available");
    expect(opened.sampleProject.entryRoutes).toEqual([
      "/dashboard/observe/observe-1/trace/trace-1?sample=true&from=onboarding",
    ]);
    expect(hidden.sampleProject.status).toBe("unavailable");
  });

  it("reports missing sample endpoints explicitly", async () => {
    const original = endpoints.onboarding.sampleProject;
    endpoints.onboarding.sampleProject = null;
    await expect(openSampleProject()).rejects.toBeInstanceOf(
      OnboardingEndpointUnavailableError,
    );
    endpoints.onboarding.sampleProject = original;
  });
});
