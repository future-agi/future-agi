import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "src/utils/test-utils";
import { DATE_PRESETS } from "../constants";
import WidgetEditorView from "../WidgetEditorView";

// L02: at 1024 px the widget editor's preview column is narrower than the
// ten time-range pills. The group used to refuse to shrink or wrap, so it ran
// past the column edge and 3M, 6M and 12M were clipped out of view. The group
// must wrap its pills and stay within the column.

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

vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
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
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => h.params,
  useNavigate: () => vi.fn(),
}));
vi.mock("src/contexts/WorkspaceContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspace: () => ({ currentWorkspaceId: "workspace" }),
}));
vi.mock("../hooks/useCanEditDashboard", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({ canDelete: true, isReadOnly: false }),
}));
vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
// Every widget editor test stubs the chart library outright; loading the real
// apexcharts module in jsdom is not needed to check the toolbar layout.
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("src/components/filter-value-label", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
  useResolvedFilterOptions: () => ({ options: [] }),
  shouldShowFilterValueContinuation: () => false,
}));
vi.mock(
  "src/sections/projects/LLMTracing/useCursorAttributeInventory",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useLegacyCursorAttributeInventory: () => h.inventory,
    attributeInventoryKey: ({ key, type }) => `${key}:${type}`,
  }),
);

beforeEach(() => {
  vi.clearAllMocks();
  h.params = { dashboardId: "dashboard", widgetId: "new" };
  h.dashboard = { id: "dashboard", name: "Layout dashboard", widgets: [] };
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

describe("WidgetEditor time-range pills at narrow widths (L02)", () => {
  it("wraps the pill group inside the preview column instead of clipping it", () => {
    render(<WidgetEditorView />);

    const range = screen.getByRole("group", { name: "Time range" });
    const style = window.getComputedStyle(range);

    expect(style.flexWrap).toBe("wrap");
    expect(style.flexShrink).toBe("1");
    expect(style.maxWidth).toBe("100%");
  });

  it("keeps every time-range pill in the group", () => {
    render(<WidgetEditorView />);

    const range = screen.getByRole("group", { name: "Time range" });
    for (const preset of DATE_PRESETS) {
      expect(
        within(range).getByRole("button", { name: preset.label }),
      ).toBeInTheDocument();
    }
  });
});
