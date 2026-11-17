import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import { normalizeAlertDetail } from "../common";
import AlertConfiguration from "../components/AlertConfiguration";
import AlertsSheetView from "../components/AlertsSheetView/AlertsSheetView";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// A service provider error rate alert's chart names the share its line draws.
// An alert kept on the earlier rule draws the share of providers with no
// errors, in the sheet and in the edit drawer's saved graph. A preview of an
// edit is computed on the current rule, the share with errors. Until that
// preview arrives, or while an invalid edit runs no preview, the drawer still
// draws the saved line and keeps its name. The y-axis names a share from 0
// to 1, the scale the thresholds are typed on.

const mocks = vi.hoisted(() => ({ chart: vi.fn(() => null) }));

vi.mock("../components/AlertsChart", () => ({ default: mocks.chart }));
// The drawer's form, reduced to the one thing the chart reads from it: an
// edit, which makes the form dirty and asks for a preview. An invalid edit
// (critical below warning on Above) makes the form dirty with the preview
// off, as the real form does for as long as its thresholds are invalid.
vi.mock("../components/AlertSettingsForm", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ setFormIsDirty, onPayloadChange }) => (
    <>
      <button
        type="button"
        data-testid="edit-alert-form"
        onClick={() => {
          setFormIsDirty(true);
          onPayloadChange(
            {
              metric_type: "service_provider_error_rates",
              threshold_type: "static",
            },
            true,
            false,
          );
        }}
      >
        Edit
      </button>
      <button
        type="button"
        data-testid="edit-alert-form-invalid"
        onClick={() => {
          setFormIsDirty(true);
          onPayloadChange(
            {
              metric_type: "service_provider_error_rates",
              threshold_type: "static",
              threshold_operator: "greater_than",
              critical_threshold_value: 0.1,
              warning_threshold_value: 0.2,
            },
            false,
            false,
          );
        }}
      >
        Edit with critical below warning
      </button>
    </>
  ),
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: vi.fn(),
}));
vi.mock("src/components/svg-color", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock(
  "src/sections/projects/LLMTracing/TracingControls",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock("../components/DuplicateALert", () => ({ default: () => null }));
vi.mock("../components/AlertsSheetView/Issues", () => ({
  default: () => null,
}));
vi.mock("../components/AlertsSheetView/AlertDetails", () => ({
  default: () => null,
}));
vi.mock("../useMuteAlerMutation", () => ({
  useMuteAlertsMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The sheet's back button is its only Iconify glyph once the rest is stubbed.
registerOfflineIcon(
  "solar:arrow-left-linear",
  bundled["solar:arrow-left-linear"],
);

const providerAlert = {
  id: "alert-provider",
  name: "Providers failing",
  project: "project-provider",
  metric_type: "service_provider_error_rates",
  metric: null,
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "current",
  critical_threshold_value: 0.6,
  warning_threshold_value: 0.2,
  alert_frequency: 5,
  filters: {},
};

const earlierProviderAlert = {
  ...providerAlert,
  threshold_rule: "earlier",
  critical_threshold_value: 95,
  warning_threshold_value: 90,
};

const POINTS = [
  { timestamp: "2026-11-11T12:00:00Z", value: 0.25 },
  { timestamp: "2026-11-11T12:05:00Z", value: 0.5 },
];
const STATIC_GRAPH = { result: POINTS };
const PERCENTAGE_GRAPH = {
  result: { graph_data: POINTS, alert_bar_data: [] },
};
const PREVIEW_GRAPH = {
  result: [...POINTS, { timestamp: "2026-11-11T12:10:00Z", value: 0.75 }],
};

const DATE_FILTER = {
  dateFilter: ["2026-11-11 00:00:00", "2026-11-11 23:59:59"],
};

let queryClient;

const lastChart = () => mocks.chart.mock.calls.at(-1)?.[0];
// A percentage-change chart draws a hidden band first; the named line is last.
const namedLine = () => lastChart()?.series?.at(-1);
const yAxisTitle = () => lastChart()?.options?.yaxis?.[0]?.title?.text;
// The share is typed and drawn from 0 to 1, never as a percent.
const SHARE_AXIS = "Share (0-1)";

const waitForLine = (points) =>
  waitFor(() => expect(namedLine()?.data).toHaveLength(points));

// Makes an edit in the drawer's form and waits for the chart drawn after it.
const editForm = async (testId) => {
  const drawnBefore = mocks.chart.mock.calls.length;
  fireEvent.click(await screen.findByTestId(testId));
  await waitFor(() =>
    expect(mocks.chart.mock.calls.length).toBeGreaterThan(drawnBefore),
  );
};

// Serves the preview read with an answer the test releases, so the drawer
// can be read while the preview is still on its way.
const deferPreview = () => {
  let answer;
  const pending = new Promise((resolve) => {
    answer = () => resolve({ data: PREVIEW_GRAPH });
  });
  vi.spyOn(axios, "post").mockReturnValue(pending);
  return answer;
};

// Puts a saved alert in the stores the way the sheet does, before the chart
// reads it, and serves the given graph for its saved graph read.
const openSavedAlert = (detail, graph) => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: graph });
  useAlertStore.setState({
    openSheetView: detail.id,
    openCreateAlerts: false,
    selectedProject: detail.project,
    alertType: detail.metric_type,
    mainPage: false,
  });
  useAlertSheetStore.setState({
    alertRuleDetails: normalizeAlertDetail(detail),
    gridRef: { current: null },
  });
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
};

const renderDrawer = () =>
  renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <AlertConfiguration dateFilter={DATE_FILTER} setDateFilter={vi.fn()} />
    </QueryClientProvider>,
  );

const renderSheet = () =>
  renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <AlertsSheetView />
    </QueryClientProvider>,
  );

