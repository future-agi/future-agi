import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import WidgetEditorView from "../WidgetEditorView";

// W10-DASHBOARDS-U04: Close and the breadcrumbs threw away an edited widget
// with no prompt. Harness mirrors WidgetEditorSavedQuery.test.jsx: only the
// data hooks are mocked, the editor's real initialization runs.
const h = vi.hoisted(() => ({
  dashboard: null,
  navigate: vi.fn(),
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
  useParams: () => ({ dashboardId: "dashboard", widgetId: "saved-widget" }),
  useNavigate: () => h.navigate,
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

const DASHBOARD_URL = "/dashboard/dashboards/dashboard";

const SAVED_WIDGET = {
  id: "saved-widget",
  name: "Latency by model",
  query_config: {
    project_ids: [],
    time_range: { preset: "7D" },
    granularity: "day",
    metrics: [
      {
        id: "trace_count",
        name: "trace_count",
        property_id: "system_attribute:traces:trace_count",
        display_name: "Traces",
        type: "system_metric",
        source: "traces",
        aggregation: "count_distinct",
      },
    ],
    filters: [],
    breakdowns: [],
  },
  chart_config: { chart_type: "line" },
};

async function openSavedWidget() {
  render(<WidgetEditorView />);
  // The saved name only shows once the editor has loaded the widget.
  await screen.findByText("Latency by model");
}

function renameWidget(name) {
  fireEvent.click(screen.getByText("Latency by model"));
  const input = screen.getByPlaceholderText("Untitled widget");
  fireEvent.change(input, { target: { value: name } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(screen.getByText(name)).toBeInTheDocument();
}

function dispatchBeforeUnload() {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event;
}

beforeEach(() => {
  vi.clearAllMocks();
  h.dashboard = {
    id: "dashboard",
    name: "Saved dash",
    widgets: [SAVED_WIDGET],
  };
  window.history.replaceState(
    {},
    "",
    "/dashboard/dashboards/dashboard/widget/saved-widget",
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

describe("WidgetEditorView — unsaved changes (W10-DASHBOARDS-U04)", () => {
  it("leaves at once when nothing was changed", async () => {
    await openSavedWidget();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(h.navigate).toHaveBeenCalledWith(DASHBOARD_URL);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(dispatchBeforeUnload().defaultPrevented).toBe(false);
  });

  it("asks before Close discards an edited widget, and leaves on Discard", async () => {
    await openSavedWidget();
    renameWidget("Latency p95");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Discard unsaved changes?"),
    ).toBeInTheDocument();
    expect(h.navigate).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Discard" }));
    expect(h.navigate).toHaveBeenCalledWith(DASHBOARD_URL);
    expect(h.write.mutate).not.toHaveBeenCalled();
    expect(h.write.mutateAsync).not.toHaveBeenCalled();
  });

  it("Keep editing stays in the editor with the changes intact", async () => {
    await openSavedWidget();
    renameWidget("Latency p95");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Keep editing",
      }),
    );

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(h.navigate).not.toHaveBeenCalled();
    expect(screen.getByText("Latency p95")).toBeInTheDocument();
  });

  it("asks before either breadcrumb discards an edited widget", async () => {
    await openSavedWidget();
    renameWidget("Latency p95");

    fireEvent.click(screen.getByText("Saved dash"));
    expect(
      within(screen.getByRole("dialog")).getByText("Discard unsaved changes?"),
    ).toBeInTheDocument();
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Keep editing",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    fireEvent.click(screen.getByText("Dashboards"));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Discard unsaved changes?"),
    ).toBeInTheDocument();
    expect(h.navigate).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Discard" }));
    expect(h.navigate).toHaveBeenCalledWith("/dashboard/dashboards");
  });

  it("asks the browser to confirm a reload or tab close while edited", async () => {
    await openSavedWidget();
    renameWidget("Latency p95");

    expect(dispatchBeforeUnload().defaultPrevented).toBe(true);
  });
});
