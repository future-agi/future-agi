import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";
import DashboardsListView from "../DashboardsListView";
import { paths } from "src/routes/paths";

const navigateMock = vi.fn();

const ALICE = { name: "Alice Smith", email: "alice@example.com" };
const BOB = { name: "Bob Editor", email: "bob@example.com" };

const makeDashboard = (overrides = {}) => ({
  id: "dash-1",
  name: "Engineering Metrics",
  description: "Overview dashboard",
  widget_count: 5,
  created_at: "2026-06-15T10:00:00.000Z",
  updated_at: "2026-06-15T12:00:00.000Z",
  created_by: ALICE,
  updated_by: BOB,
  ...overrides,
});

const h = vi.hoisted(() => ({
  dashboards: [],
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
    h.dashboards = [makeDashboard()];
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

  it("shows only the creator's avatar in the Created by cell when someone else edited it", () => {
    const { container } = render(<DashboardsListView />);

    const avatars = container.querySelectorAll(".MuiAvatar-root");
    expect(avatars).toHaveLength(1);
    expect(avatars[0]).toHaveTextContent("AS");
    expect(screen.queryByText("BE")).not.toBeInTheDocument();
  });

  it("shows a dash, not the editor, when the creator account no longer exists", async () => {
    h.dashboards = [makeDashboard({ created_by: null })];

    const { container } = render(<DashboardsListView />);

    expect(screen.getByText("—")).toBeInTheDocument();
    expect(container.querySelectorAll(".MuiAvatar-root")).toHaveLength(0);
    expect(screen.queryByText("BE")).not.toBeInTheDocument();

    // The last editor is still available on hover.
    fireEvent.mouseOver(screen.getByText("—"));
    expect(
      await screen.findByText(
        "Last edited by Bob Editor",
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
  });

  it("keeps the created and last-edited history in the hover tooltip, most recent first", async () => {
    render(<DashboardsListView />);

    fireEvent.mouseOver(screen.getByText("Alice Smith"));

    expect(
      await screen.findByText("Created by Alice Smith", {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByText("Last edited by Bob Editor")).toBeInTheDocument();

    const recent = screen.getByText("Recently Viewed By:").nextElementSibling;
    const names = within(recent)
      .getAllByText(/^(Alice Smith|Bob Editor)$/)
      .map((node) => node.textContent);
    expect(names).toEqual(["Bob Editor", "Alice Smith"]);
  });

  it("navigates to dashboard detail when clicking the creator name in the Created by cell", () => {
    render(<DashboardsListView />);

    const creatorName = screen.getByText("Alice Smith");
    fireEvent.click(creatorName);

    expect(navigateMock).toHaveBeenCalledWith(
      paths.dashboard.dashboards.detail("dash-1"),
    );
  });

  it("renders the delete button and header delete spacer when canDelete is true", () => {
    h.canEdit = {
      canCreate: true,
      canUpdate: true,
      canDelete: true,
      isReadOnly: false,
    };

    const { container } = render(<DashboardsListView />);

    // Row has delete icon button
    expect(container.querySelector(".row-actions")).toBeInTheDocument();
    // Header reserves the same width so its columns line up with the rows
    expect(
      screen.getByTestId("dashboards-header-delete-spacer"),
    ).toBeInTheDocument();
  });

  it("omits the delete button and header delete spacer when canDelete is false", () => {
    h.canEdit = {
      canCreate: false,
      canUpdate: false,
      canDelete: false,
      isReadOnly: true,
    };

    const { container } = render(<DashboardsListView />);

    // Row does not have delete icon button for Viewers
    expect(container.querySelector(".row-actions")).not.toBeInTheDocument();
    // Header must not reserve space for a button that is not there
    expect(
      screen.queryByTestId("dashboards-header-delete-spacer"),
    ).not.toBeInTheDocument();
  });
});
