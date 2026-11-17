import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";
import { paths } from "src/routes/paths";
import { palette } from "src/theme/palette";
import DashboardsListView from "../DashboardsListView";

// Controlled stubs (hoisted so the vi.mock factories can see them). `list` is
// the useDashboardList() result and is replaced per test.
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

const ALICE = { name: "Alice Park", email: "alice@example.com" };
const BOB = { name: "Bob Lee", email: "bob@example.com" };
const ALPHA = {
  id: "d-1",
  name: "Alpha",
  widget_count: 2,
  created_by: ALICE,
  created_at: "2026-11-01T10:00:00Z",
  updated_at: "2026-11-02T10:00:00Z",
};
const BETA = {
  id: "d-2",
  name: "Beta",
  widget_count: 1,
  created_by: BOB,
  created_at: "2026-11-03T10:00:00Z",
  updated_at: "2026-11-04T10:00:00Z",
};

const listResult = (overrides = {}) => ({
  data: [ALPHA, BETA],
  isLoading: false,
  isError: false,
  refetch: vi.fn(),
  ...overrides,
});

// jsdom does not apply :focus-within / :focus-visible in the cascade, so read
// the rules emotion emitted for the row's own class (as KeyboardFocus.test.jsx
// does for the sandbox rows).
function rulesFor(element, suffix) {
  const selectors = Array.from(
    element.classList,
    (name) => `.${name}${suffix}`,
  );
  return Array.from(document.styleSheets).flatMap((sheet) =>
    Array.from(sheet.cssRules).filter((rule) =>
      rule.selectorText
        ?.split(",")
        .some((selector) => selectors.includes(selector.trim())),
    ),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  h.list = listResult();
  h.canEdit = {
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  };
});

describe("DashboardsListView — load failure", () => {
  it("shows a load error with Retry, not the first-run screen, when the list fails", () => {
    h.list = listResult({ data: undefined, isError: true });
    render(<DashboardsListView />);

    expect(screen.getByText("Couldn't load dashboards")).toBeInTheDocument();
    expect(screen.queryByText("No dashboards yet")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /create dashboard/i }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(h.list.refetch).toHaveBeenCalledTimes(1);
  });

  it("shows the retry in progress while the list is refetching", () => {
    h.list = listResult({ data: undefined, isError: true, isFetching: true });
    render(<DashboardsListView />);

    expect(screen.getByRole("button", { name: "Retrying..." })).toBeDisabled();
    expect(screen.queryByText("No dashboards yet")).not.toBeInTheDocument();
  });

  it("keeps showing cached dashboards when a background refetch fails", () => {
    h.list = listResult({ data: [ALPHA], isError: true });
    render(<DashboardsListView />);

    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(
      screen.queryByText("Couldn't load dashboards"),
    ).not.toBeInTheDocument();
  });

  it("still shows the first-run screen for a workspace with no dashboards", () => {
    h.list = listResult({ data: [] });
    render(<DashboardsListView />);

    expect(screen.getByText("No dashboards yet")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create dashboard" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Couldn't load dashboards"),
    ).not.toBeInTheDocument();
  });
});

describe("DashboardsListView — creator filter with no matches", () => {
  it("offers Clear filters instead of the first-run screen after the filtered creator's last dashboard is deleted", () => {
    const { rerender } = render(<DashboardsListView />);

    fireEvent.click(screen.getByRole("button", { name: /created by anyone/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Alice Park/ }));
    // The creator menu stays open after a tick; close it so the page is not
    // aria-hidden behind the menu.
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.queryByText("Beta")).not.toBeInTheDocument();

    // Alice's only dashboard is deleted; the list refetches without it.
    h.list = listResult({ data: [BETA] });
    rerender(<DashboardsListView />);

    expect(screen.queryByText("No dashboards yet")).not.toBeInTheDocument();
    expect(
      screen.getByText("No dashboards match your filters"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /created by anyone/i }),
    ).toBeInTheDocument();
  });

  it("Clear filters also clears the search", () => {
    render(<DashboardsListView />);

    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "zzz" },
    });
    expect(
      screen.getByText("No dashboards match your filters"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
  });
});

describe("DashboardsListView — keyboard access", () => {
  it("rows are focusable buttons that open the dashboard on Enter and Space", () => {
    render(<DashboardsListView />);

    const row = screen.getByRole("button", { name: "Open dashboard Alpha" });
    expect(row).toHaveAttribute("tabindex", "0");

    fireEvent.keyDown(row, { key: "Enter" });
    expect(h.navigate).toHaveBeenCalledTimes(1);
    expect(h.navigate).toHaveBeenCalledWith(
      paths.dashboard.dashboards.detail(ALPHA.id),
    );

    h.navigate.mockClear();
    // Space is prevented so the page does not scroll as well.
    expect(fireEvent.keyDown(row, { key: " " })).toBe(false);
    expect(h.navigate).toHaveBeenCalledWith(
      paths.dashboard.dashboards.detail(ALPHA.id),
    );

    h.navigate.mockClear();
    fireEvent.keyDown(row, { key: "a" });
    expect(h.navigate).not.toHaveBeenCalled();
  });

  it("the Delete button is named and its key presses do not open the dashboard", () => {
    render(<DashboardsListView />);

    const row = screen.getByRole("button", { name: "Open dashboard Alpha" });
    const del = within(row).getByRole("button", { name: "Delete dashboard" });

    fireEvent.keyDown(del, { key: "Enter" });
    fireEvent.keyDown(del, { key: " " });
    expect(h.navigate).not.toHaveBeenCalled();

    fireEvent.click(del);
    expect(h.navigate).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Are you sure you want to delete "Alpha"/),
    ).toBeInTheDocument();
  });

  it("reveals the Delete button and draws the focus ring when the row has keyboard focus", () => {
    render(<DashboardsListView />);

    const row = screen.getByRole("button", { name: "Open dashboard Alpha" });

    const reveal = rulesFor(row, ":focus-within .row-actions")
      .map((rule) => rule.style.getPropertyValue("opacity"))
      .filter(Boolean);
    expect(reveal.pop()).toBe("1");

    const ring = rulesFor(row, ":focus-visible");
    const outline = ring
      .map((rule) => rule.style.getPropertyValue("outline"))
      .filter(Boolean)
      .pop();
    expect(outline?.toLowerCase()).toBe(
      `2px solid ${palette("light").border.focus}`.toLowerCase(),
    );
  });
});
