import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  renderWithRouter,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import {
  getCompareChartConfig,
  getSimpleLineChartConfig,
  normalizeAlertDetail,
} from "../common";
import AlertConfiguration from "../components/AlertConfiguration";
// Loaded up front, so the drawer's lazily loaded form mounts without a wait.
import "../components/AlertSettingsForm";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// Error rates and the error-free session rate are shares from 0 to 1, and a
// static threshold on them is typed and compared on that scale: 0.2 is a
// share of 0.2, not 0.2%. The chart's y-axis names the same scale, with no
// "%", and its ticks print the numbers as the threshold fields take them.

const SHARE_AXIS = "Share (0-1)";
const SHARE_METRICS = [
  "llm_api_failure_rates",
  "error_rates_for_function_calling",
  "error_free_session_rates",
  "service_provider_error_rates",
];
// What the provider threshold fields say about the number typed in them.
const PROVIDER_FIELD_UNIT = "Share of providers that had an error, from 0 to 1";

const POINTS = [
  { timestamp: "2026-11-11T12:00:00Z", value: 0.25 },
  { timestamp: "2026-11-11T12:05:00Z", value: 0.5 },
];
const STATIC_GRAPH = { result: POINTS };
const PREVIEW_GRAPH = {
  result: [...POINTS, { timestamp: "2026-11-11T12:10:00Z", value: 0.75 }],
};
const DATE_FILTER = {
  dateFilter: ["2026-11-11 00:00:00", "2026-11-11 23:59:59"],
};

// A provider error rate alert, as the details response sends it.
const providerAlert = {
  id: "alert-provider",
  name: "Providers failing",
  project: "project-provider",
  metric_type: "service_provider_error_rates",
  metric: null,
  metric_name: "Service provider error rates",
  threshold_metric_value: "",
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "current",
  critical_threshold_value: 0.6,
  warning_threshold_value: 0.2,
  alert_frequency: 5,
  auto_threshold_time_window: 5,
  filters: {},
  notification_emails: ["alerts@example.test"],
  slack_webhook_url: "",
  slack_notes: "",
};

const mocks = vi.hoisted(() => ({ chart: vi.fn(() => null) }));

