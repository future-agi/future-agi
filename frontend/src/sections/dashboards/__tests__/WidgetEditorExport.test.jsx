import React from "react";
import { format } from "date-fns";
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

// W10-DASHBOARDS-U10: the widget editor's Export CSV / Copy menu items did
// nothing without preview data, headed every sub-day column with the same
// date, and toasted "Copied" even when the clipboard refused the write.
// Harness mirrors WidgetEditorSavedQuery.test.jsx: only the data hooks are
// mocked, the editor's real initialization, debounce and series state run.
const h = vi.hoisted(() => ({
  dashboard: null,
  query: { mutate: vi.fn(), reset: vi.fn() },
  write: { mutate: vi.fn(), mutateAsync: vi.fn().mockResolvedValue({}) },
  enqueueSnackbar: vi.fn(),
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
  useSnackbar: () => ({ enqueueSnackbar: h.enqueueSnackbar }),
}));
// Render the icon name so the "..." menu button can be found without relying
// on an accessible name the button did not have before this fix.
vi.mock("src/components/iconify", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ icon }) => <span data-icon={icon} />,
}));
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

const TEN = "2026-11-09T10:00:00Z";
const ELEVEN = "2026-11-09T11:00:00Z";

const savedDashboard = (preset, granularity) => ({
  id: "dashboard",
  name: "Export dashboard",
  widgets: [
    {
      id: "saved-widget",
      name: "Latency export",
      query_config: {
        project_ids: [],
        time_range: { preset },
        granularity,
        metrics: [
          {
            id: "latency",
            name: "latency",
            display_name: "Latency",
            property_id: "system_attribute:traces:latency",
            type: "system_metric",
            source: "traces",
            aggregation: "avg",
          },
        ],
        filters: [],
        breakdowns: [],
      },
      chart_config: { chart_type: "table" },
    },
  ],
});

const complete = {
  query_complete: true,
  query_status: "complete",
  query_sampled: false,
};
const responseWith = (data) => ({
  data: {
    result: {
      ...complete,
      metrics: [
        {
          ...complete,
          name: "latency",
          aggregation: "avg",
          unit: "ms",
          series: data ? [{ name: "total", data }] : [],
        },
      ],
    },
  },
});
const TWO_HOURS = [
  { timestamp: TEN, value: 120 },
  { timestamp: ELEVEN, value: 80 },
];

async function openWithPreview(preset, granularity, data) {
  h.dashboard = savedDashboard(preset, granularity);
  render(<WidgetEditorView />);
  await waitFor(() => expect(h.query.mutate).toHaveBeenCalledOnce(), {
    timeout: 2500,
  });
  act(() => h.query.mutate.mock.calls[0][1].onSuccess(responseWith(data)));
}

function openMoreMenu() {
  const dots = document.querySelector('[data-icon="solar:menu-dots-linear"]');
  fireEvent.click(dots.closest("button"));
}

const copyItem = () => screen.getByRole("menuitem", { name: /^Copy/ });

let exported;
let originalCreateObjectURL;
let originalRevokeObjectURL;
let originalClipboard;

beforeEach(() => {
  vi.clearAllMocks();
  exported = [];
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
  // Capture the file the export would download instead of downloading it.
  originalCreateObjectURL = URL.createObjectURL;
  originalRevokeObjectURL = URL.revokeObjectURL;
  URL.createObjectURL = vi.fn(() => "blob:widget-export");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  URL.createObjectURL = originalCreateObjectURL;
  URL.revokeObjectURL = originalRevokeObjectURL;
  if (originalClipboard) {
    Object.defineProperty(navigator, "clipboard", originalClipboard);
  } else {
    delete navigator.clipboard;
  }
});

function captureBlobs() {
  vi.stubGlobal(
    "Blob",
    class {
      constructor(parts, options) {
        this.text = parts.join("");
        this.type = options?.type;
        exported.push(this);
      }
    },
  );
}

function stubClipboard(writeText) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
}