beforeEach(() => {
  resetAlertStoreState();
  resetAlertSheetStoreState();
  mocks.chart.mockClear();
});

afterEach(() => {
  cleanup();
  queryClient?.clear();
  vi.restoreAllMocks();
  resetAlertStoreState();
  resetAlertSheetStoreState();
});

describe("the edit drawer's chart for a service provider error rate alert", () => {
  it("names an earlier alert's saved line the share with no errors, on a 0 to 1 axis", async () => {
    openSavedAlert(earlierProviderAlert, STATIC_GRAPH);
    renderDrawer();

    await waitForLine(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with no errors");
    expect(yAxisTitle()).toBe(SHARE_AXIS);
    expect(axios.get).toHaveBeenCalledWith(
      endpoints.project.getAlertGraph("alert-provider"),
      expect.any(Object),
    );
  });

  it("names an earlier percentage-change alert's saved line the share with no errors", async () => {
    openSavedAlert(
      { ...earlierProviderAlert, threshold_type: "percentage_change" },
      PERCENTAGE_GRAPH,
    );
    renderDrawer();

    await waitForLine(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with no errors");
  });

  it("names a current-rule alert's line the share with errors, on a 0 to 1 axis", async () => {
    openSavedAlert(providerAlert, STATIC_GRAPH);
    renderDrawer();

    await waitForLine(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with errors");
    expect(yAxisTitle()).toBe(SHARE_AXIS);
  });

  it("names the preview of an edited earlier alert the share with errors", async () => {
    openSavedAlert(earlierProviderAlert, STATIC_GRAPH);
    vi.spyOn(axios, "post").mockResolvedValue({ data: PREVIEW_GRAPH });
    renderDrawer();
    await waitForLine(POINTS.length);

    fireEvent.click(await screen.findByTestId("edit-alert-form"));

    await waitForLine(PREVIEW_GRAPH.result.length);
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.project.getAlertGraphPreview,
      expect.any(Object),
      expect.any(Object),
    );
    expect(namedLine().name).toBe("Share of providers with errors");
    expect(yAxisTitle()).toBe(SHARE_AXIS);
  });

  it("keeps an earlier alert's saved line the share with no errors while an edit is invalid", async () => {
    openSavedAlert(earlierProviderAlert, STATIC_GRAPH);
    vi.spyOn(axios, "post").mockResolvedValue({ data: PREVIEW_GRAPH });
    renderDrawer();
    await waitForLine(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with no errors");

    await editForm("edit-alert-form-invalid");

    // No preview runs, so the drawer still draws the saved line.
    expect(axios.post).not.toHaveBeenCalled();
    expect(namedLine().data).toHaveLength(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with no errors");
    expect(yAxisTitle()).toBe(SHARE_AXIS);
  });

  it("keeps an earlier alert's saved line the share with no errors until the edit's preview arrives", async () => {
    openSavedAlert(earlierProviderAlert, STATIC_GRAPH);
    const answerPreview = deferPreview();
    renderDrawer();
    await waitForLine(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with no errors");

    await editForm("edit-alert-form");

    // The preview has been asked for and has not answered yet.
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(namedLine().data).toHaveLength(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with no errors");

    answerPreview();

    await waitForLine(PREVIEW_GRAPH.result.length);
    expect(namedLine().name).toBe("Share of providers with errors");
    expect(yAxisTitle()).toBe(SHARE_AXIS);
  });

  it("keeps a current-rule alert's line the share with errors through an edit", async () => {
    openSavedAlert(providerAlert, STATIC_GRAPH);
    const answerPreview = deferPreview();
    renderDrawer();
    await waitForLine(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with errors");

    await editForm("edit-alert-form-invalid");
    expect(namedLine().name).toBe("Share of providers with errors");

    await editForm("edit-alert-form");
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(namedLine().data).toHaveLength(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with errors");

    answerPreview();

    await waitForLine(PREVIEW_GRAPH.result.length);
    expect(namedLine().name).toBe("Share of providers with errors");
  });

  it("keeps another metric's own name and unit", async () => {
    openSavedAlert(
      {
        ...earlierProviderAlert,
        metric_type: "count_of_errors",
        metric_name: "Count of errors",
      },
      STATIC_GRAPH,
    );
    renderDrawer();

    await waitForLine(POINTS.length);
    expect(namedLine().name).toBe("Count Of Errors");
    expect(yAxisTitle()).toBe("Count");
  });
});

describe("the sheet's chart for a service provider error rate alert", () => {
  it("names an earlier alert's line the share with no errors, on a 0 to 1 axis", async () => {
    openSavedAlert(earlierProviderAlert, STATIC_GRAPH);
    renderSheet();

    await waitForLine(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with no errors");
    expect(yAxisTitle()).toBe(SHARE_AXIS);
  });

  it("names a current-rule percentage-change alert's line the share with errors", async () => {
    openSavedAlert(
      { ...providerAlert, threshold_type: "percentage_change" },
      PERCENTAGE_GRAPH,
    );
    renderSheet();

    await waitForLine(POINTS.length);
    expect(namedLine().name).toBe("Share of providers with errors");
  });
});
