import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "src/utils/test-utils";
import { renderWithRouter } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import ProjectWrapperView from "./ProjectWrapperView";

const mocks = vi.hoisted(() => ({
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
  default: (props) => (props.open ? <div>Observe setup drawer</div> : null),
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

  it("shows setup focus on the observe setup onboarding route", async () => {
    renderWithRouter(<ProjectWrapperView />, {
      route: "/dashboard/observe?setup=true&source=onboarding",
    });

    expect(screen.getByText("Connect Observe to your app")).toBeVisible();
    expect(screen.getByText("Observe list")).toBeVisible();

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

    await waitFor(() => {
      expect(screen.getByText("Observe setup drawer")).toBeVisible();
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

  it("does not show setup focus on the normal observe list route", () => {
    renderWithRouter(<ProjectWrapperView />, {
      route: "/dashboard/observe",
    });

    expect(screen.queryByText("Connect Observe to your app")).toBeNull();
    expect(mocks.recordActivationEvent).not.toHaveBeenCalled();
  });
});