vi.mock("../components/AlertsChart", async (importOriginal) => ({
  ...(await importOriginal()),
  default: mocks.chart,
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

// The form's Iconify glyphs: the pickers' arrow and clear buttons, and the
// filter bar's add button.
for (const name of [
  "solar:alt-arrow-up-linear",
  "solar:close-linear",
  "solar:add-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

let queryClient;

const lastChart = () => mocks.chart.mock.calls.at(-1)?.[0];
const namedLine = () => lastChart()?.series?.at(-1);
// A static chart with threshold bands keeps its one y-axis in a list.
const yAxis = () => lastChart()?.options?.yaxis?.[0];
const bandEdges = () =>
  (lastChart()?.options?.annotations?.yaxis ?? []).map((band) => band.y);

const field = (name) => document.querySelector(`[data-alert-field="${name}"]`);
// The text a value field shows under itself, as its helper text.
const underField = (name) =>
  within(field(name).closest(".MuiFormControl-root"));

const renderDrawer = () => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <SnackbarProvider>
        <AlertConfiguration dateFilter={DATE_FILTER} setDateFilter={vi.fn()} />
      </SnackbarProvider>
    </QueryClientProvider>,
  );
};

// The create drawer for a new provider alert: its chart is the preview of
// the values the form starts with.
const openCreateDrawer = () => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
  vi.spyOn(axios, "post").mockResolvedValue({ data: PREVIEW_GRAPH });
  useAlertStore.setState({
    openSheetView: null,
    openCreateAlerts: true,
    selectedProject: "project-new",
    alertType: "service_provider_error_rates",
    mainPage: false,
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });
  renderDrawer();
};

// The edit drawer for a saved provider alert: its chart is the saved graph.
const openEditDrawer = () => {
  const graphUrl = endpoints.project.getAlertGraph(providerAlert.id);
  vi.spyOn(axios, "get").mockImplementation((url) =>
    Promise.resolve({
      data: url === graphUrl ? STATIC_GRAPH : { result: [] },
    }),
  );
  useAlertStore.setState({
    openSheetView: providerAlert.id,
    openCreateAlerts: true,
    selectedProject: providerAlert.project,
    alertType: providerAlert.metric_type,
    duplicateAlertName: "",
    mainPage: false,
  });
  useAlertSheetStore.setState({
    alertRuleDetails: normalizeAlertDetail(providerAlert),
    gridRef: { current: null },
  });
  renderDrawer();
};

describe("the y-axis of a static alert chart", () => {
  it.each(SHARE_METRICS)(
    "names a share from 0 to 1 and prints ticks on that scale for %s",
    (metricType) => {
      const { options } = getSimpleLineChartConfig(
        { result: POINTS },
        { seriesName: "Line", unitKey: metricType },
      );

      expect(options.yaxis.title.text).toBe(SHARE_AXIS);
      expect(options.yaxis.title.text).not.toContain("%");
      expect(options.yaxis.labels.formatter(0.25)).toBe("0.25");
    },
  );

  it("reads a plain value for an eval alert, which draws a mean score or the share of one label", () => {
    const { options } = getSimpleLineChartConfig(
      { result: POINTS },
      { seriesName: "Line", unitKey: "evaluation_metrics" },
    );

    expect(options.yaxis.title.text).toBe("Value");
    expect(options.yaxis.labels.formatter(0.25)).toBe("0.25");
  });

  it.each([
    { metricType: "count_of_errors", unit: "Count" },
    { metricType: "token_usage", unit: "Count" },
    { metricType: "span_response_time", unit: "ms" },
    { metricType: "llm_response_time", unit: "ms" },
  ])("keeps $metricType on its own unit, $unit", ({ metricType, unit }) => {
    const { options } = getSimpleLineChartConfig(
      { result: POINTS },
      { seriesName: "Line", unitKey: metricType },
    );

    expect(options.yaxis.title.text).toBe(unit);
  });
});

describe("the y-axis of a percentage change alert chart", () => {
  it("keeps its plain value title and prints a share as typed", () => {
    const { options } = getCompareChartConfig(
      { result: { graph_data: POINTS, alert_bar_data: [] } },
      { seriesName: "Line", unitKey: "service_provider_error_rates" },
    );

    expect(options.yaxis.title.text).toBe("Value");
    expect(options.yaxis.labels.formatter(0.25)).toBe("0.25");
  });
});

describe("the drawers' chart for a static provider error rate alert", () => {
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

  it("titles the create drawer's y-axis with the 0 to 1 share the threshold fields take", async () => {
    openCreateDrawer();

    await waitFor(() =>
      expect(namedLine()?.data).toHaveLength(PREVIEW_GRAPH.result.length),
    );
    await waitFor(() =>
      expect(field("critical-threshold-value")).toHaveValue(0.2),
    );
    expect(
      underField("critical-threshold-value").getByText(PROVIDER_FIELD_UNIT),
    ).toBeInTheDocument();
    expect(
      underField("warning-threshold-value").getByText(PROVIDER_FIELD_UNIT),
    ).toBeInTheDocument();
    // The bands sit at the numbers typed in the fields, on the same axis.
    await waitFor(() =>
      expect(bandEdges()).toEqual(expect.arrayContaining([0.2, 0.1])),
    );
    expect(yAxis().labels.formatter(0.2)).toBe("0.20");

    expect(yAxis().title.text).toBe(SHARE_AXIS);
    expect(yAxis().title.text).not.toContain("%");
  });

  it("titles the edit drawer's y-axis with the 0 to 1 share the threshold fields take", async () => {
    openEditDrawer();

    await waitFor(() => expect(namedLine()?.data).toHaveLength(POINTS.length));
    await waitFor(() =>
      expect(field("critical-threshold-value")).toHaveValue(0.6),
    );
    expect(
      underField("critical-threshold-value").getByText(PROVIDER_FIELD_UNIT),
    ).toBeInTheDocument();
    expect(
      underField("warning-threshold-value").getByText(PROVIDER_FIELD_UNIT),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(bandEdges()).toEqual(expect.arrayContaining([0.6, 0.2])),
    );
    expect(yAxis().labels.formatter(0.6)).toBe("0.60");

    expect(yAxis().title.text).toBe(SHARE_AXIS);
    expect(yAxis().title.text).not.toContain("%");
  });
});
