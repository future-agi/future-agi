import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { paths } from "src/routes/paths";

import { normalizeActivationState } from "../activation-state-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";

vi.mock("src/hooks/useDeploymentMode", () => ({
  getDeploymentPostLoginPath: vi.fn((mode) =>
    mode === "oss" ? "/dashboard/develop" : "/dashboard/falcon-ai",
  ),
  useDeploymentMode: vi.fn(() => ({
    mode: "cloud",
    isCloud: true,
    isOSS: false,
    isEE: false,
    isLoading: false,
  })),
}));

vi.mock("src/utils/PostHog", () => ({
  isFeatureEnabled: vi.fn(),
  onFeatureFlags: vi.fn(() => undefined),
}));

vi.mock("../api/onboarding-home-api", () => ({
  fetchActivationState: vi.fn(),
  onboardingHomeQueryKeys: {
    activationState: (params = {}) => [
      "onboarding-home",
      "activation-state",
      params,
    ],
  },
}));

import { fetchActivationState } from "../api/onboarding-home-api";
import { usePostLoginDestination } from "../hooks/usePostLoginDestination";
import { isFeatureEnabled } from "src/utils/PostHog";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

const baseUser = {
  id: "user-1",
  onboarding_completed: true,
  organization_role: "Admin",
  default_workspace_role: "workspace_admin",
};

const flagsOn = {
  onboarding_activation_state_api: true,
  onboarding_first_run_home: true,
  onboarding_release_0_internal: true,
  onboarding_daily_quality_home: false,
};

const setFlags = (flags) => {
  isFeatureEnabled.mockImplementation((flagName) => flags[flagName] === true);
};

const state = (name) =>
  normalizeActivationState(getActivationStateFixture(name));

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

describe("usePostLoginDestination", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useDeploymentMode.mockReturnValue({
      mode: "cloud",
      isCloud: true,
      isOSS: false,
      isEE: false,
      isLoading: false,
    });
    setFlags(flagsOn);
  });

  it("does not fetch activation state when a required flag is off", () => {
    setFlags({
      ...flagsOn,
      onboarding_release_0_internal: false,
    });

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.falconAI,
        user: baseUser,
      }),
    );

    expect(fetchActivationState).not.toHaveBeenCalled();
    expect(result.current.destination.href).toBe(paths.dashboard.falconAI);
    expect(result.current.reason).toBe("required_flag_off");
  });

  it("fetches activation state and resolves home for eligible users", async () => {
    fetchActivationState.mockResolvedValueOnce(state("observeNoSetup"));

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.falconAI,
        user: baseUser,
      }),
    );

    await waitFor(() => expect(result.current.isResolving).toBe(false));

    expect(fetchActivationState).toHaveBeenCalledWith({
      source: "post_login",
      mode: "post_login",
    });
    expect(result.current.destination.href).toBe(paths.dashboard.home);
    expect(result.current.reason).toBe("internal_onboarding_home");
  });

  it("does not fetch activation state for safe return targets", () => {
    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.falconAI,
        returnTo: "/dashboard/observe?project=1",
        user: baseUser,
      }),
    );

    expect(fetchActivationState).not.toHaveBeenCalled();
    expect(result.current.destination.href).toBe(
      "/dashboard/observe?project=1",
    );
    expect(result.current.reason).toBe("safe_return_to");
  });

  it("does not fetch activation state for direct dashboard routes", () => {
    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: "/dashboard/observe?project=1",
        user: baseUser,
      }),
    );

    expect(fetchActivationState).not.toHaveBeenCalled();
    expect(result.current.destination.href).toBe(
      "/dashboard/observe?project=1",
    );
    expect(result.current.reason).toBe("direct_dashboard_route");
  });

  it("falls back when activation state fails", async () => {
    fetchActivationState.mockRejectedValueOnce(new Error("offline"));

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.falconAI,
        user: baseUser,
      }),
    );

    await waitFor(() =>
      expect(result.current.reason).toBe("activation_state_error"),
    );
    expect(result.current.destination.href).toBe(paths.dashboard.falconAI);
  });
});
