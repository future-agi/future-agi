import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildCurrentFlowContext,
  CurrentFlowEvents,
  getFirstIncompleteStep,
  getRouteFamily,
  isOnboardingRoute,
  isProductRoute,
  markOncePerWorkspaceSession,
  normalizeCurrentFlowProperties,
  normalizeGetStartedStep,
  trackCurrentFlow,
} from "./currentFlow";

import { trackPostHogEvent } from "src/utils/PostHog";

vi.mock("src/utils/PostHog", () => ({
  trackPostHogEvent: vi.fn(),
}));

describe("current flow analytics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
  });

  it("removes undefined and unsafe content properties", () => {
    expect(
      normalizeCurrentFlowProperties({
        workspace_id: "workspace-1",
        route: undefined,
        email: "user@example.com",
        prompt_text: "hidden",
        message_text: "hidden",
        api_key: "hidden",
      }),
    ).toEqual({ workspace_id: "workspace-1" });
  });

  it("tracks to PostHog with safe properties", () => {
    const tracked = trackCurrentFlow("current_flow_test", {
      workspace_id: "workspace-1",
      message_text: "hidden",
      route: "/dashboard/get-started",
    });

    expect(tracked).toBe(true);
    expect(trackPostHogEvent).toHaveBeenCalledWith("current_flow_test", {
      workspace_id: "workspace-1",
      route: "/dashboard/get-started",
    });
  });

  it("dedupes once-per-session events", () => {
    expect(markOncePerWorkspaceSession(["landing", "workspace-1"])).toBe(true);
    expect(markOncePerWorkspaceSession(["landing", "workspace-1"])).toBe(false);
  });

  it("skips duplicate once-per-session tracking", () => {
    const options = { onceKeyParts: ["event", "workspace-1"] };

    expect(trackCurrentFlow("current_flow_test", {}, options)).toBe(true);
    expect(trackCurrentFlow("current_flow_test", {}, options)).toBe(false);
    expect(trackPostHogEvent).toHaveBeenCalledTimes(1);
  });

  it("owns the onboarding baseline event names in the analytics helper", () => {
    expect(CurrentFlowEvents.currentFlowGetStartedViewed).toBe(
      "current_flow_get_started_viewed",
    );
    expect(CurrentFlowEvents.currentFlowFirstValueCandidate).toBe(
      "current_flow_first_value_candidate",
    );
  });

  it("classifies route families", () => {
    expect(getRouteFamily("/dashboard/falcon-ai")).toBe("falcon");
    expect(getRouteFamily("/dashboard/get-started")).toBe("get_started");
    expect(getRouteFamily("/dashboard/prompt/add/123")).toBe("prompt");
    expect(getRouteFamily("/dashboard/agents")).toBe("agent");
    expect(getRouteFamily("/dashboard/gateway/logs")).toBe("gateway");
    expect(getRouteFamily("/dashboard/evaluations")).toBe("evals");
  });

  it("separates onboarding and product routes", () => {
    expect(isOnboardingRoute("/dashboard/get-started")).toBe(true);
    expect(isProductRoute("/dashboard/get-started")).toBe(false);
    expect(isProductRoute("/dashboard/falcon-ai")).toBe(false);
    expect(isProductRoute("/dashboard/observe")).toBe(true);
  });

  it("normalizes Get Started step labels", () => {
    expect(normalizeGetStartedStep("SetupObsabilityInApplication")).toBe(
      "setup_obsability_in_application",
    );
    expect(normalizeGetStartedStep("create first dataset")).toBe(
      "create_first_dataset",
    );
  });

  it("finds the first incomplete first-check step", () => {
    expect(
      getFirstIncompleteStep({
        keys: true,
        dataset: false,
        evals: false,
      }),
    ).toBe("dataset");
    expect(getFirstIncompleteStep({ keys: true })).toBe(null);
  });

  it("builds a safe user context", () => {
    expect(
      buildCurrentFlowContext({
        route: "/dashboard/observe",
        postLoginPath: "/dashboard/falcon-ai",
        deploymentMode: "cloud",
        user: {
          id: "user-1",
          email: "user@example.com",
          default_workspace_id: "workspace-1",
          default_workspace_role: "workspace_admin",
          organization_role: "Owner",
          onboarding_completed: true,
          requires_org_setup: false,
          organization: { id: "org-1", name: "Org" },
        },
      }),
    ).toEqual({
      workspace_id: "workspace-1",
      organization_id: "org-1",
      user_id: "user-1",
      route: "/dashboard/observe",
      route_family: "observe",
      deployment_mode: "cloud",
      post_login_path: "/dashboard/falcon-ai",
      is_invited_user: false,
      organization_role: "Owner",
      workspace_role: "workspace_admin",
      onboarding_completed: true,
      requires_org_setup: false,
    });
  });
});
