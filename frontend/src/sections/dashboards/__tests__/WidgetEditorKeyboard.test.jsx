import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  userEvent,
  within,
} from "src/utils/test-utils";
import WidgetEditorView from "../WidgetEditorView";

// W10-DASHBOARDS-U01: the widget editor's openers (Metric / Filter /
// Breakdown, the empty metric slot, a chosen metric's name) and the time-range
// pills must work with Tab, Enter and Space, or a keyboard user cannot add the
// metric that Save requires.

const h = vi.hoisted(() => ({
  params: { dashboardId: "dashboard", widgetId: "new" },
  dashboard: null,
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

const TRACES_OPTION = {
  name: "trace_count",
  display_name: "Traces",
  property_id: "system_attribute:traces:trace_count",
  category: "system_metric",
  source: "traces",
};

const savedMetric = (name, displayName) => ({
  name,
  display_name: displayName,
  property_id: `system_attribute:traces:${name}`,
  type: "system_metric",
  source: "traces",
  aggregation: "avg",
});

const FIVE_METRICS = [
  savedMetric("latency", "Latency"),
  savedMetric("trace_count", "Traces"),
  savedMetric("cost", "Cost"),
  savedMetric("total_tokens", "Tokens"),
  savedMetric("error_count", "Errors"),
];

// Move focus with Tab only, as a keyboard user does, until it reaches the
// target. Bounded, so a control that is not a tab stop fails the test.
async function tabTo(user, target, maxSteps = 150) {
  for (let step = 0; step < maxSteps; step += 1) {
    if (document.activeElement === target) break;
    await user.tab();
  }
  expect(target).toHaveFocus();
}

beforeEach(() => {
  vi.clearAllMocks();
  h.params = { dashboardId: "dashboard", widgetId: "new" };
  h.dashboard = { id: "dashboard", name: "Keyboard dashboard", widgets: [] };
  h.catalog.metrics = [TRACES_OPTION];
  window.history.replaceState(
    {},
    "",
    "/dashboard/dashboards/dashboard/widget/new",
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

describe("WidgetEditor keyboard access (W10-DASHBOARDS-U01)", () => {
  it("adds a metric with Tab and Enter only", async () => {
    const user = userEvent.setup();
    render(<WidgetEditorView />);

    const addMetric = screen.getByRole("button", { name: "Add metric" });
    expect(addMetric).not.toHaveAttribute("aria-disabled", "true");
    await tabTo(user, addMetric);
    await user.keyboard("{Enter}");

    expect(
      await screen.findByPlaceholderText("Search metrics..."),
    ).toBeInTheDocument();
    const option = await screen.findByRole("button", {
      name: "Traces (Traces)",
    });
    await tabTo(user, option);
    await user.keyboard("{Enter}");

    expect(
      await screen.findByRole("button", { name: "Change metric Traces" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Select Metric")).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("Search metrics..."),
    ).not.toBeInTheDocument();
  });

  it("opens the metric picker from the empty metric slot with Enter", async () => {
    const user = userEvent.setup();
    render(<WidgetEditorView />);

    const slot = screen.getByRole("button", { name: "Select Metric" });
    await tabTo(user, slot);
    await user.keyboard("{Enter}");

    expect(
      await screen.findByPlaceholderText("Search metrics..."),
    ).toBeInTheDocument();
  });

  it.each([
    ["Add filter", "Search filter attributes..."],
    ["Add breakdown", "Search breakdown attributes..."],
  ])("opens the %s picker with Space", async (name, placeholder) => {
    const user = userEvent.setup();
    render(<WidgetEditorView />);

    const opener = screen.getByRole("button", { name });
    await tabTo(user, opener);
    await user.keyboard("[Space]");

    expect(await screen.findByPlaceholderText(placeholder)).toBeInTheDocument();
  });

  it("changes the time range with Tab, Enter and Space", async () => {
    const user = userEvent.setup();
    render(<WidgetEditorView />);

    const range = screen.getByRole("group", { name: "Time range" });
    const thirtyDays = within(range).getByRole("button", { name: "30D" });
    const sevenDays = within(range).getByRole("button", { name: "7D" });
    const threeMonths = within(range).getByRole("button", { name: "3M" });
    expect(thirtyDays).toHaveAttribute("aria-pressed", "true");
    expect(sevenDays).toHaveAttribute("aria-pressed", "false");

    await tabTo(user, sevenDays);
    await user.keyboard("{Enter}");
    expect(sevenDays).toHaveAttribute("aria-pressed", "true");
    expect(thirtyDays).toHaveAttribute("aria-pressed", "false");

    await tabTo(user, threeMonths);
    await user.keyboard("[Space]");
    expect(threeMonths).toHaveAttribute("aria-pressed", "true");
    expect(sevenDays).toHaveAttribute("aria-pressed", "false");
  });

  it("keeps Add metric focusable but unavailable at five metrics, and changes a metric from the keyboard", async () => {
    h.params = { dashboardId: "dashboard", widgetId: "saved-widget" };
    h.dashboard = {
      id: "dashboard",
      name: "Keyboard dashboard",
      widgets: [
        {
          id: "saved-widget",
          name: "Five metrics",
          query_config: {
            project_ids: [],
            time_range: { preset: "7D" },
            granularity: "day",
            metrics: FIVE_METRICS,
            filters: [],
            breakdowns: [],
          },
          chart_config: { chart_type: "line" },
        },
      ],
    };
    window.history.replaceState(
      {},
      "",
      "/dashboard/dashboards/dashboard/widgets/saved-widget",
    );
    const user = userEvent.setup();
    render(<WidgetEditorView />);

    const changeLatency = await screen.findByRole("button", {
      name: "Change metric Latency",
    });
    const addMetric = screen.getByRole("button", { name: "Add metric" });
    expect(addMetric).toHaveAttribute("aria-disabled", "true");
    await tabTo(user, addMetric);
    await user.keyboard("{Enter}");
    expect(
      screen.queryByPlaceholderText("Search metrics..."),
    ).not.toBeInTheDocument();

    await tabTo(user, changeLatency);
    await user.keyboard("{Enter}");
    expect(
      await screen.findByPlaceholderText("Search metrics..."),
    ).toBeInTheDocument();
  });
});
