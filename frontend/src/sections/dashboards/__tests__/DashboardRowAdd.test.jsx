import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import DashboardDetailView from "../DashboardDetailView";

// W10-DASHBOARDS-U04: the row "+" used to save a blank "Untitled widget" and
// narrow its neighbours before the editor opened, so backing out of the editor
// left a dead card on the dashboard. The mutations resolve like the real API so
// the old code path runs to completion instead of failing on a missing method.
const h = vi.hoisted(() => ({
  navigate: vi.fn(),
  createWidget: { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false },
  reorderWidgets: { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false },
  widgets: [],
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({
    data: { id: "dash-1", name: "My Dash", widgets: h.widgets },
    isLoading: false,
  }),
  useUpdateDashboard: () => ({ mutate: vi.fn() }),
  useUpdateWidget: () => ({ mutate: vi.fn() }),
  useDeleteWidget: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteDashboard: () => ({ mutate: vi.fn(), isPending: false }),
  useReorderWidgets: () => h.reorderWidgets,
  useDuplicateWidget: () => ({ mutate: vi.fn() }),
  useCreateWidget: () => h.createWidget,
}));

vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
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

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/dashboard/dashboards/dash-1");
  h.createWidget.mutateAsync.mockResolvedValue({
    data: { result: { id: "w-new" } },
  });
  h.reorderWidgets.mutateAsync.mockResolvedValue({});
  // One full row of two half-width widgets, so the row "+" is offered.
  h.widgets = [
    { id: "w-1", name: "Tokens", position: 0, width: 6, height: 300 },
    { id: "w-2", name: "Cost", position: 1, width: 6, height: 300 },
  ];
});

describe("DashboardDetailView — add widget to row (W10-DASHBOARDS-U04)", () => {
  it("opens the new-widget editor for that row without saving a blank widget", async () => {
    render(<DashboardDetailView />);

    fireEvent.click(screen.getByRole("button", { name: "Add widget to row" }));

    await waitFor(() => expect(h.navigate).toHaveBeenCalled());
    expect(h.navigate).toHaveBeenCalledWith(
      "/dashboard/dashboards/dash-1/widget/new?addAfter=w-2",
    );
    // Nothing is written until the user saves in the editor.
    expect(h.createWidget.mutateAsync).not.toHaveBeenCalled();
    expect(h.createWidget.mutate).not.toHaveBeenCalled();
    expect(h.reorderWidgets.mutateAsync).not.toHaveBeenCalled();
    expect(h.reorderWidgets.mutate).not.toHaveBeenCalled();
    expect(screen.getAllByTestId("widget-chart")).toHaveLength(2);
  });

  it("carries the dashboard time preset into the editor, like Add widget", async () => {
    window.history.replaceState(
      {},
      "",
      "/dashboard/dashboards/dash-1?timePreset=7D",
    );
    render(<DashboardDetailView />);

    fireEvent.click(screen.getByRole("button", { name: "Add widget to row" }));

    await waitFor(() => expect(h.navigate).toHaveBeenCalled());
    expect(h.navigate).toHaveBeenCalledWith(
      "/dashboard/dashboards/dash-1/widget/new?addAfter=w-2&timePreset=7D",
    );
    expect(h.createWidget.mutateAsync).not.toHaveBeenCalled();
  });
});
