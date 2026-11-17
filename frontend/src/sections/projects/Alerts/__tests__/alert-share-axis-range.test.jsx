import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderWithRouter, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import { getSimpleLineChartConfig } from "../common";
import AlertConfiguration from "../components/AlertConfiguration";
// Loaded up front, so the drawer's lazily loaded form mounts without a wait.
import "../components/AlertSettingsForm";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// Error rates, the error-free session rate and the service provider error
// rate are shares from 0 to 1. A static chart of one draws its y-axis over
// that whole scale, whatever the points: a share near 1 never runs the ticks
// past 1, and a share that stays at 0 still has the scale to sit on. Every
// other metric keeps an axis fitted to its points.

const SHARE_METRICS = [
  "llm_api_failure_rates",
  "error_rates_for_function_calling",
  "error_free_session_rates",
  "service_provider_error_rates",
];
const RATE_METRICS = [
  "llm_api_failure_rates",
  "error_rates_for_function_calling",
  "error_free_session_rates",
];
// The two levels a new static alert on each starts with: the two failure
// shares Above 0.2 and 0.1, the error-free session rate, higher when healthy,
// Below 0.9 and 0.95.
const STARTING_LEVELS = {
  llm_api_failure_rates: [0.2, 0.1],
  error_rates_for_function_calling: [0.2, 0.1],
  error_free_session_rates: [0.9, 0.95],
};

const TIMES = [
  "2026-03-02T09:00:00Z",
  "2026-03-02T09:05:00Z",
  "2026-03-02T09:10:00Z",
];
const pointsOf = (values) =>
  values.map((value, index) => ({ timestamp: TIMES[index], value }));

// The create drawer's chart is the preview of the values the form starts
// with, drawn over the points the preview returns.
const PREVIEW_GRAPH = { result: pointsOf([0.91, 0.95, 0.98]) };
const DATE_FILTER = {
  dateFilter: ["2026-03-02 00:00:00", "2026-03-02 23:59:59"],
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

const chartOptions = (values, unitKey, thresholds) =>
  getSimpleLineChartConfig(
    { result: pointsOf(values) },
    { seriesName: "Line", unitKey, ...(thresholds && { thresholds }) },
  ).options;

// A chart with threshold bands keeps its one y-axis in a list.
const yAxisOf = (options) =>
  Array.isArray(options?.yaxis) ? options.yaxis[0] : options?.yaxis;

let queryClient;

const lastChart = () => mocks.chart.mock.calls.at(-1)?.[0];
const namedLine = () => lastChart()?.series?.at(-1);
const yAxis = () => yAxisOf(lastChart()?.options);
const bandEdges = () =>
  (lastChart()?.options?.annotations?.yaxis ?? []).map((band) => band.y);

const openCreateDrawer = (metricType) => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
  vi.spyOn(axios, "post").mockResolvedValue({ data: PREVIEW_GRAPH });
  useAlertStore.setState({
    openSheetView: null,
    openCreateAlerts: true,
    selectedProject: "project-new",
    alertType: metricType,
    mainPage: false,
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });
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

describe("the y-axis of a static chart of a share", () => {
  it.each(SHARE_METRICS)(
    "stops at 1 for %s with values up to 0.98",
    (metricType) => {
      const axis = yAxisOf(chartOptions([0.93, 0.98], metricType));

      expect(axis.max).toBe(1);
      expect(axis.min).toBe(0);
    },
  );

  it("stops at 1 for a share that reaches 1", () => {
    const axis = yAxisOf(chartOptions([0.5, 1], "error_free_session_rates"));

    expect(axis.max).toBe(1);
    expect(axis.min).toBe(0);
  });

  it("keeps the whole 0 to 1 scale for a share that stays at 0", () => {
    const axis = yAxisOf(chartOptions([0, 0], "llm_api_failure_rates"));

    expect(axis.max).toBe(1);
    expect(axis.min).toBe(0);
  });

  it("holds threshold bands at 0.2 and 0.1 inside its 0 to 1 range", () => {
    // As the sheet and the drawers build them for Above: the top band runs
    // from far above the points down to the critical, then critical to
    // warning, then warning to the bottom.
    const thresholds = [
      { value: 110, y2: 0.2 },
      { value: 0.2, y2: 0.1 },
      { value: 0.1, y2: 0 },
    ];
    const options = chartOptions(
      [0.93, 0.98],
      "error_free_session_rates",
      thresholds,
    );
    const axis = yAxisOf(options);

    expect(axis.max).toBe(1);
    expect(axis.min).toBe(0);
    const edges = options.annotations.yaxis.map((band) => band.y);
    for (const edge of [0.2, 0.1]) {
      expect(edges).toContain(edge);
      expect(edge).toBeGreaterThanOrEqual(axis.min);
      expect(edge).toBeLessThanOrEqual(axis.max);
    }
  });
});

describe("the y-axis of a static chart of a metric that is not a share", () => {
  it("fits a count of errors to its points, as before", () => {
    const axis = yAxisOf(chartOptions([3, 12], "count_of_errors"));

    expect(axis.max).toBe(14);
    expect(axis.min).toBe(2);
  });

  it.each([
    { metricType: "span_response_time", values: [180, 937], max: 1031 },
    { metricType: "token_usage", values: [1200, 4321], max: 4754 },
  ])(
    "fits $metricType to its points, as before",
    ({ metricType, values, max }) => {
      expect(yAxisOf(chartOptions(values, metricType)).max).toBe(max);
    },
  );

  // An eval alert can draw a mean score with no upper bound, so its axis is
  // still fitted to its points and padded past 1.
  it("pads an eval alert's axis past its points, as before", () => {
    const axis = yAxisOf(chartOptions([0.93, 0.98], "evaluation_metrics"));

    expect(axis.max).toBe(2);
    expect(axis.min).toBe(0);
  });
});

describe("the create drawer's chart for a new static share alert", () => {
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

  it.each(RATE_METRICS)(
    "runs the y-axis from 0 to 1 for %s, with the starting bands inside it",
    async (metricType) => {
      openCreateDrawer(metricType);

      await waitFor(() =>
        expect(namedLine()?.data).toHaveLength(PREVIEW_GRAPH.result.length),
      );
      await waitFor(() => expect(bandEdges()).toHaveLength(3));

      expect(yAxis().max).toBe(1);
      expect(yAxis().min).toBe(0);
      // The bands sit at the values the form starts with, inside the axis.
      expect(bandEdges()).toEqual(
        expect.arrayContaining(STARTING_LEVELS[metricType]),
      );
    },
  );
});
