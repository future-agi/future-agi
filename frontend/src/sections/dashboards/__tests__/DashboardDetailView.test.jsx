import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import DashboardDetailView from "../DashboardDetailView";
import { DATE_PRESETS } from "../constants";

// Controlled stubs (hoisted so the vi.mock factory can see them). `widgets` is
// per-test controllable so we can drive both the empty and populated dashboard.
const h = vi.hoisted(() => ({
  deleteWidget: { mutate: vi.fn(), isPending: false },
  deleteDashboard: { mutate: vi.fn(), isPending: false },
  widgets: [{ id: "w-1", name: "Tokens", position: 0, width: 12 }],
  // Permission state the useCanEditDashboard mock returns; per-test controllable
  // so we can drive both the writer and viewer (read-only) paths.
  canEdit: {
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  },
}));

const WRITER = {
  canCreate: true,
  canUpdate: true,
  canDelete: true,
  isReadOnly: false,
};
const VIEWER = {
  canCreate: false,
  canUpdate: false,
  canDelete: false,
  isReadOnly: true,
};

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({
    data: {
      id: "dash-1",
      name: "My Dash",
      widgets: h.widgets,
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

vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => h.canEdit,
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
    h.canEdit = { ...WRITER };
    h.deleteWidget.isPending = false;
    h.deleteDashboard.isPending = false;
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
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
    fireEvent.click(
      screen.getByRole("menuitem", { name: /delete dashboard/i }),
    );
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

describe("DashboardDetailView — time filter bar visibility", () => {
  // A chip label unique to the global time-filter bar, read from the real
  // preset source (not hand-authored) so the assertion tracks what renders.
  const presetLabel = DATE_PRESETS.find((p) => p.value === "30D").label;

  beforeEach(() => {
    vi.clearAllMocks();
    h.canEdit = { ...WRITER };
  });

  it("hides the time filter bar on an empty (0-widget) dashboard", () => {
    h.widgets = [];
    render(<DashboardDetailView />);
    // The empty-state CTA is what should greet the user instead...
    expect(screen.getByText(/no widgets yet/i)).toBeInTheDocument();
    // ...and the interactive-but-inert time filter is not in the DOM.
    expect(screen.queryByText(presetLabel)).not.toBeInTheDocument();
  });

  it("shows the time filter bar once the dashboard has a widget", () => {
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
    render(<DashboardDetailView />);
    expect(screen.getByText(presetLabel)).toBeInTheDocument();
    expect(screen.queryByText(/no widgets yet/i)).not.toBeInTheDocument();
  });
});

describe("DashboardDetailView — RBAC gating", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
  });

  it("writer sees the write affordances", () => {
    h.canEdit = { ...WRITER };
    render(<DashboardDetailView />);
    expect(
      screen.getByRole("button", { name: /dashboard options/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /widget options/i }),
    ).toBeInTheDocument();
  });

  it("read-only viewer: every write affordance is gated out", () => {
    h.canEdit = { ...VIEWER };
    render(<DashboardDetailView />);
    // dashboard ⋮ menu (rename / add widget / delete dashboard) — trigger hidden
    expect(
      screen.queryByRole("button", { name: /dashboard options/i }),
    ).toBeNull();
    // per-widget ⋮ menu (edit / duplicate / resize / delete) — hidden
    expect(
      screen.queryByRole("button", { name: /widget options/i }),
    ).toBeNull();
    // add-widget affordance — hidden
    expect(screen.queryByRole("button", { name: /add widget/i })).toBeNull();
  });

  it("read-only viewer: the read path still renders (dashboard + widgets)", () => {
    h.canEdit = { ...VIEWER };
    render(<DashboardDetailView />);
    // chart still renders — viewers can view, just not edit
    expect(screen.getByTestId("widget-chart")).toBeInTheDocument();
  });
});