describe("WidgetEditor export (W10-DASHBOARDS-U10)", () => {
  it.each([
    ["today", "hour"],
    ["30m", "minute"],
  ])(
    "heads each %s/%s Export CSV column with its own date and time",
    async (preset, granularity) => {
      await openWithPreview(preset, granularity, TWO_HOURS);
      openMoreMenu();
      captureBlobs();
      fireEvent.click(screen.getByRole("menuitem", { name: "Export CSV" }));

      expect(exported).toHaveLength(1);
      expect(exported[0].type).toBe("text/csv");
      const [header, row] = exported[0].text
        .split("\n")
        .map((line) => line.split(","));
      expect(header.slice(2)).toEqual([
        format(new Date(TEN), "yyyy-MM-dd HH:mm"),
        format(new Date(ELEVEN), "yyyy-MM-dd HH:mm"),
      ]);
      expect(new Set(header.slice(2)).size).toBe(2);
      expect(row.slice(2)).toEqual(["120", "80"]);
    },
  );

  it("keeps day columns headed by date only", async () => {
    const days = [
      { timestamp: "2026-11-02T12:00:00Z", value: 1 },
      { timestamp: "2026-11-03T12:00:00Z", value: 2 },
    ];
    await openWithPreview("7D", "day", days);
    openMoreMenu();
    captureBlobs();
    fireEvent.click(screen.getByRole("menuitem", { name: "Export CSV" }));

    const [header] = exported[0].text.split("\n").map((l) => l.split(","));
    expect(header.slice(2)).toEqual([
      format(new Date(days[0].timestamp), "yyyy-MM-dd"),
      format(new Date(days[1].timestamp), "yyyy-MM-dd"),
    ]);
  });

  it("offers neither export while the preview has no data", async () => {
    await openWithPreview("7D", "day", null);
    openMoreMenu();

    expect(
      screen.getByRole("menuitem", { name: "Export CSV" }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(copyItem()).toHaveAttribute("aria-disabled", "true");
  });

  it("says the copy is tab-separated, and confirms it only once the clipboard took it", async () => {
    let finishWrite;
    const writeText = vi.fn(
      () =>
        new Promise((resolve) => {
          finishWrite = resolve;
        }),
    );
    stubClipboard(writeText);
    await openWithPreview("today", "hour", TWO_HOURS);
    openMoreMenu();
    expect(copyItem()).toHaveTextContent("Copy as table (TSV)");
    fireEvent.click(copyItem());

    expect(writeText).toHaveBeenCalledOnce();
    const [header, row] = writeText.mock.calls[0][0]
      .split("\n")
      .map((line) => line.split("\t"));
    expect(header.slice(2)).toEqual([
      format(new Date(TEN), "yyyy-MM-dd HH:mm"),
      format(new Date(ELEVEN), "yyyy-MM-dd HH:mm"),
    ]);
    expect(row.slice(2)).toEqual(["120", "80"]);
    // Nothing is confirmed while the browser is still deciding.
    expect(h.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Copied to clipboard",
      expect.anything(),
    );

    await act(async () => finishWrite());
    expect(h.enqueueSnackbar).toHaveBeenCalledWith("Copied to clipboard", {
      variant: "success",
    });
    expect(h.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Couldn't copy to clipboard",
      expect.anything(),
    );
  });

  it("says the copy failed when the clipboard refuses it", async () => {
    const writeText = vi.fn(() =>
      Promise.reject(new DOMException("Denied", "NotAllowedError")),
    );
    stubClipboard(writeText);
    await openWithPreview("today", "hour", TWO_HOURS);
    openMoreMenu();
    fireEvent.click(copyItem());

    await waitFor(() =>
      expect(h.enqueueSnackbar).toHaveBeenCalledWith(
        "Couldn't copy to clipboard",
        { variant: "error" },
      ),
    );
    expect(h.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Copied to clipboard",
      expect.anything(),
    );
  });
});
