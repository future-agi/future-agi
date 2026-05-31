import { describe, expect, it } from "vitest";
import {
  agentSetupQuickStartAttributionFromSearch,
  appendAgentOnboardingAttributionToHref,
  buildAgentBuilderHref,
  buildAgentCreatedPayload,
  buildAgentOnboardingReturnHref,
  buildAgentReviewRunHref,
  buildAgentTraceReviewedPayload,
} from "./agentOnboardingEvents";

const AGENT_QUICK_START_SEARCH =
  "?quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent";

describe("agentOnboardingEvents", () => {
  it("keeps setup quick-start attribution across agent routes", () => {
    const attribution = agentSetupQuickStartAttributionFromSearch(
      AGENT_QUICK_START_SEARCH,
    );

    expect(attribution).toEqual({
      quick_start_goal: "build_ai_agent",
      quick_start_id: "agent",
      quick_start_primary_path: "agent",
    });
    expect(
      appendAgentOnboardingAttributionToHref(
        "/dashboard/agents?onboarding=create",
        attribution,
      ),
    ).toBe(
      "/dashboard/agents?onboarding=create&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
    );
    expect(
      buildAgentBuilderHref({
        agentId: "agent-1",
        quickStartAttribution: attribution,
        versionId: "version-1",
      }),
    ).toBe(
      "/dashboard/agents/playground/agent-1/build?version=version-1&onboarding=run-scenario&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
    );
    expect(
      buildAgentReviewRunHref({
        agentId: "agent-1",
        search: AGENT_QUICK_START_SEARCH,
      }),
    ).toBe(
      "/dashboard/agents/playground/agent-1/executions?onboarding=review-run&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
    );
  });

  it("adds setup quick-start attribution to agent activation payloads", () => {
    const attribution = agentSetupQuickStartAttributionFromSearch(
      AGENT_QUICK_START_SEARCH,
    );

    expect(
      buildAgentCreatedPayload({
        agentId: "agent-1",
        quickStartAttribution: attribution,
      }),
    ).toMatchObject({
      eventName: "agent_created",
      quick_start_goal: "build_ai_agent",
      quick_start_id: "agent",
      quick_start_primary_path: "agent",
    });
    expect(
      buildAgentTraceReviewedPayload({
        agentId: "agent-1",
        executionId: "execution-1",
        nodeExecutionId: "node-1",
        quickStartAttribution: attribution,
      }),
    ).toMatchObject({
      eventName: "agent_trace_reviewed",
      quick_start_goal: "build_ai_agent",
      quick_start_id: "agent",
      quick_start_primary_path: "agent",
    });
  });

  it("keeps setup quick-start attribution when returning home", () => {
    const payload = buildAgentCreatedPayload({
      agentId: "agent-1",
      quickStartAttribution: agentSetupQuickStartAttributionFromSearch(
        AGENT_QUICK_START_SEARCH,
      ),
    });

    expect(buildAgentOnboardingReturnHref(payload)).toBe(
      "/dashboard/home?mode=daily-quality&source=onboarding&target_event=agent_created&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
    );
  });
});
