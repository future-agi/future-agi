import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { renderWithRouter } from "src/utils/test-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";
import { normalizeActivationState } from "../activation-state-utils";
import OnboardingHomeView from "../OnboardingHomeView";
import { readPersistedSetupQuickStartAttribution } from "src/sections/auth/jwt/setup-org-quick-starts";

const mocks = vi.hoisted(() => ({
  useActivationState: vi.fn(),
  useRecordActivationEvent: vi.fn(),
  useSaveOnboardingGoal: vi.fn(),
  useSampleProject: vi.fn(),
  useAuthContext: vi.fn(),
  useWorkspace: vi.fn(),
  trackOnboardingHomeEvent: vi.fn(),
}));

vi.mock("../hooks/useActivationState", () => ({
  useActivationState: (params) => mocks.useActivationState(params),
}));

vi.mock("../hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => mocks.useRecordActivationEvent(),
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

const pathAction = ({
  completionEvent = null,
  ctaLabel,
  description,
  href,
  id,
  kind = "setup",
  routeAvailable = true,
  targetPath,
  title,
}) => ({
  id,
  kind,
  title,
  description,
  href,
  ctaLabel,
  estimatedMinutes: 3,
  priority: 100,
  blocked: false,
  blockedReason: null,
  requiresPermission: null,
  completionEvent,
  isSample: false,
  routeAvailable,
  fallbackHref: "/dashboard/get-started",
  analytics: {
    eventName: "onboarding_recommended_action_clicked",
    source: "home",
    targetPath,
  },
});

const pathState = ({
  action,
  goal,
  pathDescription,
  pathLabel,
  primaryPath,
  stage,
}) => {
  const state = normalizedFixture("observeNoSetup");
  const pathHref = `/dashboard/home?path=${primaryPath}`;

  return {
    ...state,
    availablePaths: [
      {
        id: primaryPath,
        label: pathLabel,
        description: pathDescription,
        status: "selected",
        href: pathHref,
        isAvailable: true,
        blockedReason: null,
        requiresPermission: null,
        firstActionId: action.id,
      },
    ],
    featureFlags: {
      ...state.featureFlags,
      [`onboarding_${primaryPath}_path`]: true,
    },
    goal,
    primaryPath,
    progress: {
      build: "complete",
      test: "selected",
      observe: "not_started",
      ship: "not_started",
      improve: "not_started",
    },
    recommendedAction: action,
    routeAvailability: {
      ...state.routeAvailability,
      [`path_${primaryPath}`]: {
        href: pathHref,
        isAvailable: true,
        reason: null,
      },
      [action.id]: {
        href: action.href,
        isAvailable: action.routeAvailable,
        reason: action.routeAvailable ? null : "route_not_available",
      },
    },
    sampleProject: {
      ...state.sampleProject,
      available: false,
    },
    stage,
  };
};

const observeJourneyPlan = ({ currentStepIndex = 0 } = {}) => {
  const steps = [
    {
      id: "connect_observability",
      stage: "connect_observability",
      actionId: "create_observe_project",
      label: "Create project from manifest",
      description: "Create the observe project and prepare the first trace.",
      href: "/dashboard/observe?setup=true&source=onboarding",
      fallbackHref: "/dashboard/get-started",
      routeAvailable: true,
      tourAnchor: "observe_create_project_button",
    },
    {
      id: "send_first_trace",
      stage: "waiting_for_first_trace",
      actionId: "send_first_trace",
      label: "Send trace from manifest",
      description: "Send one production or test trace.",
      href: "/dashboard/observe/observe-1",
      fallbackHref: "/dashboard/get-started",
      routeAvailable: true,
      tourAnchor: "observe_send_trace_button",
    },
    {
      id: "review_first_trace",
      stage: "review_first_trace",
      actionId: "review_first_trace",
      label: "Review signal from manifest",
      description: "Inspect the first signal and decide what to measure.",
      href: "/dashboard/observe/observe-1/trace/trace-1",
      fallbackHref: "/dashboard/get-started",
      routeAvailable: true,
      tourAnchor: "observe_trace_review_link",
    },
    {
      id: "create_trace_evaluator",
      stage: "create_trace_evaluator",
      actionId: "create_trace_evaluator",
      label: "Create check from manifest",
      description: "Convert the reviewed trace into repeatable coverage.",
      href: "/dashboard/observe/observe-1",
      fallbackHref: "/dashboard/get-started",
      routeAvailable: true,
      tourAnchor: "observe_evaluator_button",
    },
  ].map((step, index) => ({
    ...step,
    status:
      index < currentStepIndex
        ? "complete"
        : index === currentStepIndex
          ? "current"
          : "queued",
  }));

  const currentStep = steps[currentStepIndex] || steps[0];
  return {
    id: "observe_first_run",
    primaryPath: "observe",
    eyebrow: "Observe loop",
    title: "Start with your first quality loop",
    description:
      "Connect traces, review the first signal, then turn it into a check.",
    chips: ["observe", "quality"],
    currentStepId: currentStep.id,
    currentStepIndex,
    steps,
  };
};

describe("OnboardingHomeView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    mocks.useAuthContext.mockReturnValue({ user: defaultUser });
    mocks.useWorkspace.mockReturnValue(defaultWorkspace);
    mocks.useRecordActivationEvent.mockReturnValue({
      mutate: vi.fn(),
      isLoading: false,
      isPending: false,
    });
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

  it("renders the sample Aha panel before real Observe setup for first-run users", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("newWorkspaceNoGoal"),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView("/dashboard/home?source=email&campaign_key=welcome");

    const samplePanel = screen.getByTestId("sample-project-panel");
    const observeSetupPanel = screen.getByTestId("observe-setup-panel");
    const onboardingView = screen.getByTestId("onboarding-home-view");
    const panelOrder = Array.from(
      onboardingView.querySelectorAll(
        '[data-testid="sample-project-panel"], [data-testid="observe-setup-panel"]',
      ),
    );

    expect(samplePanel).toBeVisible();
    expect(within(samplePanel).getByText("Fastest path to Aha")).toBeVisible();
    expect(
      within(samplePanel).getByText("Preview the quality loop first"),
    ).toBeVisible();
    expect(
      within(samplePanel).getByRole("button", { name: /open sample trace/i }),
    ).toBeVisible();
    expect(observeSetupPanel).toBeVisible();
    expect(
      within(observeSetupPanel).getByText("Connect one observe project"),
    ).toBeVisible();
    expect(panelOrder).toEqual([samplePanel, observeSetupPanel]);
    expect(
      screen.queryByTestId("onboarding-goal-picker"),
    ).not.toBeInTheDocument();
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

  it("renders backend observe journey progress on the setup panel", () => {
    mocks.useActivationState.mockReturnValue({
      state: {
        ...normalizedFixture("observeNoSetup"),
        journeyPlan: observeJourneyPlan({ currentStepIndex: 0 }),
      },
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("observe-setup-panel");
    const currentStep = within(panel).getByTestId(
      "observe-journey-step-connect_observability",
    );
    expect(within(panel).getByTestId("observe-journey-progress")).toBeVisible();
    expect(
      within(currentStep).getByText("Create project from manifest"),
    ).toBeVisible();
    expect(within(currentStep).getByText("Now")).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Create the observe project and prepare the first trace.",
    );
    expect(
      within(panel).getByRole("link", { name: /connect observability/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/observe?setup=true&source=onboarding&tour_anchor=observe_create_project_button&journey_step=connect_observability",
    );
  });

  it("renders backend observe journey progress while waiting for a trace", () => {
    mocks.useActivationState.mockReturnValue({
      state: {
        ...normalizedFixture("observeWaitingForTrace"),
        journeyPlan: observeJourneyPlan({ currentStepIndex: 1 }),
      },
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("waiting-for-signal-panel");
    const currentStep = within(panel).getByTestId(
      "observe-journey-step-send_first_trace",
    );
    expect(
      within(currentStep).getByText("Send trace from manifest"),
    ).toBeVisible();
    expect(within(currentStep).getByText("Now")).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Send one production or test trace.",
    );
    expect(within(panel).getByText("Projects: 1 · Traces: 0")).toBeVisible();
    expect(
      within(panel).getByRole("link", { name: /send trace/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/observe/observe-1?tour_anchor=observe_send_trace_button&journey_step=send_first_trace",
    );
  });

  it("tracks lifecycle email attribution on Home views and CTA clicks", async () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("observeNoSetup"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView(
      "/dashboard/home?source=onboarding_email&campaign_key=observe_waiting_for_first_trace&email_key=observe_waiting_v1&target_stage=waiting_for_first_trace&target_event=trace_received&send_log_id=send-123&status=stale&stale_reason=target_complete",
    );

    expect(mocks.useActivationState).toHaveBeenCalledWith(
      expect.objectContaining({
        source: "onboarding_email",
        campaignKey: "observe_waiting_for_first_trace",
        emailKey: "observe_waiting_v1",
        targetStage: "waiting_for_first_trace",
        targetEvent: "trace_received",
        sendLogId: "send-123",
        emailStatus: "stale",
        staleReason: "target_complete",
      }),
    );
    await waitFor(() =>
      expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
        "onboarding_home_viewed",
        expect.objectContaining({
          source: "onboarding_email",
          campaign_key: "observe_waiting_for_first_trace",
          email_key: "observe_waiting_v1",
          target_stage: "waiting_for_first_trace",
          target_event: "trace_received",
          send_log_id: "send-123",
          email_status: "stale",
          stale_reason: "target_complete",
        }),
      ),
    );

    await userEvent.click(
      screen.getByRole("link", { name: /connect observability/i }),
    );

    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "onboarding_recommended_action_clicked",
      expect.objectContaining({
        action_id: "create_observe_project",
        source: "onboarding_email",
        campaign_key: "observe_waiting_for_first_trace",
        email_key: "observe_waiting_v1",
        target_event: "trace_received",
        send_log_id: "send-123",
        email_status: "stale",
        stale_reason: "target_complete",
      }),
    );
  });

  it("shows a recovery message for stale lifecycle email links", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("staleEmailLink"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const alert = screen.getByTestId("lifecycle-email-context-alert");
    expect(alert).toBeVisible();
    expect(
      within(alert).getByText("Your onboarding step changed"),
    ).toBeVisible();
    expect(
      within(alert).getByText(
        "Continue with the latest recommended step below.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: /review trace/i })).toHaveAttribute(
      "href",
      "/dashboard/observe/observe-1/trace/trace-1",
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
    expect(screen.getByTestId("sample-project-panel")).toBeVisible();
    expect(screen.getByText("Connect one observe project")).toBeVisible();
  });

  it("renders sampleTraceReady as the primary sample project panel", async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue(normalizedFixture("sampleTraceReady"));
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
      state: normalizedFixture("sampleTraceReady"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const samplePanel = screen.getByTestId("sample-project-panel");
    expect(samplePanel).toBeVisible();
    expect(within(samplePanel).getByText("Fastest path to Aha")).toBeVisible();
    expect(
      within(samplePanel).getByRole("button", { name: /open sample trace/i }),
    ).toBeVisible();
    expect(
      within(samplePanel).getByRole("link", {
        name: /connect real observability/i,
      }),
    ).toHaveAttribute("href", "/dashboard/observe");
    expect(screen.queryByTestId("observe-setup-panel")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("onboarding-primary-action"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("onboarding-fallback-action"),
    ).not.toBeInTheDocument();

    await userEvent.click(
      within(samplePanel).getByRole("button", { name: /open sample trace/i }),
    );

    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "sample_project_open_clicked",
      expect.objectContaining({
        is_sample: true,
        action_path: "sample",
        primary_path: "sample",
      }),
    );
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "observe",
        source: "onboarding_home",
        reason: "review_sample_signal",
      }),
    );
  });

  it("renders a focused setup panel for non-Observe product paths", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("promptNoPrompt"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("path-focus-panel-prompt");
    expect(panel).toBeVisible();
    expect(screen.queryByTestId("observe-setup-panel")).toBeNull();
    expect(
      within(panel).getByText("Build a prompt quality loop"),
    ).toBeVisible();
    expect(
      within(panel).getByText(
        "Create one prompt, test it, save a baseline, and compare the next version.",
      ),
    ).toBeVisible();
    expect(
      within(panel).getByRole("link", { name: /create prompt/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/workbench/all?source=onboarding&action=create-prompt&tour_anchor=prompt_create_button&journey_step=start_prompt",
    );
  });

  it("keeps setup quick-start attribution on prompt path actions", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("promptNoPrompt"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView(
      "/dashboard/home?source=setup_org&quick_start_id=prompt&quick_start_goal=improve_prompts&quick_start_primary_path=prompt",
    );

    const panel = screen.getByTestId("path-focus-panel-prompt");
    const href = within(panel)
      .getByRole("link", { name: /create prompt/i })
      .getAttribute("href");
    const params = new URLSearchParams(href.split("?")[1]);

    expect(href).toContain("/dashboard/workbench/all?");
    expect(params.get("quick_start_goal")).toBe("improve_prompts");
    expect(params.get("quick_start_id")).toBe("prompt");
    expect(params.get("quick_start_primary_path")).toBe("prompt");
    expect(params.get("tour_anchor")).toBe("prompt_create_button");
    expect(params.get("journey_step")).toBe("start_prompt");
  });

  it("renders the post-aha screen after the first observe quality loop", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("observeFirstLoopComplete"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("first-loop-complete-panel");
    expect(panel).toBeVisible();
    expect(within(panel).getByText("Aha moment reached")).toBeVisible();
    expect(
      within(panel).getByText("Your first quality loop is live"),
    ).toBeVisible();
    expect(within(panel).getByText("Next best step")).toBeVisible();
    expect(
      within(panel).getByText("first_quality_loop_completed"),
    ).toBeVisible();
    expect(
      within(panel).getByRole("link", { name: /review daily quality/i }),
    ).toHaveAttribute("href", "/dashboard/home?mode=daily-quality");
    expect(
      within(panel).getByRole("link", { name: /open observe/i }),
    ).toHaveAttribute("href", "/dashboard/observe/observe-1");
  });

  it("keeps the post-aha screen actionable when daily quality is unavailable", () => {
    const activatedState = normalizedFixture("observeFirstLoopComplete");
    activatedState.routeAvailability.daily_quality_home = {
      href: "/dashboard/home?mode=daily-quality",
      isAvailable: false,
      reason: "feature_disabled",
    };
    mocks.useActivationState.mockReturnValue({
      state: activatedState,
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("first-loop-complete-panel");
    expect(panel).toBeVisible();
    expect(
      within(panel).getByText(
        "Open the current loop next. Daily quality will take over when a reviewable signal is available.",
      ),
    ).toBeVisible();
    expect(
      within(panel).queryByRole("link", { name: /review daily quality/i }),
    ).not.toBeInTheDocument();
    expect(
      within(panel).getByRole("link", { name: /open observe/i }),
    ).toHaveAttribute("href", "/dashboard/observe/observe-1");
  });

  it("uses the post-aha screen for activated non-observe paths", () => {
    const activatedState = normalizedFixture("promptActivated");
    activatedState.routeAvailability.daily_quality_home = {
      href: "/dashboard/home?mode=daily-quality&source=post-aha-test",
      isAvailable: true,
      reason: null,
    };
    mocks.useActivationState.mockReturnValue({
      state: activatedState,
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("first-loop-complete-panel");
    expect(panel).toBeVisible();
    expect(
      within(panel).getByText("Your first quality loop is live"),
    ).toBeVisible();
    expect(within(panel).getByText("prompt")).toBeVisible();
    expect(
      within(panel).getByRole("link", { name: /review daily quality/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/home?mode=daily-quality&source=post-aha-test",
    );
    expect(
      within(panel).getByRole("link", { name: /open prompt metrics/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=metrics&tab=Metrics",
    );
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

    renderView(
      "/dashboard/home?source=setup_org&quick_start_id=observe&quick_start_goal=monitor_production_ai_app&quick_start_primary_path=observe",
    );

    expect(screen.getByTestId("observe-setup-panel")).toBeVisible();
    expect(
      screen.queryByTestId("sample-project-panel"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("onboarding-state-summary"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("onboarding-product-loop-stepper"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("onboarding-path-card-grid"),
    ).not.toBeInTheDocument();
    expect(mocks.useActivationState).toHaveBeenCalledWith(
      expect.objectContaining({
        source: "setup_org",
        quickStartGoal: "monitor_production_ai_app",
        quickStartId: "observe",
        quickStartPrimaryPath: "observe",
      }),
    );
    await waitFor(() =>
      expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
        "onboarding_home_viewed",
        expect.objectContaining({
          source: "setup_org",
          quick_start_goal: "monitor_production_ai_app",
          quick_start_id: "observe",
          quick_start_primary_path: "observe",
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
    expect(readPersistedSetupQuickStartAttribution()).toEqual({
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
    });
  });

  it("lets users focus another product loop from path cards", async () => {
    const state = normalizedFixture("observeNoSetup");
    const refetch = vi.fn();
    const mutateAsync = vi.fn().mockResolvedValue({
      ...state,
      goal: "improve_prompts",
      primaryPath: "prompt",
      stage: "start_prompt",
    });
    mocks.useSaveOnboardingGoal.mockReturnValue({
      data: null,
      error: null,
      isLoading: false,
      isPending: false,
      mutateAsync,
    });
    mocks.useActivationState.mockReturnValue({
      state: {
        ...state,
        availablePaths: [
          ...state.availablePaths,
          {
            id: "prompt",
            label: "Improve prompts",
            description: "Test and compare prompt versions.",
            status: "available",
            href: "/dashboard/home?path=prompt",
            isAvailable: true,
            blockedReason: null,
            requiresPermission: "prompt:write",
            firstActionId: "create_prompt",
          },
        ],
        routeAvailability: {
          ...state.routeAvailability,
          path_prompt: {
            href: "/dashboard/home?path=prompt",
            isAvailable: true,
            reason: null,
          },
        },
      },
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch,
    });

    renderView();

    await userEvent.click(
      within(screen.getByTestId("onboarding-path-card-prompt")).getByRole(
        "button",
        { name: /focus/i },
      ),
    );

    expect(mutateAsync).toHaveBeenCalledWith({
      goal: "improve_prompts",
      primaryPath: "prompt",
      source: "path_card",
      reason: "path_change",
      expectedStage: "connect_observability",
    });
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "onboarding_home_goal_saved",
      expect.objectContaining({
        selected_goal: "improve_prompts",
        selected_path: "prompt",
        source: "path_card",
      }),
    );
  });

  it("opens sample preview quick starts to the sample Aha action", async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue(normalizedFixture("sampleTraceReady"));
    const refetch = vi.fn();
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
      state: normalizedFixture("sampleTraceReady"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch,
    });

    renderView(
      "/dashboard/home?source=setup_org&quick_start_id=sample_preview&quick_start_goal=explore_sample_data&quick_start_primary_path=sample",
    );

    const samplePanel = screen.getByTestId("sample-project-panel");
    expect(samplePanel).toBeVisible();
    expect(
      within(samplePanel).getByRole("button", { name: /open sample trace/i }),
    ).toBeVisible();
    expect(
      screen.queryByTestId("onboarding-state-summary"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("onboarding-product-loop-stepper"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("onboarding-path-card-grid"),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "observe",
        source: "setup_org",
        reason: "sample_preview",
        openAfterCreate: true,
        quickStartGoal: "explore_sample_data",
        quickStartId: "sample_preview",
        quickStartPrimaryPath: "sample",
      }),
    );
    await waitFor(() =>
      expect(window.location.pathname + window.location.search).toContain(
        "/dashboard/home?sample=true",
      ),
    );
    const attributionParams = new URLSearchParams(window.location.search);
    expect(attributionParams.get("quick_start_goal")).toBe(
      "explore_sample_data",
    );
    expect(attributionParams.get("quick_start_id")).toBe("sample_preview");
    expect(attributionParams.get("quick_start_primary_path")).toBe("sample");
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("keeps the sample preview fallback visible when auto-open fails", async () => {
    const mutateAsync = vi.fn().mockRejectedValue(new Error("sample failed"));
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
      state: normalizedFixture("sampleTraceReady"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView(
      "/dashboard/home?source=setup_org&quick_start_id=sample_preview&quick_start_goal=explore_sample_data&quick_start_primary_path=sample",
    );

    expect(screen.getByTestId("sample-project-panel")).toBeVisible();
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("sample-project-panel")).toBeVisible();
    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "sample_project_open_failed",
      expect.objectContaining({
        is_sample: true,
        action_path: "sample",
        reason: "sample failed",
      }),
    );
  });

  it("drops unrecognized quick-start URL attribution before tracking", async () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("observeNoSetup"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView(
      "/dashboard/home?source=setup_org&quick_start_id=user@example.com&quick_start_goal=secret&quick_start_primary_path=observe",
    );

    await waitFor(() =>
      expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
        "onboarding_home_viewed",
        expect.not.objectContaining({
          quick_start_goal: expect.any(String),
          quick_start_id: expect.any(String),
          quick_start_primary_path: expect.any(String),
        }),
      ),
    );
    expect(readPersistedSetupQuickStartAttribution()).toEqual({});
  });

  it("renders daily quality home for activated observe workspaces", async () => {
    const mutate = vi.fn();
    mocks.useRecordActivationEvent.mockReturnValue({
      mutate,
      isLoading: false,
      isPending: false,
    });
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("dailyQualityObserveNewSignal"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView(
      "/dashboard/home?mode=daily-quality&source=onboarding_email&campaign_key=daily_quality_open_actions&email_key=daily_quality_open_actions_v1&target_stage=daily_review&target_event=daily_quality_item_reviewed&send_log_id=send-daily&email_status=current&link_issued_at=2026-05-29T08:00:00Z&context_status=current",
    );

    expect(screen.getByTestId("onboarding-daily-quality")).toBeVisible();
    expect(screen.getByTestId("daily-quality-top-signal")).toBeVisible();
    expect(screen.queryByTestId("first-loop-complete-panel")).toBeNull();
    await waitFor(() =>
      expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
        "daily_quality_home_viewed",
        expect.objectContaining({
          daily_quality_mode: "new_signal",
          signal_id: "trace_failure:trace-2",
        }),
      ),
    );
    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "daily_quality_top_signal_shown",
      expect.objectContaining({
        signal_type: "trace_failure",
      }),
    );
    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "daily_quality_digest_destination_opened",
      expect.objectContaining({
        digest_context_id: "daily_quality_open_actions",
        send_log_id: "send-daily",
      }),
    );
    expect(screen.getByTestId("weekly-quality-review")).toBeVisible();

    await userEvent.click(screen.getByTestId("weekly-quality-review-action"));

    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "weekly_quality_review_opened",
      expect.objectContaining({
        weekly_review_status: "due",
        unresolved_count: 1,
        route: "/dashboard/home?mode=weekly-review",
      }),
    );

    await userEvent.click(screen.getByTestId("daily-quality-primary-action"));

    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "daily_quality_action_opened",
      expect.objectContaining({
        recommended_action_id: "review_failed_trace",
        route: "/dashboard/observe/observe-1/trace/trace-2",
      }),
    );
    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "daily_quality_item_reviewed",
      expect.objectContaining({
        signal_id: "trace_failure:trace-2",
        source_type: "trace",
      }),
    );
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "daily_quality_item_reviewed",
        primaryPath: "observe",
        stage: "daily_review",
        artifactType: "trace",
        artifactId: "trace-2",
        campaignKey: "daily_quality_open_actions",
        emailKey: "daily_quality_open_actions_v1",
        sendLogId: "send-daily",
        emailStatus: "current",
        targetStage: "daily_review",
        targetEvent: "daily_quality_item_reviewed",
        linkIssuedAt: "2026-05-29T08:00:00Z",
        contextStatus: "current",
      }),
    );
  });

  it("renders daily quality no-signal state with one useful action", async () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("dailyQualityObserveNoSignal"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    expect(screen.getByTestId("onboarding-daily-quality")).toBeVisible();
    expect(screen.getByTestId("daily-quality-empty-state")).toBeVisible();
    expect(
      screen.getByTestId("daily-quality-primary-action"),
    ).toHaveTextContent("Create alert");
    expect(screen.queryByText("Connect one observe project")).toBeNull();
    await waitFor(() =>
      expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
        "daily_quality_empty_state_viewed",
        expect.objectContaining({
          daily_quality_mode: "no_new_signal",
        }),
      ),
    );
  });

  it("renders carried-forward daily quality actions", async () => {
    const mutate = vi.fn();
    mocks.useRecordActivationEvent.mockReturnValue({
      mutate,
      isLoading: false,
      isPending: false,
    });
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("dailyQualityObserveOpenAction"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    expect(screen.getByTestId("onboarding-daily-quality")).toBeVisible();
    expect(
      screen.getByTestId("daily-quality-primary-action"),
    ).toHaveTextContent("Continue action");
    const actionCard = screen.getByTestId(
      "daily-quality-action-card-assign_trace_owner",
    );
    expect(actionCard).toBeVisible();
    expect(actionCard).toHaveTextContent("Assign trace owner");
    expect(actionCard).toHaveTextContent("Owner Ava");
    expect(actionCard).toHaveTextContent(/Due .*2026/);
    expect(screen.getByTestId("weekly-quality-review")).toBeVisible();

    await userEvent.click(
      within(actionCard).getByRole("button", { name: /done/i }),
    );

    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "daily_quality_action_completed",
      expect.objectContaining({
        recommended_action_id: "assign_trace_owner",
        route: "/dashboard/observe/observe-1?mode=quality-actions",
        resolution: "completed",
      }),
    );
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "daily_quality_action_completed",
        primaryPath: "observe",
        stage: "daily_review",
        artifactType: "project",
        artifactId: "observe-1",
        projectId: "observe-1",
        metadata: expect.objectContaining({
          action_id: "assign_trace_owner",
          source_type: "project",
          source_id: "observe-1",
          resolution: "completed",
        }),
      }),
      expect.objectContaining({
        onSuccess: expect.any(Function),
      }),
    );

    await userEvent.click(
      screen.getByTestId("daily-quality-primary-action-dismiss"),
    );

    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "daily_quality_action_dismissed",
      expect.objectContaining({
        recommended_action_id: "continue_trace_action",
        route: "/dashboard/observe/observe-1",
        resolution: "dismissed",
      }),
    );
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "daily_quality_action_dismissed",
        artifactType: "project",
        artifactId: "observe-1",
        metadata: expect.objectContaining({
          action_id: "continue_trace_action",
          resolution: "dismissed",
        }),
      }),
      expect.objectContaining({
        onSuccess: expect.any(Function),
      }),
    );

    await userEvent.click(
      within(actionCard).getByRole("link", { name: /open/i }),
    );

    expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
      "daily_quality_action_opened",
      expect.objectContaining({
        recommended_action_id: "assign_trace_owner",
        route: "/dashboard/observe/observe-1?mode=quality-actions",
      }),
    );
  });

  it("renders daily quality home for activated non-observe paths", async () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("dailyQualityPromptNoSignal"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView("/dashboard/home?mode=daily-quality");

    expect(screen.getByTestId("onboarding-daily-quality")).toBeVisible();
    expect(screen.getByTestId("daily-quality-empty-state")).toBeVisible();
    expect(screen.queryByText("Recommended action")).toBeNull();
    expect(
      screen.getByTestId("daily-quality-primary-action"),
    ).toHaveTextContent("Review prompt metrics");
    expect(
      screen.getByTestId("daily-quality-product-card-prompt"),
    ).toBeVisible();
    await waitFor(() =>
      expect(mocks.trackOnboardingHomeEvent).toHaveBeenCalledWith(
        "daily_quality_home_viewed",
        expect.objectContaining({
          daily_quality_mode: "no_new_signal",
          primary_path: "prompt",
          recommended_action_id: "open_prompt_metrics",
        }),
      ),
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

  it("renders the first trace review panel when the trace arrives", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("observeFirstTraceReady"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("first-signal-panel");
    expect(panel).toBeVisible();
    expect(within(panel).getByText("First trace received")).toBeVisible();
    expect(within(panel).getByText("trace-1")).toBeVisible();
    expect(within(panel).getByText("Not reviewed")).toBeVisible();
    expect(
      within(panel).getByRole("link", { name: /review trace/i }),
    ).toHaveAttribute("href", "/dashboard/observe/observe-1/trace/trace-1");
  });

  it("renders backend observe journey progress on the first signal panel", () => {
    mocks.useActivationState.mockReturnValue({
      state: {
        ...normalizedFixture("observeFirstTraceReady"),
        journeyPlan: observeJourneyPlan({ currentStepIndex: 2 }),
      },
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("first-signal-panel");
    const currentStep = within(panel).getByTestId(
      "observe-journey-step-review_first_trace",
    );
    expect(
      within(currentStep).getByText("Review signal from manifest"),
    ).toBeVisible();
    expect(within(currentStep).getByText("Now")).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Inspect the first signal and decide what to measure.",
    );
    expect(within(panel).getByText("trace-1")).toBeVisible();
    expect(
      within(panel).getByRole("link", { name: /review trace/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/observe/observe-1/trace/trace-1?tour_anchor=observe_trace_review_link&journey_step=review_first_trace",
    );
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

  it("renders prompt onboarding as a focused prompt path panel", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("promptCreatedNoRun"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("path-focus-panel-prompt");
    expect(screen.getByText("Run a prompt test")).toBeVisible();
    expect(
      within(panel).getByText("Build a prompt quality loop"),
    ).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Run one focused example before saving.",
    );
    expect(
      within(panel).getByRole("link", { name: /run test/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test&tour_anchor=prompt_run_test_button&journey_step=run_prompt_test",
    );
    expect(screen.getByText("Selected path")).toBeVisible();
    expect(screen.getAllByText("prompt").length).toBeGreaterThan(0);
  });

  it("renders agent onboarding as a focused agent path panel", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("agentCreatedNoRun"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("path-focus-panel-agent");
    expect(screen.getByText("Run a scenario")).toBeVisible();
    expect(
      within(panel).getByText("Prototype an agent with a quality check"),
    ).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Exercise the agent on one task.",
    );
    expect(
      within(panel).getByRole("link", { name: /run scenario/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/agents/playground/agent-1/build?onboarding=run-scenario&tour_anchor=agent_run_scenario_button&journey_step=run_agent_scenario",
    );
    expect(screen.getByText("Selected path")).toBeVisible();
    expect(screen.getAllByText("agent").length).toBeGreaterThan(0);
  });

  it("renders gateway onboarding as a focused gateway path panel", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("gatewayKeyNoRequest"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView(
      "/dashboard/home?source=setup_org&quick_start_id=gateway&quick_start_goal=control_model_traffic&quick_start_primary_path=gateway",
    );

    const panel = screen.getByTestId("path-focus-panel-gateway");
    expect(screen.getByText("Run a gateway request")).toBeVisible();
    expect(within(panel).getByText("Route one request safely")).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Send one request through the gateway.",
    );
    expect(
      within(panel).getByRole("link", { name: /send request/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/gateway?onboarding=test-request&quick_start_goal=control_model_traffic&quick_start_id=gateway&quick_start_primary_path=gateway&tour_anchor=gateway_request_button&journey_step=run_gateway_request",
    );
    expect(screen.getAllByText("gateway").length).toBeGreaterThan(0);
  });

  it("does not add path anchors to unavailable path fallback actions", () => {
    mocks.useActivationState.mockReturnValue({
      state: normalizedFixture("selectedPathUnavailable"),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("path-focus-panel-prompt");
    within(panel)
      .getAllByRole("link", { name: /start with observe/i })
      .forEach((link) => {
        expect(link).toHaveAttribute(
          "href",
          "/dashboard/observe?setup=true&source=onboarding",
        );
      });
  });

  it("renders eval onboarding with path-specific hero and focused panel copy", () => {
    const href = "/dashboard/evaluations/create?source=onboarding&step=run";
    mocks.useActivationState.mockReturnValue({
      state: pathState({
        action: pathAction({
          id: "run_eval",
          kind: "test",
          title: "Run eval",
          description: "Run the first eval and review the result.",
          href,
          ctaLabel: "Run eval",
          completionEvent: "eval_run_completed",
          targetPath: "evals",
        }),
        goal: "evaluate_quality",
        pathDescription: "Create a small eval and review the first failure.",
        pathLabel: "Evaluate quality",
        primaryPath: "evals",
        stage: "run_eval",
      }),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("path-focus-panel-evals");
    const cta = within(panel).getByRole("link", { name: /run eval/i });
    expect(screen.getByText("Eval run")).toBeVisible();
    expect(screen.getByText("Run the first eval")).toBeVisible();
    expect(
      screen.getByText("Run the eval once so the first result is reviewable."),
    ).toBeVisible();
    expect(within(panel).getByText("Eval loop")).toBeVisible();
    expect(
      within(panel).getByText("Create one eval and review the first failure"),
    ).toBeVisible();
    expect(
      within(panel).getByText(
        "Add a small dataset, attach a scorer, run the eval, and inspect what failed.",
      ),
    ).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Run the check once.",
    );
    expect(screen.queryByText("Open Get Started")).not.toBeInTheDocument();
    expect(cta).toHaveAttribute(
      "href",
      `${href}&tour_anchor=eval_run_button&journey_step=run_eval`,
    );
    expect(cta.getAttribute("href")).not.toMatch(/^\/\//);
  });

  it("renders voice onboarding with path-specific hero and disabled unavailable CTA", () => {
    mocks.useActivationState.mockReturnValue({
      state: pathState({
        action: pathAction({
          id: "review_voice_call",
          kind: "review",
          title: "Review call",
          description: "Review the transcript and call outcome.",
          href: "/dashboard/simulate/test/voice-test-1/run/execution-1?onboarding=review-voice-call",
          ctaLabel: "Review call",
          routeAvailable: false,
          targetPath: "voice",
        }),
        goal: "connect_voice_ai_agent",
        pathDescription: "Run or review a call with clear success criteria.",
        pathLabel: "Connect a voice AI agent",
        primaryPath: "voice",
        stage: "review_voice_call",
      }),
      isLoading: false,
      isRefetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderView();

    const panel = screen.getByTestId("path-focus-panel-voice");
    const cta = within(panel).getByRole("button", { name: /review call/i });
    expect(screen.getByText("Voice call")).toBeVisible();
    expect(screen.getByText("Review the voice call")).toBeVisible();
    expect(
      screen.getByText(
        "Inspect the call transcript and find the first quality signal.",
      ),
    ).toBeVisible();
    expect(within(panel).getByText("Voice loop")).toBeVisible();
    expect(
      within(panel).getByText("Connect a voice agent quality loop"),
    ).toBeVisible();
    expect(
      within(panel).getByText(
        "Create or connect a voice agent, run one call, review it, and add success criteria.",
      ),
    ).toBeVisible();
    expect(within(panel).getByTestId("current-step-guide")).toHaveTextContent(
      "Inspect the transcript and outcome.",
    );
    expect(screen.queryByText("Open Get Started")).not.toBeInTheDocument();
    expect(cta).toBeDisabled();
    expect(cta).not.toHaveAttribute("href");
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
      state: normalizedFixture("goalPickerFallback"),
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
