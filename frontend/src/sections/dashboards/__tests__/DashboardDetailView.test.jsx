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
  dashboardData: {
    id: "dash-1",
    name: "My Dash",
    widgets: undefined,
  }, // set to null to simulate 404
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
    data: h.dashboardData ? { ...h.dashboardData, widgets: h.widgets } : null,
    isLoading: false,
    isError: !h.dashboardData,
    error: h.dashboardData ? null : { statusCode: 404 },
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
    h.dashboardData = {
      id: "dash-1",
      name: "My Dash",
    };
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
    h.dashboardData = {
      id: "dash-1",
      name: "My Dash",
    };
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
    h.dashboardData = {
      id: "dash-1",
      name: "My Dash",
    };
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

// ---------------------------------------------------------------------------
// Cross-workspace recovery behaviour
// ---------------------------------------------------------------------------

// Controlled stubs for the recovery hook so we can drive the resolve states.
const recovery = vi.hoisted(() => ({
  isResolving: false,
  isSwitching: false,
  resolveAttempted: false,
}));

vi.mock("src/hooks/use_cross_workspace_recovery", () => ({
  useCrossWorkspaceRecovery: () => ({
    isResolving: recovery.isResolving,
    isSwitching: recovery.isSwitching,
    resolveAttempted: recovery.resolveAttempted,
  }),
}));

describe("DashboardDetailView — cross-workspace recovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    recovery.isResolving = false;
    recovery.isSwitching = false;
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
    // Simulate a 404 — the primary fetch failed, triggering the resolve path.
    h.dashboardData = null;
  });

  it("shows 'Looking for this dashboard…' when resolving", () => {
    recovery.isResolving = true;

    render(<DashboardDetailView />);
    expect(screen.getByText(/Looking for this dashboard/i)).toBeInTheDocument();
  });

  it("keeps showing the resolving state while the workspace switch is in flight", () => {
    // Between the resolve response and the hard reload there is a window
    // where neither fetch nor resolve is pending — the switch POST must keep
    // the loading state up so "not found" never flashes.
    recovery.isSwitching = true;

    render(<DashboardDetailView />);
    expect(screen.getByText(/Looking for this dashboard/i)).toBeInTheDocument();
  });

  it("shows 'Dashboard not found' when resolve also fails", () => {
    recovery.isResolving = false;
    recovery.resolveAttempted = true;

    render(<DashboardDetailView />);
    expect(
      screen.getByText(
        /Dashboard not found or you may not have access to this workspace/i,
      ),
    ).toBeInTheDocument();
  });
});

describe("DashboardDetailView — widget description (TH-7678)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.canEdit = { ...WRITER };
    // Reset the 404 state the cross-workspace recovery suite leaves behind.
    h.dashboardData = { id: "dash-1", name: "My Dash" };
  });

  it("renders the description on the widget card", () => {
    h.widgets = [
      {
        id: "w-1",
        name: "Tokens",
        description: "Total tokens consumed per day",
        position: 0,
        width: 12,
      },
    ];
    render(<DashboardDetailView />);
    expect(screen.getByText("Tokens")).toBeInTheDocument();
    expect(
      screen.getByText("Total tokens consumed per day"),
    ).toBeInTheDocument();
  });

  it("renders the description for a read-only viewer too", () => {
    h.canEdit = { ...VIEWER };
    h.widgets = [
      {
        id: "w-1",
        name: "Tokens",
        description: "Total tokens consumed per day",
        position: 0,
        width: 12,
      },
    ];
    render(<DashboardDetailView />);
    expect(
      screen.getByText("Total tokens consumed per day"),
    ).toBeInTheDocument();
  });

  it("renders no description line when the widget has none", () => {
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
    const { container } = render(<DashboardDetailView />);
    // The card header holds the title row and nothing else.
    const header = container.querySelector(
      '[data-widget-id="w-1"] .MuiCardContent-root > div',
    );
    expect(header.children).toHaveLength(1);
  });

  it("treats a whitespace-only description as no description", () => {
    h.widgets = [
      {
        id: "w-1",
        name: "Tokens",
        description: "   ",
        position: 0,
        width: 12,
      },
    ];
    const { container } = render(<DashboardDetailView />);
    const header = container.querySelector(
      '[data-widget-id="w-1"] .MuiCardContent-root > div',
    );
    expect(header.children).toHaveLength(1);
  });
});
