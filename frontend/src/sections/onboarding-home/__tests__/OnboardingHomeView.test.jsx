import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { renderWithRouter } from "src/utils/test-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";
import { normalizeActivationState } from "../activation-state-utils";
import OnboardingHomeView from "../OnboardingHomeView";

const mocks = vi.hoisted(() => ({
  useActivationState: vi.fn(),
  useSaveOnboardingGoal: vi.fn(),
  useSampleProject: vi.fn(),
  useAuthContext: vi.fn(),
  useWorkspace: vi.fn(),
  trackOnboardingHomeEvent: vi.fn(),
}));

vi.mock("../hooks/useActivationState", () => ({
  useActivationState: (params) => mocks.useActivationState(params),
}));

vi.mock("../hooks/useSaveOnboardingGoal", () => ({
  useSaveOnboardingGoal: () => mocks.useSaveOnboardingGoal(),
}));

vi.mock("../hooks/useSampleProject", () => ({
  useSampleProject: () => mocks.useSampleProject(),
}));

vi.mock("../analytics/onboarding-events", async () => {
  const actual = await vi.importActual("../analytics/onboarding-events");
  return {
    ...actual,
    trackOnboardingHomeEvent: (...args) =>
      mocks.trackOnboardingHomeEvent(...args),
  };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => mocks.useAuthContext(),
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => mocks.useWorkspace(),
}));

const defaultUser = {
  id: "usr_onboarding",
  default_workspace_id: "wrk_onboarding",
  organization: {
    id: "org_onboarding",
  },
};

const defaultWorkspace = {
  currentWorkspaceId: "wrk_onboarding",
  currentWorkspaceDisplayName: "Quality Workspace",
  isReady: true,
};

const normalizedFixture = (name) =>
  normalizeActivationState(getActivationStateFixture(name));

const renderView = (route = "/dashboard/home") =>
  renderWithRouter(<OnboardingHomeView />, { route });

describe("OnboardingHomeView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useAuthContext.mockReturnValue({ user: defaultUser });
    mocks.useWorkspace.mockReturnValue(defaultWorkspace);
    mocks.useSaveOnboardingGoal.mockReturnValue({
      data: null,
      error: null,
      isLoading: false,
      isPending: false,
      mutateAsync: vi.fn(),
    });
    mocks.useSampleProject.mockReturnValue({
      openSampleProject: {
        isLoading: false,
        isPending: false,
        mutateAsync: vi.fn(),
      },
      hideSampleProject: {
        isLoading: false,
        isPending: false,
        mutateAsync: vi.fn(),
      },
    });
  });

  it("renders the route skeleton while activation state is loading", () => {
    mocks.useActivationState.mockReturnValue({
      state: null,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    expect(screen.getByTestId("onboarding-home-skeleton")).toBeInTheDocument();
  });

  it("falls back to Get Started when onboarding home is feature disabled", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("featureDisabled"),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    expect(screen.getByText("Start with the setup checklist")).toBeVisible();
    expect(screen.getAllByText("Open Get Started").length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        "The existing setup checklist is available for this workspace.",
      ),
    ).toBeVisible();
  });

  it("renders the recommended first action from activation state", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("newWorkspaceNoGoal"),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView("/dashboard/home?source=email&campaign_key=welcome");

    expect(screen.getByText("Choose what to set up first")).toBeVisible();
    expect(screen.getByTestId("onboarding-goal-picker")).toBeVisible();
    expect(screen.getAllByText("Monitor a production AI app").length).toBe(2);
    expect(screen.getByText("Workspace: Quality Workspace")).toBeVisible();
    expect(mocks.useActivationState).toHaveBeenCalledWith(
      expect.objectContaining({
        organizationId: "org_onboarding",
        workspaceId: "wrk_onboarding",
        source: "email",
        campaignKey: "welcome",
      }),
    );
  });

  it("renders the observe setup panel for the observe MVP path", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("observeNoSetup"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    expect(screen.getByTestId("observe-setup-panel")).toBeVisible();
    expect(screen.getByText("Connect one observe project")).toBeVisible();
  });

  it("tracks canonical home and recommendation view events", async () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("observeNoSetup"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    await waitFor(() =>
      expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
        "onboarding_home_viewed",
        expect.objectContaining({
          workspace_id: "wrk_onboarding",
          organization_id: "org_onboarding",
          user_id: "usr_onboarding",
          activation_stage: "connect_observability",
          primary_path: "observe",
          is_sample: false,
          permission_limited: false,
        }),
      ),
    );
    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "onboarding_recommended_action_viewed",
      expect.objectContaining({
        recommended_action_id: "create_observe_project",
        target_success_event: "observe_project_created",
        route_available: true,
      }),
    );
  });

  it("checks again from the waiting-for-signal panel", async () => {
    const refetch = vi.fn();
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("observeWaitingForTrace"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch,
    });

    renderView();

    expect(screen.getByTestId("waiting-for-signal-panel")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /check again/i }));

    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("opens the sample panel from the waiting state", async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue(normalizedFixture("observeWaitingWithSample"));
    mocks.useSampleProject.mockReturnValue({
      openSampleProject: {
        isLoading: false,
        isPending: false,
        mutateAsync,
      },
      hideSampleProject: {
        isLoading: false,
        isPending: false,
        mutateAsync: vi.fn(),
      },
    });
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("observeWaitingWithSample"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    expect(screen.getByTestId("sample-project-panel")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: /open sample trace/i }),
    );

    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "observe",
        source: "onboarding_home",
        reason: "waiting_for_first_trace_sample_available",
      }),
    );
  });

  it("saves a selected goal through the goal mutation", async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue(normalizedFixture("observeNoSetup"));
    mocks.useSaveOnboardingGoal.mockReturnValue({
      data: null,
      error: null,
      isLoading: false,
      isPending: false,
      mutateAsync,
    });
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("newWorkspaceNoGoal"),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    await userEvent.click(screen.getByLabelText("Monitor a production AI app"));
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({
        goal: "monitor_production_ai_app",
        primaryPath: "observe",
        source: "goal_picker",
        reason: "first_selection",
        expectedStage: "choose_goal",
      }),
    );
    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalled();
  });

  it("renders a hard-error fallback and retries on demand", async () => {
    const refetch = vi.fn();
    mocks.useActivationState.mockReturnValue({
      state: null,
      isLoading: false,
      isError: true,
      error: { message: "Activation state failed" },
      refetch,
    });

    renderView();

    expect(screen.getByTestId("onboarding-home-error")).toBeInTheDocument();
    expect(screen.getByText("Activation state failed")).toBeVisible();
    expect(screen.getByRole("link", { name: /get started/i })).toHaveAttribute(
      "href",
      "/dashboard/get-started",
    );

    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
