import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { paths } from "src/routes/paths";

import { normalizeActivationState } from "../activation-state-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";

vi.mock("src/hooks/useDeploymentMode", () => ({
  getDeploymentPostLoginPath: vi.fn(() => "/dashboard/home"),
  useDeploymentMode: vi.fn(() => ({
    mode: "cloud",
    isCloud: true,
    isOSS: false,
    isEE: false,
    isLoading: false,
  })),
}));

vi.mock("src/utils/PostHog", () => ({
  getFeatureFlagValue: vi.fn(),
  isPostHogAvailable: vi.fn(),
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
import { getFeatureFlagValue, isPostHogAvailable } from "src/utils/PostHog";
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
  onboarding_first_run_home_kill: false,
  onboarding_release_0_internal: true,
  onboarding_daily_quality_home: false,
};

const setFlags = (flags) => {
  getFeatureFlagValue.mockImplementation((flagName) =>
    Object.prototype.hasOwnProperty.call(flags, flagName)
      ? flags[flagName]
      : undefined,
  );
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
    isPostHogAvailable.mockReturnValue(true);
    setFlags(flagsOn);
  });

  it("does not fetch activation state when the activation API flag is off", () => {
    setFlags({
      ...flagsOn,
      onboarding_activation_state_api: false,
    });

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.falconAI,
        user: baseUser,
      }),
    );

    expect(fetchActivationState).not.toHaveBeenCalled();
    expect(result.current.destination.href).toBe(paths.dashboard.home);
    expect(result.current.reason).toBe("required_flag_off");
  });

  it("does not fetch activation state when guided home is explicitly disabled", () => {
    setFlags({
      ...flagsOn,
      onboarding_first_run_home: false,
    });

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.falconAI,
        user: baseUser,
      }),
    );

    expect(fetchActivationState).not.toHaveBeenCalled();
    expect(result.current.destination.href).toBe(paths.dashboard.home);
    expect(result.current.reason).toBe("required_flag_off");
  });

  it("does not fetch activation state when the guided home kill switch is on", () => {
    setFlags({
      ...flagsOn,
      onboarding_first_run_home_kill: true,
    });

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.falconAI,
        user: baseUser,
      }),
    );

    expect(fetchActivationState).not.toHaveBeenCalled();
    expect(result.current.destination.href).toBe(paths.dashboard.home);
    expect(result.current.reason).toBe("required_flag_off");
  });

  it("defaults guided home on when PostHog is unavailable", async () => {
    isPostHogAvailable.mockReturnValue(false);
    getFeatureFlagValue.mockImplementation(() => false);
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
    expect(result.current.flags.onboarding_activation_state_api).toBe(true);
    expect(result.current.flags.onboarding_first_run_home).toBe(true);
    expect(result.current.flags.onboarding_first_run_home_kill).toBe(false);
    expect(result.current.destination.href).toBe(paths.dashboard.home);
    expect(result.current.reason).toBe("guided_onboarding_home");
  });

  it("defaults absent guided home flags on when PostHog is available", async () => {
    setFlags({
      onboarding_activation_state_api: true,
    });
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
    expect(result.current.flags.onboarding_first_run_home).toBe(true);
    expect(result.current.flags.onboarding_first_run_home_kill).toBe(false);
    expect(result.current.destination.href).toBe(paths.dashboard.home);
    expect(result.current.reason).toBe("guided_onboarding_home");
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
    expect(result.current.reason).toBe("guided_onboarding_home");
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

  it("checks activation before honoring a safe return target for incomplete users", async () => {
    fetchActivationState.mockResolvedValueOnce(state("observeNoSetup"));

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.falconAI,
        returnTo: "/dashboard/observe?project=1",
        user: {
          ...baseUser,
          onboarding_completed: false,
        },
      }),
    );

    await waitFor(() => expect(result.current.isResolving).toBe(false));

    expect(fetchActivationState).toHaveBeenCalledWith({
      source: "post_login",
      mode: "post_login",
    });
    expect(result.current.destination.href).toBe(paths.auth.jwt.setup_org);
    expect(result.current.reason).toBe("onboarding_incomplete");
    expect(result.current.destination.usedReturnTo).toBe(false);
  });

  it("honors safe return targets for incomplete users after workspace activation", async () => {
    fetchActivationState.mockResolvedValueOnce(
      state("observeFirstLoopComplete"),
    );

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.auth.jwt.setup_org,
        returnTo: "/dashboard/observe?project=1",
        user: {
          ...baseUser,
          onboarding_completed: false,
        },
      }),
    );

    await waitFor(() => expect(result.current.isResolving).toBe(false));

    expect(fetchActivationState).toHaveBeenCalledWith({
      source: "post_login",
      mode: "post_login",
    });
    expect(result.current.destination.href).toBe(
      "/dashboard/observe?project=1",
    );
    expect(result.current.reason).toBe("safe_return_to");
    expect(result.current.destination.usedReturnTo).toBe(true);
  });

  it("does not preserve legacy fallback return targets", async () => {
    fetchActivationState.mockResolvedValueOnce(state("observeNoSetup"));

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.falconAI,
        returnTo: `${paths.dashboard.falconAI}?from=login`,
        user: baseUser,
      }),
    );

    await waitFor(() => expect(result.current.isResolving).toBe(false));

    expect(fetchActivationState).toHaveBeenCalledWith({
      source: "post_login",
      mode: "post_login",
    });
    expect(result.current.destination.href).toBe(paths.dashboard.home);
    expect(result.current.reason).toBe("guided_onboarding_home");
    expect(result.current.destination.usedReturnTo).toBe(false);
    expect(result.current.destination.shouldClearReturnTo).toBe(true);
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

  it("checks activation state before routing incomplete users back to setup", async () => {
    fetchActivationState.mockResolvedValueOnce(state("observeNoSetup"));

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.home,
        user: {
          ...baseUser,
          onboarding_completed: false,
        },
      }),
    );

    await waitFor(() => expect(result.current.isResolving).toBe(false));

    expect(fetchActivationState).toHaveBeenCalledWith({
      source: "post_login",
      mode: "post_login",
    });
    expect(result.current.destination.href).toBe(paths.auth.jwt.setup_org);
    expect(result.current.reason).toBe("onboarding_incomplete");
    expect(result.current.destination.shouldReplace).toBe(true);
  });

  it("routes incomplete users to home when their workspace is already activated", async () => {
    fetchActivationState.mockResolvedValueOnce(
      state("observeFirstLoopComplete"),
    );

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.auth.jwt.setup_org,
        user: {
          ...baseUser,
          onboarding_completed: false,
        },
      }),
    );

    await waitFor(() => expect(result.current.isResolving).toBe(false));

    expect(fetchActivationState).toHaveBeenCalledWith({
      source: "post_login",
      mode: "post_login",
    });
    expect(result.current.destination.href).toBe(paths.dashboard.home);
    expect(result.current.reason).toBe("workspace_setup_complete");
  });

  it("keeps incomplete users on setup without fetching when rollout flags are off", () => {
    setFlags({
      ...flagsOn,
      onboarding_activation_state_api: false,
    });

    const { result } = renderWithQueryClient(() =>
      usePostLoginDestination({
        currentPath: paths.dashboard.home,
        user: {
          ...baseUser,
          onboarding_completed: false,
        },
      }),
    );

    expect(fetchActivationState).not.toHaveBeenCalled();
    expect(result.current.destination.href).toBe(paths.auth.jwt.setup_org);
    expect(result.current.reason).toBe("onboarding_incomplete");
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
    expect(result.current.destination.href).toBe(paths.dashboard.home);
  });
});
