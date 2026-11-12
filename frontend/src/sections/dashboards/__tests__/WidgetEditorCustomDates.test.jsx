import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import WidgetEditorView from "../WidgetEditorView";

// OBS-SW-168: the dashboard's Custom dates must survive the trip into the
// widget editor and back. The URL used to carry only `timePreset=custom`, so a
// new widget saved the 30D default and returning from the editor kept the
// Custom chip but dropped its dates.

const h = vi.hoisted(() => ({
  params: { dashboardId: "dashboard", widgetId: "new" },
  dashboard: null,
  navigate: vi.fn(),
  enqueueSnackbar: vi.fn(),
  query: { mutate: vi.fn(), reset: vi.fn() },
  write: { mutate: vi.fn(), mutateAsync: vi.fn() },
  catalog: {
    metrics: [],
    isLoading: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
  },
  agents: { data: [] },
  inventory: { filteredAttributes: [], inventoryControlProps: {} },
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({ data: h.dashboard, isLoading: false }),
  useDashboardQuery: () => h.query,
  useCreateWidget: () => h.write,
  useUpdateWidget: () => h.write,
  useDeleteWidget: () => h.write,
  useReorderWidgets: () => h.write,
  useSimulationAgents: () => h.agents,
  usePropertyCatalog: () => h.catalog,
  useLegacyDashboardMetricsPaginated: () => h.catalog,
  isPropertyCatalogNotReadyError: () => false,
}));
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useParams: () => h.params,
  useNavigate: () => h.navigate,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace" }),
}));
vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => ({ canDelete: true, isReadOnly: false }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: h.enqueueSnackbar }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("src/components/filter-value-label", () => ({
  default: () => null,
  useResolvedFilterOptions: () => ({ options: [] }),
  shouldShowFilterValueContinuation: () => false,
}));
vi.mock("src/sections/projects/LLMTracing/useCursorAttributeInventory", () => ({
  useLegacyCursorAttributeInventory: () => h.inventory,
  attributeInventoryKey: ({ key, type }) => `${key}:${type}`,
}));

const START = "2026-10-01T00:00:00.000Z";
const END = "2026-10-15T00:00:00.000Z";
const CUSTOM_SEARCH = new URLSearchParams({
  timePreset: "custom",
  customStart: START,
  customEnd: END,
}).toString();

const TRACES_OPTION = {
  name: "trace_count",
  display_name: "Traces",
  property_id: "system_attribute:traces:trace_count",
  category: "system_metric",
  source: "traces",
};

const openAt = (path) => window.history.replaceState({}, "", path);

const searchOf = (url) => new URL(url, "http://localhost").searchParams;

async function addTracesMetric() {
  fireEvent.click(screen.getByRole("button", { name: "Add metric" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Traces (Traces)" }),
  );
  await screen.findByRole("button", { name: "Change metric Traces" });
}

beforeEach(() => {
  vi.clearAllMocks();
  h.params = { dashboardId: "dashboard", widgetId: "new" };
  h.dashboard = { id: "dashboard", name: "Custom dates", widgets: [] };
  h.catalog.metrics = [TRACES_OPTION];
  h.write.mutateAsync.mockResolvedValue({
    data: { result: { id: "created-widget" } },
  });
  // Any unmocked browser request is a test error, never a service fallback.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network request");
    }),
  );
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(() => {
    throw new Error("Unexpected XMLHttpRequest");
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("WidgetEditor — dashboard Custom dates (OBS-SW-168)", () => {
  it("saves a new widget on the dashboard's Custom dates and returns with them", async () => {
    openAt(`/dashboard/dashboards/dashboard/widget/new?${CUSTOM_SEARCH}`);
    render(<WidgetEditorView />);

    await addTracesMetric();
    fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));

    await waitFor(() => expect(h.write.mutateAsync).toHaveBeenCalledOnce());
    const [{ data }] = h.write.mutateAsync.mock.calls[0];
    expect(data.query_config.time_range).toEqual({
      custom_start: START,
      custom_end: END,
    });

    await waitFor(() => expect(h.navigate).toHaveBeenCalled());
    const [returnUrl] = h.navigate.mock.calls.at(-1);
    expect(returnUrl.split("?")[0]).toBe("/dashboard/dashboards/dashboard");
    const search = searchOf(returnUrl);
    expect(search.get("timePreset")).toBe("custom");
    expect(search.get("customStart")).toBe(START);
    expect(search.get("customEnd")).toBe(END);
  });

  it("refuses to save a Custom range that has no dates instead of saving 30D", async () => {
    openAt("/dashboard/dashboards/dashboard/widget/new?timePreset=custom");
    render(<WidgetEditorView />);

    await addTracesMetric();
    fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));

    await waitFor(() =>
      expect(h.enqueueSnackbar).toHaveBeenCalledWith(
        "Choose the dates for the custom range",
        { variant: "warning" },
      ),
    );
    expect(h.write.mutateAsync).not.toHaveBeenCalled();
  });

  it("closes an existing widget back to the dashboard's Custom dates", async () => {
    h.params = { dashboardId: "dashboard", widgetId: "saved-widget" };
    h.dashboard = {
      id: "dashboard",
      name: "Custom dates",
      widgets: [
        {
          id: "saved-widget",
          name: "Saved on 7D",
          query_config: {
            project_ids: [],
            time_range: { preset: "7D" },
            granularity: "day",
            metrics: [
              {
                name: "trace_count",
                display_name: "Traces",
                property_id: "system_attribute:traces:trace_count",
                type: "system_metric",
                source: "traces",
                aggregation: "count_distinct",
              },
            ],
            filters: [],
            breakdowns: [],
          },
          chart_config: { chart_type: "line" },
        },
      ],
    };
    openAt(
      `/dashboard/dashboards/dashboard/widget/saved-widget?${CUSTOM_SEARCH}`,
    );
    render(<WidgetEditorView />);

    await screen.findByRole("button", { name: "Change metric Traces" });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(h.navigate).toHaveBeenCalledOnce();
    const search = searchOf(h.navigate.mock.calls[0][0]);
    expect(search.get("timePreset")).toBe("custom");
    expect(search.get("customStart")).toBe(START);
    expect(search.get("customEnd")).toBe(END);
  });
});
