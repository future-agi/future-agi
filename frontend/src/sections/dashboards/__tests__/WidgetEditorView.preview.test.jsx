import React from "react";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  reset: vi.fn(),
  chartType: "line",
}));
const catalog = {
  metrics: [],
  categoryCounts: {},
  categoryCountsExact: true,
  isLoading: false,
};
const dashboard = () => ({
  id: "dashboard-1",
  name: "Fixture dashboard",
  widgets: [
    {
      id: "widget-1",
      name: "Fixture widget",
      query_config: {
        time_range: { preset: "7D" },
        granularity: "day",
        metrics: [
          {
            name: "latency",
            display_name: "Latency",
            type: "system_metric",
            source: "traces",
            aggregation: "avg",
          },
        ],
      },
      chart_config: { chart_type: h.chartType },
    },
  ],
});
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useParams: () => ({ dashboardId: "dashboard-1", widgetId: "widget-1" }),
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({ data: dashboard(), isLoading: false }),
  useDashboardQuery: () => ({
    mutate: h.mutate,
    reset: h.reset,
    isIdle: false,
    isPending: false,
  }),
  useCreateWidget: () => ({}),
  useUpdateWidget: () => ({}),
  useDeleteWidget: () => ({}),
  useSimulationAgents: () => ({ data: [] }),
  usePropertyCatalog: () => catalog,
  useLegacyDashboardMetricsPaginated: () => catalog,
  isPropertyCatalogNotReadyError: () => false,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => ({ canDelete: false, isReadOnly: false }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/filter-value-label", () => ({
  default: () => null,
  useResolvedFilterOptions: () => ({ options: [] }),
}));
vi.mock("src/sections/projects/LLMTracing/useCursorAttributeInventory", () => ({
  attributeInventoryKey: (x) => x,
  useLegacyCursorAttributeInventory: () => ({
    filteredAttributes: [],
    inventoryControlProps: {},
  }),
}));
vi.mock("react-apexcharts", () => ({
  default: () => <div data-testid="editor-chart" />,
}));
import WidgetEditorView from "../WidgetEditorView";
const pending = {
  data: {
    result: {
      metrics: [],
      query_complete: false,
      query_status: "pending",
      query_sampled: false,
      query_refreshing: true,
    },
  },
};
const exact = {
  data: {
    result: {
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      metrics: [
        {
          name: "latency",
          aggregation: "avg",
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          series: [
            {
              name: "total",
              data: [{ timestamp: "2026-07-09T00:00:00Z", value: 24 }],
            },
          ],
        },
      ],
    },
  },
};
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});
describe("full dashboard editor preview wiring", () => {
  it.each(["line", "bar"])(
    "renders a polling pause and resumes the exact %s preview",
    async (chartType) => {
      vi.useFakeTimers();
      h.chartType = chartType;
      h.mutate.mockImplementation((_request, options) =>
        options.onSuccess(pending),
      );
      const view = render(<WidgetEditorView />);
      await act(async () => vi.advanceTimersByTimeAsync(500_000));
      expect(h.mutate).toHaveBeenCalled();
      expect(
        screen.getByRole("button", { name: "Continue" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/Data could not be prepared/i),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/Preparing data/)).not.toBeInTheDocument();
      h.mutate.mockImplementation((_request, options) =>
        options.onSuccess(exact),
      );
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
      expect(
        screen.queryByRole("button", { name: "Continue" }),
      ).not.toBeInTheDocument();
      if (chartType === "line")
        expect(screen.getByTestId("editor-chart")).toBeInTheDocument();
      else expect(view.container).toHaveTextContent("24");
    },
  );
  it("cancels its debounced preview read when the editor unmounts", async () => {
    vi.useFakeTimers();
    h.mutate.mockImplementation(() => {});
    const view = render(<WidgetEditorView />);
    await act(async () => vi.advanceTimersByTimeAsync(500));
    expect(h.mutate).toHaveBeenCalledOnce();
    const request = h.mutate.mock.calls[0][0];
    expect(request.signal.aborted).toBe(false);
    view.unmount();
    expect(request.signal.aborted).toBe(true);
  });
});
