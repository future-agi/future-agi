import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import WidgetEditorView from "../WidgetEditorView";

// W10-DASHBOARDS-L06 through the real editor: reopen a saved Line widget that
// has Cost/Sum twice (filtered to project A and to project B), let its preview
// arrive, and press Save. A selection saved before the fix must come back on
// the same project and be written so it keeps naming that project. Harness
// mirrors WidgetEditorSavedQuery.test.jsx: only the data hooks are mocked, the
// editor's real initialization, debounce, series state and save run.
const h = vi.hoisted(() => ({
  dashboard: null,
  query: { mutate: vi.fn(), reset: vi.fn() },
  write: { mutate: vi.fn(), mutateAsync: vi.fn().mockResolvedValue({}) },
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
  useParams: () => ({ dashboardId: "dashboard", widgetId: "saved-widget" }),
  useNavigate: () => vi.fn(),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace" }),
}));
vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => ({ canDelete: true, isReadOnly: false }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("../ChartLegend", () => ({ default: () => null }));
vi.mock("src/components/filter-value-label", () => ({
  default: () => null,
  useResolvedFilterOptions: () => ({ options: [] }),
  shouldShowFilterValueContinuation: () => false,
}));
vi.mock("src/sections/projects/LLMTracing/useCursorAttributeInventory", () => ({
  useLegacyCursorAttributeInventory: () => h.inventory,
  attributeInventoryKey: ({ key, type }) => `${key}:${type}`,
}));

const PROJECT_A = "a407da87-7a8c-4e8f-9605-6998b7a69470";
const PROJECT_B = "b407da87-7a8c-4e8f-9605-6998b7a69470";

const costFor = (project) => ({
  id: "cost",
  name: "cost",
  display_name: "Cost",
  property_id: "system_attribute:traces:cost",
  type: "system_metric",
  source: "traces",
  aggregation: "sum",
  filters: [
    {
      column_id: "project",
      property_id: "system_attribute:traces:project",
      display_name: "Project",
      source: "traces",
      output_type: "string",
      filter_config: {
        filter_type: "text",
        filter_op: "in",
        filter_value: [project],
        col_type: "SYSTEM_METRIC",
      },
    },
  ],
});

const MODEL_BREAKDOWN = {
  name: "model",
  property_id: "system_attribute:traces:model",
  display_name: "Model",
  type: "system_metric",
  source: "traces",
};

const savedDashboard = (visibleSeries, breakdowns) => ({
  id: "dashboard",
  name: "Spend",
  widgets: [
    {
      id: "saved-widget",
      name: "Cost by project",
      query_config: {
        project_ids: [],
        time_range: { preset: "7D" },
        granularity: "day",
        metrics: [costFor(PROJECT_A), costFor(PROJECT_B)],
        filters: [],
        breakdowns,
      },
      chart_config: { chart_type: "line", visible_series: visibleSeries },
    },
  ],
});

const complete = {
  query_complete: true,
  query_status: "complete",
  query_sampled: false,
};
const costResult = (value, buckets) => ({
  ...complete,
  id: "cost",
  name: "Cost",
  aggregation: "sum",
  unit: "$",
  series: buckets.map((name) => ({
    name,
    data: [{ timestamp: "2026-11-09T00:00:00Z", value }],
  })),
});
// Project A spent $10 and project B $100; each copy reports its own buckets.
const responseWith = (bucketsA, bucketsB) => ({
  data: {
    result: {
      ...complete,
      metrics: [costResult(10, bucketsA), costResult(100, bucketsB)],
    },
  },
});

// Reopen the saved widget, deliver its preview, press Save, and return the
// selection the editor wrote.
async function reopenAndSave({
  visibleSeries,
  breakdowns = [],
  bucketsA,
  bucketsB,
}) {
  h.dashboard = savedDashboard(visibleSeries, breakdowns);
  render(<WidgetEditorView />);
  await waitFor(() => expect(h.query.mutate).toHaveBeenCalledOnce(), {
    timeout: 2500,
  });
  act(() =>
    h.query.mutate.mock.calls[0][1].onSuccess(responseWith(bucketsA, bucketsB)),
  );
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await waitFor(() => expect(h.write.mutateAsync).toHaveBeenCalledOnce());
  const [{ widgetId, data }] = h.write.mutateAsync.mock.calls[0];
  expect(widgetId).toBe("saved-widget");
  return data.chart_config.visible_series;
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(
    {},
    "",
    "/dashboard/dashboards/dashboard/widgets/saved-widget",
  );
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

describe("WidgetEditor save with a repeated metric (W10-DASHBOARDS-L06)", () => {
  it("keeps project A when the copies return different breakdown buckets", async () => {
    // Before the fix cost|sum|gpt-4o named A's only series; B has no gpt-4o.
    const saved = await reopenAndSave({
      visibleSeries: ["cost|sum|gpt-4o"],
      breakdowns: [MODEL_BREAKDOWN],
      bucketsA: ["gpt-4o"],
      bucketsB: ["gpt-4o-mini"],
    });

    expect(saved).toEqual(["cost|sum#1|gpt-4o"]);
  });

  it("keeps project B for a shared total key saved before the fix", async () => {
    // Both copies report "total"; the dashboard always showed B for this key.
    const saved = await reopenAndSave({
      visibleSeries: ["cost|sum|total"],
      bucketsA: ["total"],
      bucketsB: ["total"],
    });

    expect(saved).toEqual(["cost|sum|total"]);
  });
});
