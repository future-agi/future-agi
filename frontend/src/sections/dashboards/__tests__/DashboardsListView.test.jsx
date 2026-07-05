import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import DashboardsListView from "../DashboardsListView";

const h = vi.hoisted(() => ({
  dashboards: [],
  navigate: vi.fn(),
  enqueueSnackbar: vi.fn(),
  createDashboard: {
    mutateAsync: vi.fn(),
    isPending: false,
  },
  deleteDashboard: {
    mutate: vi.fn(),
    isPending: false,
  },
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardList: () => ({
    data: h.dashboards,
    isLoading: false,
  }),
  useCreateDashboard: () => h.createDashboard,
  useDeleteDashboard: () => h.deleteDashboard,
}));

vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
  useNavigate: () => h.navigate,
}));

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: h.enqueueSnackbar }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span aria-hidden="true" data-icon={icon} />,
}));

vi.mock("src/components/svg-color", () => ({
  default: ({ src }) => <span aria-hidden="true" data-src={src} />,
}));

vi.mock("src/components/EmptyLayout/EmptyLayout", () => ({
  default: ({ title, description }) => (
    <div>
      <div>{title}</div>
      <div>{description}</div>
    </div>
  ),
}));

vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ placeholder, searchQuery, onChange }) => (
    <input
      aria-label={placeholder}
      placeholder={placeholder}
      value={searchQuery}
      onChange={onChange}
    />
  ),
}));

const DASHBOARDS = [
  {
    id: "dash-1",
    name: "Latency Overview",
    widget_count: 1,
    created_at: "2026-06-01T12:00:00.000Z",
    updated_at: "2026-06-15T12:00:00.000Z",
    created_by: {
      name: "Alice Creator",
      email: "alice@example.com",
    },
    updated_by: {
      name: "Uma Updater",
      email: "uma@example.com",
    },
  },
  {
    id: "dash-2",
    name: "Fallback Owner Dashboard",
    widget_count: 0,
    created_at: "2026-05-20T12:00:00.000Z",
    updated_at: null,
    created_by: {
      email: "owner@example.com",
    },
  },
];

describe("DashboardsListView list metadata", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.dashboards = DASHBOARDS;
    h.createDashboard.isPending = false;
    h.deleteDashboard.isPending = false;
  });

  it("renders column headers above non-empty dashboard rows", () => {
    render(<DashboardsListView />);

    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Widgets")).toBeInTheDocument();
    expect(screen.getByText("Last updated")).toBeInTheDocument();
    expect(screen.getByText("Created by")).toBeInTheDocument();
    expect(screen.getByText("Viewers")).toBeInTheDocument();
  });

  it("renders shared calendar-date text instead of relative row timestamps", () => {
    render(<DashboardsListView />);

    expect(screen.getByText("15 Jun 2026")).toBeInTheDocument();
    expect(screen.getByText("20 May 2026")).toBeInTheDocument();
    expect(screen.queryByText(/ago/i)).not.toBeInTheDocument();
  });

  it("renders each creator inline using name with email fallback", () => {
    render(<DashboardsListView />);

    expect(screen.getByText("Alice Creator")).toBeInTheDocument();
    expect(screen.getByText("owner@example.com")).toBeInTheDocument();
  });

  it("does not render list column headers for the empty state", () => {
    h.dashboards = [];

    render(<DashboardsListView />);

    expect(screen.getByText("Create your first dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Name")).not.toBeInTheDocument();
    expect(screen.queryByText("Widgets")).not.toBeInTheDocument();
    expect(screen.queryByText("Last updated")).not.toBeInTheDocument();
    expect(screen.queryByText("Created by")).not.toBeInTheDocument();
    expect(screen.queryByText("Viewers")).not.toBeInTheDocument();
  });

  it("keeps row navigation on click and keyboard activation", () => {
    render(<DashboardsListView />);

    const latencyRow = screen.getByRole("button", {
      name: /Latency Overview 1 widget/i,
    });

    fireEvent.click(latencyRow);
    expect(h.navigate).toHaveBeenCalledWith("/dashboard/dashboards/dash-1");

    h.navigate.mockClear();
    fireEvent.keyDown(latencyRow, { key: "Enter" });
    expect(h.navigate).toHaveBeenCalledWith("/dashboard/dashboards/dash-1");
  });
});
