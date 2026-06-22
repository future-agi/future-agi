import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import DashboardDetailView from "../DashboardDetailView";

// Controlled mutation stubs (hoisted so the vi.mock factory can see them).
const h = vi.hoisted(() => ({
  deleteWidget: { mutate: vi.fn(), isPending: false },
  deleteDashboard: { mutate: vi.fn(), isPending: false },
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({
    data: {
      id: "dash-1",
      name: "My Dash",
      widgets: [{ id: "w-1", name: "Tokens", position: 0, width: 12 }],
    },
    isLoading: false,
  }),
  useUpdateDashboard: () => ({ mutate: vi.fn() }),
  useUpdateWidget: () => ({ mutate: vi.fn() }),
  useDeleteWidget: () => h.deleteWidget,
  useDeleteDashboard: () => h.deleteDashboard,
  useReorderWidgets: () => ({ mutate: vi.fn() }),
  useDuplicateWidget: () => ({ mutate: vi.fn() }),
  useCreateWidget: () => ({ mutate: vi.fn() }),
}));

vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
  useParams: () => ({ dashboardId: "dash-1" }),
  useNavigate: () => vi.fn(),
}));

vi.mock("../WidgetChart", () => ({
  default: () => <div data-testid="widget-chart" />,
}));

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

const openWidgetDeleteDialog = () => {
  fireEvent.click(screen.getByRole("button", { name: /widget options/i }));
  // The widget menu item is labelled just "Delete" (dashboard's is "Delete Dashboard").
  fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
};

describe("DashboardDetailView — delete confirmation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.deleteWidget.isPending = false;
    h.deleteDashboard.isPending = false;
  });

  it("widget delete: opens the keyed dialog with the widget's name", () => {
    render(<DashboardDetailView />);
    openWidgetDeleteDialog();
    expect(
      screen.getByText(/Are you sure you want to delete "Tokens"/),
    ).toBeInTheDocument();
  });

  it("widget delete: confirms with the right id and closes on settle (not synchronously)", () => {
    render(<DashboardDetailView />);
    openWidgetDeleteDialog();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(h.deleteWidget.mutate).toHaveBeenCalledWith(
      { dashboardId: "dash-1", widgetId: "w-1" },
      // close happens in onSettled — pins the reviewer's fix (reverting to a
      // synchronous setConfirmDelete(null) would drop this callback).
      expect.objectContaining({ onSettled: expect.any(Function) }),
    );
  });

  it("widget delete: Delete button is disabled while the mutation is pending", () => {
    h.deleteWidget.isPending = true;
    render(<DashboardDetailView />);
    openWidgetDeleteDialog();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("dashboard delete: deletes the dashboard by id, closing on settle", () => {
    render(<DashboardDetailView />);
    fireEvent.click(screen.getByRole("button", { name: /dashboard options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete dashboard/i }));
    expect(
      screen.getByText(/Are you sure you want to delete "My Dash"/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(h.deleteDashboard.mutate).toHaveBeenCalledWith(
      "dash-1",
      expect.objectContaining({ onSettled: expect.any(Function) }),
    );
    expect(h.deleteWidget.mutate).not.toHaveBeenCalled();
  });
});
