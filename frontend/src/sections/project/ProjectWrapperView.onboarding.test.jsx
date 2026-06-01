import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "src/utils/test-utils";
import { renderWithRouter } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import ProjectWrapperView from "./ProjectWrapperView";

const mocks = vi.hoisted(() => ({
  activationState: null,
  openSampleProject: vi.fn(),
  recordActivationEvent: vi.fn(),
  recordActivationState: null,
  useQuery: vi.fn(),
}));

vi.mock("react-helmet-async", () => ({
  Helmet: () => null,
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({
    isPending: false,
    mutate: vi.fn(),
  }),
  useQuery: (args) => mocks.useQuery(args),
  useQueryClient: () => ({
    invalidateQueries: vi.fn(),
  }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useSnackbar: () => ({
      enqueueSnackbar: vi.fn(),
    }),
  };
});

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    data: mocks.recordActivationState,
    mutate: (...args) => mocks.recordActivationEvent(...args),
  }),
}));

vi.mock("src/sections/onboarding-home/hooks/useActivationState", () => ({
  useActivationState: () => ({
    state: mocks.activationState,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));

vi.mock("src/sections/onboarding-home/hooks/useSampleProject", () => ({
  useSampleProject: () => ({
    openSampleProject: {
      isPending: false,
      mutateAsync: (...args) => mocks.openSampleProject(...args),
    },
  }),
}));

vi.mock("./ObserveListView", async () => {
  const React = await import("react");
  const ObserveListMock = React.forwardRef(function ObserveListMock() {
    return <div>Observe list</div>;
  });
  return {
    default: ObserveListMock,
  };
});

vi.mock("./ExperimentListView", async () => {
  const React = await import("react");
  const ExperimentListMock = React.forwardRef(function ExperimentListMock() {
    return <div>Prototype list</div>;
  });
  return {
    default: ExperimentListMock,
  };
});

vi.mock("./RightSection/ProjectRightSection", () => ({
  default: () => <button type="button">Add Project</button>,
}));

vi.mock("./NewProject/NewProjectDrawer", () => ({
  default: (props) =>
    props.open ? (
      <div>
        <div>Observe setup drawer</div>
        {props.observeSetupVerification ? (
          <div>{props.observeSetupVerification.title}</div>
        ) : null}
      </div>
    ) : null,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    delete: vi.fn(),
    get: vi.fn(),
  },
  endpoints: {
    project: {
      deleteObservePrototype: "/project/delete",
      projectExperimentList: "/project/experiments",
      projectObserveList: "/project/observe",
    },
  },
}));

describe("ProjectWrapperView observe setup onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.activationState = null;
    mocks.recordActivationState = {
      sampleProject: {
        available: true,
        isHidden: false,
        status: "not_created",
      },
    };
    mocks.openSampleProject.mockResolvedValue({
      sampleProject: {
        entryRoute:
          "/dashboard/observe/sample-project/trace/sample-trace?sample=true&from=onboarding",
      },
    });
    mocks.useQuery.mockReturnValue({
      data: {
        result: {
          metadata: { total_rows: 1 },
          projects: [{ id: "project-1" }],
        },
      },
      isLoading: false,
    });
  });

  it("uses the activation-state Observe project when the list has multiple projects", async () => {
    const user = userEvent.setup();
    mocks.activationState = {
      signals: {
        firstObserveId: "project-2",
      },
    };
    mocks.useQuery.mockReturnValue({
      data: {
        result: {
          metadata: { total_rows: 2 },
          projects: [{ id: "project-1" }, { id: "project-2" }],
        },
      },
      isLoading: false,
    });

    renderWithRouter(<ProjectWrapperView />, {
      route: "/dashboard/observe?setup=true&source=onboarding",
    });

    await user.click(
      screen.getByRole("button", { name: /wait for first trace/i }),
    );

    await waitFor(() => {
      expect(window.location.pathname).toBe(
        "/dashboard/observe/project-2/llm-tracing",
      );
      const params = new URLSearchParams(window.location.search);
      expect(params.get("source")).toBe("onboarding");
      expect(params.get("onboarding")).toBe("send-first-trace");
      expect(params.get("selectedTab")).toBe("trace");
    });
  });

  it("moves existing observe projects to the first-trace step", async () => {
    const user = userEvent.setup();
    renderWithRouter(<ProjectWrapperView />, {
      route: "/dashboard/observe?setup=true&source=onboarding",
    });

    expect(screen.getByText("Connect Observe to your app")).toBeVisible();
    expect(screen.getByText("Observe list")).toBeVisible();
    expect(screen.getByText("Observe setup drawer")).toBeVisible();
    expect(screen.getByText("Waiting for first trace")).toBeVisible();

    await waitFor(() => {
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          artifactType: "observe_setup",
          eventName: "onboarding_observe_route_focus_viewed",
          metadata: {
            route_mode: "setup-observe",
            setup: true,
          },
          primaryPath: "observe",
          stage: "connect_observability",
        }),
      );
    });

    await user.click(
      screen.getByRole("button", { name: /wait for first trace/i }),
    );

    await waitFor(() => {
      expect(window.location.pathname).toBe(
        "/dashboard/observe/project-1/llm-tracing",
      );
      const params = new URLSearchParams(window.location.search);
      expect(params.get("source")).toBe("onboarding");
      expect(params.get("onboarding")).toBe("send-first-trace");
      expect(params.get("selectedTab")).toBe("trace");
    });
  });

  it("keeps returned credential users focused on pasting keys and sending a trace", async () => {
    const user = userEvent.setup();

    renderWithRouter(<ProjectWrapperView />, {
      route:
        "/dashboard/observe?setup=true&source=onboarding&credential_step=done&provider=anthropic&language=typescript",
    });

    expect(screen.getByText("Credentials copied")).toBeVisible();
    expect(
      screen.getByText("Waiting for Anthropic TypeScript trace"),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Paste both copied values into the Anthropic TypeScript setup snippet, then run one request.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Keys")).toBeVisible();

    await waitFor(() => {
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          artifactType: "observe_setup",
          eventName: "onboarding_observe_route_focus_viewed",
          metadata: {
            credential_step: "done",
            route_mode: "setup-observe",
            setup_language: "typescript",
            setup_provider: "anthropic",
            setup: true,
          },
          primaryPath: "observe",
          stage: "connect_observability",
        }),
      );
    });

    await user.click(
      screen.getByRole("button", {
        name: /wait for anthropic typescript trace/i,
      }),
    );

    await waitFor(() => {
      expect(window.location.pathname).toBe(
        "/dashboard/observe/project-1/llm-tracing",
      );
      const params = new URLSearchParams(window.location.search);
      expect(params.get("source")).toBe("onboarding");
      expect(params.get("onboarding")).toBe("send-first-trace");
      expect(params.get("selectedTab")).toBe("trace");
      expect(params.get("provider")).toBe("anthropic");
      expect(params.get("language")).toBe("typescript");
    });
  });

  it("shows setup focus from Home journey-step params", async () => {
    renderWithRouter(<ProjectWrapperView />, {
      route:
        "/dashboard/observe?tour_anchor=observe_create_project_button&journey_step=connect_observability",
    });

    expect(screen.getByText("Connect Observe to your app")).toBeVisible();

    await waitFor(() => {
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          artifactType: "observe_setup",
          eventName: "onboarding_observe_route_focus_viewed",
          primaryPath: "observe",
          source: "observe_setup_onboarding",
          stage: "connect_observability",
        }),
      );
    });
  });

  it("hides the sample trace shortcut when sample data is unavailable", () => {
    mocks.recordActivationState = {
      sampleProject: {
        available: false,
        isHidden: true,
        status: "unavailable",
      },
    };

    renderWithRouter(<ProjectWrapperView />, {
      route: "/dashboard/observe?setup=true&source=onboarding",
    });

    expect(
      screen.queryByRole("button", { name: /open sample trace/i }),
    ).toBeNull();
  });

  it("opens a sample trace from the observe setup focus", async () => {
    const user = userEvent.setup();
    mocks.useQuery.mockReturnValue({
      data: {
        result: {
          metadata: { total_rows: 0 },
          projects: [],
        },
      },
      isLoading: false,
    });

    renderWithRouter(<ProjectWrapperView />, {
      route: "/dashboard/observe?setup=true&source=onboarding",
    });

    expect(screen.getByText("Checking for your first trace")).toBeVisible();
    const focusPanel = screen.getByTestId("observe-onboarding-focus");
    const focusButtons = within(focusPanel).getAllByRole("button");
    expect(focusButtons[0]).toHaveTextContent(/open sample trace/i);
    expect(focusButtons[1]).toHaveTextContent(/review setup/i);

    await user.click(
      screen.getByRole("button", { name: /open sample trace/i }),
    );

    expect(mocks.openSampleProject).toHaveBeenCalledWith({
      path: "observe",
      source: "observe_setup_onboarding",
      reason: "setup_observe",
      openAfterCreate: true,
    });
    await waitFor(() => {
      expect(window.location.pathname).toBe(
        "/dashboard/observe/sample-project/trace/sample-trace",
      );
      expect(window.location.search).toBe("?sample=true&from=onboarding");
    });
  });

  it("returns sample trace users to real setup guidance", async () => {
    renderWithRouter(<ProjectWrapperView />, {
      route: "/dashboard/observe?setup=true&source=sample_trace_review",
    });

    expect(screen.getByText("Connect your app")).toBeVisible();
    expect(
      screen.getByText(
        "Use the setup below to send one real or test trace from your app.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Sample review")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /open sample trace/i }),
    ).toBeNull();

    await waitFor(() => {
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          artifactType: "observe_setup",
          eventName: "onboarding_observe_route_focus_viewed",
          metadata: {
            route_mode: "setup-observe",
            setup: true,
            setup_source: "sample_trace_review",
          },
          primaryPath: "observe",
          source: "sample_trace_review",
          stage: "connect_real_data",
        }),
      );
    });
  });

  it("does not show setup focus on the normal observe list route", () => {
    renderWithRouter(<ProjectWrapperView />, {
      route: "/dashboard/observe",
    });

    expect(screen.queryByText("Connect Observe to your app")).toBeNull();
    expect(mocks.recordActivationEvent).not.toHaveBeenCalled();
  });
});
