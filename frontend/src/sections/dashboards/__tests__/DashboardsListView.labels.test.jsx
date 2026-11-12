import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import DashboardsListView from "../DashboardsListView";

// The avatar hovercard on a dashboard row lists the creator and the last
// editor with their creation and edit times (nothing records who viewed a
// dashboard), so it must not call them viewers. The page title and the
// first-run docs link match the rest of the Dashboards flow.

const h = vi.hoisted(() => ({
  list: null,
  navigate: vi.fn(),
  deleteDashboard: { mutate: vi.fn(), isPending: false },
  createDashboard: { mutateAsync: vi.fn(), isPending: false },
  canEdit: {
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  },
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardList: () => h.list,
  useCreateDashboard: () => h.createDashboard,
  useDeleteDashboard: () => h.deleteDashboard,
}));

vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
  useNavigate: () => h.navigate,
}));

vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => h.canEdit,
}));

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

const DASHBOARD_DOCS_URL =
  "https://docs.futureagi.com/docs/observe/features/dashboard";

const ALICE = { name: "Alice Park", email: "alice@example.com" };
const BOB = { name: "Bob Lee", email: "bob@example.com" };
const ALPHA = {
  id: "d-1",
  name: "Alpha",
  widget_count: 2,
  created_by: ALICE,
  created_at: "2026-11-01T10:00:00Z",
  updated_by: BOB,
  updated_at: "2026-11-02T10:00:00Z",
};

const listResult = (overrides = {}) => ({
  data: [ALPHA],
  isLoading: false,
  isError: false,
  refetch: vi.fn(),
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  h.list = listResult();
});

describe("DashboardsListView — hovercard and page labels", () => {
  it("labels the hovercard list as the creator and last editor, not viewers", async () => {
    const user = userEvent.setup();
    render(<DashboardsListView />);

    const row = screen.getByRole("button", { name: "Open dashboard Alpha" });
    await user.hover(within(row).getByText("BL"));

    const card = await screen.findByRole("tooltip");
    expect(card).toHaveTextContent("Created and last edited by:");
    expect(card).not.toHaveTextContent(/viewed/i);
    expect(within(card).getByText("Bob Lee")).toBeInTheDocument();
    expect(within(card).getByText("Alice Park")).toBeInTheDocument();
  });

  it("titles the page Dashboards, as the sidebar does", () => {
    render(<DashboardsListView />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Dashboards" }),
    ).toBeInTheDocument();
  });

  it("points the first-run Read the docs link at the dashboard docs page", () => {
    h.list = listResult({ data: [] });
    render(<DashboardsListView />);

    expect(
      screen.getByRole("link", { name: /read the docs/i }),
    ).toHaveAttribute("href", DASHBOARD_DOCS_URL);
  });
});
