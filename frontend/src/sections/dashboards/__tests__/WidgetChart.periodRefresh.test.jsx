import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "src/utils/test-utils";
import WidgetChart from "../WidgetChart";

const h = vi.hoisted(() => ({
  query: { data: null, isPending: false, isError: false, mutate: vi.fn() },
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardQuery: () => h.query,
}));
vi.mock("react-apexcharts", () => ({ default: () => <div /> }));
vi.mock("../ChartLegend", () => ({ default: () => <div /> }));

const widget = {
  id: "w-1",
  query_config: { metrics: [{ name: "Latency", aggregation: "avg" }] },
  chart_config: { chart_type: "metric" },
};
const range = (end) => ({ start: "2026-11-01T00:00:00Z", end });
const result = {
  data: {
    result: {
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      query_exact: true,
      query_provenance: "exact_snapshot",
      metrics: [
        {
          name: "Latency",
          aggregation: "avg",
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          series: [
            {
              name: "total",
              aggregate_value: 19,
              data: [
                { timestamp: "2026-11-01T00:00:00Z", value: 10 },
                { timestamp: "2026-11-02T00:00:00Z", value: 100 },
              ],
            },
          ],
        },
      ],
    },
  },
};

beforeEach(() => {
  h.query.data = null;
  h.query.mutate.mockReset();
});

describe("OBS-SW-06/07 scalar and manual refresh", () => {
  it("renders the exact full-period value rather than 55", () => {
    h.query.data = result;
    render(<WidgetChart widget={widget} />);
    expect(screen.getByText(/^19(?:\.0+)?$/)).toBeInTheDocument();
    expect(screen.queryByText(/^55(?:\.0+)?$/)).not.toBeInTheDocument();
  });

  it("sends manual refresh and settles even when relative endpoints change", () => {
    const settled = vi.fn();
    const { rerender } = render(
      <WidgetChart
        widget={widget}
        dashboardId="dash-1"
        refreshRequestId={0}
        globalDateRange={range("2026-11-02T00:00:00Z")}
        onQuerySettled={settled}
      />,
    );
    const stale = h.query.mutate.mock.calls[0];
    rerender(
      <WidgetChart
        widget={widget}
        dashboardId="dash-1"
        refreshRequestId={1}
        globalDateRange={range("2026-11-03T00:00:00Z")}
        onQuerySettled={settled}
      />,
    );
    const [request, callbacks] = h.query.mutate.mock.calls.at(-1);
    expect(request.refresh).toBe(true);
    expect(request.queryConfig.time_range.custom_end).toBe(
      "2026-11-03T00:00:00Z",
    );
    expect(stale[0].signal.aborted).toBe(true);
    act(() => stale[1].onSuccess(result));
    expect(settled).not.toHaveBeenCalled();
    act(() => callbacks.onSuccess(result));
    expect(settled).toHaveBeenCalledWith(
      expect.objectContaining({
        refreshRequestId: 1,
        manualRefresh: true,
        exact: true,
      }),
    );
  });

  it("hands a pending manual refresh to the query that replaces it", () => {
    const settled = vi.fn();
    const props = { widget, dashboardId: "dash-1", onQuerySettled: settled };
    const { rerender } = render(
      <WidgetChart
        {...props}
        refreshRequestId={0}
        globalDateRange={range("2026-11-02T00:00:00Z")}
      />,
    );
    rerender(
      <WidgetChart
        {...props}
        refreshRequestId={1}
        globalDateRange={range("2026-11-03T00:00:00Z")}
      />,
    );
    const refreshing = h.query.mutate.mock.calls.at(-1);
    expect(refreshing[0].refresh).toBe(true);

    // The date range changes before the refresh answers.
    rerender(
      <WidgetChart
        {...props}
        refreshRequestId={1}
        globalDateRange={range("2026-11-04T00:00:00Z")}
      />,
    );
    const [replacement, callbacks] = h.query.mutate.mock.calls.at(-1);
    expect(refreshing[0].signal.aborted).toBe(true);
    expect(replacement.queryConfig.time_range.custom_end).toBe(
      "2026-11-04T00:00:00Z",
    );
    act(() => refreshing[1].onSuccess(result));
    expect(settled).not.toHaveBeenCalled();

    act(() => callbacks.onSuccess(result));
    expect(settled).toHaveBeenCalledTimes(1);
    expect(settled).toHaveBeenCalledWith(
      expect.objectContaining({
        widgetId: "w-1",
        refreshRequestId: 1,
        manualRefresh: true,
        exact: true,
      }),
    );
  });

  it("reports a range change after a settled refresh as an ordinary read", () => {
    const settled = vi.fn();
    const props = { widget, dashboardId: "dash-1", onQuerySettled: settled };
    const { rerender } = render(
      <WidgetChart
        {...props}
        refreshRequestId={0}
        globalDateRange={range("2026-11-02T00:00:00Z")}
      />,
    );
    rerender(
      <WidgetChart
        {...props}
        refreshRequestId={1}
        globalDateRange={range("2026-11-03T00:00:00Z")}
      />,
    );
    act(() => h.query.mutate.mock.calls.at(-1)[1].onSuccess(result));
    expect(settled).toHaveBeenLastCalledWith(
      expect.objectContaining({ refreshRequestId: 1, manualRefresh: true }),
    );

    rerender(
      <WidgetChart
        {...props}
        refreshRequestId={1}
        globalDateRange={range("2026-11-04T00:00:00Z")}
      />,
    );
    const [request, callbacks] = h.query.mutate.mock.calls.at(-1);
    expect(request.refresh).toBe(false);
    act(() => callbacks.onSuccess(result));
    expect(settled).toHaveBeenLastCalledWith(
      expect.objectContaining({ refreshRequestId: 1, manualRefresh: false }),
    );
  });
});
