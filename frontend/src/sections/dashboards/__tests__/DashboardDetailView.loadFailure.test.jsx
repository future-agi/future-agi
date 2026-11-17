import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { paths } from "src/routes/paths";
import DashboardDetailView from "../DashboardDetailView";

// OBS-SW-121: when the dashboard failed to load (network error, timeout,
// server error) the page said "Dashboard not found" with no Back or Retry,
// which reads as data loss. A failed load must say it could not load and
// offer Retry and Back to dashboards; "not found" is kept for a real 404 and
// gets a way back too.
const h = vi.hoisted(() => ({
  detail: null,
  navigate: vi.fn(),
  // Catch-all for every other dashboards hook the grid calls (update, delete,
  // reorder, duplicate, ...): render has no QueryClientProvider.
  mutation: { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false },
}));

vi.mock("src/hooks/useDashboards", async (importOriginal) => {
  const actual = await importOriginal();
  const hookStubs = Object.fromEntries(
    Object.keys(actual)
      .filter((name) => name.startsWith("use"))
      .map((name) => [name, () => h.mutation]),
  );
  return {
    ...actual,
    ...hookStubs,
    useDashboardDetail: () => h.detail,
  };
});

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ dashboardId: "dash-1" }),
  useNavigate: () => h.navigate,
}));

vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => ({
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  }),
}));

vi.mock("../WidgetChart", () => ({
  default: () => <div data-testid="widget-chart" />,
}));

vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

// useDashboardDetail result for a load that ended without a dashboard.
const failedLoad = (overrides = {}) => ({
  data: undefined,
  isLoading: false,
  isPending: false,
  isError: true,
  error: { statusCode: 503, message: "Service Unavailable" },
  refetch: vi.fn(),
  ...overrides,
});

const expectBackToDashboards = () => {
  fireEvent.click(screen.getByRole("button", { name: "Back to dashboards" }));
  expect(h.navigate).toHaveBeenCalledWith(paths.dashboard.dashboards.root);
};

beforeEach(() => {
  vi.clearAllMocks();
  h.detail = failedLoad();
});

describe("DashboardDetailView — load failure (OBS-SW-121)", () => {
  it.each([
    ["a server error", { statusCode: 503, message: "Service Unavailable" }],
    [
      "a network error",
      {
        statusCode: undefined,
        message: "Network Error",
        transportCode: "ERR_NETWORK",
      },
    ],
  ])(
    "says the dashboard could not load after %s, with Retry and Back",
    (_label, error) => {
      h.detail = failedLoad({ error });
      render(<DashboardDetailView />);

      expect(
        screen.getByText("Could not load this dashboard"),
      ).toBeInTheDocument();
      expect(screen.queryByText("Dashboard not found")).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(h.detail.refetch).toHaveBeenCalledTimes(1);

      expectBackToDashboards();
    },
  );

  it("does not call a fetch paused while offline 'not found'", () => {
    // React Query v5: a paused fetch is pending but neither loading nor
    // errored, so it used to fall through to "Dashboard not found".
    h.detail = failedLoad({
      isPending: true,
      isError: false,
      error: null,
      fetchStatus: "paused",
    });
    render(<DashboardDetailView />);

    expect(
      screen.getByText("Could not load this dashboard"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Dashboard not found")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("keeps 'Dashboard not found' for a 404 and offers a way back", () => {
    h.detail = failedLoad({
      error: { statusCode: 404, message: "Not found." },
    });
    render(<DashboardDetailView />);

    expect(screen.getByText("Dashboard not found")).toBeInTheDocument();
    expect(
      screen.queryByText("Could not load this dashboard"),
    ).not.toBeInTheDocument();
    // Retrying cannot bring back a dashboard the server says is gone.
    expect(
      screen.queryByRole("button", { name: "Retry" }),
    ).not.toBeInTheDocument();

    expectBackToDashboards();
  });

  it("keeps the loaded dashboard when a background refetch fails", () => {
    h.detail = failedLoad({
      data: { id: "dash-1", name: "My Dash", widgets: [] },
    });
    render(<DashboardDetailView />);

    expect(
      screen.getByRole("heading", { name: "My Dash" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Could not load this dashboard"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Dashboard not found")).not.toBeInTheDocument();
  });
});
