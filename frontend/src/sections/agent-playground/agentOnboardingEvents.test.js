import { describe, expect, it } from "vitest";
import {
  agentSetupQuickStartAttributionFromSearch,
  appendAgentOnboardingAttributionToHref,
  buildAgentBuilderHref,
  buildAgentCreatedPayload,
  buildAgentEvalBuilderHref,
  buildAgentEvalCreatedPayload,
  buildAgentNodeAddedPayload,
  buildAgentOnboardingStarterPromptConfig,
  buildAgentOnboardingReturnHref,
  buildAgentPrototypeRunCompletedPayload,
  buildAgentReviewRunHref,
  buildAgentScenarioSavedAsEvalPayload,
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
    expect(
      buildAgentEvalBuilderHref({
        agentId: "agent-1",
        quickStartAttribution: attribution,
        versionId: "version-1",
      }),
    ).toBe(
      "/dashboard/agents/playground/agent-1/build?version=version-1&onboarding=add-eval&tour_anchor=agent_save_eval_button&journey_step=save_agent_eval&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
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
      buildAgentNodeAddedPayload({
        agentId: "agent-1",
        nodeId: "node-1",
        quickStartAttribution: attribution,
        versionId: "version-1",
      }),
    ).toMatchObject({
      eventName: "agent_node_added",
      primaryPath: "agent",
      stage: "add_agent_node",
      source: "agent_playground",
      metadata: {
        agent_id: "agent-1",
        node_id: "node-1",
        version_id: "version-1",
      },
      idempotencyKey: "agent_node_added:agent-1:node-1",
      quick_start_goal: "build_ai_agent",
      quick_start_id: "agent",
      quick_start_primary_path: "agent",
    });
    expect(
      buildAgentPrototypeRunCompletedPayload({
        agentId: "agent-1",
        executionId: "execution-1",
        quickStartAttribution: attribution,
        status: "success",
        versionId: "version-1",
      }),
    ).toMatchObject({
      eventName: "agent_prototype_run_completed",
      primaryPath: "agent",
      stage: "run_agent_scenario",
      source: "agent_playground",
      metadata: {
        agent_id: "agent-1",
        graph_execution_id: "execution-1",
        status: "success",
        version_id: "version-1",
      },
      idempotencyKey: "agent_prototype_run_completed:execution-1",
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
    expect(
      buildAgentScenarioSavedAsEvalPayload({
        agentId: "agent-1",
        nodeId: "eval-node-1",
        quickStartAttribution: attribution,
        versionId: "version-1",
      }),
    ).toMatchObject({
      eventName: "agent_scenario_saved_as_eval",
      primaryPath: "agent",
      stage: "save_agent_eval",
      metadata: {
        agent_id: "agent-1",
        eval_node_id: "eval-node-1",
        version_id: "version-1",
      },
      idempotencyKey: "agent_scenario_saved_as_eval:agent-1:eval-node-1",
      quick_start_goal: "build_ai_agent",
      quick_start_id: "agent",
      quick_start_primary_path: "agent",
    });
    expect(
      buildAgentEvalCreatedPayload({
        agentId: "agent-1",
        executionId: "execution-2",
        quickStartAttribution: attribution,
        status: "success",
        versionId: "version-1",
      }),
    ).toMatchObject({
      eventName: "agent_eval_created",
      primaryPath: "agent",
      stage: "agent_create_eval",
      metadata: {
        agent_id: "agent-1",
        graph_execution_id: "execution-2",
        status: "success",
        version_id: "version-1",
      },
      idempotencyKey: "agent_eval_created:execution-2",
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

  it("builds a runnable starter prompt for first agent setup", () => {
    const config = buildAgentOnboardingStarterPromptConfig();

    expect(config.modelConfig).toMatchObject({
      model: "gpt-4o-mini",
      responseFormat: "text",
      toolChoice: "auto",
      tools: [],
    });
    expect(config.messages).toEqual([
      expect.objectContaining({
        role: "system",
        content: [
          expect.objectContaining({
            text: expect.stringContaining("triage AI product issues"),
          }),
        ],
      }),
      expect.objectContaining({
        role: "user",
        content: [
          expect.objectContaining({
            text: expect.stringContaining("outdated pricing"),
          }),
        ],
      }),
    ]);
  });
});
