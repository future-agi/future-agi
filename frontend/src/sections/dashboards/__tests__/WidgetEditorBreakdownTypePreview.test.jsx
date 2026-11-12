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

// W10-DASHBOARDS-L07: changing a breakdown's storage type (same attribute,
// string -> number or boolean) cleared the preview without querying again,
// because the auto-preview only watched breakdown IDs. Harness mirrors
// WidgetEditorSavedQuery.test.jsx: only the data hooks are mocked, the
// editor's real initialization, debounce and query builder run.
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
vi.mock("src/components/filter-value-label", () => ({
  default: () => null,
  useResolvedFilterOptions: () => ({ options: [] }),
  shouldShowFilterValueContinuation: () => false,
}));
vi.mock("src/sections/projects/LLMTracing/useCursorAttributeInventory", () => ({
  useLegacyCursorAttributeInventory: () => h.inventory,
  attributeInventoryKey: ({ key, type }) => `${key}:${type}`,
}));

const freeze = (value) => {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
};

const queryWith = (metric, breakdowns = []) => ({
  project_ids: [],
  time_range: { preset: "7D" },
  granularity: "day",
  metrics: [metric],
  filters: [],
  breakdowns,
});

async function captureReopen(queryConfig) {
  const original = structuredClone(queryConfig);
  h.dashboard = freeze({
    id: "dashboard",
    name: "Saved identities",
    widgets: [
      {
        id: "saved-widget",
        name: "Saved identity widget",
        query_config: queryConfig,
        chart_config: { chart_type: "table" },
      },
    ],
  });
  render(<WidgetEditorView />);
  await waitFor(() => expect(h.query.mutate).toHaveBeenCalledOnce(), {
    timeout: 2500,
  });
  expect(h.write.mutate).not.toHaveBeenCalled();
  expect(queryConfig).toEqual(original);
  const [request] = h.query.mutate.mock.calls[0];
  expect(request.refresh).toBe(false);
  expect(request.signal).toBeInstanceOf(AbortSignal);
  return request.queryConfig;
}

beforeEach(() => {
  vi.clearAllMocks();
  h.catalog.metrics = [];
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

describe("WidgetEditor typed breakdown auto-preview (W10-DASHBOARDS-L07)", () => {
  it.each(["number", "boolean"])(
    "automatically queries a same-ID breakdown replacement from string to %s",
    async (attributeType) => {
      h.catalog.metrics = [
        {
          name: "region",
          display_name: "Region",
          property_id: "custom_attribute:traces:region",
          category: "custom_attribute",
          source: "traces",
          type: "json",
          attribute_types: ["string", "number", "boolean"],
          attribute_types_exact: true,
        },
      ];
      const initialQuery = await captureReopen(
        queryWith(
          {
            id: "latency",
            name: "latency",
            display_name: "Latency",
            type: "system_metric",
            source: "traces",
            aggregation: "avg",
          },
          [
            {
              name: "region",
              property_id: "custom_attribute:traces:region",
              display_name: "Region",
              type: "custom_attribute",
              source: "traces",
              attribute_type: "string",
            },
          ],
        ),
      );
      const complete = {
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
      };
      const responseFor = (name) => ({
        data: {
          result: {
            ...complete,
            metrics: [
              {
                ...complete,
                name: "latency",
                aggregation: "avg",
                unit: "ms",
                series: [
                  {
                    name,
                    data: [{ timestamp: "2026-11-09T12:00:00Z", value: 120 }],
                  },
                ],
              },
            ],
          },
        },
      });
      act(() =>
        h.query.mutate.mock.calls[0][1].onSuccess(responseFor("eu-west")),
      );
      expect(screen.getAllByText("eu-west").length).toBeGreaterThan(0);

      // The real picker replaces the breakdown without changing its ID. Keep
      // mutation/reset handles stable so they cannot mask a missing dependency.
      fireEvent.click(screen.getByText("Region"));
      fireEvent.click(
        screen.getByRole("button", {
          name: `Region (${attributeType}, Traces)`,
        }),
      );
      expect(h.query.mutate).toHaveBeenCalledOnce();
      await waitFor(() => expect(h.query.mutate).toHaveBeenCalledTimes(2), {
        timeout: 2500,
      });
      const [replacement, handlers] = h.query.mutate.mock.calls[1];
      expect(replacement.queryConfig).toEqual({
        ...initialQuery,
        breakdowns: [
          { ...initialQuery.breakdowns[0], attribute_type: attributeType },
        ],
      });
      expect(replacement.refresh).toBe(false);
      expect(replacement.signal).toBeInstanceOf(AbortSignal);

      const selectedValue = attributeType === "number" ? "7" : "true";
      act(() => handlers.onSuccess(responseFor(selectedValue)));
      expect(screen.getAllByText(selectedValue).length).toBeGreaterThan(0);
      expect(screen.queryByText("eu-west")).not.toBeInTheDocument();
      expect(h.write.mutate).not.toHaveBeenCalled();
    },
  );
});
