import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import { renderWithRouter } from "src/utils/test-utils";
import ProjectWrapperView from "./ProjectWrapperView";

const mocks = vi.hoisted(() => ({
  recordActivationEvent: vi.fn(),
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
    mutate: (...args) => mocks.recordActivationEvent(...args),
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
    const user = userEvent.setup();

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

    await user.click(screen.getByRole("button", { name: /open setup/i }));

    expect(screen.getByText("Observe setup drawer")).toBeVisible();
  });

  it("does not show setup focus on the normal observe list route", () => {
    renderWithRouter(<ProjectWrapperView />, {
      route: "/dashboard/observe",
    });

    expect(screen.queryByText("Connect Observe to your app")).toBeNull();
    expect(mocks.recordActivationEvent).not.toHaveBeenCalled();
  });
});
