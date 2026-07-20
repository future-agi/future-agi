import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  within,
} from "src/utils/test-utils";
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
    widget_count: "1",
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
    widget_count: "0",
    created_at: "2026-05-20T12:00:00.000Z",
    updated_at: null,
    created_by: {
      email: "owner@example.com",
    },
  },
  {
    id: "dash-3",
    name: "No Metadata Dashboard",
    widget_count: "2",
    created_at: null,
    updated_at: null,
    created_by: null,
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
    expect(screen.getByText("People")).toBeInTheDocument();
  });

  it("renders shared calendar-date text instead of relative row timestamps", () => {
    render(<DashboardsListView />);

    expect(screen.getByText("15 Jun 2026")).toBeInTheDocument();
    expect(screen.getByText("20 May 2026")).toBeInTheDocument();
    expect(screen.queryByText(/ago/i)).not.toBeInTheDocument();
  });

  it("renders creator names inline without exposing email fallbacks", () => {
    render(<DashboardsListView />);

    expect(screen.getByText("Alice Creator")).toBeInTheDocument();
    expect(
      screen.getAllByText("Unknown creator").length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("owner@example.com")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Created by anyone/i }));

    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.queryByText("owner@example.com")).not.toBeInTheDocument();
  });

  it("normalizes API-shaped widget counts and missing metadata", () => {
    render(<DashboardsListView />);

    expect(screen.getByText("1 widget")).toBeInTheDocument();
    expect(screen.getByText("0 widgets")).toBeInTheDocument();
    expect(screen.getByText("2 widgets")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "No Metadata Dashboard" }),
    ).toHaveAccessibleDescription(
      /2 widgets\. Last updated —\. Created by Unknown creator\. No people\./,
    );
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("falls back safely for invalid widget counts", () => {
    h.dashboards = [
      {
        ...DASHBOARDS[2],
        id: "dash-invalid",
        name: "Invalid Count Dashboard",
        widget_count: "not-a-number",
      },
    ];

    render(<DashboardsListView />);

    expect(screen.getByText("0 widgets")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Invalid Count Dashboard" }),
    ).toHaveAccessibleDescription(
      /0 widgets\. Last updated —\. Created by Unknown creator\. No people\./,
    );
  });

  it("does not render list column headers for the empty state", () => {
    h.dashboards = [];

    render(<DashboardsListView />);

    expect(screen.getByText("Create your first dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Name")).not.toBeInTheDocument();
    expect(screen.queryByText("Widgets")).not.toBeInTheDocument();
    expect(screen.queryByText("Last updated")).not.toBeInTheDocument();
    expect(screen.queryByText("Created by")).not.toBeInTheDocument();
    expect(screen.queryByText("People")).not.toBeInTheDocument();
  });

  it("keeps headers hidden when search filters all dashboards out", () => {
    render(<DashboardsListView />);

    fireEvent.change(screen.getByLabelText("Search"), {
      target: { value: "does-not-exist" },
    });

    expect(
      screen.getByText("No dashboards match your search"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Name")).not.toBeInTheDocument();
    expect(screen.queryByText("Latency Overview")).not.toBeInTheDocument();
  });

  it("keeps row navigation on a semantic link with accessible metadata", () => {
    render(<DashboardsListView />);

    const latencyRow = screen.getByRole("link", {
      name: "Latency Overview",
    });

    expect(latencyRow).toHaveAttribute("href", "/dashboard/dashboards/dash-1");
    expect(latencyRow).toHaveAccessibleDescription(
      /1 widget\. Last updated 15 Jun 2026\. Created by Alice Creator\. 2 people\./,
    );

    fireEvent.click(latencyRow);
    expect(h.navigate).toHaveBeenCalledWith("/dashboard/dashboards/dash-1");

    // jsdom attempts a real navigation for unhandled modified anchor clicks.
    // The href is asserted above; remove it here to isolate the SPA handler.
    latencyRow.removeAttribute("href");
    h.navigate.mockClear();
    fireEvent.click(latencyRow, { metaKey: true });
    fireEvent.click(latencyRow, { ctrlKey: true });
    fireEvent.click(latencyRow, { shiftKey: true });
    expect(h.navigate).not.toHaveBeenCalled();
  });

  it("makes people metadata available as a focusable row action", () => {
    render(<DashboardsListView />);

    expect(
      screen.getByRole("button", {
        name: "People for Latency Overview: 2 people",
      }),
    ).toBeInTheDocument();
  });

  it("keeps people tooltip fallbacks private when names are missing", async () => {
    const user = userEvent.setup();
    render(<DashboardsListView />);

    await user.hover(
      screen.getByRole("button", {
        name: "People for Fallback Owner Dashboard: 1 person",
      }),
    );

    expect(
      await screen.findByText("Created by Unknown creator"),
    ).toBeInTheDocument();
    expect(screen.getByText("Unknown user")).toBeInTheDocument();
    expect(screen.queryByText("owner@example.com")).not.toBeInTheDocument();
  });

  it("keeps selected creator filter private after dashboard data changes", () => {
    const { rerender } = render(<DashboardsListView />);

    fireEvent.click(screen.getByRole("button", { name: /Created by anyone/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Unknown creator/ }));
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

    expect(
      screen.getByRole("button", { name: "Unknown creator" }),
    ).toBeInTheDocument();

    h.dashboards = [DASHBOARDS[0]];
    rerender(<DashboardsListView />);

    expect(
      screen.getByRole("button", { name: "Unknown creator" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("owner@example.com")).not.toBeInTheDocument();
  });

  it("disambiguates multiple unnamed creator filters without exposing emails", () => {
    h.dashboards = [
      DASHBOARDS[1],
      {
        ...DASHBOARDS[1],
        id: "dash-4",
        name: "Second Fallback Owner Dashboard",
        created_by: {
          email: "second-owner@example.com",
        },
      },
    ];

    render(<DashboardsListView />);

    fireEvent.click(screen.getByRole("button", { name: /Created by anyone/i }));

    expect(
      screen.getByRole("menuitem", { name: /Unknown creator 1/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /Unknown creator 2/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText("owner@example.com")).not.toBeInTheDocument();
    expect(
      screen.queryByText("second-owner@example.com"),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("menuitem", { name: /Unknown creator 2/ }),
    );
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

    expect(
      screen.getByRole("button", { name: "Unknown creator 2" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Second Fallback Owner Dashboard" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Fallback Owner Dashboard" }),
    ).not.toBeInTheDocument();
  });

  it("keeps delete as a separate action from row navigation", () => {
    render(<DashboardsListView />);

    fireEvent.click(
      screen.getByRole("button", { name: "Delete Latency Overview" }),
    );

    expect(h.navigate).not.toHaveBeenCalled();
    expect(screen.getByText("Delete Dashboard")).toBeInTheDocument();
    expect(
      screen.getByText(
        'Are you sure you want to delete "Latency Overview"? This action cannot be undone.',
      ),
    ).toBeInTheDocument();
  });

  it("confirms deletion for the selected dashboard without navigating", () => {
    render(<DashboardsListView />);

    fireEvent.click(
      screen.getByRole("button", {
        name: "Delete Fallback Owner Dashboard",
      }),
    );

    const dialog = screen.getByRole("dialog", { name: "Delete Dashboard" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(h.navigate).not.toHaveBeenCalled();
    expect(h.deleteDashboard.mutate).toHaveBeenCalledTimes(1);
    expect(h.deleteDashboard.mutate).toHaveBeenCalledWith(
      "dash-2",
      expect.any(Object),
    );
  });
});
