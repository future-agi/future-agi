import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import DashboardsListView from "../DashboardsListView";
import { paths } from "src/routes/paths";

const navigateMock = vi.fn();

const h = vi.hoisted(() => ({
  dashboards: [
    {
      id: "dash-1",
      name: "Engineering Metrics",
      description: "Overview dashboard",
      widget_count: 5,
      created_at: "2026-06-15T10:00:00.000Z",
      updated_at: "2026-06-15T12:00:00.000Z",
      created_by: {
        name: "Alice Smith",
        email: "alice@example.com",
      },
      updated_by: {
        name: "Bob Editor",
        email: "bob@example.com",
      },
    },
  ],
  canEdit: {
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  },
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardList: () => ({
    data: h.dashboards,
    isLoading: false,
    refetch: vi.fn(),
  }),
  useCreateDashboard: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteDashboard: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
  useNavigate: () => navigateMock,
  useParams: () => ({}),
}));

vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => h.canEdit,
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="icon">{icon}</span>,
}));

describe("DashboardsListView", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    h.canEdit = {
      canCreate: true,
      canUpdate: true,
      canDelete: true,
      isReadOnly: false,
    };
  });

  it("renders table header row with column labels", () => {
    render(<DashboardsListView />);

    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Widgets")).toBeInTheDocument();
    expect(screen.getByText("Updated")).toBeInTheDocument();
    expect(screen.getByText("Created by")).toBeInTheDocument();
  });

  it("renders a formatted real date rather than relative text in the Updated column", () => {
    render(<DashboardsListView />);

    // Renders formatted date from shared fDate ("15 Jun 2026")
    expect(screen.getByText("15 Jun 2026")).toBeInTheDocument();
    // Does not render relative text like "ago" in the visible row text
    expect(screen.queryByText(/ago$/i)).not.toBeInTheDocument();
  });

  it("renders the creator name inline in the Created by column", () => {
    render(<DashboardsListView />);

    expect(screen.getByText("Alice Smith")).toBeInTheDocument();
  });

  it("navigates to dashboard detail when clicking the creator name in the Created by cell", () => {
    render(<DashboardsListView />);

    const creatorName = screen.getByText("Alice Smith");
    fireEvent.click(creatorName);

    expect(navigateMock).toHaveBeenCalledWith(paths.dashboard.dashboards.detail("dash-1"));
  });

  it("renders the delete button and delete spacer when canDelete is true", () => {
    h.canEdit = {
      canCreate: true,
      canUpdate: true,
      canDelete: true,
      isReadOnly: false,
    };

    const { container } = render(<DashboardsListView />);

    // Row has delete icon button
    expect(container.querySelector(".row-actions")).toBeInTheDocument();
  });

  it("omits the delete button when canDelete is false", () => {
    h.canEdit = {
      canCreate: false,
      canUpdate: false,
      canDelete: false,
      isReadOnly: true,
    };

    const { container } = render(<DashboardsListView />);

    // Row does not have delete icon button for Viewers
    expect(container.querySelector(".row-actions")).not.toBeInTheDocument();
  });
});
