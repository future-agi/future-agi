import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import WidgetEditorView from "../WidgetEditorView";

const h = vi.hoisted(() => ({
  dashboard: { id: "dashboard", name: "Picker states", widgets: [] },
  query: { mutate: vi.fn(), reset: vi.fn() },
  write: { mutate: vi.fn() },
  catalog: null,
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
  useDuplicateWidget: () => h.write,
  useSimulationAgents: () => h.agents,
  usePropertyCatalog: () => h.catalog,
  useLegacyDashboardMetricsPaginated: () => h.catalog,
  isPropertyCatalogNotReadyError: () => false,
}));
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useParams: () => ({ dashboardId: "dashboard", widgetId: "new" }),
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

const METRICS_FAILED =
  "Couldn't load metrics. Check your connection and try again.";
const ATTRIBUTES_FAILED =
  "Couldn't load attributes. Check your connection and try again.";
const NO_METRICS =
  "No metrics yet. Send traces or run an eval to populate this list.";

// The settled shape usePropertyCatalog returns for one catalog read.
const catalogState = (overrides = {}) => ({
  metrics: [],
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null,
  isFetchNextPageError: false,
  isFetchingNextPage: false,
  isPlaceholderData: false,
  isRemoteCatalogSearchPending: false,
  cursorChainStopped: false,
  legacyFallbackRequired: false,
  queryReadState: "complete",
  categoryCounts: null,
  categoryCountsExact: false,
  hasNextPage: false,
  continuationKey: null,
  fetchNextPage: vi.fn(),
  refetch: vi.fn(),
  ...overrides,
});

// GET /tracer/dashboard/metrics/ timed out: retry is off and the global
// toast is suppressed, so the picker is the only place the user can learn it.
const failedCatalog = () =>
  catalogState({
    isError: true,
    error: Object.assign(new Error("Request failed with status code 504"), {
      statusCode: 504,
    }),
    queryReadState: "degraded",
  });

const openMetricPicker = () => {
  render(<WidgetEditorView />);
  fireEvent.click(screen.getByText("Select Metric"));
};

const openFilterPicker = () => {
  render(<WidgetEditorView />);
  fireEvent.click(
    screen.getByText("Filter", { selector: ".filter-section-title" }),
  );
};

const alertFor = async (copy) => {
  const message = await screen.findByText(copy);
  const alert = message.closest('[role="alert"]');
  expect(alert).not.toBeNull();
  return alert;
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(
    {},
    "",
    "/dashboard/dashboards/dashboard/widgets/new",
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

describe("WidgetEditor metric picker load states (W10-DASHBOARDS-U05)", () => {
  it("says the metric list failed to load and retries the catalog read", async () => {
    h.catalog = failedCatalog();
    openMetricPicker();

    const alert = await alertFor(METRICS_FAILED);
    expect(screen.queryByText("No attributes found")).toBeNull();
    expect(screen.queryByText(/504/)).toBeNull();

    const retry = within(alert).getByRole("button", { name: "Retry" });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    expect(h.catalog.refetch).toHaveBeenCalledTimes(1);
  });

  it("disables Retry while the catalog read is running again", async () => {
    h.catalog = { ...failedCatalog(), isFetching: true };
    openMetricPicker();

    const alert = await alertFor(METRICS_FAILED);
    expect(
      within(alert).getByRole("button", { name: "Retrying…" }),
    ).toBeDisabled();
  });

  it("treats a stopped catalog walk with no rows as a failed load", async () => {
    h.catalog = catalogState({
      cursorChainStopped: true,
      queryReadState: "degraded",
    });
    openMetricPicker();

    await alertFor(METRICS_FAILED);
    expect(screen.queryByText("No attributes found")).toBeNull();
  });

  it("names metrics, not attributes, when the catalog has none", async () => {
    h.catalog = catalogState();
    openMetricPicker();

    expect(await screen.findByText(NO_METRICS)).toBeInTheDocument();
    expect(screen.queryByText("No attributes found")).toBeNull();
    expect(screen.queryByText(METRICS_FAILED)).toBeNull();
  });

  it("echoes the search when no metric matches it", async () => {
    h.catalog = catalogState();
    openMetricPicker();

    fireEvent.change(screen.getByPlaceholderText("Search metrics..."), {
      target: { value: "  latency  " },
    });

    expect(
      await screen.findByText('No metrics match "latency"'),
    ).toBeInTheDocument();
    expect(screen.queryByText(NO_METRICS)).toBeNull();
  });

  it("keeps attribute wording in the filter picker", async () => {
    h.catalog = catalogState();
    openFilterPicker();
    expect(await screen.findByText("No attributes found")).toBeInTheDocument();
    cleanup();

    h.catalog = failedCatalog();
    openFilterPicker();
    const alert = await alertFor(ATTRIBUTES_FAILED);
    expect(screen.queryByText("No attributes found")).toBeNull();
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(h.catalog.refetch).toHaveBeenCalledTimes(1);
  });
});
