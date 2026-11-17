import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import WidgetEditorView from "../WidgetEditorView";

// W10-DASHBOARDS-U07: icon-only buttons in the widget editor need an
// accessible name, and actions revealed on hover must also show when keyboard
// focus is inside their card. Harness mirrors WidgetEditorSavedQuery.test.jsx.
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

const PROJECT_FILTER = {
  column_id: "project",
  property_id: "system_attribute:traces:project",
  display_name: "Project",
  source: "traces",
  output_type: "string",
  filter_config: {
    filter_type: "text",
    filter_op: "in",
    filter_value: ["a407da87-7a8c-4e8f-9605-6998b7a69470"],
    col_type: "SYSTEM_METRIC",
  },
};
const MODEL_FILTER = {
  column_id: "model",
  property_id: "system_attribute:traces:model",
  display_name: "Model",
  source: "traces",
  output_type: "string",
  filter_config: {
    filter_type: "text",
    filter_op: "in",
    filter_value: ["gpt-4o"],
    col_type: "SYSTEM_METRIC",
  },
};
const REGION_BREAKDOWN = {
  name: "region",
  property_id: "custom_attribute:traces:region",
  display_name: "Region",
  type: "custom_attribute",
  source: "traces",
  attribute_type: "string",
};

const queryWith = (metric, breakdowns = []) => ({
  project_ids: [],
  time_range: { preset: "7D" },
  granularity: "day",
  metrics: [metric],
  filters: [],
  breakdowns,
});

// jsdom cannot paint opacity and only matches :focus-within on the focused
// element itself, so read the emitted rule instead (as the sandbox
// KeyboardFocus test does): the opacity a container sets for its hidden
// `.${hiddenClass}` controls while it is in `state`.
function revealOpacity(control, hiddenClass, state) {
  const selectors = [];
  for (let node = control; node; node = node.parentElement) {
    for (const name of node.classList) {
      selectors.push(`.${name}${state} .${hiddenClass}`);
    }
  }
  const rule = Array.from(document.styleSheets)
    .flatMap((sheet) => Array.from(sheet.cssRules))
    .find((candidate) =>
      candidate.selectorText
        ?.split(",")
        .some((part) => selectors.includes(part.trim().replace(/\s+/g, " "))),
    );
  return rule?.style.getPropertyValue("opacity");
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

describe("WidgetEditor icon buttons (W10-DASHBOARDS-U07)", () => {
  const FIND = { timeout: 5000 };

  // One saved metric ("Latency") carrying one per-metric filter ("Project"),
  // plus any widget-level filters and breakdowns the case adds.
  const renderSavedWidget = ({ filters = [], breakdowns = [] } = {}) => {
    h.dashboard = {
      id: "dashboard",
      name: "Saved identities",
      widgets: [
        {
          id: "saved-widget",
          name: "Latency widget",
          query_config: {
            ...queryWith(
              {
                id: "latency",
                name: "latency",
                display_name: "Latency",
                property_id: "system_attribute:traces:latency",
                type: "system_metric",
                source: "traces",
                aggregation: "avg",
                filters: [PROJECT_FILTER],
              },
              breakdowns,
            ),
            filters,
          },
          chart_config: { chart_type: "table" },
        },
      ],
    };
    render(<WidgetEditorView />);
  };

  it("names the top bar's more-actions button", async () => {
    renderSavedWidget();
    expect(
      await screen.findByRole("button", { name: "More widget actions" }, FIND),
    ).toBeEnabled();
  });

  it("names the remove buttons after the metric and filter they remove", async () => {
    renderSavedWidget();
    expect(
      await screen.findByRole(
        "button",
        { name: "Remove metric Latency" },
        FIND,
      ),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Remove filter Project" }),
    ).toBeEnabled();
  });

  it("shows a metric's hover actions when focus is inside the metric", async () => {
    renderSavedWidget();
    const addFilter = await screen.findByRole(
      "button",
      { name: "Add filter to this metric" },
      FIND,
    );
    // Pointer users see the actions on hover...
    expect(revealOpacity(addFilter, "metric-hover-action", ":hover")).toBe("1");
    // ...and Tab must not land on an invisible button.
    expect(
      revealOpacity(addFilter, "metric-hover-action", ":focus-within"),
    ).toBe("1");
  });

  it("names the widget filter and breakdown remove buttons", async () => {
    renderSavedWidget({
      filters: [MODEL_FILTER],
      breakdowns: [REGION_BREAKDOWN],
    });
    expect(
      await screen.findByRole("button", { name: "Remove filter Model" }, FIND),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Remove breakdown Region" }),
    ).toBeEnabled();
  });

  it("shows the filter and breakdown remove buttons when focus is inside their row", async () => {
    renderSavedWidget({
      filters: [MODEL_FILTER],
      breakdowns: [REGION_BREAKDOWN],
    });
    // Found by class, not by name, so this case is about the reveal alone.
    await screen.findByText("Latency widget", {}, FIND);
    const removeFilter = document.querySelector("button.filter-hover-action");
    const removeBreakdown = document.querySelector(
      "button.breakdown-hover-action",
    );
    expect(removeFilter).not.toBeNull();
    expect(removeBreakdown).not.toBeNull();
    expect(revealOpacity(removeFilter, "filter-hover-action", ":hover")).toBe(
      "1",
    );
    expect(
      revealOpacity(removeFilter, "filter-hover-action", ":focus-within"),
    ).toBe("1");
    expect(
      revealOpacity(removeBreakdown, "breakdown-hover-action", ":hover"),
    ).toBe("1");
    expect(
      revealOpacity(removeBreakdown, "breakdown-hover-action", ":focus-within"),
    ).toBe("1");
  });
});
