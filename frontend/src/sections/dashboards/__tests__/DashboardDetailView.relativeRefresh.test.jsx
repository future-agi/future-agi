import React, { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import DashboardDetailView from "../DashboardDetailView";

const h = vi.hoisted(() => ({ props: {}, mounts: 0, preset: "30m" }));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({
    data: {
      id: "dash-1",
      name: "Dashboard",
      widgets: [1, 2].map((id) => ({
        id: `w-${id}`,
        name: `Widget ${id}`,
        position: id,
        width: 12,
        query_config: { metrics: [{ name: "Latency" }] },
      })),
    },
    isLoading: false,
  }),
  useUpdateDashboard: () => ({ mutate: vi.fn() }),
  useUpdateWidget: () => ({ mutate: vi.fn() }),
  useDeleteWidget: () => ({ mutate: vi.fn() }),
  useDeleteDashboard: () => ({ mutate: vi.fn() }),
  useReorderWidgets: () => ({ mutate: vi.fn() }),
  useDuplicateWidget: () => ({ mutate: vi.fn() }),
  useCreateWidget: () => ({ mutate: vi.fn() }),
}));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
  useParams: () => ({ dashboardId: "dash-1" }),
  useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams({ timePreset: h.preset })],
}));
vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => ({
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("../WidgetChart", async () => {
  const { default: PropTypes } = await import("prop-types");
  function MockWidgetChart(props) {
    h.props[props.widget.id] = props;
    useEffect(() => {
      h.mounts += 1;
    }, []);
    return <div />;
  }
  MockWidgetChart.propTypes = {
    widget: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
  };
  return { default: MockWidgetChart };
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-09T12:00:00Z"));
  h.preset = "30m";
  h.props = {};
  h.mounts = 0;
});
afterEach(() => vi.useRealTimers());

function settle() {
  act(() => {
    Object.values(h.props).forEach((props) =>
      props.onQuerySettled({
        dashboardId: "dash-1",
        widgetId: props.widget.id,
        refreshRequestId: props.refreshRequestId,
        manualRefresh: true,
        exact: true,
        updatedAt: new Date(),
      }),
    );
  });
}

describe("OBS-SW-07 shared refresh clock", () => {
  it("advances every relative range without remounting widgets and settles refresh", () => {
    render(<DashboardDetailView />);
    const oldRange = h.props["w-1"].globalDateRange;
    const mounts = h.mounts;
    vi.setSystemTime(new Date("2026-11-09T12:10:00Z"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    const next = h.props["w-1"].globalDateRange;
    expect(new Date(next.start) - new Date(oldRange.start)).toBe(600000);
    expect(new Date(next.end) - new Date(oldRange.end)).toBe(600000);
    expect(h.props["w-2"].globalDateRange).toEqual(next);
    expect(h.props["w-1"].refreshRequestId).toBe(1);
    expect(h.mounts).toBe(mounts);
    settle();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
  });

  it("advances today across local midnight", () => {
    h.preset = "today";
    vi.setSystemTime(new Date(2026, 10, 9, 23, 59));
    render(<DashboardDetailView />);
    vi.setSystemTime(new Date(2026, 10, 10, 0, 1));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(h.props["w-1"].globalDateRange.start).toBe(
      new Date(2026, 10, 10).toISOString(),
    );
    settle();
  });

  it("leaves custom endpoints fixed when refreshing", () => {
    render(<DashboardDetailView />);
    fireEvent.click(screen.getByText("Custom", { exact: true }));
    for (const label of ["Start Date", "End Date"]) {
      fireEvent.change(screen.getByLabelText(label), {
        target: { value: "2026-11-01" },
      });
    }
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    const custom = h.props["w-1"].globalDateRange;
    vi.setSystemTime(new Date("2026-11-10T12:00:00Z"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(h.props["w-1"].globalDateRange).toEqual(custom);
    settle();
  });
});
