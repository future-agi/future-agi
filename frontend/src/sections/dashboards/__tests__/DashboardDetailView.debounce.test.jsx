import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent } from "src/utils/test-utils";
import DashboardDetailView from "../DashboardDetailView";

const h = vi.hoisted(() => ({
  widgets: [{ id: "w-1", name: "Tokens", position: 0, width: 12 }],
  widgetChartProps: null,
  canEdit: {
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  },
}));

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
  useDeleteWidget: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteDashboard: () => ({ mutate: vi.fn(), isPending: false }),
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
  default: (props) => {
    h.widgetChartProps = props;
    return <div data-testid="widget-chart" />;
  },
}));

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

describe("DashboardDetailView — time filter debounce", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-13T12:00:00.000Z"));
    h.widgetChartProps = null;
    h.widgets = [{ id: "w-1", name: "Tokens", position: 0, width: 12 }];
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("updates the chip immediately and only propagates the final rapid selection", async () => {
    render(<DashboardDetailView />);

    const sevenDayLabel = screen.getByText("7D");
    const thirtyDayLabel = screen.getByText("30D");
    const sevenDayChip = sevenDayLabel.closest(".MuiChip-root");
    const thirtyDayChip = thirtyDayLabel.closest(".MuiChip-root");

    expect(h.widgetChartProps.globalDateRange).toBeNull();

    fireEvent.click(sevenDayChip);
    expect(sevenDayChip).toHaveClass("MuiChip-filled");
    expect(h.widgetChartProps.globalDateRange).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(250);
    });

    fireEvent.click(thirtyDayChip);
    expect(thirtyDayChip).toHaveClass("MuiChip-filled");
    expect(h.widgetChartProps.globalDateRange).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(499);
    });
    expect(h.widgetChartProps.globalDateRange).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(1);
    });

    expect(h.widgetChartProps.globalDateRange).toEqual({
      start: "2026-07-14T12:00:00.250Z",
      end: "2026-08-13T12:00:00.250Z",
    });
  });
});
